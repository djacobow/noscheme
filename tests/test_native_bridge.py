"""Versioned registry snapshots preserve Python registration semantics."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from noscheme.erc_registry import load_registry
from noscheme.metadata import SCHEMAS
from noscheme.native_bridge import describe_registry, respond


def test_core_and_standard_fields():
    registry = load_registry()
    result = describe_registry(registry)
    for scope, core in SCHEMAS.items():
        fields = {f["name"] for f in result["schemas"][scope]}
        assert fields == core.keys() | registry.schemas.get(scope, {}).keys()
    assert set(result["rules"]) == registry.rules.keys()
    assert result["exports"] == {}
    # No callback, repr of a callback, or non-JSON default travels over the wire.
    assert json.loads(json.dumps(result, allow_nan=False)) == result
    for name, rule in registry.rules.items():
        assert [p["name"] for p in result["rules"][name]["parameters"]] == list(rule.parameters)
        assert [p["name"] for p in result["rules"][name]["parameters"] if p["has_default"]] == [
            name for name in rule.parameters if name in rule.defaults
        ]


def test_plugin_schema_and_opaque_default(tmp_path):
    plugin = tmp_path / "rules.py"
    plugin.write_text("""def register(r):
    r.fields("pin", {"ratings": {"limit": "voltage"}, "mode": ("pull", {"up", "none"})})
    r.net_rule("example.check", lambda ctx, **kw: None,
               parameters={"z": "literal", "a": "number"}, defaults={"z": object()})
    r.predicate("example.active", lambda endpoint: True)
""")
    response = respond({"version": 1, "method": "describe_registry", "erc_modules": [str(plugin)]})
    data = response["registry"]
    assert data["exports"] == {"example": {"check": "example.check", "active": "example.active"}}
    assert data["rules"]["example.check"]["parameters"] == [
        {"name": "z", "schema": {"kind": "literal"}, "has_default": True},
        {"name": "a", "schema": {"kind": "number"}, "has_default": False},
    ]
    assert {f["name"]: f["schema"] for f in data["schemas"]["pin"]}["mode"] == {
        "kind": "enum",
        "namespace": "pull",
        "values": ["none", "up"],
    }
    assert json.loads(json.dumps(response)) == response


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"version": True},
        {"version": 2},
        {"version": 1, "method": "run_code"},
        {"version": 1, "method": "describe_registry", "erc_modules": "file.py"},
    ],
)
def test_protocol_rejections(payload):
    with pytest.raises(ValueError):
        respond(payload)


def invoke(request):
    env = os.environ | {"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    return subprocess.run(
        [sys.executable, "-m", "noscheme.native_bridge"],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        env=env,
        timeout=10,
        check=True,
    )


def test_stdout_is_protocol_and_plugins_reload(tmp_path):
    plugin = tmp_path / "rules.py"
    source = """print("plugin import log")
def register(r):
    print("plugin registration log")
    r.fields("net", {"first": "number"})
"""
    plugin.write_text(source)
    request = {"version": 1, "method": "describe_registry", "erc_modules": [str(plugin)]}
    before = invoke(request)
    assert "plugin import log" in before.stderr
    assert "plugin registration log" in before.stderr
    first = json.loads(before.stdout)
    stamp = plugin.stat()
    plugin.write_text(source.replace("first", "later"))
    os.utime(plugin, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    second = json.loads(invoke(request).stdout)
    assert first["registry"]["schemas"]["net"][-1]["name"] == "first"
    assert second["registry"]["schemas"]["net"][-1]["name"] == "later"


def test_plugin_error_response(tmp_path):
    plugin = tmp_path / "broken.py"
    plugin.write_text('raise RuntimeError("broken plugin")\n')
    result = json.loads(
        invoke({"version": 1, "method": "describe_registry", "erc_modules": [str(plugin)]}).stdout
    )
    assert result["diagnostics"][0]["code"] == "erc.plugin_load"
    assert "broken plugin" in result["diagnostics"][0]["message"]


PLUGIN = """from noscheme.erc_registry import Application
def register(r):
    def wide(ctx, *, limit):
        if len(ctx.pins) > limit:
            yield ctx.error("too many pins")
    r.net_rule("example.wide", wide, parameters={"limit": "nonnegative"})
    r.predicate("example.passive", lambda pin, ctx: "passive" in pin.attributes.get("role", ()))
    def extra(ctx):
        if not ctx.pin:
            yield Application("count", "std.count", {"what": (("predicate", "example.passive"),), "max": 0})
    r.rule_set("example.extra", extra)
"""


def plugin_request(tmp_path):
    from noscheme.circuit import elaborate_project
    from noscheme.resolve import resolve_project
    from test_circuit import make, PART

    plugin = tmp_path / "rules.py"
    plugin.write_text(PLUGIN)
    root = make(
        tmp_path,
        "from example import wide\n"
        + PART
        + "block B:\n  net.passive n:\n    checks:\n      w: wide(limit=0)\n"
        + "  P p(1: n)\n",
    )
    config = root / "noscheme.toml"
    config.write_text(
        config.read_text()
        + 'erc_modules = ["rules.py"]\nerc_rule_sets = ["std.default", "example.extra"]\n'
    )
    project, definitions = resolve_project(root)
    circuit, _ = elaborate_project(project, definitions)
    return {
        "version": 1,
        "method": "plugin_checks",
        "erc_modules": [str(plugin)],
        "registry": describe_registry(project.config.erc_registry),
        "circuit": circuit,
        "configurations": [None],
        "native_rules": ["std.count"],
        "native_predicates": [],
    }


def test_plugin_checks_run_only_plugin_work(tmp_path):
    result = respond(plugin_request(tmp_path))
    (work,) = result["configurations"]
    # Plugin rule-set applications are returned by owner and rule set for the
    # native schedule; the max argument is a tagged exact integer.
    (count,) = work["applications"]["net:n"]["example.extra"]
    assert count["name"] == "count"
    assert count["params"]["max"] == {"type": "number", "tag": "integer", "value": "0"}
    # Plugin rules run here; native rules do not.
    assert work["results"]["net:n:w"]["diagnostics"][0]["message"] == "too many pins"
    assert "net:n:count" not in work["results"]
    # Plugin predicates are evaluated for each endpoint of native applications.
    assert work["predicates"]["net:n:count"] == {"example.passive": {"p.1": False}}


@pytest.mark.parametrize(
    "number,expected",
    [
        (
            {"type": "number", "tag": "integer", "value": "-9223372036854775808"},
            -9223372036854775808,
        ),
        ({"type": "number", "tag": "double", "value": 4.7e-12}, 4.7e-12),
    ],
)
def test_native_metadata_numbers(number, expected):
    from noscheme.native_bridge import decode_metadata

    assert decode_metadata(number) == expected


def test_large_native_metadata_integer_without_global_settings():
    from noscheme.native_bridge import decode_metadata

    before = sys.get_int_max_str_digits()
    with pytest.raises(ValueError):
        decode_metadata({"type": "number", "tag": "integer", "value": "1" + "0" * 5000})
    assert sys.get_int_max_str_digits() == before


@pytest.mark.parametrize(
    "number",
    [
        {"type": "number", "tag": "integer", "value": "1.0"},
        {"type": "number", "tag": "integer", "value": " 1"},
        {"type": "number", "tag": "integer", "value": "+1"},
        {"type": "number", "tag": "integer", "value": "01"},
        {"type": "number", "tag": "integer", "value": "1" * 20001},
        {"type": "number", "tag": "integer", "value": 1},
        {"type": "number", "tag": "double", "value": True},
        {"type": "number", "tag": "double", "value": float("inf")},
        {"type": "number", "tag": "double", "value": float("nan")},
        {"type": "number", "tag": "other", "value": 1},
        {"type": "number", "tag": "integer", "value": "1", "extra": 2},
    ],
)
def test_malformed_native_metadata_numbers(number):
    from noscheme.native_bridge import decode_metadata

    with pytest.raises(ValueError):
        decode_metadata(number)


@pytest.mark.parametrize("change", ["registry", "modules", "configurations", "schema", "stage"])
def test_plugin_checks_reject_inconsistent_requests(tmp_path, change):
    request = plugin_request(tmp_path)
    if change == "registry":
        request["registry"]["rules"].pop("std.sum")
    elif change == "modules":
        request["circuit"]["erc_modules"] = ["unexpected.py"]
    elif change == "configurations":
        request["configurations"] = "all"
    elif change == "schema":
        request["circuit"]["schema_version"] = True
    else:
        request["circuit"]["stage"] = "other"
    with pytest.raises(ValueError):
        respond(request)


def test_registered_checks_are_stable_across_hash_seeds(tmp_path):
    request = plugin_request(tmp_path)
    outputs = []
    for seed in ("1", "2", "3"):
        env = os.environ | {"PYTHONHASHSEED": seed}
        result = subprocess.run(
            [sys.executable, "-m", "noscheme.native_bridge"],
            input=json.dumps(request),
            text=True,
            capture_output=True,
            env=env,
            timeout=10,
            check=True,
        )
        outputs.append(json.loads(result.stdout))
    assert outputs[0] == outputs[1] == outputs[2]


def test_editor_help_is_shared_but_snapshot_owned():
    from noscheme.builtin_help import BUILTIN_HELP
    from noscheme.lsp_hover import BUILTIN_HELP as hover_help

    snapshot = describe_registry(load_registry())
    assert snapshot["help"] == BUILTIN_HELP == hover_help
    snapshot["help"]["role"]["passive"] = "changed snapshot"
    assert BUILTIN_HELP["role"]["passive"] != "changed snapshot"


@pytest.mark.parametrize(
    "value", [(1 << 63) - 1, -(1 << 63), 0], ids=["maximum", "minimum", "zero"]
)
def test_plugin_argument_integer_transport(value):
    from noscheme.native_bridge import decode_metadata, encode_arguments

    before = sys.get_int_max_str_digits()
    params = {"min": value, "what": (("field", "amount", value),), "flag": True, "scale": 1.25}
    encoded = json.loads(json.dumps(encode_arguments(params)))
    assert encoded["min"]["tag"] == "integer"
    assert decode_metadata(encoded) == {
        "min": value,
        "what": [["field", "amount", value]],
        "flag": True,
        "scale": 1.25,
    }
    assert type(encoded["flag"]) is bool
    assert sys.get_int_max_str_digits() == before


@pytest.mark.parametrize("value", [1 << 63, -(1 << 63) - 1])
def test_plugin_argument_integer_overflow(value):
    from noscheme.native_bridge import decode_metadata, encode_arguments

    with pytest.raises(ValueError):
        encode_arguments(value)
    with pytest.raises(ValueError):
        decode_metadata({"type": "number", "tag": "integer", "value": str(value)})
