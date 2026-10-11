"""Inherited public contract parity, before body/reference validation."""

import os
from pathlib import Path
import runpy

import pytest

from noscheme import ast as a
from noscheme.diagnostics import DiagnosticError
from noscheme.native_bridge import describe_registry
from noscheme.project import load_project, read_config
from noscheme.resolve import Resolver

from test_metadata_parity import metadata_data
from test_project_parity import ROOT, make_project
import test_project_parity

probe = test_project_parity.probe
pytestmark = pytest.mark.skipif(
    not os.environ.get("NOSCHEME_NATIVE_PROBE"), reason="native probe supplied by Meson"
)
PYTHON_TESTS = runpy.run_path(str(ROOT / "tests/test_resolve.py"))
CASES = next(
    m.args[1] for m in PYTHON_TESTS["test_definition_errors"].pytestmark if m.name == "parametrize"
)
PRELUDE = "from noscheme import role, use, net, num, std\n"
BOARD = "block B:\n  net.passive n\n"


def normalize(value):
    offsets = {}

    def walk(v):
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, dict):
            if {"filename", "start_pos", "end_pos"} <= v.keys():
                filename = v["filename"]
                if filename not in offsets:
                    text = Path(filename).read_text()
                    positions = None
                    if not text.isascii():
                        positions, total = {0: 0}, 0
                        for index, char in enumerate(text, 1):
                            total += len(char.encode())
                            positions[total] = index
                    offsets[filename] = positions
                positions = offsets[filename]
                return (
                    v
                    if positions is None
                    else v | {k: positions[v[k]] for k in ("start_pos", "end_pos")}
                )
            return {k: walk(x) for k, x in v.items()}
        return v

    return walk(value)


def compare(probe, path, **options):
    config = read_config(path)
    request = {
        "project": str(path),
        "resolve_interfaces": True,
        "registry_response": {"version": 1, "registry": describe_registry(config.erc_registry)},
        **options,
    }
    try:
        project = load_project(path)
        resolver = Resolver(
            project,
            max_pins=options.get("max_pins", 100000),
            max_expression_depth=options.get("max_expression_depth", 128),
        )
        definitions = (
            resolver.resolve_all() if options.get("resolve_all") else resolver.resolve_interfaces()
        )
    except DiagnosticError as error:
        actual = probe(request)
        assert "diagnostics" in actual, actual
        assert normalize(
            [{k: v for k, v in d.items() if k != "message"} for d in actual["diagnostics"]]
        ) == [{k: v for k, v in a.to_data(d).items() if k != "message"} for d in error.diagnostics]
        return actual
    expected = []
    for (module, name), definition in definitions.items():
        expected.append(
            {
                "module": module,
                "name": name,
                "kind": definition.kind,
                "parameters": {
                    name: {
                        "type": p.type,
                        "default": a.to_data(p.default),
                        "span": a.to_data(p.span),
                    }
                    for name, p in definition.parameters.items()
                },
                "interface": {
                    name: {
                        "name": p.name,
                        "type": a.to_data(p.type),
                        "metadata": metadata_data(p.metadata),
                        "span": a.to_data(p.span),
                    }
                    for name, p in definition.interface.items()
                },
                "aliases": definition.aliases,
                "metadata": metadata_data(definition.metadata),
                "body": a.to_data(definition.body),
                "body_module": definition.body_module,
                "electrical": definition.electrical,
            }
        )
    actual = probe(request)
    assert "definitions" in actual, actual
    assert normalize(actual["definitions"]) == expected
    return actual


def source_project(tmp_path, text):
    return make_project(tmp_path, {"src/main.nosch": PRELUDE + text + BOARD})


@pytest.mark.parametrize(
    "example", sorted((ROOT / "examples").glob("*/noscheme.toml")), ids=lambda p: p.parent.name
)
def test_examples(probe, example):
    compare(probe, example)


@pytest.mark.parametrize("text,unused_code", CASES)
def test_python_interface_cases(probe, tmp_path, text, unused_code):
    # Cases belonging to later expression/body checks intentionally succeed here.
    compare(probe, source_project(tmp_path, text))


@pytest.mark.parametrize(
    "pins,limit",
    [
        ("1..3: role.passive", 3),
        ("1..3: role.passive", 2),
        ("[1, 2, 3]: role.passive", 2),
        ("[1, 2, 1]: role.passive", 10),
        ("3..1: role.passive", 10),
        ("01..02: role.passive", 10),
        ("99999999999999999998..99999999999999999999: role.passive", 2),
        ("1..999999999999999999999: role.passive", 3),
        ("1: role.passive", 0),
    ],
)
def test_pin_ranges_and_limits(probe, tmp_path, pins, limit):
    compare(
        probe, source_project(tmp_path, "component C:\n  pins:\n    " + pins + "\n"), max_pins=limit
    )


@pytest.mark.parametrize(
    "override",
    [
        "pins:\n    3: role.passive",
        "pins:\n    A (new):",
        "pins:\n    A:\n      use: use.required\n    1:\n      use: use.optional",
        'pins:\n    1 ("2"):',
        'metadata:\n    custom:\n      note: "héllo"',
    ],
)
def test_component_specialization(probe, tmp_path, override):
    source = (
        """component Base:
  pins:
    1 (A): role.passive
    2: role.passive
extend Base Child:
  """
        + override
        + "\n"
    )
    compare(probe, source_project(tmp_path, source))


@pytest.mark.parametrize("port", ["net.digital a", "net.passive b", "a:\n      placeholder: true"])
def test_block_specialization(probe, tmp_path, port):
    source = (
        "block Base:\n  ports:\n    net.passive a\nextend Base Child:\n  ports:\n    " + port + "\n"
    )
    compare(probe, source_project(tmp_path, source))


def test_inherited_scope_and_body(probe, tmp_path):
    config = make_project(
        tmp_path,
        {
            "src/library.nosch": PRELUDE
            + """component R:
  params:
    resistance value = 10k
  pins:
    [1, 2]: role.passive
block Base:
  params:
    resistance value = 10k
  ports:
    net.passive a
  R<value: value> r():
    [1, 2]: a
""",
            "src/main.nosch": PRELUDE
            + """from library import Base
extend Base<value: 47k> Child:
  ports:
    a:
      placeholder: true
"""
            + BOARD,
        },
    )
    compare(probe, config)


@pytest.mark.parametrize(
    "role,individual,members",
    [
        ("power_input", "", "[vdd, 2]"),
        ("passive", "", "[vdd, 2]"),
        ("power_input", "max_load_current: 10m", "[vdd, 2]"),
        ("power_input", "", "[vdd, 1]"),
        ("power_input", "", "[missing]"),
    ],
)
def test_current_groups(probe, tmp_path, role, individual, members):
    source = f"""component C:
  pins:
    1 (vdd): role.{role}
      {individual}
    2: role.power_input
  metadata:
    current_loads:
      core:
        pins: {members}
        max_load_current: 20m
"""
    compare(probe, source_project(tmp_path, source))


@pytest.mark.parametrize(
    "second,group",
    [
        ("role: role.passive", "[1, 2]"),
        ("role: role.ground", "[1, 2]"),
        ("role: role.passive", "[a, 1]"),
        ("role: role.passive", "[1]"),
        ("role: role.passive", "[1, missing]"),
        ('role: role.passive\n      custom:\n        note: "different"', "[a, 2]"),
        ("role: role.passive\n      capacitance: 1.0", "[a, 2]"),
    ],
)
def test_interchangeable(probe, tmp_path, second, group):
    first = "      capacitance: 1\n" if "capacitance" in second else ""
    source = f"""component C:
  pins:
    1 (a): role.passive
{first}    2:
      {second}
  metadata:
    interchangeable: {group}
"""
    compare(probe, source_project(tmp_path, source))


def test_clear_inherited_group(probe, tmp_path):
    compare(
        probe,
        source_project(
            tmp_path,
            """component Base:
  pins:
    [1, 2]: role.passive
  metadata:
    interchangeable: [1, 2]
extend Base Child:
  pins:
    2: role.ground
  metadata:
    interchangeable: []
""",
        ),
    )


def test_nettype_inheritance(probe, tmp_path):
    compare(
        probe,
        source_project(
            tmp_path,
            """extend net.power Rail:
  custom:
    note: "rail"
extend Rail Small:
  custom:
    other: true
bundle Bus:
  Small power
  net.ground ground
""",
        ),
    )


@pytest.mark.parametrize("selection", ['"usb"', '"bad"'])
def test_board_configuration(probe, tmp_path, selection):
    source = (
        PRELUDE
        + f"""block B:
  metadata:
    erc_choices:
      supply: ["usb", "external"]
    configurations:
      powered:
        supply: {selection}
"""
    )
    compare(probe, make_project(tmp_path, {"src/main.nosch": source}))


def test_group_error_order(probe, tmp_path):
    compare(
        probe,
        source_project(
            tmp_path,
            """component C:
  pins:
    1: role.power_input
  metadata:
    current_loads:
      z_first:
        pins: [1]
        max_load_current: 10m
      a_second:
        pins: [1]
        max_load_current: 20m
""",
        ),
    )


def test_inherited_group_error_order(probe, tmp_path):
    compare(
        probe,
        source_project(
            tmp_path,
            """component Base:
  pins:
    [1, 2]: role.power_input
  metadata:
    current_loads:
      z_first:
        pins: [1]
        max_load_current: 10m
      a_second:
        pins: [2]
        max_load_current: 20m
extend Base Child:
  metadata:
    current_loads:
      z_first:
        pins: [2]
""",
        ),
    )


def test_diagnostic_precedence(probe, tmp_path):
    compare(
        probe,
        source_project(
            tmp_path,
            """component C:
  pins:
    2..1: role.passive
  metadata:
    value:
      nested: true
""",
        ),
    )


def test_deep_inheritance(probe, tmp_path):
    source = "component C0:\n  pins:\n    [1, 2]: role.passive\n"
    for i in range(1, 180):
        source += f'extend C{i - 1} C{i}:\n  metadata:\n    value: "{i}"\n'
    compare(probe, source_project(tmp_path, source))
