"""Compare native ERC diagnostics and reports with the Python engine."""

import json
import os
from pathlib import Path
import subprocess
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


PLUGIN = """def register(r):
    def wide(ctx, *, limit):
        if len(ctx.pins) > limit:
            yield ctx.error("too many pins")
    r.net_rule("example.wide", wide, parameters={"limit": "nonnegative"})
"""


def plugin_project(tmp_path, plugin=PLUGIN):
    (tmp_path / "rules.py").write_text(plugin)
    source = """from example import wide
board B:
  net n: nettype.passive
    checks:
      test: wide(limit=0)
"""
    return make_project(tmp_path, {"src/main.nosch": PRELUDE + source}, erc_modules=["rules.py"])


def payload(path):
    return {
        "project": str(path),
        "elaborate": True,
        "registry_response": {
            "version": 1,
            "registry": describe_registry(load_project(path).config.erc_registry),
        },
    }


def test_standard_rules_need_no_python(probe, tmp_path):
    path = make_project(
        tmp_path, {"src/main.nosch": PRELUDE + "board B:\n  net n: nettype.passive\n"}
    )
    hang = fake_python(tmp_path, "import time\ntime.sleep(30)\n")
    result = probe(payload(path) | {"erc_python": hang, "erc_timeout_ms": 1000})
    assert result["circuit"]["erc"]["diagnostics"], result


def test_timeout_and_next_request(probe, tmp_path):
    path = plugin_project(tmp_path)
    result = probe(
        payload(path)
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
    path = plugin_project(tmp_path, PLUGIN + '    r.fields("net", {"first": "number"})\n')
    snapshot = payload(path)
    (tmp_path / "rules.py").write_text(PLUGIN + '    r.fields("net", {"later": "number"})\n')
    result = probe(snapshot | {"erc_python": sys.executable})
    assert result["diagnostics"][0]["code"] == "native.erc-bridge"
    assert "registry changed" in result["diagnostics"][0]["message"]


VALID = {
    "version": 1,
    "configurations": [
        {"applications": {}, "results": {"net:n:test": {"diagnostics": []}}, "predicates": {}}
    ],
}


@pytest.mark.parametrize(
    "change", ["valid", "version", "missing", "count", "result", "diagnostic", "deep_json"]
)
def test_malformed_plugin_response(probe, tmp_path, change):
    path = plugin_project(tmp_path)
    response = json.loads(json.dumps(VALID))
    work = response["configurations"][0]
    if change == "version":
        response["version"] = 2
    elif change == "missing":
        del response["configurations"]
    elif change == "count":
        response["configurations"].append(work)
    elif change == "result":
        work["results"] = {}
    elif change == "diagnostic":
        work["results"]["net:n:test"]["diagnostics"].append({"code": "erc.x"})
    text = json.dumps(response)
    if change == "deep_json":
        text = "[" * 200 + "0" + "]" * 200
    executable = fake_python(tmp_path, "import sys\nsys.stdin.read()\nprint(" + repr(text) + ")\n")
    result = probe(payload(path) | {"erc_python": executable})
    if change == "valid":
        assert "circuit" in result, result
    else:
        assert result["diagnostics"][0]["code"] == "native.erc-bridge", result


PREDICATE_PLUGIN = """def register(r):
    r.predicate("example.big", lambda pin, ctx: pin.label.endswith("2") or None)
    def boom(pin, ctx):
        raise ValueError("predicate failed")
    r.predicate("example.boom", boom)
"""


@pytest.mark.parametrize(
    "check",
    [
        "std.count(big, max=0)",
        "std.any(big)",
        "std.all(big)",
        "std.all(role.passive, where=big)",
        "std.sum(capacitance, where=big, max=1p)",
        "std.count([role.passive, boom], max=0)",
        "std.count([role.digital_input, boom], max=0)",
    ],
)
def test_plugin_predicates_in_standard_rules(probe, tmp_path, check):
    (tmp_path / "rules.py").write_text(PREDICATE_PLUGIN)
    source = f"""from example import big, boom
component P:
  pins:
    [1..2]:
      role: role.passive
      capacitance: 10p
board B:
  net n: nettype.passive
    checks:
      test: {check}
  inst p[2]: P
    connections:
      [1, 2]: n
"""
    compare(
        probe,
        make_project(tmp_path, {"src/main.nosch": PRELUDE + source}, erc_modules=["rules.py"]),
    )


def test_plugin_rule_set(probe, tmp_path):
    (tmp_path / "rules.py").write_text("""from noscheme.erc_registry import Application
def register(r):
    def wide(ctx, *, limit):
        if len(ctx.pins) > limit:
            yield ctx.error("too many pins")
    r.net_rule("example.wide", wide, parameters={"limit": "nonnegative"})
    def extra(ctx):
        if not ctx.pin:
            yield Application("wide", "example.wide", {"limit": 0})
            yield Application("count", "std.count", {"what": (("role", "passive"),), "max": 0})
    r.rule_set("example.extra", extra)
""")
    source = """component P:
  pins:
    1: role.passive
board B:
  net n: nettype.passive
  inst p: P(1: n)
"""
    path = make_project(
        tmp_path,
        {"src/main.nosch": PRELUDE + source},
        erc_modules=["rules.py"],
        erc_rule_sets=["std.default", "example.extra"],
    )
    compare(probe, path)


@pytest.mark.parametrize(
    "rule_sets",
    [
        ["example.first", "example.second"],
        ["example.second", "example.first"],
        ["example.first", "std.default", "example.second"],
    ],
)
def test_multiple_plugin_rule_sets_preserve_order(probe, tmp_path, rule_sets):
    (tmp_path / "rules.py").write_text("""from noscheme.erc_registry import Application
def register(r):
    def check(ctx):
        yield ctx.error(ctx.application)
    r.net_rule("example.check", check)
    def first(ctx):
        if not ctx.pin:
            yield Application("first", "example.check")
    def second(ctx):
        if not ctx.pin:
            yield Application("second", "example.check")
    r.rule_set("example.first", first)
    r.rule_set("example.second", second)
""")
    path = make_project(
        tmp_path,
        {"src/main.nosch": PRELUDE + "board B:\n  net n: nettype.passive\n"},
        erc_modules=["rules.py"],
        erc_rule_sets=rule_sets,
    )
    result = compare(probe, path)
    assert [
        d["message"]
        for d in result["circuit"]["erc"]["diagnostics"]
        if d["code"] == "erc.example.check"
    ] == [f"net:n:{name.rsplit('.', 1)[1]}" for name in rule_sets if name != "std.default"]


@pytest.mark.parametrize("amount", [10**20, -(10**20)])
@pytest.mark.parametrize("offset", [0, 1])
def test_plugin_rule_set_exact_integer_limits(probe, tmp_path, amount, offset):
    (tmp_path / "rules.py").write_text(f"""from noscheme.erc_registry import Application
def register(r):
    r.fields("pin", {{"amount": "number"}})
    def extra(ctx):
        if not ctx.pin:
            yield Application("limit", "std.sum", {{"field": "amount", "min": {amount + offset}}})
            yield Application("selection", "std.count", {{"what": (("field", "amount", {amount + 1}),), "max": 0}})
    r.rule_set("example.extra", extra)
""")
    source = f"""component P:
  pins:
    1: role.passive
      amount: {amount}
board B:
  net n: nettype.passive
  inst p: P(1: n)
"""
    path = make_project(
        tmp_path,
        {"src/main.nosch": PRELUDE + source},
        erc_modules=["rules.py"],
        erc_rule_sets=["example.extra"],
    )
    result = compare(probe, path)
    diagnostics = result["circuit"]["erc"]["diagnostics"]
    assert len(diagnostics) == offset
    if offset:
        assert diagnostics[0]["message"] == f"reduction is below minimum {amount + offset}"


# Python tests whose projects exercise ERC rules, waivers, and configurations.
CORPUS_TESTS = [
    "test_bias_reference.py",
    "test_current_loads.py",
    "test_erc.py",
    "test_erc_applications.py",
    "test_erc_config.py",
    "test_erc_contracts.py",
    "test_erc_extensions.py",
    "test_erc_families.py",
    "test_erc_targeted_waivers.py",
    "test_interchangeable.py",
    "test_nettypes.py",
    "test_voltage_limits.py",
]


def test_python_erc_test_projects(probe, tmp_path):
    """Every project loaded by the Python ERC tests checks identically."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "erc_corpus", "-o", "addopts="]
        + [str(ROOT / "tests" / name) for name in CORPUS_TESTS],
        cwd=ROOT,
        env=os.environ
        | {
            "NOSCHEME_ERC_CORPUS": str(corpus),
            "PYTHONPATH": os.pathsep.join([str(Path(__file__).parent), str(ROOT / "tests")]),
        },
        check=True,
        capture_output=True,
        timeout=600,
    )
    projects = sorted(corpus.iterdir())
    assert len(projects) > 100
    failures, compared = [], 0
    for project in projects:
        # Projects that fail before ERC belong to the circuit parity tests.
        try:
            loaded = load_project(project)
            elaborate_project(loaded, Resolver(loaded).resolve_all())
        except DiagnosticError:
            continue
        try:
            compare(probe, project / "noscheme.toml")
            compared += 1
        except AssertionError as error:
            failures.append(f"{project.name}: {str(error)[:300]}")
    assert not failures, f"{len(failures)} of {compared + len(failures)} differ:\n" + "\n".join(
        failures[:10]
    )
