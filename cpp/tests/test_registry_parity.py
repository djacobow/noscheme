"""Native registry validation, real Python subprocesses, and failure cleanup."""

import copy
import os
import sys

import pytest

from noscheme.ast import to_data
from noscheme.native_bridge import describe_registry
from noscheme.project import erc_exports, load_project, project_summary, read_config

from test_project_parity import ROOT, make_project
import test_project_parity

probe = test_project_parity.probe
pytestmark = pytest.mark.skipif(
    not os.environ.get("NOSCHEME_NATIVE_PROBE"), reason="native probe supplied by Meson"
)


@pytest.mark.parametrize(
    "example", sorted((ROOT / "examples").glob("*/noscheme.toml")), ids=lambda p: p.parent.name
)
def test_project_with_real_bridge(probe, example):
    config = read_config(example)
    expected = describe_registry(config.erc_registry)
    actual = probe({"project": str(example), "registry_python": sys.executable})
    assert actual["registry"] == expected
    assert actual["project"] == project_summary(load_project(example))
    assert expected["exports"] == {
        space: {name: symbol.name for name, symbol in names.items()}
        for space, names in erc_exports(config).items()
    }


def test_plugin_imports_and_snapshot_edits(probe, tmp_path):
    path = make_project(
        tmp_path,
        {"src/main.nosch": "from local import custom\nboard B:\n  T n\n"},
        erc_modules=["rules.py"],
    )
    plugin = tmp_path / "rules.py"
    source = """print("registry plugin loaded")
def register(r):
    r.net_rule("local.custom", lambda ctx: None)
    r.fields("net", {"first": "number"})
"""
    plugin.write_text(source)
    payload = {"project": str(path), "registry_python": sys.executable}
    first = probe(payload)
    assert first["project"] == project_summary(load_project(path))
    stamp = plugin.stat()
    plugin.write_text(source.replace("first", "later"))
    os.utime(plugin, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    second = probe(payload)
    assert first["registry"]["schemas"]["net"][-1]["name"] == "first"
    assert second["registry"]["schemas"]["net"][-1]["name"] == "later"


def test_plugin_error_location(probe, tmp_path):
    path = make_project(tmp_path, erc_modules=["bad.py"])
    (tmp_path / "bad.py").write_text('raise ValueError("broken")')
    actual = probe({"project": str(path), "registry_python": sys.executable})
    diagnostic = actual["diagnostics"][0]
    assert diagnostic["code"] == "erc.plugin_load"
    assert diagnostic["span"] == to_data(read_config(path).span)


def test_supplied_snapshot(probe, tmp_path):
    path = make_project(tmp_path)
    snapshot = describe_registry(read_config(path).erc_registry)
    response = {"version": 1, "registry": snapshot}
    assert probe({"project": str(path), "registry_response": response})["registry"] == snapshot


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(version=2),
        lambda r: r.update(version=True),
        lambda r: r["registry"].update(schemas=[]),
        lambda r: r["registry"]["schemas"].update(
            net=[{"name": "x", "schema": {"kind": "enum", "namespace": "bias", "values": []}}]
        ),
        lambda r: r["registry"]["rules"]["std.count"].update(scope="unknown"),
        lambda r: r["registry"]["rules"]["std.count"]["parameters"].append(
            r["registry"]["rules"]["std.count"]["parameters"][0]
        ),
        lambda r: r["registry"].update(exports={"bad": {"missing": "bad.missing"}}),
        lambda r: r.update(diagnostics=[]),
    ],
)
def test_malformed_snapshot(probe, tmp_path, mutation):
    path = make_project(tmp_path)
    response = copy.deepcopy(
        {"version": 1, "registry": describe_registry(read_config(path).erc_registry)}
    )
    mutation(response)
    actual = probe({"project": str(path), "registry_response": response})
    assert actual["diagnostics"][0]["code"] == "native.erc-registry"


def fake_python(tmp_path, source):
    path = tmp_path / "python with spaces"
    path.write_text(f"#!{sys.executable}\n" + source)
    path.chmod(0o755)
    return str(path)


@pytest.mark.parametrize(
    "source,needle",
    [
        ("import time\ntime.sleep(10)\n", "timed out"),
        ("import sys\nsys.exit(7)\n", "unsuccessfully"),
        ('import sys\nsys.stdin.read()\nprint("not JSON")\n', "decode"),
        ('import sys\nsys.stdout.write("x" * (17 * 1024 * 1024))\n', "16 MiB"),
        ("import os, time\nos.close(1)\nos.close(0)\ntime.sleep(10)\n", "timed out"),
    ],
)
def test_subprocess_failures_and_next_request(probe, tmp_path, source, needle):
    path = make_project(tmp_path)
    actual = probe(
        {
            "project": str(path),
            "registry_python": fake_python(tmp_path, source),
            "registry_timeout_ms": 1000,
        }
    )
    assert actual["diagnostics"][0]["code"] == "native.erc-registry"
    assert needle in actual["diagnostics"][0]["message"]
    assert "registry" in probe({"project": str(path), "registry_python": sys.executable})


def test_missing_python(probe, tmp_path):
    path = make_project(tmp_path)
    actual = probe({"project": str(path), "registry_python": str(tmp_path / "missing")})
    assert "cannot start" in actual["diagnostics"][0]["message"]


def test_timeout_reaps_child(probe, tmp_path):
    path = make_project(tmp_path)
    pidfile = tmp_path / "bridge.pid"
    script = fake_python(
        tmp_path,
        f"import os, time\nfrom pathlib import Path\nPath({str(pidfile)!r}).write_text(str(os.getpid()))\ntime.sleep(30)\n",
    )
    actual = probe({"project": str(path), "registry_python": script, "registry_timeout_ms": 1000})
    assert "timed out" in actual["diagnostics"][0]["message"]
    pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
