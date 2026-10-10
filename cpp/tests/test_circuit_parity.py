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
  net.passive n
  P p(1: n, 2: no_connect)
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
  net.passive{net_shape} n
  P p{inst_shape}:
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
        part + "board B:\n  net.passive n\n  P p[2]:\n    " + bindings + "\n",
    )


BUNDLES = """bundle Bus:
  net.digital x
  net.digital y
bundle Other:
  net.digital x
  net.digital y
block W:
  ports:
    Bus bus:
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
        ("net.passive", "", "bus: n"),
        ("Bus", "", "bus: no_connect"),
    ],
)
def test_bundles(probe, tmp_path, net_type, inst_shape, binding):
    check_source(
        probe,
        tmp_path,
        BUNDLES
        + f"""board B:
  {net_type} n
  W w{inst_shape}:
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
    net.passive p"""
        + (":\n" if support else "\n")
        + support
        + """board B:
  net.passive n
  W w(p: n)
""",
    )


@pytest.mark.parametrize("use,declare", [(False, False), (True, False), (True, True)])
def test_globals(probe, tmp_path, use, declare):
    source = (
        PART
        + """block W:
  P p(1: global.gnd)
board B:
"""
    )
    source += "  global net.ground gnd\n" if declare else "  net.passive n\n"
    if use:
        source += "  W w\n"
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
  P p[3]:
    metadata:
      identity: "stable"
""",
        expansion_limits=limits,
    )


@pytest.mark.parametrize(
    "body",
    [
        'P p:\n    metadata:\n      identity: ""',
        'P p[2]:\n    metadata:\n      ref: "R1"',
        'P p:\n    metadata:\n      ref: ""',
        'P p:\n    metadata:\n      identity: "q"\n  P q',
        "P p(1: no_connect)",
        "P p",
    ],
)
def test_identity_and_omissions(probe, tmp_path, body):
    check_source(probe, tmp_path, PART + "board B:\n  " + body + "\n")


@pytest.mark.parametrize("unused", [False, True])
def test_instance_cycles(probe, tmp_path, unused):
    source = "block W:\n  W w\nboard B:\n"
    source += "  net.passive n\n" if unused else "  W w\n"
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
    net.passive a
    net.passive b
  P p(1: a, 2: b)
board B:
  net.passive n
  W w(a: n, b: n)
""",
    )


def test_inherited_identity(probe, tmp_path):
    check_source(
        probe,
        tmp_path,
        PART
        + """block W:
  P p[2]
board B:
  W w[2]:
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
            "    1: bus\n  Device",
            """    1: bus
    metadata:
      pins:
        1:
          pull_resistance: 2200
  Device""",
        )
    )
    assert "circuit" in check_source(probe, tmp_path, source)


def test_quantity_override_uses_caller(probe, tmp_path):
    from test_body_parity import fixture_source

    source = (
        fixture_source("test_numeric_metadata.py")
        .replace("board B:\n", "block Circuit:\n  params:\n    resistance resistance = 2200\n")
        .replace(
            "    1: bus\n  Device",
            """    1: bus
    metadata:
      value: resistance
      pins:
        1:
          pull_resistance: resistance
  Device""",
        )
        + "board B:\n  Circuit circuit\n"
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
extend Base Child:
  pins:
    1:
      max_source_current: (0 - 3)
board B:
  Child c
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
    current current = 12m
  ports:
    net.power p:
      placeholder: true
      aggregate_load:
        max_load_current: current * 2
block Caller:
  params:
    current current = 10m
  W w"""
        + (":\n" if override else "\n")
        + override
        + """board B:
  Caller caller
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
    number value = 1000 + 1
    string text = "abcdef"
board B:
  C c[3]
""",
        expression_limits={kind: limit},
    )


def test_type_depth_limit(probe, tmp_path):
    check_source(
        probe,
        tmp_path,
        """bundle A:
  net.passive n
bundle B:
  A a
board B:
  B n
""".replace("bundle B:", "bundle Bus:").replace("B n", "Bus n"),
        expansion_limits={"type_depth": 0},
    )


def test_port_index_within_instance(probe, tmp_path):
    check_source(
        probe,
        tmp_path,
        """block W:
  ports:
    net.digital data[3]:
      placeholder: true
board B:
  net.digital n[2]
  W w[2]:
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
    net.passive p
  P p(1: p)
board B:
  W w(p: no_connect)
""".replace("P p", "P part"),
    )


def test_caller_does_not_implement_empty_port(probe, tmp_path):
    check_source(
        probe,
        tmp_path,
        """block W:
  ports:
    net.passive a:
      placeholder: true
    net.passive b
board B:
  net.passive n
  W w(a: n, b: n)
""",
    )


def test_inherited_lexical_scope(probe, tmp_path):
    path = make_project(
        tmp_path,
        {
            "src/main.nosch": PRELUDE
            + """from library import Base
extend Base<value: 20k> Derived
board B:
  net.passive n
  Derived d(p: n)
""",
            "src/library.nosch": PRELUDE
            + """component R:
  params:
    resistance value
  pins:
    1..2: role.passive
  metadata:
    interchangeable: [1, 2]
block Base:
  params:
    resistance value = 10k
  ports:
    net.passive p
  R<value: value> r(p, no_connect)
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
  net.passive n
  P p(1: n, 2: no_connect)
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
    capacitance{default} value
  pins:
    1: role.passive
board B:
  C{args} c
""",
    )


def test_named_net_type_metadata(probe, tmp_path):
    check_source(
        probe,
        tmp_path,
        """from noscheme import std
extend net.open_drain Bus:
  checks:
    capacitance: std.sum(capacitance, max=400p)
extend Bus Child:
  custom:
    label: "derived"
board B:
  Child n[2]:
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
      count: 9223372036854775807
      node: "Number"
      type: "number"
      constraint: "positive"
      label: "héllo"
board B:
  C c
""",
    )
