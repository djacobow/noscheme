"""Scalar metadata parity; specialized applications/choices are staged separately."""

import os

import pytest

from noscheme import ast as a
from noscheme.diagnostics import DiagnosticError
from noscheme.evaluate import Number, encoded
from noscheme.metadata import NumericExpression, Value, pin_metadata, read_metadata
from noscheme.native_bridge import describe_registry
from noscheme.project import load_project

from test_parameters_parity import normalized
from test_project_parity import ROOT, make_project
import test_project_parity

probe = test_project_parity.probe
pytestmark = pytest.mark.skipif(
    not os.environ.get("NOSCHEME_NATIVE_PROBE"), reason="native probe supplied by Meson"
)


def metadata_data(value):
    if isinstance(value, Value):
        return {
            "value": metadata_data(value.value),
            "span": a.to_data(value.span),
            "module": value.module,
        }
    if isinstance(value, (NumericExpression, a.Node)):
        return a.to_data(value)
    if type(value) in {int, float}:
        return encoded(Number(value))
    if isinstance(value, dict):
        return {k: metadata_data(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [metadata_data(v) for v in value]
    return value


def compare(
    probe, tmp_path, text, scope="component", pin=False, locals=(), plugin=None, imports=""
):
    body = (
        "  pins:\n    1: " + text
        if pin
        else "  metadata:\n" + "\n".join("    " + line for line in text.splitlines())
    )
    source = (
        "from noscheme import role, unused, bias, num, std\nfrom noscheme import role as r\ncomponent Part:\n"
        + body
        + "\nboard B:\n  inst p: Part\n"
    )
    source = imports + source
    if plugin:
        (tmp_path / "rules.py").write_text(plugin)
    config = make_project(
        tmp_path, {"src/main.nosch": source}, **({"erc_modules": ["rules.py"]} if plugin else {})
    )
    project = load_project(config)
    options = {"module": "main", "definition": "Part", "scope": scope, "locals": list(locals)}
    if pin:
        options["pin"] = "1"
    request = {
        "project": str(config),
        "registry_response": {
            "version": 1,
            "registry": describe_registry(project.config.erc_registry),
        },
        "metadata": options,
    }
    definition = project.modules["main"].symbols["Part"].declaration
    fields = definition.body[0].entries
    try:
        expected = (
            pin_metadata(fields[0], scope, project, "main", locals)
            if pin
            else read_metadata(fields, scope, project, "main", locals)
        )
    except DiagnosticError as exc:
        actual = probe(request)
        assert "diagnostics" in actual, actual
        assert normalized(
            [{k: v for k, v in d.items() if k != "message"} for d in actual["diagnostics"]],
            native=True,
        ) == [{k: v for k, v in a.to_data(d).items() if k != "message"} for d in exc.diagnostics]
        return actual
    actual = probe(request)
    assert "error" not in actual and "diagnostics" not in actual, actual
    assert normalized(actual["metadata"], native=True) == metadata_data(expected)
    return actual


@pytest.mark.parametrize(
    "text,scope",
    [
        ('footprint: "A:B"\nvalue: num.ceil(n)\nref_prefix: "R"', "component"),
        (
            'custom:\n  description: "héllo"\n  active: true\n  n: -4.7k\n  nested:\n    list: [1, "µ", false, [2, 3]]',
            "component",
        ),
        ("custom:\n  value: 123456789012345678901234567890", "component"),
        ("custom:\n  value: 1e999", "component"),
        ("custom:\n  value: -(2)", "component"),
        ("custom:\n  value: unknown", "component"),
        ('custom: "invalid"', "component"),
        ('custom:\n  type:\n    node: "user data"\n  expression: 2', "component"),
        ("footprint: 3", "component"),
        ("unknown: 3", "component"),
        ("erc: 3", "component"),
        ('interchangeable: [1, "2", anode]', "component"),
        ("interchangeable: [1.0]", "component"),
        ('interchangeable: [""]', "component"),
        ("interchangeable: 1", "component"),
        (
            "role: [role.passive, r.digital_input]\nunused: unused.always\nmax_load_current: 10m",
            "pin",
        ),
        ("role: []", "pin"),
        ('role: "passive"', "pin"),
        ("role: role.missing", "pin"),
        ("tristate_capable: true", "pin"),
        ("tristate_capable: 1", "pin"),
        ("max_load_current: -1", "pin"),
        ("max_load_current: true", "pin"),
        ("max_load_current: 0", "pin"),
        ("max_load_current: current * 3", "pin"),
        ("max_load_current: (3)", "pin"),
        ("aggregate_load:\n  max_load_current: load", "port"),
        ("aggregate_load: 1", "port"),
        ("role: role.digital_input", "instance_pin"),
        ("role: [role.digital_input, role.passive]", "instance_pin"),
    ],
)
def test_values(probe, tmp_path, text, scope):
    compare(probe, tmp_path, text, scope)


@pytest.mark.parametrize(
    "text,scope,locals",
    [
        ("[role.open_drain, bias.none, unused.always]", "pin", ()),
        ("[role.passive, role.digital_input]", "pin", ()),
        ("[]", "pin", ()),
        ("role.passive\n      role: role.passive", "pin", ()),
        ("[unused.always, unused.never]", "pin", ()),
        ("[role.passive, role.digital_input]", "instance_pin", ()),
        ("role.passive", "pin", ("role",)),
        ("num.ceil(1)", "pin", ()),
        ("role.missing", "pin", ()),
        ("r.passive", "pin", ()),
    ],
)
def test_pin_shorthand(probe, tmp_path, text, scope, locals):
    compare(probe, tmp_path, text, scope, pin=True, locals=locals)


PLUGIN = """def register(r):
    r.fields("component", {
        "ratings": {"volts": "voltage", "amps": "current"},
        "mode": ("bias", {"none", "pull_up"}),
        "flags": "attributes", "resistance": "resistance",
    })
"""


@pytest.mark.parametrize(
    "text",
    [
        "ratings:\n  volts: -3\n  amps: budget * 2",
        'ratings:\n  volts: "bad"',
        "ratings: 1",
        'mode: "pull_up"',
        "mode: bias.none",
        "mode: bias.bad",
        "resistance: 0",
        "resistance: 4.7k",
        "flags:\n  role: role.passive\n  nested:\n    mode: bias.none",
        "flags:\n  role: role.bad",
    ],
)
def test_plugin_schemas(probe, tmp_path, text):
    compare(probe, tmp_path, text, plugin=PLUGIN)


def test_shadowed_enum(probe, tmp_path):
    compare(probe, tmp_path, "role: role.passive", "pin", locals={"role"})


def test_metadata_number_limit(probe, tmp_path):
    compare(probe, tmp_path, "custom:\n  large: " + "9" * 4500)


def test_merge_preserves_user_keys_and_provenance(probe, tmp_path):
    from noscheme.metadata import merge

    config = make_project(
        tmp_path,
        {
            "src/main.nosch": """component Parent:
  metadata:
    value: 10
    custom:
      type:
        before: 1
        after: 2
      node: "user data"
      expression:
        old: true
component Child:
  metadata:
    value: 20
    custom:
      type:
        after: 3
      expression:
        new: false
board B:
  inst p: Child
"""
        },
    )
    project = load_project(config)
    snapshot = {"version": 1, "registry": describe_registry(project.config.erc_registry)}
    request = {
        "project": str(config),
        "registry_response": snapshot,
        "metadata": {"module": "main", "definition": "Parent", "scope": "component"},
    }
    request["metadata"].update(definition="Child", parent_definition="Parent")
    actual = probe(request)
    expected = []
    for name in ("Parent", "Child"):
        fields = project.modules["main"].symbols[name].declaration.body[0].entries
        expected.append(read_metadata(fields, "component", project, "main"))
    assert "metadata" in actual, actual
    assert normalized(actual["metadata"], native=True) == metadata_data(merge(*expected))


def test_shadowed_attribute_enum(probe, tmp_path):
    compare(probe, tmp_path, "flags:\n  x: role.passive", locals={"role"}, plugin=PLUGIN)


@pytest.mark.parametrize(
    "scope,text",
    [
        ("net", "checks:\n  capacitance: std.sum(capacitance, max=400p)"),
        ("net", 'waivers:\n  missing_driver: "demo"'),
        ("board", 'erc_choices:\n  supply: ["usb", "header"]'),
        ("board", 'configurations:\n  usb:\n    supply: "usb"'),
        ("component", "current_loads:\n  core:\n    pins: [1, 2]\n    max_load_current: 10m"),
        ("instance_pin", "role: choice(mode, input=role.digital_input)"),
    ],
)
def test_specialized_forms(probe, tmp_path, scope, text):
    compare(probe, tmp_path, text, scope)


@pytest.mark.parametrize(
    "scope,text",
    [
        ("net", 'checks:\n  cap: std.sum(capacitance, max=limit * 2, missing="error")'),
        ("net", "checks:\n  any: std.any([role.open_drain, bias.pull_up, tristate_capable])"),
        ("net", "checks:\n  any: std.any([])"),
        ("net", "checks:\n  cap: std.sum()"),
        ("pin", 'checks:\n  ref: std.bias_reference("1")'),
        ("port", "checks:\n  cap: std.sum(capacitance, max=400p)"),
        ("pin", "checks:\n  cap: std.sum(capacitance, max=400p)"),
        ("net", 'checks:\n  ref: std.bias_reference("1")'),
        ("net", "checks:\n  bad: 1"),
        ("net", "checks:\n  bad: std.unknown()"),
        ("net", "checks:\n  bad: std.any(role.passive, role.passive)"),
        ("net", "checks:\n  bad: std.any(role.passive, what=role.passive)"),
        ("net", "checks:\n  bad: std.any(unknown=1)"),
        ("net", 'checks:\n  bad: std.any(role.passive, missing="other")'),
        ("net", "checks:\n  bad: std.any(role.passive, missing=1)"),
        ("net", "checks:\n  bad: std.sum(unknown, max=2)"),
        ("net", 'checks:\n  bad: std.sum("capacitance", max=2)'),
        ("net", "checks:\n  bad: std.any(123)"),
        ("net", "checks:\n  bad: std.any(role.bad)"),
        ("net", "checks:\n  bad: std.any(bias.bad)"),
        ("net", "checks: 1"),
        ("net", "checks:\n  bad:\n    value: 1"),
        ("board", 'erc_choices:\n  supply#: ["usb", "header"]'),
        ("board", "erc_choices:\n  supply: []"),
        ("board", 'erc_choices:\n  supply: ["usb", "usb"]'),
        ("board", 'erc_choices:\n  supply: ["usb", 1]'),
        ("board", 'erc_choices:\n  supply: ["usb", "\u2003"]'),
        ("board", 'erc_choices:\n  "bad.name": ["usb"]'),
        (
            "board",
            'configurations:\n  active:\n    supply: "usb"\n  inactive:\n    supply: "header"',
        ),
        ("board", "configurations:\n  active:\n    supply: 1"),
        ("instance", 'erc_choices:\n  supply: "usb"'),
        (
            "instance",
            'erc_choices:\n  supply: choice(mode, on="usb", off="header")',
        ),
        ("instance", "erc_choices:\n  supply: choice(mode)"),
        (
            "instance",
            'erc_choices:\n  supply: choice(parent.mode, on="usb")',
        ),
        ("instance", "erc_choices:\n  supply: choice(mode, on=3)"),
        (
            "instance_pin",
            "role: choice(mode, in=role.digital_input, out=role.digital_output)",
        ),
        ("instance_pin", 'role: choice(mode, in="digital_input")'),
        ("net", 'waivers:\n  custom_application: "prototypé"'),
        ("net", 'waivers:\n  missing_driver:\n    reason: "prototype"\n    pins: ["r.1", "r.2"]'),
        ("net", 'waivers:\n  unused_pin: "bad waiver"'),
        ("net", 'waivers:\n  "checker:app": "bad waiver"'),
        ("net", 'waivers:\n  missing_driver: "\u00a0"'),
        ("net", "waivers:\n  missing_driver: 1"),
        ("net", 'waivers:\n  missing_driver:\n    reason: "prototype"'),
        ("net", 'waivers:\n  missing_driver:\n    reason: "prototype"\n    pins: []'),
        ("net", 'waivers:\n  missing_driver:\n    reason: "prototype"\n    pins: ["r.1", "r.1"]'),
        ("net", 'waivers:\n  missing_driver:\n    reason: "prototype"\n    pins: ["r.1", 2]'),
        ("net", 'waivers:\n  missing_driver:\n    reason: "prototype"\n    pins: "r.1"'),
        ("component", "current_loads:\n  core:\n    pins: []\n    max_load_current: 10m"),
        ("component", "current_loads:\n  core: 10m"),
        ("component", "current_loads:\n  core:\n    pins: [1]\n    max_load_current: -1"),
        (
            "component",
            'current_loads:\n  core:\n    pins: [1, "2"]\n    max_load_current: load * 2',
        ),
    ],
)
def test_forms_and_diagnostics(probe, tmp_path, scope, text):
    compare(probe, tmp_path, text, scope)


@pytest.mark.parametrize(
    "text,locals",
    [
        ("checks:\n  x: std.any(role.passive)", {"std"}),
        ("checks:\n  x: std.any(role.passive)", {"role"}),
        ("checks:\n  x: std.any(bias.none)", {"bias"}),
        ("checks:\n  x: std.any(tristate_capable)", {"tristate_capable"}),
    ],
)
def test_rule_shadowing(probe, tmp_path, text, locals):
    compare(probe, tmp_path, text, "net", locals=locals)


RULE_PLUGIN = """def register(r):
    r.fields("pin", {"enabled": "bool", "load": "current"})
    r.fields("net", {"budget": "current"})
    r.predicate("demo.active", lambda endpoint: True)
    r.net_rule("demo.check", lambda ctx, **kw: None,
               parameters={"z": "field", "a": "net_fields", "where": "selector", "mode": ("bias", ("none", "pull_up"))})
"""


@pytest.mark.parametrize(
    "call",
    [
        'check(load, [budget], [active, enabled], "none")',
        "check(load, budget, [], bias.pull_up)",
        'check(load, [budget, budget], active, "none")',
        'check(load, [max_load_current], active, "none")',
        'check(load, "budget", active, "none")',
        'check(enabled, [], [enabled], "bad")',
    ],
)
def test_plugin_rule_arguments(probe, tmp_path, call):
    compare(
        probe,
        tmp_path,
        "checks:\n  test: " + call,
        "net",
        plugin=RULE_PLUGIN,
        imports="from demo import check, active\n",
    )


def test_qualified_plugin_rule(probe, tmp_path):
    compare(
        probe,
        tmp_path,
        'checks:\n  test: demo.check(load, budget, demo.active, "none")',
        "net",
        plugin=RULE_PLUGIN,
        imports="import demo\n",
    )


@pytest.mark.parametrize(
    "parent_call,child_call",
    [
        ("std.sum(capacitance, max=400p)", "std.sum(capacitance, max=200p)"),
        ("std.sum(capacitance, max=400p)", "std.count(role.passive, max=3)"),
    ],
)
def test_inherited_application(probe, tmp_path, parent_call, child_call):
    from noscheme.metadata import merge

    config = make_project(
        tmp_path,
        {
            "src/main.nosch": f"""from noscheme import std, role
component Parent:
  metadata:
    checks:
      cap: {parent_call}
component Child:
  metadata:
    checks:
      cap: {child_call}
board B:
  inst p: Child
"""
        },
    )
    project = load_project(config)
    snapshot = {"version": 1, "registry": describe_registry(project.config.erc_registry)}
    request = {
        "project": str(config),
        "registry_response": snapshot,
        "metadata": {"module": "main", "definition": "Parent", "scope": "net"},
    }
    request["metadata"].update(definition="Child", parent_definition="Parent")
    actual = probe(request)
    values = [
        read_metadata(
            project.modules["main"].symbols[name].declaration.body[0].entries,
            "net",
            project,
            "main",
        )
        for name in ("Parent", "Child")
    ]
    try:
        expected = merge(*values)
    except DiagnosticError as exc:
        assert actual["diagnostics"][0]["code"] == exc.diagnostics[0].code
        assert normalized(actual["diagnostics"][0]["span"], native=True) == a.to_data(
            exc.diagnostics[0].span
        )
    else:
        assert normalized(actual["metadata"], native=True) == metadata_data(expected)


def metadata_sites(definition):
    """Yield actual source fields with the scopes used by the Python resolver."""
    for i, node in enumerate(definition.body):
        base = ["body", i]
        if isinstance(node, a.Field):
            if definition.kind == "nettype":
                yield base, "net", node, False
            elif definition.kind == "bundle":
                for j, f in enumerate(node.fields):
                    yield base + ["fields", j], "port", f, False
        elif isinstance(node, a.Section):
            for j, field in enumerate(node.entries):
                at = base + ["entries", j]
                if node.name == "metadata":
                    yield at, definition.kind, field, False
                elif node.name == "pins":
                    yield at, "pin", field, True
                elif node.name == "ports":
                    for k, f in enumerate(field.fields):
                        yield at + ["fields", k], "port", f, False
        elif isinstance(node, a.Net):
            for j, field in enumerate(node.fields):
                yield base + ["fields", j], "net", field, False
        elif isinstance(node, a.Instance):
            for j, section in enumerate(node.sections):
                if section.name != "metadata":
                    continue
                for k, field in enumerate(section.entries):
                    at = base + ["sections", j, "entries", k]
                    if field.key.first.text == "pins":
                        for n, pin in enumerate(field.fields):
                            yield at + ["fields", n], "instance_pin", pin, True
                    else:
                        yield at, "instance", field, False


@pytest.mark.parametrize(
    "example", sorted((ROOT / "examples").glob("*/noscheme.toml")), ids=lambda p: p.parent.name
)
def test_example_metadata(probe, example):
    from noscheme.resolve import Resolver

    project = load_project(example)
    definitions = Resolver(project).resolve_interfaces()
    snapshot = {"version": 1, "registry": describe_registry(project.config.erc_registry)}
    requests, expected = [], []
    offsets = {}
    for module in project.modules.values():
        text = module.path.read_text()
        if not text.isascii():
            positions, total = {0: 0}, 0
            for index, char in enumerate(text, 1):
                total += len(char.encode())
                positions[total] = index
            offsets[str(module.path)] = positions
        for definition in module.ast.items:
            if not isinstance(definition, a.Definition):
                continue
            locals = list(definitions[module.name, definition.name.text].parameters)
            for path, scope, field, pin in metadata_sites(definition):
                requests.append(
                    {
                        "module": module.name,
                        "definition": definition.name.text,
                        "scope": scope,
                        "path": path,
                        "single": True,
                        "as_pin": pin,
                        "locals": locals,
                    }
                )
                value = (
                    pin_metadata(field, scope, project, module.name, locals)
                    if pin
                    else read_metadata((field,), scope, project, module.name, locals)
                )
                expected.append(metadata_data(value))

    def normalize(value):
        if isinstance(value, list):
            return [normalize(v) for v in value]
        if isinstance(value, dict):
            if {"filename", "start_pos", "end_pos"} <= value.keys():
                positions = offsets.get(value["filename"])
                return (
                    value
                    if positions is None
                    else value | {k: positions[value[k]] for k in ("start_pos", "end_pos")}
                )
            return {k: normalize(v) for k, v in value.items()}
        return value

    assert requests
    actual = probe({"project": str(example), "registry_response": snapshot, "metadata": requests})
    assert "metadata" in actual, actual
    assert len(actual["metadata"]) == len(expected)
    for options, value, reference in zip(requests, actual["metadata"], expected, strict=True):
        assert normalize(value) == reference, options
