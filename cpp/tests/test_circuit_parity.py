"""Compare expanded circuits, including connectivity, provenance, and metadata."""

import os

import pytest

from noscheme import ast as a
from noscheme.circuit import elaborate_project, ExpansionLimits
from noscheme.diagnostics import DiagnosticError
from noscheme.evaluate import Limits
from noscheme.native_bridge import describe_registry
from noscheme.project import load_project, read_config
from noscheme.resolve import Resolver

from test_resolve_parity import normalize, PRELUDE
from test_project_parity import ROOT, make_project
import test_project_parity

probe = test_project_parity.probe
pytestmark = pytest.mark.skipif(
    not os.environ.get("NOSCHEME_NATIVE_PROBE"), reason="native probe supplied by Meson"
)


def decode_metadata(value):
    if isinstance(value, list):
        return [decode_metadata(v) for v in value]
    if isinstance(value, dict):
        if value.get("type") == "number":
            return int(value["value"]) if value["tag"] == "integer" else value["value"]
        return {k: decode_metadata(v) for k, v in value.items()}
    return value


def circuit_data(value):
    if isinstance(value, list):
        return [circuit_data(v) for v in value]
    if isinstance(value, dict):
        return {
            k: decode_metadata(v) if k == "metadata" else circuit_data(v) for k, v in value.items()
        }
    return value


def compare(probe, path, **options):
    request = {
        "project": str(path),
        "elaborate": True,
        "registry_response": {
            "version": 1,
            "registry": describe_registry(read_config(path).erc_registry),
        },
        **options,
    }
    try:
        project = load_project(path)
        definitions = Resolver(project).resolve_all()
        expected, _ = elaborate_project(
            project,
            definitions,
            limits=ExpansionLimits(**options.get("expansion_limits", {})),
            expression_limits=Limits(**options.get("expression_limits", {})),
        )
    except DiagnosticError as error:
        actual = probe(request)
        assert "diagnostics" in actual, actual
        assert normalize(
            [{k: v for k, v in d.items() if k != "message"} for d in actual["diagnostics"]]
        ) == [{k: v for k, v in a.to_data(d).items() if k != "message"} for d in error.diagnostics]
        return actual
    actual = probe(request)
    assert "circuit" in actual, actual
    assert circuit_data(normalize(actual["circuit"])) == expected
    return actual


def check_source(probe, tmp_path, source, **options):
    return compare(
        probe,
        make_project(tmp_path, {"src/main.nosch": PRELUDE.replace(", std", "") + source}),
        **options,
    )


@pytest.mark.parametrize(
    "example", sorted((ROOT / "examples").glob("*/noscheme.toml")), ids=lambda p: p.parent.name
)
def test_examples(probe, example):
    result = compare(probe, example)
    assert "circuit" in result, result


PART = """component P:
  pins:
    1..2: role.passive
  metadata:
    footprint: "Test:Part"
"""


def test_simple(probe, tmp_path):
    check_source(
        probe,
        tmp_path,
        PART
        + """board B:
  net n: nettype.passive
  inst p: P(1: n, 2: no_connect)
""",
    )


@pytest.mark.parametrize(
    "net_shape,inst_shape,binding",
    [
        ("[2][3]", "[2][3]", "1: n"),
        ("", "[2][3]", "1: n"),
        ("[4]", "", "1..2: n[1:3]"),
        ("[4]", "[2][2]", "1: n"),
        ("[3]", "[2][3]", "1: n"),
        ("[1]", "", "1: n"),
        ("[2]", "", "1: n[2]"),
        ("[2]", "", "1: n[-1]"),
        ("[2]", "", "1: n[1:1]"),
        ("", "", "1: n[0]"),
        ("[2]", "", "1: n[1.0]"),
        ("[2.0]", "[2.0]", "1: n"),
        ("[0]", "", "1: n"),
    ],
)
def test_shapes(probe, tmp_path, net_shape, inst_shape, binding):
    check_source(
        probe,
        tmp_path,
        PART
        + f"""board B:
  net n: nettype.passive{net_shape}
  inst p{inst_shape}: P
    {binding}
""",
    )


@pytest.mark.parametrize(
    "bindings",
    [
        "1: n\n    A: n",
        "1..2: n\n    2: n",
        "1: n\n    [0].1: n",
        "1: no_connect\n    A: n",
        "[0].1: n\n    [1].1: no_connect",
        "1..2: no_connect",
    ],
)
def test_overlapping_bindings(probe, tmp_path, bindings):
    part = PART.replace("1..2: role.passive", "1 (A): role.passive\n    2: role.passive")
    check_source(
        probe,
        tmp_path,
        part + "board B:\n  net n: nettype.passive\n  inst p[2]: P\n    " + bindings + "\n",
    )


BUNDLES = """bundle Bus:
  x: nettype.digital
  y: nettype.digital
bundle Other:
  x: nettype.digital
  y: nettype.digital
block W:
  ports:
    bus: Bus
      placeholder: true
"""


@pytest.mark.parametrize(
    "net_type,inst_shape,binding",
    [
        ("Bus", "", "bus: n"),
        ("Bus", "[2]", "bus: n"),
        ("Bus[2]", "[2]", "bus: n"),
        ("Other", "", "bus: n"),
        ("Other", "", "bus.x: n.y\n    bus.y: n.x"),
        ("Bus", "", "bus: n\n    bus.x: n.x"),
        ("nettype.passive", "", "bus: n"),
        ("Bus", "", "bus: no_connect"),
    ],
)
def test_bundles(probe, tmp_path, net_type, inst_shape, binding):
    check_source(
        probe,
        tmp_path,
        BUNDLES
        + f"""board B:
  net n: {net_type}
  inst w{inst_shape}: W
    {binding}
""",
    )


@pytest.mark.parametrize(
    "support", ["", "      placeholder: true\n", "      external_role: role.digital_input\n"]
)
def test_port_support(probe, tmp_path, support):
    check_source(
        probe,
        tmp_path,
        """block W:
  ports:
    p: nettype.passive
"""
        + support
        + """board B:
  net n: nettype.passive
  inst w: W(p: n)
""",
    )


@pytest.mark.parametrize("use,declare", [(False, False), (True, False), (True, True)])
def test_globals(probe, tmp_path, use, declare):
    source = (
        PART
        + """block W:
  inst p: P(1: global.gnd)
board B:
"""
    )
    source += "  global net gnd: nettype.ground\n" if declare else "  net n: nettype.passive\n"
    if use:
        source += "  inst w: W\n"
    check_source(probe, tmp_path, source)


@pytest.mark.parametrize(
    "limits",
    [{}, {"instances": 2}, {"leaves": 2}, {"leaves": 1}, {"hierarchy_depth": 1}, {"instances": 0}],
)
def test_limits(probe, tmp_path, limits):
    check_source(
        probe,
        tmp_path,
        PART
        + """board B:
  inst p[3]: P
    metadata:
      identity: "stable"
""",
        expansion_limits=limits,
    )


@pytest.mark.parametrize(
    "body",
    [
        'inst p: P\n    metadata:\n      identity: ""',
        'inst p[2]: P\n    metadata:\n      ref: "R1"',
        'inst p: P\n    metadata:\n      ref: ""',
        'inst p: P\n    metadata:\n      identity: "q"\n  inst q: P',
        "inst p: P(1: no_connect)",
        "inst p: P",
    ],
)
def test_identity_and_omissions(probe, tmp_path, body):
    check_source(probe, tmp_path, PART + "board B:\n  " + body + "\n")


@pytest.mark.parametrize("unused", [False, True])
def test_instance_cycles(probe, tmp_path, unused):
    source = "block W:\n  inst w: W\nboard B:\n"
    source += "  net n: nettype.passive\n" if unused else "  inst w: W\n"
    check_source(probe, tmp_path, source)


@pytest.mark.parametrize("selector", ["[1]", "[0][1]", "[2]"])
def test_element_overrides(probe, tmp_path, selector):
    from test_body_parity import fixture_source

    check_source(
        probe, tmp_path, fixture_source("test_element_overrides.py").replace("[1]:", selector + ":")
    )


def test_port_union(probe, tmp_path):
    check_source(
        probe,
        tmp_path,
        PART
        + """block W:
  ports:
    a: nettype.passive
    b: nettype.passive
  inst p: P(1: a, 2: b)
board B:
  net n: nettype.passive
  inst w: W(a: n, b: n)
""",
    )


def test_inherited_identity(probe, tmp_path):
    check_source(
        probe,
        tmp_path,
        PART
        + """block W:
  inst p[2]: P
board B:
  inst w[2]: W
    metadata:
      identity: "stable"
""",
    )


@pytest.mark.parametrize("quantity", ["resistance", "0", "-1", '"bad"', "1e308 * 1e308"])
def test_numeric_metadata(probe, tmp_path, quantity):
    from test_body_parity import fixture_source

    source = fixture_source("test_numeric_metadata.py").replace(
        "pull_resistance: resistance", f"pull_resistance: ({quantity})"
    )
    check_source(probe, tmp_path, source)


def test_quantity_override_does_not_evaluate_shadowed_expression(probe, tmp_path):
    from test_body_parity import fixture_source

    source = (
        fixture_source("test_numeric_metadata.py")
        .replace("pull_resistance: resistance", "pull_resistance: resistance / 0")
        .replace(
            "      resistance: 3.3k\n",
            """      resistance: 3.3k
    metadata:
      pins:
        1:
          pull_resistance: 2200
""",
        )
    )
    assert "circuit" in check_source(probe, tmp_path, source)


def test_quantity_override_uses_caller(probe, tmp_path):
    from test_body_parity import fixture_source

    source = (
        fixture_source("test_numeric_metadata.py")
        .replace("board B:\n", "block Circuit:\n  params:\n    resistance: resistance = 2200\n")
        .replace(
            "      resistance: 3.3k\n",
            """      resistance: 3.3k
    metadata:
      value: resistance
      pins:
        1:
          pull_resistance: resistance
""",
        )
        + "board B:\n  inst circuit: Circuit\n"
    )
    assert "circuit" in check_source(probe, tmp_path, source)


def test_inherited_quantity_error_order(probe, tmp_path):
    check_source(
        probe,
        tmp_path,
        """component Base:
  pins:
    1:
      max_source_current: (0 - 1)
      max_load_current: (0 - 2)
component Child extends Base:
  pins:
    1:
      max_source_current: (0 - 3)
board B:
  inst c: Child
""",
    )


@pytest.mark.parametrize(
    "override",
    [
        "",
        "    metadata:\n      pins:\n        p:\n          aggregate_load:\n            max_load_current: current\n",
    ],
)
def test_nested_quantity_metadata(probe, tmp_path, override):
    check_source(
        probe,
        tmp_path,
        """block W:
  params:
    current: current = 12m
  ports:
    p: nettype.power
      placeholder: true
      aggregate_load:
        max_load_current: current * 2
block Caller:
  params:
    current: current = 10m
  inst w: W
"""
        + override
        + """board B:
  inst caller: Caller
""",
    )


@pytest.mark.parametrize(
    "kind,limit",
    [("integer_bits", 8), ("dimension", 2), ("expression_depth", 0), ("string_length", 2)],
)
def test_expression_limits(probe, tmp_path, kind, limit):
    check_source(
        probe,
        tmp_path,
        """component C:
  params:
    value: number = 1000 + 1
    text: string = "abcdef"
board B:
  inst c[3]: C
""",
        expression_limits={kind: limit},
    )


def test_type_depth_limit(probe, tmp_path):
    check_source(
        probe,
        tmp_path,
        """bundle A:
  n: nettype.passive
bundle B:
  a: A
board B:
  net n: B
""".replace("bundle B:", "bundle Bus:").replace("net n: B", "net n: Bus"),
        expansion_limits={"type_depth": 0},
    )


def test_port_index_within_instance(probe, tmp_path):
    check_source(
        probe,
        tmp_path,
        """block W:
  ports:
    data: nettype.digital[3]
      placeholder: true
board B:
  net n: nettype.digital[2]
  inst w[2]: W
    data[1]: n
""",
    )


def test_no_connect_block_port(probe, tmp_path):
    check_source(
        probe,
        tmp_path,
        PART
        + """block W:
  ports:
    p: nettype.passive
  inst p: P(1: p)
board B:
  inst w: W(p: no_connect)
""".replace("inst p: P", "inst part: P"),
    )


def test_caller_does_not_implement_empty_port(probe, tmp_path):
    check_source(
        probe,
        tmp_path,
        """block W:
  ports:
    a: nettype.passive
      placeholder: true
    b: nettype.passive
board B:
  net n: nettype.passive
  inst w: W(a: n, b: n)
""",
    )


def test_inherited_lexical_scope(probe, tmp_path):
    path = make_project(
        tmp_path,
        {
            "src/main.nosch": PRELUDE
            + """from library import Base
block Derived extends Base:
  defaults:
    value: 20k
board B:
  net n: nettype.passive
  inst d: Derived(p: n)
""",
            "src/library.nosch": PRELUDE
            + """component R:
  params:
    value: resistance
  pins:
    1..2: role.passive
  metadata:
    interchangeable: [1, 2]
block Base:
  params:
    value: resistance = 10k
  ports:
    p: nettype.passive
  inst r: R(p, no_connect, value=value)
""",
        },
    )
    assert "circuit" in compare(probe, path)


def test_repeat_and_edit_in_one_process(probe, tmp_path):
    path = make_project(
        tmp_path,
        {
            "src/main.nosch": PRELUDE
            + PART
            + """board B:
  net n: nettype.passive
  inst p: P(1: n, 2: no_connect)
"""
        },
    )
    first = compare(probe, path)["circuit"]
    assert compare(probe, path)["circuit"] == first
    source = tmp_path / "src/main.nosch"
    original = source.read_text()
    source.write_text(original.replace("1: n", "1: no_connect"))
    assert compare(probe, path)["circuit"] != first
    source.write_text(original)
    assert compare(probe, path)["circuit"] == first
    assert not (tmp_path / "build").exists()


@pytest.mark.parametrize(
    "default,args",
    [
        ("", ""),
        ("", "(value=100n)"),
        (" = 100n", ""),
        (" = 100n", '(value="string")'),
        (" = 100n", "(value=1 / 0)"),
    ],
)
def test_parameter_binding(probe, tmp_path, default, args):
    check_source(
        probe,
        tmp_path,
        f"""component C:
  params:
    value: capacitance{default}
  pins:
    1: role.passive
board B:
  inst c: C{args}
""",
    )


def test_named_net_type_metadata(probe, tmp_path):
    check_source(
        probe,
        tmp_path,
        """from noscheme import std
nettype Bus extends nettype.open_drain:
  checks:
    capacitance: std.sum(capacitance, max=400p)
nettype Child extends Bus:
  custom:
    label: "derived"
board B:
  net n: Child[2]
    custom:
      extra: "local"
""",
    )


def test_lossless_custom_metadata(probe, tmp_path):
    assert "circuit" in check_source(
        probe,
        tmp_path,
        """component C:
  metadata:
    custom:
      count: 123456789012345678901234567890
      node: "Number"
      type: "number"
      constraint: "positive"
      label: "héllo"
board B:
  inst c: C
""",
    )
