"""Black-box stdio protocol tests for the experimental native server."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from tools.lsp_client import LspClient, frame

SERVER = os.environ.get("NOSCHEME_NATIVE_LSP")
pytestmark = pytest.mark.skipif(not SERVER, reason="native server supplied by Meson")
URI = "file:///tmp/a%20space.nosch"


def message(method, params=None, **extra):
    return {"jsonrpc": "2.0", "method": method, "params": params or {}, **extra}


def run(messages, *, launcher=False, suffix=b"", timeout=15, project=None):
    command = [SERVER, "--python", sys.executable]
    if launcher:
        command = [
            sys.executable,
            "-m",
            "noscheme.lsp",
            "--implementation",
            "native",
            "--native-server",
            SERVER,
        ]
    if project is not None:
        command.extend(["--project", str(project)])
    with Client(command) as client:
        versions = {}
        for entry in messages:
            client.send(entry)
            if not isinstance(entry, dict):
                continue
            method = entry.get("method", "")
            if method not in {
                "textDocument/didOpen",
                "textDocument/didChange",
                "textDocument/didClose",
            }:
                continue
            doc = entry["params"]["textDocument"]
            uri = doc["uri"]
            if method == "textDocument/didClose":
                versions.pop(uri, None)
                version = None
            else:
                version = doc["version"]
                if method == "textDocument/didChange" and (
                    uri not in versions or version <= versions[uri]
                ):
                    continue
                invalid = any("range" in c for c in entry["params"].get("contentChanges", []))
                if not invalid:
                    versions[uri] = version
            client.until(
                lambda r: r.get("method") == "window/logMessage"
                or (
                    r.get("method") == "textDocument/publishDiagnostics"
                    and r["params"]["uri"] == uri
                    and r["params"].get("version") == version
                ),
                timeout,
            )
        if suffix:
            client.process.stdin.write(suffix)
            client.process.stdin.flush()
        client.process.stdin.close()
        client.process.wait(timeout=timeout)
        client.reader.join(timeout=timeout)
        assert not client.reader.is_alive()
        client.stderr.seek(0)
        return subprocess.CompletedProcess(
            command, client.process.returncode, b"", client.stderr.read()
        ), client.replies


class Client(LspClient):
    def __init__(self, command=None):
        super().__init__(command or [SERVER, "--python", sys.executable])


def opening(text, version=1):
    return message(
        "textDocument/didOpen",
        {"textDocument": {"uri": URI, "languageId": "noscheme", "version": version, "text": text}},
    )


def change(text, version=2):
    return message(
        "textDocument/didChange",
        {"textDocument": {"uri": URI, "version": version}, "contentChanges": [{"text": text}]},
    )


def session(*messages, launcher=False, timeout=15):
    return run(
        [
            message("initialize", id="init"),
            message("initialized"),
            *messages,
            message("shutdown", id=9),
            message("exit"),
        ],
        launcher=launcher,
        timeout=timeout,
    )


@pytest.mark.parametrize("launcher", [False, True])
def test_lifecycle_and_capabilities(launcher):
    result, replies = session(launcher=launcher)
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    assert replies[0]["id"] == "init"
    assert replies[0]["result"]["capabilities"] == {
        "positionEncoding": "utf-16",
        "codeActionProvider": {"codeActionKinds": ["refactor.rewrite"]},
        "completionProvider": {"triggerCharacters": [".", '"', ":", "<", "(", ","]},
        "definitionProvider": True,
        "referencesProvider": True,
        "documentFormattingProvider": True,
        "renameProvider": {"prepareProvider": True},
        "hoverProvider": True,
        "textDocumentSync": {"openClose": True, "change": 1, "save": True},
    }
    assert replies[1] == {"jsonrpc": "2.0", "id": 9, "result": None}


def test_document_lifecycle():
    result, replies = session(
        opening("board B:\n  net\n"),
        change("board B:\n  Signal ok\n"),
        message("textDocument/didClose", {"textDocument": {"uri": URI}}),
    )
    assert result.returncode == 0, result.stderr
    diagnostics = [r["params"] for r in replies if "params" in r]
    assert diagnostics[0]["version"] == 1
    assert diagnostics[0]["diagnostics"]
    assert diagnostics[0]["diagnostics"][0]["source"] == "noscheme-native"
    assert diagnostics[1] == {"uri": URI, "version": 2, "diagnostics": []}
    assert diagnostics[2] == {"uri": URI, "diagnostics": []}


def test_stale_versions_and_reopen():
    result, replies = session(
        opening("board B:\n  Signal a\n", 10),
        change("bad", 9),
        change("bad", 10),
        message("textDocument/didClose", {"textDocument": {"uri": URI}}),
        change("bad", 11),
        opening("board B:\n  Signal a\n", 1),
    )
    assert result.returncode == 0
    diagnostics = [r["params"] for r in replies if "params" in r]
    assert len(diagnostics) == 3
    assert [d.get("version") for d in diagnostics] == [10, None, 1]
    assert all(d["diagnostics"] == [] for d in diagnostics)


def test_unicode_offsets_and_crlf():
    text = 'component C:\r\n  metadata:\r\n    value: "😀é" @\r\n'
    result, replies = session(opening(text))
    assert result.returncode == 0
    diagnostic = replies[1]["params"]["diagnostics"][0]
    assert diagnostic["range"]["start"] == {
        "line": 2,
        "character": len('    value: "😀é" '.encode("utf-16-le")) // 2,
    }


def test_bad_notification_does_not_destroy_document():
    bad = change("", 2)
    bad["params"]["contentChanges"][0]["range"] = {}
    result, replies = session(
        opening("board B:\n  Signal a\n"), bad, change("board B:\n  Signal b\n", 2)
    )
    assert result.returncode == 0
    assert replies[2]["method"] == "window/logMessage"
    assert replies[3]["params"]["version"] == 2
    assert replies[3]["params"]["diagnostics"] == []


def test_requests_and_notifications():
    result, replies = run(
        [
            message("unknown", id=0),
            message("initialized"),
            message("initialize", id=1),
            message("unknown", id=2),
            message("unknown"),
            message("shutdown", id=3),
            message("unknown", id=4),
            message("exit"),
        ]
    )
    assert result.returncode == 0
    assert [r["id"] for r in replies] == [0, 1, 2, 3, 4]
    assert [r["error"]["code"] for r in replies if "error" in r] == [-32002, -32601, -32600]


@pytest.mark.parametrize(
    "value,code",
    [
        (b"{", -32700),
        ([], -32600),
        ({"jsonrpc": "2.0", "method": 3}, -32600),
        (b"[" * 150 + b"0" + b"]" * 150, -32700),
    ],
)
def test_bad_json_recovery(value, code):
    result, replies = run(
        [value, message("initialize", id=1), message("shutdown", id=2), message("exit")]
    )
    assert result.returncode == 0
    assert replies[0]["error"]["code"] == code
    assert replies[1]["result"]["serverInfo"]["name"] == "noscheme-native"


@pytest.mark.parametrize(
    "header",
    [
        b"Content-Length: 999999999\r\n\r\n",
        b"X: x\r\n\r\n",
        b"Content-Length: 1\r\nContent-Length: 1\r\n\r\n",
        b"Content-Length: 10\r\n\r\nx",
        b"x" * 8193,
        b"Content-Length: -1\r\n\r\n",
    ],
)
def test_framing_failure(header):
    result, replies = run([], suffix=header)
    assert result.returncode == 1
    assert b"noscheme-native-lsp:" in result.stderr
    assert replies == []


def test_exit_without_shutdown():
    result, replies = run([message("exit")])
    assert result.returncode == 1
    assert replies == []


def test_example_sources():
    root = Path(__file__).resolve().parents[2]
    files = sorted(
        p
        for p in (root / "examples").rglob("*.nosch")
        if "build" not in p.relative_to(root).parts
        and not any(part.startswith(".") for part in p.relative_to(root).parts)
    )
    assert files
    messages = []
    for index, path in enumerate(files):
        opened = opening(path.read_text(), index)
        opened["params"]["textDocument"]["uri"] = "untitled:" + path.name + str(index)
        messages.append(opened)
        messages.append(
            message(
                "textDocument/didClose",
                {"textDocument": {"uri": opened["params"]["textDocument"]["uri"]}},
            )
        )
    result, replies = session(*messages, timeout=60)
    assert result.returncode == 0, result.stderr
    diagnostics = [r["params"] for r in replies if "params" in r]
    assert len({d["uri"] for d in diagnostics}) == len(files)
    assert not [d for d in diagnostics if d["diagnostics"]]


def project_files(tmp_path):
    root = tmp_path / "project with spaces"
    root.mkdir()
    (root / "noscheme.toml").write_text(
        'language_version = "0.1"\nboard = "main.B"\nsource_paths = ["."]\nlibrary_paths = []\noutput_dir = "build"\n'
    )
    source = root / "main.nosch"
    source.write_text(
        "from noscheme import net\nfrom parts import C\nboard B:\n  net.passive wire\n  C c(1: wire)\n"
    )
    parts = root / "parts.nosch"
    parts.write_text(
        'from noscheme import role\ncomponent C:\n  pins:\n    1: role.passive\n  metadata:\n    footprint: "Test:Footprint"\n'
    )
    return source, parts


def at_path(notification, path):
    notification["params"]["textDocument"]["uri"] = path.as_uri()
    return notification


def project_session(messages, **options):
    return run(
        [message("initialize", id=1), *messages, message("shutdown", id=2), message("exit")],
        **options,
    )


def test_project_erc_matches_python(tmp_path):
    from noscheme.lsp_analysis import analyze

    source, parts = project_files(tmp_path)
    expected = analyze(source, source.parent, {source: source.read_text()})
    result, replies = project_session([at_path(opening(source.read_text()), source)])
    assert result.returncode == 0, result.stderr
    assert not [r for r in replies if r.get("method") == "window/logMessage"], replies
    actual = [
        d
        for r in replies
        if r.get("method") == "textDocument/publishDiagnostics"
        for d in r["params"]["diagnostics"]
    ]
    assert sorted((d["code"], d["message"]) for d in actual) == sorted(
        (d.code, d.message) for d in expected.diagnostics
    )
    assert actual  # exercise ERC, not just an empty parse report
    assert not (source.parent / "build").exists()


def test_unsaved_import_and_close_restore_disk(tmp_path):
    source, parts = project_files(tmp_path)
    broken = parts.read_text().replace("role.passive", "role.not_a_role")
    result, replies = project_session(
        [
            at_path(opening(source.read_text()), source),
            at_path(opening(broken), parts),
            at_path(message("textDocument/didClose", {"textDocument": {}}), parts),
        ]
    )
    assert result.returncode == 0, result.stderr
    diagnostics = [
        r["params"] for r in replies if r.get("method") == "textDocument/publishDiagnostics"
    ]
    imported = [d for d in diagnostics if d["uri"] == parts.as_uri()]
    assert any(d["diagnostics"] for d in imported), replies
    assert imported[-1]["diagnostics"] == []
    assert any(d["uri"] == source.as_uri() and d["diagnostics"] for d in diagnostics)
    assert parts.read_text() != broken


def test_explicit_project_outside_ancestor(tmp_path):
    source, parts = project_files(tmp_path)
    unrelated = tmp_path / "scratch.nosch"
    result, replies = project_session(
        [at_path(opening("component Empty\n"), unrelated)], project=source.parent
    )
    assert result.returncode == 0, result.stderr
    assert any(r.get("params", {}).get("uri") == source.as_uri() for r in replies)


def test_unsaved_configuration_and_recovery(tmp_path):
    source, parts = project_files(tmp_path)
    config = source.parent / "noscheme.toml"
    result, replies = project_session(
        [
            at_path(opening(source.read_text()), source),
            at_path(opening("board = [\n"), config),
            at_path(change(config.read_text()), config),
        ]
    )
    assert result.returncode == 0, result.stderr
    published = [
        r["params"] for r in replies if r.get("method") == "textDocument/publishDiagnostics"
    ]
    config_diags = [d for d in published if d["uri"] == config.as_uri()]
    assert any(d["diagnostics"] for d in config_diags)
    assert config_diags[-1]["diagnostics"] == []
    assert config_diags[-1]["version"] == 2


@pytest.mark.parametrize(
    "config",
    sorted((Path(__file__).resolve().parents[2] / "examples").glob("*/noscheme.toml"))
    + [Path(__file__).resolve().parents[2] / "examples/wren/full/noscheme.toml"],
    ids=lambda p: p.parent.name,
)
def test_project_examples(config):
    from noscheme.lsp_analysis import analyze
    from noscheme.project import load_project

    project = load_project(config)
    source = project.modules[project.config.board_module].path
    expected = analyze(source, config, {source: source.read_text()})
    result, replies = project_session(
        [at_path(opening(source.read_text()), source)], project=config, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert not [r for r in replies if r.get("method") == "window/logMessage"], replies
    actual = [
        (r["params"]["uri"], d["code"], d["message"], d["severity"])
        for r in replies
        if r.get("method") == "textDocument/publishDiagnostics"
        for d in r["params"]["diagnostics"]
    ]
    assert sorted(actual) == sorted(
        (Path(d.span.filename).as_uri(), d.code, d.message, 1 if d.severity == "error" else 2)
        for d in expected.diagnostics
    )


def test_semantic_related_ranges_and_fix(tmp_path):
    source, parts = project_files(tmp_path)
    text = source.read_text()
    broken = text + "  net.passive wire\n"
    result, replies = project_session(
        [at_path(opening(broken), source), at_path(change(text), source)]
    )
    assert result.returncode == 0, result.stderr
    published = [
        r["params"] for r in replies if r.get("method") == "textDocument/publishDiagnostics"
    ]
    first = next(p for p in published if p["uri"] == source.as_uri() and p.get("version") == 1)
    related = [d for d in first["diagnostics"] if d.get("relatedInformation")]
    assert related, first
    assert related[0]["relatedInformation"][0]["location"]["uri"] == source.as_uri()
    last = [p for p in published if p["uri"] == source.as_uri()][-1]
    assert last["version"] == 2
    assert not set(d["code"] for d in related) & set(d["code"] for d in last["diagnostics"])


def test_rapid_edits_publish_latest_only():
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        updates = (
            [opening("bad", 1)]
            + [change("bad", i) for i in range(2, 100)]
            + [change("board B:\n  Signal a\n", 100)]
        )
        client.process.stdin.write(b"".join(frame(m) for m in updates))
        client.process.stdin.flush()
        reply = client.until(lambda r: r.get("method") == "textDocument/publishDiagnostics")
        assert reply["params"]["version"] == 100
        assert reply["params"]["diagnostics"] == []
        client.send(message("shutdown", id=2))
        client.until(lambda r: r.get("id") == 2)
        client.send(message("exit"))
        assert client.process.wait(timeout=3) == 0


@pytest.mark.parametrize("finish", ["edit", "shutdown", "eof"])
def test_busy_erc_is_cancelled(tmp_path, finish):
    source, _ = project_files(tmp_path)
    marker = source.parent / "started"
    plugin = source.parent / "rules.py"
    plugin.write_text("""import os
import time
from pathlib import Path
def register(registry):
    def slow(ctx):
        Path(__file__).with_name("started").write_text(str(os.getpid()))
        time.sleep(30)
    registry.net_rule("demo.slow", slow)
""")
    config = source.parent / "noscheme.toml"
    config.write_text(config.read_text() + 'erc_modules = ["rules.py"]\n')
    base = "from noscheme import net\nfrom demo import slow\nboard B:\n  net.passive a:\n"
    slow = base + "    checks:\n      slow_check: slow()\n"
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(slow), source))
        deadline = time.monotonic() + 15
        while not marker.exists() or not marker.read_text().isdigit():
            assert time.monotonic() < deadline, client.replies
            time.sleep(0.01)
        pid = int(marker.read_text())
        client.send(
            message(
                "textDocument/hover",
                {
                    "textDocument": {"uri": source.as_uri()},
                    "position": {"line": 3, "character": 14},
                },
                id="while-erc",
            )
        )
        hover = client.until(lambda r: r.get("id") == "while-erc", 2)["result"]
        assert "net a" in hover["contents"]["value"]
        client.send(message("unsupported", id="responsive"))
        assert client.until(lambda r: r.get("id") == "responsive", 2)["error"]["code"] == -32601
        if finish == "edit":
            client.send(at_path(change(base), source))
            reply = client.until(
                lambda r: r.get("method") == "textDocument/publishDiagnostics"
                and r["params"].get("version") == 2,
                10,
            )
            assert not any(d["code"].startswith("native.") for d in reply["params"]["diagnostics"])
            assert not any(r.get("params", {}).get("version") == 1 for r in client.replies)
        if finish == "eof":
            client.process.stdin.close()
        else:
            client.send(message("shutdown", id=2))
            client.until(lambda r: r.get("id") == 2, 2)
            client.send(message("exit"))
        assert client.process.wait(timeout=3) == 0
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def cursor(text, needle, *, last=False):
    offset = text.rindex(needle) if last else text.index(needle)
    before = text[:offset]
    return {
        "line": before.count("\n"),
        "character": len(before.rsplit("\n", 1)[-1].encode("utf-16-le")) // 2,
    }


def navigation(method, path, text, needle, request_id, *, last=False):
    return message(
        "textDocument/" + method,
        {"textDocument": {"uri": path.as_uri()}, "position": cursor(text, needle, last=last)},
        id=request_id,
    )


@pytest.mark.parametrize("markdown", [False, True])
def test_navigation_imports_locals_and_hover(tmp_path, markdown):
    from noscheme.lsp_analysis import analyze

    source, parts = project_files(tmp_path)
    text = (
        source.read_text()
        .replace("from parts import C", "from parts import C as Cap\nimport parts")
        .replace("C c(1: wire)", "Cap c(/*😀*/ 1: wire)\n  parts.C other(1: wire)")
    )
    expected = analyze(source, source.parent, {source: text})
    assert expected.references
    with Client() as client:
        client.send(
            message(
                "initialize",
                {
                    "capabilities": {
                        "textDocument": {
                            "hover": {"contentFormat": ["markdown" if markdown else "plaintext"]}
                        }
                    }
                },
                id=1,
            )
        )
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        # Request before diagnostics: the server must wait for this snapshot.
        client.send(navigation("definition", source, text, "Cap c(/*", 2))
        reply = client.until(lambda r: r.get("id") == 2)
        assert reply["result"]["uri"] == parts.as_uri()
        assert reply["result"]["range"]["start"] == cursor(parts.read_text(), "C:")
        for request_id, needle, last in [
            (3, "wire)", True),
            (4, "parts.C", True),
            (5, "1: wire", False),
        ]:
            client.send(navigation("definition", source, text, needle, request_id, last=last))
            result = client.until(lambda r: r.get("id") == request_id)["result"]
            offset = text.rindex(needle) if last else text.index(needle)
            reference = expected.reference_at(source, offset)
            assert reference is not None
            assert result["uri"] == Path(reference.target.filename).as_uri()
            target_text = text if reference.target.filename == str(source) else parts.read_text()
            start = target_text[: reference.target.start_pos]
            assert result["range"]["start"] == {
                "line": start.count("\n"),
                "character": len(start.rsplit("\n", 1)[-1].encode("utf-16-le")) // 2,
            }
        client.send(navigation("hover", source, text, "Cap c(/*", 6))
        hover = client.until(lambda r: r.get("id") == 6)["result"]
        assert hover["contents"]["kind"] == ("markdown" if markdown else "plaintext")
        assert "component parts.C" in hover["contents"]["value"]
        assert "Interface: 1" in hover["contents"]["value"]


def test_unsaved_definition_moves_target(tmp_path):
    source, parts = project_files(tmp_path)
    changed = "// 😀 unsaved\n\n" + parts.read_text()
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(source.read_text()), source))
        client.send(at_path(opening(changed), parts))
        client.send(navigation("definition", source, source.read_text(), "C c(1", 2))
        target = client.until(lambda r: r.get("id") == 2)["result"]
        assert target["uri"] == parts.as_uri()
        assert target["range"]["start"] == cursor(changed, "C:")


@pytest.mark.parametrize("action,code", [("cancel", -32800), ("edit", -32801)])
def test_pending_navigation_cancel_or_edit(tmp_path, action, code):
    source, parts = project_files(tmp_path)
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        frames = [
            at_path(opening(source.read_text()), source),
            navigation("definition", source, source.read_text(), "C c(1", 2),
        ]
        frames.append(
            message("$/cancelRequest", {"id": 2})
            if action == "cancel"
            else at_path(change(source.read_text()), source)
        )
        client.process.stdin.write(b"".join(frame(m) for m in frames))
        client.process.stdin.flush()
        assert client.until(lambda r: r.get("id") == 2)["error"]["code"] == code
        client.send(navigation("definition", source, source.read_text(), "C c(1", 3))
        assert client.until(lambda r: r.get("id") == 3)["result"]["uri"] == parts.as_uri()


def test_invalid_navigation_position(tmp_path):
    source, parts = project_files(tmp_path)
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(source.read_text()), source))
        client.send(
            message(
                "textDocument/definition",
                {
                    "textDocument": {"uri": source.as_uri()},
                    "position": {"line": -1, "character": 0},
                },
                id=2,
            )
        )
        assert client.until(lambda r: r.get("id") == 2)["error"]["code"] == -32602


def test_inheritance_aliases_parameters_and_globals(tmp_path):
    from noscheme.lsp_analysis import analyze

    source, parts = project_files(tmp_path)
    parts.write_text("""from noscheme import role
component C:
  params:
    number value = 1
  pins:
    1 (signal):
      role: role.passive
  metadata:
    footprint: "Test:Footprint"
    value: value
extend C D:
  metadata:
    ref_prefix: "R"\n""")
    text = """from noscheme import net
from parts import D
board B:
  global net.ground gnd
  D<value: 2> c(signal: global.gnd)
"""
    expected = analyze(source, source.parent, {source: text})
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        for request_id, needle in [(2, "signal:"), (3, "gnd)"), (6, "value: 2")]:
            client.send(navigation("definition", source, text, needle, request_id))
            target = client.until(lambda r: r.get("id") == request_id)["result"]
            reference = expected.reference_at(source, text.index(needle))
            assert reference is not None
            assert target["uri"] == Path(reference.target.filename).as_uri()
            assert target["range"]["start"]["line"] == reference.target.line - 1
        client.send(at_path(opening(parts.read_text()), parts))
        client.send(navigation("definition", parts, parts.read_text(), "C D:", 4, last=True))
        assert client.until(lambda r: r.get("id") == 4)["result"]["range"]["start"] == cursor(
            parts.read_text(), "C:"
        )
        client.send(navigation("hover", parts, parts.read_text(), "value\n", 5))
        assert (
            "parameter value: number"
            in client.until(lambda r: r.get("id") == 5)["result"]["contents"]["value"]
        )


BUNDLE_LIBRARY = """from noscheme import net
bundle SPI:
  net.digital sck
  net.digital mosi
bundle Buses:
  SPI sd[2]
block Peripheral:
  ports:
    Buses bus
  SPI secret
extend Peripheral Specialized:
  metadata:
    custom:
      note: "inherited"\n"""


@pytest.mark.parametrize(
    "selection,target", [("[0]", "global.shared.sd[0].sck"), ("[0:2]", "local[0:2].sd[1].sck")]
)
@pytest.mark.parametrize("grouped", [False, True])
def test_nested_bundle_navigation(tmp_path, selection, target, grouped):
    from noscheme.lsp_analysis import analyze

    source, parts = project_files(tmp_path)
    library = (
        BUNDLE_LIBRARY.replace("net.digital sck\n  net.digital mosi", "net.digital sck, mosi")
        if grouped
        else BUNDLE_LIBRARY
    )
    parts.write_text(library)
    text = f"""import parts
from noscheme import net
board B:
  global parts.Buses shared
  parts.Buses local[2]
  parts.Specialized p[2]:
    {selection}.bus.sd[0].sck: {target}
"""
    expected = analyze(source, source.parent, {source: text})
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        offsets = [
            text.index("bus.sd") + 4,
            text.index(".sck") + 1,
            text.rindex(".sd") + 1,
            text.rindex(".sck") + 1,
        ]
        for request_id, offset in enumerate(offsets, 2):
            prefix = text[:offset]
            params = {
                "textDocument": {"uri": source.as_uri()},
                "position": {
                    "line": prefix.count("\n"),
                    "character": len(prefix.rsplit("\n", 1)[-1]),
                },
            }
            client.send(message("textDocument/definition", params, id=request_id))
            result = client.until(lambda r: r.get("id") == request_id)["result"]
            reference = expected.reference_at(source, offset)
            assert reference is not None, expected.diagnostics
            assert result["uri"] == parts.as_uri()
            assert result["range"]["start"] == {
                "line": reference.target.line - 1,
                "character": reference.target.column - 1,
            }
            client.send(message("textDocument/hover", params, id=request_id + 100))
            hover = client.until(lambda r: r.get("id") == request_id + 100)["result"]
            assert "bundle field" in hover["contents"]["value"]
            assert "Type: " in hover["contents"]["value"]


def test_bundle_field_declaration_and_unsaved_type(tmp_path):
    source, parts = project_files(tmp_path)
    parts.write_text(BUNDLE_LIBRARY)
    modified = BUNDLE_LIBRARY.replace("sck", "clock#")
    text = """import parts
board B:
  parts.Buses signals
  parts.Specialized p:
    bus.sd[0].clock#: signals.sd[1].clock#
"""
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        client.send(at_path(opening(modified), parts))
        client.send(navigation("definition", source, text, "clock#\n", 2))
        location = client.until(lambda r: r.get("id") == 2)["result"]
        assert location["uri"] == parts.as_uri()
        assert location["range"]["start"] == cursor(modified, "clock#")
        client.send(navigation("hover", parts, modified, "clock#", 3))
        hover = client.until(lambda r: r.get("id") == 3)["result"]
        assert "bundle field clock#" in hover["contents"]["value"]
        assert "net.digital" in hover["contents"]["value"]


@pytest.mark.parametrize("rhs", ["p.secret.sck", "signals.sd[0].missing", "shared.sd[0].sck"])
def test_bundle_navigation_does_not_guess(tmp_path, rhs):
    source, parts = project_files(tmp_path)
    parts.write_text(BUNDLE_LIBRARY)
    text = f"""import parts
board B:
  global parts.Buses shared
  parts.Buses signals
  parts.Specialized p:
    bus.sd[0].sck: {rhs}
"""
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        needle = rhs.rsplit(".", 1)[-1] + "\n"
        client.send(navigation("definition", source, text, needle, 2, last=True))
        assert client.until(lambda r: r.get("id") == 2)["result"] is None


def test_hover_metadata_and_parameter_defaults(tmp_path):
    source, parts = project_files(tmp_path)
    parts.write_text("""from noscheme import role, unused
component C:
  params:
    number value = 10
  pins:
    1 (signal):
      role: role.passive
      unused: unused.always
  metadata:
    footprint: "Test:Footprint"\n""")
    text = source.read_text().replace("C c(1: wire)", "C<value: 20> c(signal: wire)")
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        for rid, needle, snippets in [
            (2, "C<value", ["footprint: Test:Footprint", "from parts"]),
            (3, "signal:", ["role: passive", "aliases:", "unused: always"]),
            (4, "value: 20", ["Default: 10", "parameter value: number"]),
        ]:
            client.send(navigation("hover", source, text, needle, rid))
            hover = client.until(lambda r: r.get("id") == rid)["result"]
            assert hover is not None
            assert all(part in hover["contents"]["value"] for part in snippets), hover


@pytest.mark.parametrize("spelling", ["role.", "role.di", "r."])
def test_enum_completion_with_incomplete_member(tmp_path, spelling):
    source, parts = project_files(tmp_path)
    text = (
        parts.read_text()
        .replace("import role", "import role as r" if spelling.startswith("r.") else "import role")
        .replace("role.passive", spelling)
    )
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), parts))
        if spelling.endswith("."):
            diagnostic = client.until(
                lambda reply: reply.get("method") == "textDocument/publishDiagnostics"
                and reply["params"]["uri"] == parts.as_uri()
            )
            assert diagnostic["params"]["diagnostics"]
        # The board remains on disk; the completion file is an unsaved import.
        position = cursor(text, spelling)
        position["character"] += len(spelling)
        client.send(
            message(
                "textDocument/completion",
                {"textDocument": {"uri": parts.as_uri()}, "position": position},
                id=2,
            )
        )
        completion = client.until(lambda r: r.get("id") == 2)["result"]
        assert completion["isIncomplete"] is False
        items = {i["label"]: i for i in completion["items"]}
        assert "digital_input" in items, client.replies
        if spelling.endswith("."):
            assert "passive" in items
        else:
            assert "passive" not in items
        assert items["digital_input"]["textEdit"]["newText"] == "digital_input"
        assert "Receives a digital signal" in items["digital_input"]["detail"]


def test_builtin_hover_and_no_definition(tmp_path):
    from noscheme.builtin_help import BUILTIN_HELP

    source, parts = project_files(tmp_path)
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(parts.read_text()), parts))
        client.send(navigation("hover", parts, parts.read_text(), "role.passive", 2))
        hover = client.until(lambda r: r.get("id") == 2)["result"]
        assert BUILTIN_HELP["role"]["passive"] in hover["contents"]["value"]
        client.send(navigation("definition", parts, parts.read_text(), "role.passive", 3))
        assert client.until(lambda r: r.get("id") == 3)["result"] is None


def test_qualified_type_completion(tmp_path):
    source, parts = project_files(tmp_path)
    text = (
        source.read_text()
        .replace("from parts import C", "import parts")
        .replace("C c(1", "parts. c(1")
    )
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        position = cursor(text, "parts. c(")
        position["character"] += len("parts.")
        client.send(
            message(
                "textDocument/completion",
                {"textDocument": {"uri": source.as_uri()}, "position": position},
                id=2,
            )
        )
        result = client.until(lambda r: r.get("id") == 2)["result"]
        assert [i["label"] for i in result["items"]] == ["C"]


@pytest.mark.parametrize("suffix", [" // role.", " /* role.", ' "role.'])
def test_completion_ignores_comments_and_strings(tmp_path, suffix):
    source, parts = project_files(tmp_path)
    text = source.read_text() + suffix
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        position = cursor(text, suffix)
        position["character"] += len(suffix)
        client.send(
            message(
                "textDocument/completion",
                {"textDocument": {"uri": source.as_uri()}, "position": position},
                id=2,
            )
        )
        result = client.until(lambda r: r.get("id") == 2)["result"]
        assert result["items"] == []


def test_standard_rule_hover_and_completion(tmp_path):
    source, parts = project_files(tmp_path)
    text = """from noscheme import net, std as rules
board B:
  net.passive n:
    checks:
      empty: rules.unconnected_net()
"""
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        client.send(navigation("hover", source, text, "rules.unconnected_net", 2))
        hover = client.until(lambda r: r.get("id") == 2)["result"]
        assert "net rule std.unconnected_net()" in hover["contents"]["value"]
        assert "Scope: net" in hover["contents"]["value"]
        position = cursor(text, "rules.unconnected_net")
        position["character"] += len("rules.un")
        client.send(
            message(
                "textDocument/completion",
                {"textDocument": {"uri": source.as_uri()}, "position": position},
                id=3,
            )
        )
        items = client.until(lambda r: r.get("id") == 3)["result"]["items"]
        item = next(i for i in items if i["label"] == "unconnected_net")
        assert item["textEdit"]["range"]["end"]["character"] == cursor(
            text, "rules.unconnected_net"
        )["character"] + len("rules.unconnected_net")


def test_plugin_rule_hover_and_completion(tmp_path):
    source, parts = project_files(tmp_path)
    (source.parent / "rules.py").write_text("""def register(r):
    r.net_rule("custom.limit", lambda ctx, limit: None, parameters={"limit": "number"}, defaults={"limit": 5})
""")
    config = source.parent / "noscheme.toml"
    config.write_text(config.read_text() + 'erc_modules = ["rules.py"]\n')
    text = """from noscheme import net
import custom
board B:
  net.passive n:
    checks:
      budget: custom.limit(10)
"""
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        client.send(navigation("hover", source, text, "custom.limit", 2))
        hover = client.until(lambda r: r.get("id") == 2)["result"]
        assert "custom.limit" in hover["contents"]["value"]
        assert "custom.limit(limit)" in hover["contents"]["value"]
        changed = text.replace("custom.limit", "custom.")
        client.send(at_path(change(changed), source))
        position = cursor(changed, "custom.(")
        position["character"] += len("custom.")
        client.send(
            message(
                "textDocument/completion",
                {"textDocument": {"uri": source.as_uri()}, "position": position},
                id=3,
            )
        )
        assert [
            i["label"] for i in client.until(lambda r: r.get("id") == 3)["result"]["items"]
        ] == ["limit"]


def test_completion_respects_local_shadowing(tmp_path):
    source, parts = project_files(tmp_path)
    text = """from noscheme import role as r
component Earlier:
  metadata:
    value: r. + r. + r. + r. + r. + r. + r. + r.
component C:
  params:
    number r = 1
  metadata:
    value: r.
"""
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), parts))
        position = {"line": text[: text.rindex("value: r.")].count("\n"), "character": 4}
        position["character"] += len("value: r.")
        client.send(
            message(
                "textDocument/completion",
                {"textDocument": {"uri": parts.as_uri()}, "position": position},
                id=2,
            )
        )
        assert client.until(lambda r: r.get("id") == 2)["result"]["items"] == []
        position = cursor(text, "value: r.")
        position["character"] += len("value: r.")
        client.send(
            message(
                "textDocument/completion",
                {"textDocument": {"uri": parts.as_uri()}, "position": position},
                id=3,
            )
        )
        items = client.until(lambda r: r.get("id") == 3)["result"]["items"]
        assert "passive" in {item["label"] for item in items}


@pytest.mark.parametrize(
    "endpoint,target,needle,expected",
    [
        ("bu", "local", "bu:", {"bus"}),
        ("bus.", "local", "bus.:", {"sd"}),
        ("bus.sd[0].", "local.sd[0].sck", "bus.sd[0].:", {"sck", "mosi"}),
        ("bus.sd[0].sck", "local.sd[0].", "local.sd[0].", {"sck", "mosi"}),
        ("bus.sd[0].sck", "global.shared.sd[0:2].", "global.shared.sd[0:2].", {"sck", "mosi"}),
    ],
)
def test_public_interface_completion(tmp_path, endpoint, target, needle, expected):
    source, parts = project_files(tmp_path)
    parts.write_text(BUNDLE_LIBRARY)
    text = f"""import parts
board B:
  global parts.Buses shared
  parts.Buses local
  parts.Specialized p:
    {endpoint}: {target}
"""
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        position = cursor(text, needle)
        position["character"] += len(needle.rstrip(":"))
        client.send(
            message(
                "textDocument/completion",
                {"textDocument": {"uri": source.as_uri()}, "position": position},
                id=2,
            )
        )
        items = client.until(lambda r: r.get("id") == 2)["result"]["items"]
        assert {item["label"] for item in items} == expected
        assert all(item["textEdit"]["newText"] == item["label"] for item in items)


@pytest.mark.parametrize("prefix,expected", [("sig", {"signal"}), ("1", {"1"})])
def test_pin_alias_completion_from_unsaved_library(tmp_path, prefix, expected):
    source, parts = project_files(tmp_path)
    library = """from noscheme import role
component C:
  pins:
    1 (signal):
      role: role.passive
"""
    text = f"""from parts import C
from noscheme import net
board B:
  net.passive n
  C c({prefix}: n)
"""
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(library), parts))
        client.send(at_path(opening(text), source))
        position = cursor(text, f"{prefix}: n")
        position["character"] += len(prefix)
        client.send(
            message(
                "textDocument/completion",
                {"textDocument": {"uri": source.as_uri()}, "position": position},
                id=2,
            )
        )
        items = client.until(lambda r: r.get("id") == 2)["result"]["items"]
        assert {item["label"] for item in items} == expected


FOOTPRINT_LIBRARY = """from noscheme import role
component C:
  pins:
    1:
      role: role.passive
  metadata:
    footprint: "Test:Z_default"
    custom:
      kicad_footprint_candidates: ["Test:A", "Test:Z_default", "Test:é"]
"""


@pytest.mark.parametrize(
    "raw", ['"Test:', '"Test:"', "", '"Test:A" // note', '"Test:é', '"Test:|old"', r'"Test:\u00e9']
)
@pytest.mark.parametrize("location", ["instance", "component"])
def test_footprint_completion(tmp_path, raw, location):
    source, parts = project_files(tmp_path)
    parts.write_text(FOOTPRINT_LIBRARY)
    cursor_marker = raw.find("|")
    raw = raw.replace("|", "")
    if location == "instance":
        path = source
        text = f"""from parts import C
from noscheme import net
board B:
  net.passive n
  C c(1: n):
    metadata:
      footprint: {raw}
"""
    else:
        path = parts
        text = (
            FOOTPRINT_LIBRARY
            + f"""extend C D:
  metadata:
    footprint: {raw}
"""
        )
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), path))
        start = text.rindex("footprint: ") + len("footprint: ")
        typed = raw.split(" //")[0]
        # Place cursor immediately before a closing quote when present.
        prefix = typed[:-1] if len(typed) > 1 and typed.endswith('"') else typed
        if cursor_marker >= 0:
            prefix = typed[:cursor_marker]
        if typed and not typed.endswith('"'):
            diagnostic = client.until(
                lambda reply: reply.get("method") == "textDocument/publishDiagnostics"
                and reply["params"]["uri"] == path.as_uri()
            )
            assert diagnostic["params"]["diagnostics"]
        before = text[:start] + prefix
        position = {"line": before.count("\n"), "character": 0}
        position["character"] = len(before.rsplit("\n", 1)[-1].encode("utf-16-le")) // 2
        client.send(
            message(
                "textDocument/completion",
                {"textDocument": {"uri": path.as_uri()}, "position": position},
                id=2,
            )
        )
        items = client.until(lambda r: r.get("id") == 2)["result"]["items"]
        expected = [
            name
            for name in ["Test:Z_default", "Test:A", "Test:é"]
            if name.startswith(json.loads('"' + prefix.lstrip('"') + '"'))
        ]
        assert [item["label"] for item in items] == expected, client.replies
        for item in items:
            assert json.loads(item["textEdit"]["newText"]) == item["label"]
            edit = item["textEdit"]["range"]
            assert edit["start"]["character"] == len(text[:start].rsplit("\n", 1)[-1])
            assert (
                edit["end"]["character"] - edit["start"]["character"]
                == len(typed.encode("utf-16-le")) // 2
            )


@pytest.mark.parametrize(
    "suffix",
    [
        '      custom:\n        footprint: "Test:',
        '      value: "footprint: Test:',
        '      // footprint: "Test:',
        '      /* footprint: "Test: */',
    ],
)
def test_footprint_completion_excludes_other_contexts(tmp_path, suffix):
    source, parts = project_files(tmp_path)
    parts.write_text(FOOTPRINT_LIBRARY)
    text = "from parts import C\nboard B:\n  C c:\n    metadata:\n" + suffix + "\n"
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        position = cursor(text, "Test:")
        position["character"] += len("Test:")
        client.send(
            message(
                "textDocument/completion",
                {"textDocument": {"uri": source.as_uri()}, "position": position},
                id=2,
            )
        )
        assert client.until(lambda r: r.get("id") == 2)["result"]["items"] == []


@pytest.mark.parametrize(
    "reference,expected",
    [
        ("wi", {"wire"}),
        ("global.g", {"gnd"}),
        ("pa", {"pad"}),
        ("pri", set()),
    ],
)
def test_local_connection_target_completion(tmp_path, reference, expected):
    source, parts = project_files(tmp_path)
    text = f"""from noscheme import net
from parts import C
block Other:
  net.passive private
board B:
  global net.ground gnd
  net.passive wire
  net.passive pad
  C c(1: {reference})
"""
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        position = cursor(text, reference + ")")
        position["character"] += len(reference)
        client.send(
            message(
                "textDocument/completion",
                {"textDocument": {"uri": source.as_uri()}, "position": position},
                id=2,
            )
        )
        items = client.until(lambda r: r.get("id") == 2)["result"]["items"]
        assert {item["label"] for item in items} == expected


@pytest.mark.parametrize(
    "call,expected,absent",
    [
        ("rules.count(wh|)", {"what"}, set()),
        ("rules.count(|", {"what", "min", "max", "missing"}, set()),
        ("rules.count(  |", {"what", "min", "max", "missing"}, set()),
        ("rules.count(wh|, 1)", {"what"}, {"min"}),
        ("rules.count(wh|", {"what"}, set()),
        ("rules.count(rules.active_digital_driver, mi|)", {"min", "missing"}, {"what"}),
        ("rules.count(what=rules.active_digital_driver, ma|)", {"max"}, {"what"}),
        (
            "rules.count(what=rules.active_digital_driver, |)",
            {"min", "max", "missing"},
            {"what", "rules.active_digital_driver"},
        ),
        ("rules.count(what=|)", {"rules.active_digital_driver", "tristate_capable"}, {"max"}),
        ("rules.sum(field=tri|)", {"tristate_capable"}, {"rules.active_digital_driver"}),
        ("rules.count(missing=|)", {'"warning"', '"error"', '"ignore"'}, {"max"}),
        ('rules.count(missing="wa|rning")', {'"warning"'}, {'"error"', '"ignore"'}),
        ('rules.count(missing="ig|', {'"ignore"'}, {'"warning"', '"error"'}),
        ("rules.count(ma|x=3)", {"max"}, set()),
        ("rules.count(ma|, max=2)", set(), {"max"}),
        (
            "rules.count(\n        what=rules.active_digital_driver,\n        mi|\n      )",
            {"min", "missing"},
            {"what"},
        ),
    ],
)
def test_erc_argument_completion(tmp_path, call, expected, absent):
    source, parts = project_files(tmp_path)
    marked = f"""from noscheme import net, std as rules
board B:
  net.passive n:
    checks:
      budget: {call}
"""
    offset = marked.index("|")
    text = marked.replace("|", "")
    before = marked[:offset]
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        position = {"line": before.count("\n"), "character": len(before.rsplit("\n", 1)[-1])}
        client.send(
            message(
                "textDocument/completion",
                {"textDocument": {"uri": source.as_uri()}, "position": position},
                id=2,
            )
        )
        items = client.until(lambda r: r.get("id") == 2)["result"]["items"]
        names = {item["label"] for item in items}
        assert expected <= names, client.replies
        assert not absent & names
        for item in items:
            if item["kind"] == 10:
                assert item["textEdit"]["newText"] == item["label"] + (
                    "" if call == "rules.count(ma|x=3)" else "="
                )


def test_plugin_erc_argument_completion(tmp_path):
    source, parts = project_files(tmp_path)
    (source.parent / "rules.py").write_text("""def register(r):
    r.net_rule("custom.limit", lambda ctx, amount: None, parameters={"amount": "number"})
""")
    config = source.parent / "noscheme.toml"
    config.write_text(config.read_text() + 'erc_modules = ["rules.py"]\n')
    text = """from noscheme import net
from custom import limit as budget
board B:
  net.passive n:
    checks:
      budget: budget(am)
"""
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        position = cursor(text, "am)")
        position["character"] += 2
        client.send(
            message(
                "textDocument/completion",
                {"textDocument": {"uri": source.as_uri()}, "position": position},
                id=2,
            )
        )
        items = client.until(lambda r: r.get("id") == 2)["result"]["items"]
        assert [item["label"] for item in items] == ["amount"]
        assert items[0]["textEdit"]["newText"] == "amount="


@pytest.mark.parametrize(
    "body",
    [
        '  metadata:\n    value: "std.count(mi|)"',
        "  metadata:\n    custom:\n      checks:\n        budget: std.count(mi|)",
        "  net.passive n:\n    checks:\n      budget: std.count() // std.count(mi|",
        '  net.passive n:\n    checks:\n      budget: std.count("mi|")',
        "  net.passive n:\n    checks:\n      budget: std.count( // comment |",
        '  net.passive n:\n    checks:\n      budget: std.count(" |")',
    ],
)
def test_erc_completion_excludes_other_contexts(tmp_path, body):
    source, parts = project_files(tmp_path)
    marked = "from noscheme import net, std\nboard B:\n" + body + "\n"
    before = marked[: marked.index("|")]
    text = marked.replace("|", "")
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        position = {"line": before.count("\n"), "character": len(before.rsplit("\n", 1)[-1])}
        client.send(
            message(
                "textDocument/completion",
                {"textDocument": {"uri": source.as_uri()}, "position": position},
                id=2,
            )
        )
        assert client.until(lambda r: r.get("id") == 2)["result"]["items"] == []


INLINE_LIBRARY = """from noscheme import role
component C:
  params:
    number value = 1
  pins:
    1 (signal):
      role: role.passive
    2:
      role: role.passive
  metadata:
    interchangeable: [1, 2]
extend C D:
  metadata:
    ref_prefix: "R"
"""


@pytest.mark.parametrize(
    "arguments,expected,absent",
    [
        ("|", {"1", "2", "signal", "wire"}, set()),
        ("|)", {"1", "2", "signal", "wire"}, set()),
        ("wi|)", {"wire"}, set()),
        ("wire, |)", {"wire", "other"}, {"1", "2", "signal"}),
        ("1: |)", {"wire", "other"}, {"1", "signal", "value"}),
        ("signal: wire, |)", {"2"}, {"1", "signal", "wire"}),
        ("wire, other, |)", set(), {"wire", "other"}),
        ("\n    1: wire,\n    2: ot|\n  )", {"other"}, set()),
        ("1: wi|re)", {"wire"}, set()),
        ("si|gnal: wire)", {"signal"}, set()),
    ],
)
def test_inline_instance_completion(tmp_path, arguments, expected, absent):
    source, parts = project_files(tmp_path)
    parts.write_text(INLINE_LIBRARY)
    marked = f"""from parts import D
from noscheme import net
board B:
  net.passive wire
  net.passive other
  D r({arguments}
"""
    before = marked[: marked.index("|")]
    text = marked.replace("|", "")
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        client.send(
            message(
                "textDocument/completion",
                {
                    "textDocument": {"uri": source.as_uri()},
                    "position": {
                        "line": before.count("\n"),
                        "character": len(before.rsplit("\n", 1)[-1]),
                    },
                },
                id=2,
            )
        )
        items = client.until(lambda r: r.get("id") == 2)["result"]["items"]
        names = {item["label"] for item in items}
        assert expected <= names, client.replies
        assert not absent & names
        for item in items:
            if item["kind"] == 5:
                assert item["textEdit"]["newText"] == item["label"] + (
                    "" if arguments == "si|gnal: wire)" else ": "
                )
            if item["label"] == "value":
                assert item["textEdit"]["newText"] == (
                    "value" if arguments == "va|lue=10)" else "value="
                )


@pytest.mark.parametrize(
    "scope,prefix", [("pin", "std."), ("net", "std."), ("pin", ""), ("net", "")]
)
def test_erc_rule_scope_completion(tmp_path, scope, prefix):
    source, parts = project_files(tmp_path)
    if scope == "pin":
        path = parts
        text = f"""from noscheme import role, std
component C:
  pins:
    1:
      role: role.passive
      checks:
        test: {prefix}
"""
    else:
        path = source
        text = f"""from noscheme import net, std
board B:
  net.passive n:
    checks:
      test: {prefix}
"""
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), path))
        position = cursor(text, "test: ")
        position["character"] += len("test: ") + len(prefix)
        client.send(
            message(
                "textDocument/completion",
                {"textDocument": {"uri": path.as_uri()}, "position": position},
                id=2,
            )
        )
        items = client.until(lambda r: r.get("id") == 2)["result"]["items"]
        names = {item["label"] for item in items}
        assert names
        if prefix:
            assert ("pin_do_not_connect" in names) == (scope == "pin")
            assert ("unconnected_net" in names) == (scope == "net")
        assert all(("pin rule" if scope == "pin" else "net rule") in i["detail"] for i in items)


def test_noninterchangeable_instance_offers_only_named_bindings(tmp_path):
    source, parts = project_files(tmp_path)
    parts.write_text(INLINE_LIBRARY.replace("    interchangeable: [1, 2]", '    ref_prefix: "R"'))
    text = """from parts import C
from noscheme import net
board B:
  net.passive wire
  C r()
"""
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        position = cursor(text, "C r()")
        position["character"] += 4
        client.send(
            message(
                "textDocument/completion",
                {"textDocument": {"uri": source.as_uri()}, "position": position},
                id=2,
            )
        )
        items = client.until(lambda r: r.get("id") == 2)["result"]["items"]
        assert {i["label"] for i in items} == {"1", "2", "signal"}


@pytest.mark.parametrize("raw", [" // text ", ' "text '])
def test_inline_completion_excludes_comments_and_strings(tmp_path, raw):
    source, parts = project_files(tmp_path)
    parts.write_text(INLINE_LIBRARY)
    text = "from parts import C\nboard B:\n  C r(" + raw + "\n"
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        position = {"line": 2, "character": len(text.splitlines()[2])}
        client.send(
            message(
                "textDocument/completion",
                {"textDocument": {"uri": source.as_uri()}, "position": position},
                id=2,
            )
        )
        assert client.until(lambda r: r.get("id") == 2)["result"]["items"] == []


@pytest.mark.parametrize(
    "binding,expected,absent",
    [
        ("|", {"1", "2", "signal"}, {"value", "wire"}),
        ("si|", {"signal"}, {"value"}),
        ("1: |", {"wire", "other"}, {"1", "value"}),
        ("1: wi|re", {"wire"}, {"value"}),
        ("signal: wire\n    |", {"2"}, {"1", "signal"}),
        ("si|gnal: wire", {"signal"}, {"value"}),
        ("1: wire\n    2: |", {"wire", "other"}, {"value"}),
    ],
)
def test_section_binding_completion(tmp_path, binding, expected, absent):
    source, parts = project_files(tmp_path)
    parts.write_text(INLINE_LIBRARY)
    marked = f"""from parts import D
from noscheme import net
board B:
  net.passive wire
  net.passive other
  D r:
    {binding}
"""
    before = marked[: marked.index("|")]
    text = marked.replace("|", "")
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        client.send(
            message(
                "textDocument/completion",
                {
                    "textDocument": {"uri": source.as_uri()},
                    "position": {
                        "line": before.count("\n"),
                        "character": len(before.rsplit("\n", 1)[-1]),
                    },
                },
                id=2,
            )
        )
        items = client.until(lambda r: r.get("id") == 2)["result"]["items"]
        names = {item["label"] for item in items}
        assert expected <= names, client.replies
        assert not absent & names
        for item in items:
            if item["kind"] == 5:
                assert item["textEdit"]["newText"] == item["label"] + (
                    "" if binding == "si|gnal: wire" else ": "
                )
        if binding == "|":
            diagnostics = next(
                (
                    r
                    for r in client.replies
                    if r.get("method") == "textDocument/publishDiagnostics"
                    and r["params"]["uri"] == source.as_uri()
                ),
                None,
            )
            if diagnostics is None:
                diagnostics = client.until(
                    lambda r: r.get("method") == "textDocument/publishDiagnostics"
                    and r["params"]["uri"] == source.as_uri()
                )
            assert diagnostics["params"]["diagnostics"]


@pytest.mark.parametrize("binding", ["bus.sd[0].|", "[0].bus.sd[0].|"])
def test_section_partial_bundle_without_colon(tmp_path, binding):
    source, parts = project_files(tmp_path)
    parts.write_text(BUNDLE_LIBRARY)
    marked = f"""import parts
board B:
  parts.Specialized p[2]:
    {binding}
"""
    before = marked[: marked.index("|")]
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(marked.replace("|", "")), source))
        client.send(
            message(
                "textDocument/completion",
                {
                    "textDocument": {"uri": source.as_uri()},
                    "position": {
                        "line": before.count("\n"),
                        "character": len(before.rsplit("\n", 1)[-1]),
                    },
                },
                id=2,
            )
        )
        items = client.until(lambda r: r.get("id") == 2)["result"]["items"]
        assert {i["label"] for i in items} == {"sck", "mosi"}
        assert {i["textEdit"]["newText"] for i in items} == {"sck: ", "mosi: "}


@pytest.mark.parametrize(
    "section",
    [
        "    // |",
        "    1: wire[|",
        '    1: " |',
        "metadata:\n      custom:\n        |",
        "    1: wire\n  params:\n    |",
    ],
)
def test_section_completion_boundaries(tmp_path, section):
    source, parts = project_files(tmp_path)
    parts.write_text(INLINE_LIBRARY)
    marked = f"""from parts import D
from noscheme import net
board B:
  net.passive wire
  D r:
    {section}
"""
    before = marked[: marked.index("|")]
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(marked.replace("|", "")), source))
        client.send(
            message(
                "textDocument/completion",
                {
                    "textDocument": {"uri": source.as_uri()},
                    "position": {
                        "line": before.count("\n"),
                        "character": len(before.rsplit("\n", 1)[-1]),
                    },
                },
                id=2,
            )
        )
        assert client.until(lambda r: r.get("id") == 2)["result"]["items"] == []


@pytest.mark.parametrize(
    "marked, expected",
    [
        ("comp|onent", {"component"}),
        ("|", {"from", "import", "component", "block", "board", "bundle", "extend"}),
        ("ex|", {"extend"}),
        ("from Device imp|", {"import"}),
        ("from Device import R a|", {"as"}),
        ("board B:\n  C r[2]:\n    elements:\n      [0]:\n        |", {"params", "metadata"}),
        ("board B:\n  |", {"metadata", "global"}),
        ("board B:\n  global n|", set()),
        ("block B:\n  |", {"params", "ports", "metadata"}),
        ("component C:\n  |", {"params", "pins", "metadata"}),
        ("extend Other C:\n  |", {"pins", "ports", "metadata"}),
        ("extend Other B:\n  |", {"pins", "ports", "metadata"}),
        ("bundle B:\n  |", {"metadata"}),
        ("board B:\n  C c:\n    |", {"params", "metadata", "elements"}),
        ("board B:\n  C c():\n    met|", {"metadata"}),
        ("// comp|", set()),
        ("/* comp|", set()),
        ('board B:\n  metadata:\n    value: "comp|', set()),
        ("board B:\n  metadata:\n    comp|", set()),
        ("board B:\n  C c(\n    comp|", set()),
        ("component C:\n  params:\n    comp|", set()),
    ],
)
def test_keyword_completion(tmp_path, marked, expected):
    # No project or successfully parsed module is needed for keywords.
    source = tmp_path / "fresh.nosch"
    before, after = marked.split("|")
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(before + after), source))
        client.send(
            message(
                "textDocument/completion",
                {
                    "textDocument": {"uri": source.as_uri()},
                    "position": {
                        "line": before.count("\n"),
                        "character": len(before.rsplit("\n", 1)[-1]),
                    },
                },
                id=2,
            )
        )
        items = client.until(lambda r: r.get("id") == 2)["result"]["items"]
        assert {i["label"] for i in items} == expected
        assert all(i["kind"] == 14 for i in items)
        if marked == "comp|onent":
            assert items[0]["textEdit"] == {
                "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 9}},
                "newText": "component",
            }


@pytest.mark.parametrize(
    "binding, expected",
    [
        ("[|]: wire", {"1", "signal", "2", "3", "pad-x"}),
        ("[1, |]: wire", {"2", "3", "pad-x"}),
        ("[signal, |", {"2", "3", "pad-x"}),
        ("[|, 2..3]: wire", {"1", "signal", "pad-x"}),
        ('["1", |]: wire', {"2", "3", "pad-x"}),
        ('["pad-x", |]: wire', {"1", "signal", "2", "3"}),
        ("[1,\n        |\n      ]: wire", {"2", "3", "pad-x"}),
        ("[1, /* skip 2 */ |]: wire", {"2", "3", "pad-x"}),
        ("[si|gnal, 2]: wire", {"signal"}),
        ("2..3: wire\n    [|]: wire", {"1", "signal", "pad-x"}),
        ("[1, 2]: wire\n    [|]: wire", {"3", "pad-x"}),
        ("[1, 2]: wire\n    |", {"3", "pad-x", "params", "metadata", "elements"}),
        ("[1, 2]: |", {"wire"}),
        ("[1..|]: wire", set()),
        ('["1"|]: wire', set()),
        ("[1..9999999999999999999999999, |]: wire", {"pad-x"}),
        ('[1, /* "2" */ |]: wire', {"2", "3", "pad-x"}),
        ('"pad-x": wire\n    [|]: wire', {"1", "signal", "2", "3"}),
        ('["pa|', {"pad-x"}),
        ('["pa|d-x"]: wire', {"pad-x"}),
        ('["1", "|"]: wire', {"2", "3", "pad-x"}),
        ("[0].[1, |]: wire", {"2", "3", "pad-x"}),
        ("[0][1].[1, |]: wire", {"2", "3", "pad-x"}),
        ("[0].[1, 2]: wire\n    [1].[|]: wire", {"1", "signal", "2", "3", "pad-x"}),
        ("[0].[1, 2]: wire\n    [0].[|]: wire", {"3", "pad-x"}),
        ("[1, // |", set()),
        ("[1, [|", set()),
    ],
)
def test_grouped_pin_completion(tmp_path, binding, expected):
    source, parts = project_files(tmp_path)
    parts.write_text(
        INLINE_LIBRARY.replace(
            "  metadata:\n    interchangeable",
            '    3:\n      role: role.passive\n    "pad-x":\n      role: role.passive\n  metadata:\n    interchangeable',
        )
    )
    marked = f"""from parts import D
from noscheme import net
board B:
  net.passive wire
  D r:
    {binding}
"""
    before, after = marked.split("|")
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(before + after), source))
        client.send(
            message(
                "textDocument/completion",
                {
                    "textDocument": {"uri": source.as_uri()},
                    "position": {
                        "line": before.count("\n"),
                        "character": len(before.rsplit("\n", 1)[-1]),
                    },
                },
                id=2,
            )
        )
        items = client.until(lambda r: r.get("id") == 2)["result"]["items"]
        assert {i["label"] for i in items} == expected
        if binding == "[si|gnal, 2]: wire":
            assert items[0]["textEdit"] == {
                "range": {
                    "start": {"line": 5, "character": 5},
                    "end": {"line": 5, "character": 11},
                },
                "newText": "signal",
            }
        if binding.startswith("[|"):
            for item in items:
                assert item["textEdit"]["newText"] == (
                    json.dumps(item["label"]) if item["label"] == "pad-x" else item["label"]
                )


@pytest.mark.parametrize("pin", ["signal", '"1"', "1..2", "[1..2]"])
@pytest.mark.parametrize("broken", [False, True])
def test_grouped_pin_navigation_and_references(tmp_path, pin, broken):
    source, parts = project_files(tmp_path)
    parts.write_text(INLINE_LIBRARY)
    key = pin if pin == "[1..2]" else f"[{pin}]"
    text = f"""from parts import D
from noscheme import net
board B:
  net.passive wire
  D r:
    {key}: wire
  D s(1: wire)
"""
    if broken:
        text += "  D bad(missing: wire)\n"
    params = {
        "textDocument": {"uri": source.as_uri()},
        "position": {"line": 5, "character": 5},
    }
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        client.send(message("textDocument/definition", params, id=2))
        result = client.until(lambda r: r.get("id") == 2)["result"]
        assert result["uri"] == parts.as_uri()
        assert result["range"]["start"]["line"] == 5
        client.send(message("textDocument/hover", params, id=3))
        hover = client.until(lambda r: r.get("id") == 3)["result"]
        assert "interface D.1" in hover["contents"]["value"]
        for request_id, include in [(4, False), (5, True)]:
            client.send(
                message(
                    "textDocument/references",
                    {
                        **params,
                        "context": {"includeDeclaration": include},
                    },
                    id=request_id,
                )
            )
            locations = client.until(lambda r: r.get("id") == request_id)["result"]
            assert len(locations) == (4 if include else 3)
            assert {
                loc["range"]["start"]["line"] for loc in locations if loc["uri"] == source.as_uri()
            } == {5, 6}
            # The alias declaration is also a reference to physical pin 1.
            assert sum(loc["uri"] == parts.as_uri() for loc in locations) == (2 if include else 1)


def test_elaborated_instance_hover(tmp_path):
    source, parts = project_files(tmp_path)
    parts.write_text(INLINE_LIBRARY)
    text = """from parts import D
from noscheme import net
board B:
  net.passive wire
  D<value: 2200> resistors[6](wire, wire)
"""
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        client.send(
            message(
                "textDocument/hover",
                {
                    "textDocument": {"uri": source.as_uri()},
                    "position": {"line": 4, "character": 20},
                },
                id=2,
            )
        )
        hover = client.until(lambda r: r.get("id") == 2)["result"]
        description = hover["contents"]["value"]
        assert "Elaborated instances: 6" in description
        assert "Parameter value: 2200" in description
        assert "Instance: resistors[0]" in description
        assert "2 more instances" in description
        assert "Instance: resistors[5]" not in description


@pytest.mark.parametrize("broken", [False, True])
def test_standalone_and_partial_navigation(tmp_path, broken):
    source = tmp_path / "standalone.nosch"
    text = """// Unicode before spans: µ
from noscheme import role
component C:
  pins:
    1:
      role: role.passive
"""
    if broken:
        text += "component Broken:\n  pins:\n    ?\n"
    text += "extend C D:\n  metadata:\n    value: 10k\n"
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        position = cursor(text, "C D:", last=True)
        params = {"textDocument": {"uri": source.as_uri()}, "position": position}
        client.send(message("textDocument/definition", params, id=2))
        target = client.until(lambda r: r.get("id") == 2)["result"]
        assert target["uri"] == source.as_uri()
        assert target["range"]["start"] == {"line": 2, "character": 10}
        client.send(message("textDocument/hover", params, id=3))
        hover = client.until(lambda r: r.get("id") == 3)["result"]
        assert "component local.C" in hover["contents"]["value"]
        diagnostics = next(
            (r for r in client.replies if r.get("method") == "textDocument/publishDiagnostics"),
            None,
        )
        if diagnostics is None:
            diagnostics = client.until(
                lambda r: r.get("method") == "textDocument/publishDiagnostics"
            )
        assert bool(diagnostics["params"]["diagnostics"]) == broken
        # A declaration removed from the current buffer must not survive in the index.
        client.send(at_path(change(text.replace("component C:", "component E:")), source))
        client.send(message("textDocument/definition", params, id=4))
        assert client.until(lambda r: r.get("id") == 4)["result"] is None


@pytest.mark.parametrize("root_kind", ["rootUri", "workspaceFolders"])
@pytest.mark.parametrize("ambiguous", [False, True])
def test_workspace_project_discovery(tmp_path, root_kind, ambiguous):
    source, parts = project_files(tmp_path)
    old = source.parent / "noscheme.toml"
    settings = old.read_text().replace('source_paths = ["."]', 'source_paths = [".."]')
    config_dir = source.parent / "project"
    config_dir.mkdir()
    old.rename(config_dir / "noscheme.toml")
    (config_dir / "noscheme.toml").write_text(settings)
    if ambiguous:
        other = source.parent / "other"
        other.mkdir()
        (other / "noscheme.toml").write_text(settings)
    roots = (
        {"rootUri": source.parent.as_uri()}
        if root_kind == "rootUri"
        else {"workspaceFolders": [{"uri": source.parent.as_uri(), "name": "project"}]}
    )
    with Client() as client:
        client.send(message("initialize", roots, id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(source.read_text()), source))
        client.send(
            message(
                "textDocument/definition",
                {
                    "textDocument": {"uri": source.as_uri()},
                    "position": cursor(source.read_text(), "C c(1"),
                },
                id=2,
            )
        )
        target = client.until(lambda r: r.get("id") == 2)["result"]
        if ambiguous:
            assert target is None
            diagnostic = next(
                (r for r in client.replies if r.get("method") == "textDocument/publishDiagnostics"),
                None,
            )
            if diagnostic is None:
                diagnostic = client.until(
                    lambda r: r.get("method") == "textDocument/publishDiagnostics"
                )
            assert any(
                "Multiple NoScheme projects" in d["message"]
                for d in diagnostic["params"]["diagnostics"]
            )
        else:
            assert target["uri"] == parts.as_uri()


def test_native_document_formatting(tmp_path):
    from noscheme.formatter import format_source

    source = tmp_path / "format.nosch"
    text = "// µ\ncomponent  C :\n    pins:\n      [1,2]:role.passive // pins\n"
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        params = {
            "textDocument": {"uri": source.as_uri()},
            "options": {"tabSize": 8, "insertSpaces": False},
        }
        client.send(message("textDocument/formatting", params, id=2))
        edits = client.until(lambda r: r.get("id") == 2)["result"]
        assert edits[0]["newText"] == format_source(text)
        assert edits[0]["range"]["end"] == {"line": 4, "character": 0}
        client.send(at_path(change(edits[0]["newText"]), source))
        client.send(message("textDocument/formatting", params, id=3))
        assert client.until(lambda r: r.get("id") == 3)["result"] == []
        client.send(at_path(change("component ?", version=3), source))
        client.send(message("textDocument/formatting", params, id=4))
        assert client.until(lambda r: r.get("id") == 4)["error"]["code"] == -32803


def apply_lsp_edits(text, edits):
    def offset(position):
        lines = text.splitlines(keepends=True)
        prefix = "".join(lines[: position["line"]])
        if position["line"] == len(lines):
            return len(text)
        encoded = lines[position["line"]].encode("utf-16-le")[: position["character"] * 2]
        return len(prefix) + len(encoded.decode("utf-16-le"))

    for edit in sorted(edits, key=lambda e: offset(e["range"]["start"]), reverse=True):
        begin, end = offset(edit["range"]["start"]), offset(edit["range"]["end"])
        text = text[:begin] + edit["newText"] + text[end:]
    return text


@pytest.mark.parametrize(
    "target, new_name, alias, array",
    [
        ("wire\n", "power", False, False),
        ("c(1:", "resistor", False, False),
        ("c[2]", "resistors", False, True),
        ("C c(1:", "Resistor", False, False),
        ("Local c(1:", "Device", True, False),
    ],
)
def test_native_rename_matches_python(tmp_path, target, new_name, alias, array):
    from noscheme.lsp_rename import RenameSession

    source, parts = project_files(tmp_path)
    text = source.read_text()
    if alias:
        text = text.replace("import C", "import C as Local").replace("C c", "Local c")
    if array:
        text = text.replace("C c", "C c[2]")
    source.write_text(text)
    sources = {source: text, parts: parts.read_text()}
    session = RenameSession(source.parent, sources)
    expected = dict(sources)
    edits = session.rename(source, text.index(target), new_name)
    for span, value in sorted(edits, key=lambda e: (e[0].filename, e[0].start_pos), reverse=True):
        path = Path(span.filename)
        expected[path] = expected[path][: span.start_pos] + value + expected[path][span.end_pos :]
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        params = {"textDocument": {"uri": source.as_uri()}, "position": cursor(text, target)}
        client.send(message("textDocument/prepareRename", params, id=2))
        prepared = client.until(lambda r: r.get("id") == 2)
        assert "error" not in prepared, prepared
        client.send(message("textDocument/rename", {**params, "newName": new_name}, id=3))
        response = client.until(lambda r: r.get("id") == 3)
        assert "error" not in response, response
        changes = response["result"]["changes"]
        actual = {
            path: apply_lsp_edits(content, changes.get(path.as_uri(), []))
            for path, content in sources.items()
        }
        assert actual == expected
        assert source.read_text() == text  # Only a proposed workspace edit.


@pytest.mark.parametrize(
    "target,new_name", [("wire\n", "global"), ("wire\n", "c"), ("1: wire", "pin"), ("B:", "Board")]
)
def test_native_rename_refusals(tmp_path, target, new_name):
    source, _ = project_files(tmp_path)
    text = source.read_text()
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        client.send(
            message(
                "textDocument/rename",
                {
                    "textDocument": {"uri": source.as_uri()},
                    "position": cursor(text, target),
                    "newName": new_name,
                },
                id=2,
            )
        )
        response = client.until(lambda r: r.get("id") == 2)
        assert response["error"]["code"] == -32803
        assert source.read_text() == text


def test_bound_pin_hover(tmp_path):
    source, _ = project_files(tmp_path)
    text = source.read_text()
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        client.send(
            message(
                "textDocument/hover",
                {
                    "textDocument": {"uri": source.as_uri()},
                    "position": cursor(text, "1: wire"),
                },
                id=2,
            )
        )
        hover = client.until(lambda r: r.get("id") == 2)["result"]
        assert "Effective role: passive; net: wire" in hover["contents"]["value"]


def test_disk_dependency_changes_without_editor_notification(tmp_path):
    source, parts = project_files(tmp_path)
    text = source.read_text()
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        params = {"textDocument": {"uri": source.as_uri()}, "position": cursor(text, "C c(1")}
        client.send(message("textDocument/definition", params, id=2))
        first = client.until(lambda r: r.get("id") == 2)["result"]
        before = parts.stat()
        parts.write_text("// shifted\n" + parts.read_text())
        # Exact content comparison also catches changes with a preserved mtime.
        os.utime(parts, ns=(before.st_atime_ns, before.st_mtime_ns))
        client.send(message("textDocument/definition", params, id=3))
        second = client.until(lambda r: r.get("id") == 3)["result"]
        assert second["range"]["start"]["line"] == first["range"]["start"]["line"] + 1


def test_rename_versioned_workspace_edits(tmp_path):
    source, parts = project_files(tmp_path)
    text = source.read_text()
    with Client() as client:
        client.send(
            message(
                "initialize",
                {"capabilities": {"workspace": {"workspaceEdit": {"documentChanges": True}}}},
                id=1,
            )
        )
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text, version=7), source))
        client.send(
            message(
                "textDocument/rename",
                {
                    "textDocument": {"uri": source.as_uri()},
                    "position": cursor(text, "C c(1"),
                    "newName": "Capacitor",
                },
                id=2,
            )
        )
        edit = client.until(lambda r: r.get("id") == 2)["result"]
        assert "changes" not in edit
        versions = {
            e["textDocument"]["uri"]: e["textDocument"]["version"] for e in edit["documentChanges"]
        }
        assert versions == {source.as_uri(): 7, parts.as_uri(): None}
        assert all(e["edits"] for e in edit["documentChanges"])


@pytest.mark.parametrize("header", ["", " ", "\t"])
def test_empty_instance_type_lists_all_visible_parts_and_blocks(tmp_path, header):
    source, parts = project_files(tmp_path)
    parts.write_text(
        parts.read_text()
        + """
from noscheme import net
block Group:
  ports:
    net.passive p
bundle Bus:
  net.passive p
"""
    )
    text = (
        """import parts
from parts import C as Alias
from noscheme import role, net
component Local:
  pins:
    1: role.passive
board B:
  """
        + header
    )
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        position = {"line": len(text.splitlines()) - 1, "character": len(text.splitlines()[-1])}
        client.send(
            message(
                "textDocument/completion",
                {"textDocument": {"uri": source.as_uri()}, "position": position},
                id=2,
            )
        )
        result = client.until(lambda r: r.get("id") == 2)["result"]
        items = result["items"]
        assert {i["label"] for i in items if i["kind"] == 7} == {
            "Alias",
            "Local",
            "parts.C",
            "parts.Group",
            "parts.Bus",
        }
        for item in items:
            assert item["textEdit"]["range"] == {"start": position, "end": position}


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("extend ", {"parts.C", "Alias", "parts.Group", "parts.Signal"}),
        ("board B:\n  global ", {"parts.Signal", "parts.Bus"}),
        ("extend parts.", {"C", "Group", "Signal"}),
        ("block LocalGroup:\n  ", {"Alias", "parts.C", "parts.Group", "parts.Signal", "parts.Bus"}),
    ],
)
def test_empty_type_contexts_filter_visible_namespace(tmp_path, header, expected):
    source, parts = project_files(tmp_path)
    parts.write_text(
        parts.read_text()
        + """from noscheme import net
block Group:
  ports:
    net.passive p
extend net.passive Signal:
  wiring_capacitance: 1p
bundle Bus:
  net.passive p
"""
    )
    prelude = "import parts\nfrom parts import C as Alias\n"
    if not header.startswith("board"):
        prelude += "board B:\n  metadata:\n    custom:\n      demo: true\n"
    text = prelude + header
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        position = {"line": len(text.splitlines()) - 1, "character": len(text.splitlines()[-1])}
        client.send(
            message(
                "textDocument/completion",
                {"textDocument": {"uri": source.as_uri()}, "position": position},
                id=2,
            )
        )
        result = client.until(lambda r: r.get("id") == 2)["result"]
        assert {
            i["label"]
            for i in result["items"]
            if i["kind"] != 14 and not i["label"].startswith(("parts.net.", "net."))
        } == expected


@pytest.mark.parametrize("header", ["board B:\n  global ", "extend "])
def test_type_completion_with_builtin_alias(tmp_path, header):
    source, _ = project_files(tmp_path)
    text = "from noscheme import net as nt\n"
    if header.startswith("extend"):
        text += "board B:\n  metadata:\n    custom:\n      demo: true\n"
    text += header
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        position = {"line": len(text.splitlines()) - 1, "character": len(text.splitlines()[-1])}
        client.send(
            message(
                "textDocument/completion",
                {"textDocument": {"uri": source.as_uri()}, "position": position},
                id=2,
            )
        )
        items = client.until(lambda r: r.get("id") == 2)["result"]["items"]
        labels = {i["label"] for i in items}
        assert {"nt.digital", "nt.unchecked"} <= labels
        assert all(label.startswith("nt.") for label in labels)


@pytest.mark.parametrize(
    "case",
    json.loads(
        (
            Path(__file__).resolve().parents[2] / "tests/fixtures/lsp_scope_completion.json"
        ).read_text()
    ),
    ids=lambda case: case["name"],
)
def test_scope_completion_parity(tmp_path, case):
    source, _ = project_files(tmp_path)
    offset = case["source"].index("|")
    text = case["source"].replace("|", "")
    prefix = text[:offset]
    position = {"line": prefix.count("\n"), "character": len(prefix.rsplit("\n", 1)[-1])}
    with Client() as client:
        client.send(message("initialize", id=1))
        client.until(lambda r: r.get("id") == 1)
        client.send(at_path(opening(text), source))
        client.send(
            message(
                "textDocument/completion",
                {"textDocument": {"uri": source.as_uri()}, "position": position},
                id=2,
            )
        )
        result = client.until(lambda r: r.get("id") == 2)["result"]
        labels = {i["label"] for i in result["items"]}
        assert set(case["include"]) <= labels
        assert not set(case["exclude"]) & labels
        from noscheme.lsp_completion import completion_items

        expected = completion_items(source, source.parent, {source: text}, offset)
        assert labels == {suggestion.label for suggestion in expected}
        by_label = {item["label"]: item for item in result["items"]}

        def position_at(index):
            prefix = text[:index]
            return {"line": prefix.count("\n"), "character": len(prefix.rsplit("\n", 1)[-1])}

        for suggestion in expected:
            assert by_label[suggestion.label]["textEdit"] == {
                "newText": suggestion.text,
                "range": {
                    "start": position_at(suggestion.start),
                    "end": position_at(suggestion.end),
                },
            }


@pytest.mark.parametrize(
    "case",
    json.loads(
        (Path(__file__).resolve().parents[2] / "tests/fixtures/lsp_code_actions.json").read_text()
    ),
    ids=lambda case: case["name"],
)
def test_instance_code_actions_parity(tmp_path, case):
    from noscheme.lsp_code_actions import instance_actions

    source, _ = project_files(tmp_path)
    text = case["source"]
    offset = text.index(" p", text.index("board B:")) + 1

    def position_at(at):
        prefix = text[:at]
        return {
            "line": prefix.count("\n"),
            "character": len(prefix.rsplit("\n", 1)[-1].encode("utf-16-le")) // 2,
        }

    pos = position_at(offset)
    with Client() as client:
        versioned = case["name"] == "bare"
        client.send(
            message(
                "initialize",
                {"capabilities": {"workspace": {"workspaceEdit": {"documentChanges": versioned}}}},
                id=1,
            )
        )
        capabilities = client.until(lambda r: r.get("id") == 1)["result"]["capabilities"]
        assert capabilities["codeActionProvider"]["codeActionKinds"] == ["refactor.rewrite"]
        client.send(at_path(opening(text), source))
        params = {
            "textDocument": {"uri": source.as_uri()},
            "range": {"start": pos, "end": pos},
            "context": {"diagnostics": []},
        }
        client.send(message("textDocument/codeAction", params, id=2))
        result = client.until(lambda r: r.get("id") == 2)["result"]
        expected = instance_actions(source, source.parent, {source: text}, offset)
        assert [a["title"] for a in result] == [a["title"] for a in expected]
        for actual, action in zip(result, expected):
            assert actual["kind"] == "refactor.rewrite"
            if versioned:
                change = actual["edit"]["documentChanges"][0]
                assert change["textDocument"] == {"uri": source.as_uri(), "version": 1}
                edits = change["edits"]
            else:
                edits = actual["edit"]["changes"][source.as_uri()]
            assert edits == [
                {"range": {"start": position_at(at), "end": position_at(at)}, "newText": inserted}
                for at, inserted in action["edits"]
            ]
        params["context"]["only"] = ["quickfix"]
        client.send(message("textDocument/codeAction", params, id=3))
        assert client.until(lambda r: r.get("id") == 3)["result"] == []


@pytest.mark.parametrize(
    "declaration,trigger,expected",
    [
        ("Push", None, {"PushButton"}),
        ("Capacitor0603<", "<", {"value"}),
        ("Capacitor0603<100n> foo(", "(", {"1", "2"}),
        ("Capacitor0603<100n> foo(1: global.gnd,", ",", {"2"}),
    ],
)
def test_component_completion_in_stm32_board(declaration, trigger, expected):
    source = Path(__file__).resolve().parents[2] / "examples/stm32h523/stm32h523.nosch"
    anchor = "  PushButton reset_button(debug.reset#, global.gnd)\n"
    text = source.read_text().replace(anchor, anchor + f"  {declaration}\n")
    offset = text.index(anchor) + len(anchor) + len("  " + declaration)
    prefix = text[:offset]
    position = {"line": prefix.count("\n"), "character": len(prefix.rsplit("\n", 1)[-1])}
    with Client() as client:
        client.send(message("initialize", id=1))
        capabilities = client.until(lambda r: r.get("id") == 1)["result"]["capabilities"]
        if trigger:
            assert trigger in capabilities["completionProvider"]["triggerCharacters"]
        client.send(at_path(opening(text), source))
        client.send(
            message(
                "textDocument/completion",
                {
                    "textDocument": {"uri": source.as_uri()},
                    "position": position,
                    "context": {"triggerKind": 2, "triggerCharacter": trigger}
                    if trigger
                    else {"triggerKind": 1},
                },
                id=2,
            )
        )
        result = client.until(lambda r: r.get("id") == 2)["result"]
        assert expected <= {item["label"] for item in result["items"]}
        from noscheme.lsp_completion import completion_items

        assert expected <= {
            s.label for s in completion_items(source, source.parent, {source: text}, offset)
        }
