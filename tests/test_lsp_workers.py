"""Cache correctness and isolation of interactive work from background ERC."""

import asyncio
import os
import time

import pytest

from noscheme.analysis_cache import AnalysisCache, ParseCache
from noscheme.diagnostics import DiagnosticError
from noscheme.lsp_analysis import analyze
from noscheme.lsp_transport import WorkerLane
from noscheme.lsp_worker import Worker
from noscheme.parser import parse
from test_circuit import make
from test_lsp import SOURCE, Client, open_document


def request(root, operation="analysis", **changes):
    result = dict(
        operation=operation,
        generation=1,
        sources={},
        roots=[root],
        explicit=root,
        paths=[root / "main.nosch"],
        path=root / "main.nosch",
        offset=0,
    )
    result.update(changes)
    return result


def test_parse_cache_failures_eviction_and_opt_in():
    cache = AnalysisCache(ParseCache(max_entries=2))
    with cache.activate():
        original = parse(SOURCE, "main.nosch")
        assert parse(SOURCE, "main.nosch") is original
        for _ in range(2):
            with pytest.raises(DiagnosticError):
                parse("@bad", "bad.nosch")
        assert cache.parses.misses == 2
        assert cache.parses.hits == 2
        parse(SOURCE + "\n", "main.nosch")
        parse(SOURCE, "main.nosch")
        assert cache.parses.misses == 4
    assert parse(SOURCE, "main.nosch") is not original
    small = ParseCache(max_bytes=1)
    small.parse(parse, SOURCE, "main.nosch")
    assert not small.entries and small.bytes == 0


def test_worker_reuses_parses_and_checks_board_once(tmp_path, monkeypatch):
    make(tmp_path, "")
    (tmp_path / "main.nosch").write_text(SOURCE)
    extra = tmp_path / "extra.nosch"
    extra.write_text("component Extra:\n    pins:\n        1:\n")
    import noscheme.lsp_analysis as analysis

    original = analysis.apply_erc
    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(analysis, "apply_erc", counted)
    worker = Worker()
    req = request(tmp_path, "diagnostics", paths=[tmp_path / "main.nosch", extra])
    first = worker.execute(req)
    assert first["valid"]
    assert len(calls) == 1
    assert first["value"][extra] is first["value"][tmp_path / "main.nosch"]
    misses = worker.parses.misses
    assert worker.execute(req) == first
    assert worker.parses.misses == misses and len(calls) == 1
    worker.execute(req | {"operation": "analysis"})
    assert len(calls) == 1  # Interactive semantic work never invokes ERC.
    worker.execute(req | {"generation": 2, "sources": {extra: extra.read_text() + "\n"}})
    assert worker.parses.misses == misses + 1 and len(calls) == 2


def test_worker_disk_edit_same_size_and_mtime_and_delete(tmp_path):
    make(tmp_path, "")
    (tmp_path / "main.nosch").write_text(SOURCE)
    path = tmp_path / "main.nosch"
    worker = Worker()
    initial = worker.execute(request(tmp_path))
    stat = path.stat()
    path.write_text(path.read_text().replace("Receiver", "Listener"))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    updated = worker.execute(request(tmp_path))
    assert updated["valid"]
    assert updated["sources"][path] != initial["sources"][path]
    assert updated["value"][path] == analyze(path, tmp_path, {}, run_erc=False)
    path.unlink()
    assert worker.execute(request(tmp_path))["value"][path].diagnostics
    path.write_text(SOURCE)
    assert worker.execute(request(tmp_path))["value"][path] == initial["value"][path]


def test_crlf_cache_validity_and_unreachable_syntax_errors(tmp_path):
    make(tmp_path, "")
    (tmp_path / "main.nosch").write_text(SOURCE)
    path = tmp_path / "main.nosch"
    path.write_bytes(SOURCE.replace("\n", "\r\n").encode())
    extra = tmp_path / "extra.nosch"
    extra.write_text("@bad")
    result = Worker().execute(request(tmp_path, paths=[path, extra]))
    assert result["valid"]
    assert any(d.span.filename == str(extra) for d in result["value"][path].diagnostics)
    assert result["value"][path].references


def test_config_and_plugin_same_timestamp_updates(tmp_path):
    make(tmp_path, "")
    (tmp_path / "main.nosch").write_text(SOURCE)
    config = tmp_path / "noscheme.toml"
    config.write_text(config.read_text() + 'erc_modules = ["checks.py"]\n')
    plugin = tmp_path / "checks.py"
    plugin.write_text('def register(r):\n    raise ValueError("first")\n')
    stat = plugin.stat()
    worker = Worker()
    first = worker.execute(request(tmp_path))
    assert any("first" in d.message for d in first["value"][tmp_path / "main.nosch"].diagnostics)
    plugin.write_text(plugin.read_text().replace("first", "other"))
    os.utime(plugin, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    second = worker.execute(request(tmp_path))
    assert any("other" in d.message for d in second["value"][tmp_path / "main.nosch"].diagnostics)
    config.write_text(config.read_text().replace('erc_modules = ["checks.py"]', "erc_modules = []"))
    third = worker.execute(request(tmp_path))
    assert not any(
        d.code == "erc.plugin_load" for d in third["value"][tmp_path / "main.nosch"].diagnostics
    )


def test_worker_transport_recovery_and_shutdown(tmp_path):
    make(tmp_path, "")
    (tmp_path / "main.nosch").write_text(SOURCE)

    async def exercise():
        lane = WorkerLane()
        try:
            first = await lane.call(request(tmp_path))
            process = lane.process
            process.kill()
            await process.wait()
            assert await lane.call(request(tmp_path)) == first
            assert lane.process.pid != process.pid
            with pytest.raises(ValueError):
                await lane.call({})
            assert lane.process is None
            assert await lane.call(request(tmp_path)) == first
        finally:
            process = lane.process
            await lane.close()
            assert process.returncode is not None

    asyncio.run(exercise())


def test_slow_erc_does_not_block_navigation_or_publish_stale_edits(tmp_path):
    make(tmp_path, "")
    (tmp_path / "main.nosch").write_text(SOURCE)
    config = tmp_path / "noscheme.toml"
    config.write_text(
        config.read_text() + 'erc_modules = ["checks.py"]\nerc_rule_sets = ["test.rules"]\n'
    )
    started, release = tmp_path / "started", tmp_path / "release"
    (tmp_path / "checks.py").write_text(f"""from pathlib import Path
import time
from noscheme.erc_registry import Application

def register(r):
    r.net_rule("test.slow", check)
    r.rule_set("test.rules", lambda ctx: [Application("slow", "test.slow")] if ctx.net and not ctx.pin else [])

def check(ctx):
    Path({str(started)!r}).touch()
    deadline = time.monotonic() + 15
    while not Path({str(release)!r}).exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    return ()
""")
    client = Client(tmp_path)
    try:
        client.request(
            "initialize", {"processId": None, "rootUri": tmp_path.as_uri(), "capabilities": {}}
        )
        client.send("initialized", {})
        path = tmp_path / "main.nosch"
        open_document(client, path, SOURCE)
        deadline = time.monotonic() + 10
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert started.exists()
        params = {"textDocument": {"uri": path.as_uri()}, "position": {"line": 7, "character": 6}}
        assert "Receiver" in client.request("textDocument/hover", params)["contents"]["value"]
        for version in (2, 3):
            client.send(
                "textDocument/didChange",
                {
                    "textDocument": {"uri": path.as_uri(), "version": version},
                    "contentChanges": [{"text": SOURCE + "\n" * version}],
                },
            )
        # This response proves the main loop has processed both edits while ERC waits.
        assert client.request("textDocument/definition", params)
        assert not release.exists()
        release.touch()
        client.diagnostics(path.as_uri(), 3)
        assert not any(
            m.get("method") == "textDocument/publishDiagnostics"
            and m["params"].get("version") in (1, 2)
            for m in client.saved
        )
        assert client.request("shutdown") is None
    finally:
        release.touch()
        client.close()


def test_stale_interactive_result_rejected(tmp_path):
    from noscheme.lsp import NoSchemeServer
    from pygls.exceptions import JsonRpcContentModified

    async def exercise():
        server = NoSchemeServer(tmp_path)
        server.sources = lambda: {}
        server.roots = lambda: [tmp_path]
        started, release = asyncio.Event(), asyncio.Event()

        async def delayed(request):
            started.set()
            await release.wait()
            return {"value": [], "sources": {}, "valid": True}

        server.interactive.call = delayed
        task = asyncio.create_task(server.request_analysis("rename"))
        await started.wait()
        server.generation += 1
        release.set()
        with pytest.raises(JsonRpcContentModified):
            await task

    asyncio.run(exercise())


def test_cancellation_terminates_worker_and_next_request_recovers(tmp_path):
    make(tmp_path, "")
    (tmp_path / "main.nosch").write_text(SOURCE)
    config = tmp_path / "noscheme.toml"
    config.write_text(config.read_text() + 'erc_modules = ["checks.py"]\n')
    started = tmp_path / "started"
    plugin = tmp_path / "checks.py"
    plugin.write_text(
        f"from pathlib import Path\nimport time\ndef register(r):\n"
        f"    Path({str(started)!r}).touch()\n    time.sleep(30)\n"
    )

    async def exercise():
        lane = WorkerLane()
        task = asyncio.create_task(lane.call(request(tmp_path)))
        try:
            async with asyncio.timeout(10):
                while not started.exists():
                    await asyncio.sleep(0.01)
            process = lane.process
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert process.returncode is not None and lane.process is None
            plugin.write_text("def register(r):\n    pass\n")
            assert (await lane.call(request(tmp_path)))["valid"]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await lane.close()

    asyncio.run(exercise())


def test_missing_config_and_plugin_creation_invalidates_cache(tmp_path):
    path = tmp_path / "main.nosch"
    path.write_text(SOURCE)
    worker = Worker()
    first = worker.execute(request(tmp_path))
    assert first["value"][path].diagnostics[0].code == "lsp.io"
    make(tmp_path, "")
    path.write_text(SOURCE)
    config = tmp_path / "noscheme.toml"
    config.write_text(config.read_text() + 'erc_modules = ["checks.py"]\n')
    assert worker.execute(request(tmp_path))["value"][path].diagnostics
    (tmp_path / "checks.py").write_text("def register(r):\n    pass\n")
    result = worker.execute(request(tmp_path))
    assert not any(d.severity == "error" for d in result["value"][path].diagnostics)


def test_import_creation_and_library_path_change(tmp_path):
    make(tmp_path, "")
    source = SOURCE.replace(
        "component Receiver:\n    pins:\n        signal:\n            role: role.digital_input\n",
        "from parts import Receiver\n",
    )
    path = tmp_path / "main.nosch"
    path.write_text(source)
    worker = Worker()
    assert any(
        d.severity == "error" for d in worker.execute(request(tmp_path))["value"][path].diagnostics
    )
    library = tmp_path / "library"
    library.mkdir()
    part = library / "parts.nosch"
    part.write_text(
        "from noscheme import role\ncomponent Receiver:\n    pins:\n        signal:\n            role: role.digital_input\n"
    )
    config = tmp_path / "noscheme.toml"
    config.write_text(
        config.read_text().replace("library_paths = []", 'library_paths = ["library"]')
    )
    result = worker.execute(request(tmp_path))
    assert not any(d.severity == "error" for d in result["value"][path].diagnostics)
    misses = worker.parses.misses
    stat = part.stat()
    part.write_text(part.read_text().replace("signal:", "foobar:"))
    os.utime(part, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    result = worker.execute(request(tmp_path))
    assert any(d.severity == "error" for d in result["value"][path].diagnostics)
    assert worker.parses.misses == misses + 1


def test_standalone_files_all_receive_syntax_diagnostics(tmp_path):
    first, second = tmp_path / "first.nosch", tmp_path / "second.nosch"
    first.write_text(SOURCE)
    second.write_text("@bad")
    result = Worker().execute(request(tmp_path, explicit=None, paths=[first, second]))
    assert any(d.span.filename == str(second) for d in result["value"][second].diagnostics)
