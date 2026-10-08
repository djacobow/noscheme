"""Run the existing Python ERC engine on native elaborated circuits."""

import json
import os
from pathlib import Path
import sys

import pytest

from noscheme import ast as a
from noscheme.circuit import elaborate_project
from noscheme.diagnostics import DiagnosticError
from noscheme.erc import apply_erc, report_files
from noscheme.native_bridge import describe_registry
from noscheme.project import load_project
from noscheme.resolve import Resolver

from test_circuit_parity import circuit_data
from test_resolve_parity import normalize, PRELUDE
from test_project_parity import ROOT, make_project
from test_registry_parity import fake_python
import test_project_parity

probe = test_project_parity.probe
pytestmark = pytest.mark.skipif(
    not os.environ.get("NOSCHEME_NATIVE_PROBE"), reason="native probe supplied by Meson"
)


def compare(probe, path, configurations=None):
    project = load_project(path)
    expected, _ = elaborate_project(project, Resolver(project).resolve_all())
    request = {
        "project": str(path),
        "elaborate": True,
        "erc_python": sys.executable,
        "registry_response": {
            "version": 1,
            "registry": describe_registry(project.config.erc_registry),
        },
        "configurations": configurations,
    }
    try:
        apply_erc(expected, configurations, registry=project.config.erc_registry)
    except DiagnosticError as error:
        actual = probe(request)
        assert normalize(actual["diagnostics"]) == [a.to_data(d) for d in error.diagnostics]
        return actual
    actual = probe(request)
    assert "circuit" in actual, actual
    assert normalize(actual["circuit"]["erc"]) == expected["erc"]
    assert circuit_data(normalize(actual["circuit"])) == expected
    reports = report_files(expected, Path("."))
    assert normalize(json.loads(actual["reports"]["erc.json"])) == expected["erc"]
    assert actual["reports"]["erc.txt"] == reports[Path("erc.txt")]
    return actual


@pytest.mark.parametrize(
    "example", sorted((ROOT / "examples").glob("*/noscheme.toml")), ids=lambda p: p.parent.name
)
def test_examples(probe, example):
    assert "circuit" in compare(probe, example)


@pytest.mark.parametrize(
    "configurations",
    [
        None,
        [],
        ["usb_powered"],
        ["header_powered", "usb_powered"],
        ["usb_powered", "usb_powered"],
        ["missing"],
    ],
)
def test_configurations(probe, configurations):
    compare(probe, ROOT / "examples/power_modes/noscheme.toml", configurations)


def test_rule_failure_is_source_located(probe, tmp_path):
    plugin = tmp_path / "rules.py"
    plugin.write_text("""def register(r):
    def broken(ctx):
        raise ValueError("callback failure")
    r.net_rule("example.broken", broken)
""")
    path = make_project(
        tmp_path,
        {
            "src/main.nosch": PRELUDE
            + """from example import broken
board B:
  net n: nettype.passive
    checks:
      test: broken()
"""
        },
        erc_modules=["rules.py"],
    )
    assert "diagnostics" in compare(probe, path)


def test_timeout_and_next_request(probe, tmp_path):
    path = make_project(
        tmp_path, {"src/main.nosch": PRELUDE + "board B:\n  net n: nettype.passive\n"}
    )
    project = load_project(path)
    payload = {
        "project": str(path),
        "elaborate": True,
        "registry_response": {
            "version": 1,
            "registry": describe_registry(project.config.erc_registry),
        },
    }
    result = probe(
        payload
        | {
            "erc_python": fake_python(tmp_path, "import time\ntime.sleep(30)\n"),
            "erc_timeout_ms": 1000,
        }
    )
    assert result["diagnostics"][0]["code"] == "native.erc-bridge"
    assert "timed out" in result["diagnostics"][0]["message"]
    assert "circuit" in compare(probe, path)


@pytest.mark.parametrize("inherited", [False, True])
def test_application_order(probe, tmp_path, inherited):
    source = """board B:
  net n: nettype.passive
    checks:
      z_first: std.count(role.passive, min=1)
      a_second: std.count(role.passive, min=2)
"""
    if inherited:
        source = """nettype Base extends nettype.passive:
  checks:
    z_first: std.count(role.passive, min=1)
    a_second: std.count(role.passive, min=2)
nettype Child extends Base:
  checks:
    z_first: std.count(role.passive, min=3)
board B:
  net n: Child
    checks:
      b_last: std.count(role.passive, min=4)
"""
    compare(probe, make_project(tmp_path, {"src/main.nosch": PRELUDE + source}))


@pytest.mark.parametrize("target", ['"p[0].1"', '"p[7].1"'])
def test_targeted_waivers(probe, tmp_path, target):
    source = f"""component P:
  pins:
    1: role.power_input
board B:
  net gnd: nettype.ground
    waivers:
      incompatible_role:
        reason: "Ground the disabled power domain; héllo."
        pins: [{target}]
  inst p[2]: P(1: gnd)
"""
    compare(probe, make_project(tmp_path, {"src/main.nosch": PRELUDE + source}))


def test_plugin_defaults_decode_and_single_execution(probe, tmp_path):
    log = tmp_path / "calls.txt"
    plugin = tmp_path / "rules.py"
    plugin.write_text(f"""from pathlib import Path
TOKEN = object()
def register(r):
    print("registration log")
    def once(ctx, token):
        print("checker log")
        assert token is TOKEN
        assert ctx.net.attributes["budget"] == 123456789012345678901234567890
        with Path({str(log)!r}).open("a") as stream:
            stream.write("call\\n")
        yield ctx.diagnostic("example", "first", severity="warning")
    r.fields("net", {{"budget": "number"}})
    r.net_rule("example.once", once, parameters={{"token": "literal"}}, defaults={{"token": TOKEN}})
""")
    source = """from example import once
board B:
  net n: nettype.passive
    budget: 123456789012345678901234567890
    checks:
      one: once()
"""
    path = make_project(tmp_path, {"src/main.nosch": PRELUDE + source}, erc_modules=["rules.py"])
    payload = {
        "project": str(path),
        "registry_python": sys.executable,
        "elaborate": True,
        "erc_python": sys.executable,
    }
    first = probe(payload)
    assert "circuit" in first, first
    assert log.read_text() == "call\n"
    assert any(d["message"] == "first" for d in first["circuit"]["erc"]["diagnostics"])
    stamp = plugin.stat()
    plugin.write_text(plugin.read_text().replace('"first"', '"later"'))
    os.utime(plugin, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    second = probe(payload)
    assert log.read_text() == "call\ncall\n"
    assert any(d["message"] == "later" for d in second["circuit"]["erc"]["diagnostics"])
    assert not (tmp_path / "build").exists()


def test_changed_registry_rejected(probe, tmp_path):
    path = make_project(
        tmp_path,
        {"src/main.nosch": PRELUDE + "board B:\n  net n: nettype.passive\n"},
        erc_modules=["rules.py"],
    )
    plugin = tmp_path / "rules.py"
    plugin.write_text('def register(r):\n    r.fields("net", {"first": "number"})\n')
    snapshot = describe_registry(load_project(path).config.erc_registry)
    plugin.write_text('def register(r):\n    r.fields("net", {"later": "number"})\n')
    result = probe(
        {
            "project": str(path),
            "registry_response": {"version": 1, "registry": snapshot},
            "elaborate": True,
            "erc_python": sys.executable,
        }
    )
    assert result["diagnostics"][0]["code"] == "native.erc-bridge"
    assert "registry changed" in result["diagnostics"][0]["message"]


@pytest.mark.parametrize(
    "change", ["version", "missing", "count", "diagnostic", "json", "text", "deep_json"]
)
def test_malformed_execution_response(probe, tmp_path, change):
    path = make_project(
        tmp_path, {"src/main.nosch": PRELUDE + "board B:\n  net n: nettype.passive\n"}
    )
    project = load_project(path)
    circuit, _ = elaborate_project(project, Resolver(project).resolve_all())
    apply_erc(circuit, registry=project.config.erc_registry)
    response = {
        "version": 1,
        "erc": circuit["erc"],
        "reports": {p.name: s for p, s in report_files(circuit, Path(".")).items()},
    }
    if change == "version":
        response["version"] = 2
    elif change == "missing":
        del response["erc"]
    elif change == "count":
        response["erc"]["errors"] = 999
    elif change == "diagnostic":
        response["erc"]["diagnostics"][0]["severity"] = "not-a-severity"
    elif change == "json":
        response["reports"]["erc.json"] = "{}"
    elif change == "text":
        response["reports"]["erc.txt"] = None
    else:
        response["reports"]["erc.json"] = "[" * 200 + "0" + "]" * 200
    executable = fake_python(
        tmp_path, "import sys\nsys.stdin.read()\nprint(" + repr(json.dumps(response)) + ")\n"
    )
    result = probe(
        {
            "project": str(path),
            "registry_response": {
                "version": 1,
                "registry": describe_registry(project.config.erc_registry),
            },
            "elaborate": True,
            "erc_python": executable,
        }
    )
    assert result["diagnostics"][0]["code"] == "native.erc-bridge"
