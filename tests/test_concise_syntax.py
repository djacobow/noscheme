"""Type-first declarations, `<...>` parameters, `extend`, instance bodies, `*`, grouped
nets, voltage ranges, inline aliases, flat(), and descending ranges.

SOURCES also drive the native parity tests.
"""

import pytest

from noscheme.diagnostics import DiagnosticError
from noscheme.formatter import format_source
from test_circuit import PART, compile_source

BLOCK = """block Unit:
  ports:
    net.power supply
    net.ground ground
    net.passive data[2]
  P p[2]:
    1: data
    2: supply
  P q:
    1: ground
    2: no_connect
"""
OCTAL = """component Octal:
  pins:
    1..10: role.passive
"""
BOARD = "board B:\n  net.passive n\n"


def wildcard(arguments):
    return PART + BLOCK + f"board B:\n  net.passive unrelated\n  Unit u{arguments}\n"


def voltage(value):
    return (
        PART
        + f"""block Unit:
  params:
    number supply = 3
  ports:
    net.power rail:
      unused: unused.always
  net.power n:
    voltage: {value}
  P p(1: n, 2: rail):
    metadata:
      pins:
        1:
          voltage: 0..supply * 2
board B:
  net.power rail
  Unit u(rail: rail)
"""
    )


def voltage_fields(fields):
    return PART + f"board B:\n  net.power n:\n{fields}  P p(1: n, 2: n)\n"


def flat(binding):
    return OCTAL + f"board B:\n  net.passive x[4][2]\n  Octal o:\n    {binding}\n"


ALIASES = PART + BOARD + "  P p(A: n, 2: no_connect)\n"
GROUPED = PART + "board B:\n  net.power a, b:\n    voltage: 4.75..5.25\n  P p(1: a, 2: b)\n"
BODY = PART + BOARD + "  P p:\n    1: n\n    2: no_connect\n"
VALUED = """component V:
  params:
    resistance value
    string label = "x"
  pins:
    1..2: role.passive
  metadata:
    interchangeable: [1, 2]
"""
PAIR = "board B:\n  net.passive a, b\n"
SOURCES = {
    "body": BODY,
    "body_after_section": BODY.replace(
        "    1: n\n", "    metadata:\n      custom:\n        x: 1\n    1: n\n"
    ),
    "body_quoted_section_word": PART.replace("1 (A):", '"params":')
    + 'board B:\n  P p:\n    "params": no_connect\n    2: no_connect\n',
    "wildcard": PART
    + BLOCK
    + """board B:
  net.power supply
  net.ground ground, other
  net.passive data[2]
  Unit u(*)
  Unit v(*, ground: other)
""",
    "wildcard_enclosing_ports": PART
    + """block Wrapper:
  ports:
    net.passive A
  P p(*, 2: no_connect)
board B:
  net.passive n
  Wrapper w(A: n)
  P q(1: n, 2: no_connect)
""",
    "wildcard_alias": PART + "board B:\n  net.passive A, n1\n  P p(*, 2: no_connect)\n",
    "wildcard_two_names": PART.replace("1 (A):", "n1 (A):")
    + "board B:\n  net.passive A, n1\n  P p(*, 2: no_connect)\n",
    "wildcard_unmatched": wildcard("(*)"),
    "wildcard_twice": wildcard("(*, *)"),
    "wildcard_slash": wildcard("(/)"),
    "grouped": GROUPED,
    "grouped_duplicate": GROUPED.replace("net.power a, b:", "net.power a, a:"),
    "voltage_percent": voltage("5 ± 5%"),
    "voltage_ascii": voltage("5 +/- 5%"),
    "voltage_absolute": voltage("3.3 ± 0.1"),
    "voltage_range": voltage("-1..supply"),
    "voltage_plain": voltage_fields("    voltage: 5\n"),
    "voltage_both_forms": voltage_fields("    voltage: 4..6\n    min_voltage: 4\n"),
    "range_elsewhere": voltage_fields("    wiring_capacitance: 1..2\n"),
    "aliases": ALIASES,
    "aliases_on_net": ALIASES.replace("  P p(", "  net.passive m (x)\n  P p("),
    "aliases_in_metadata": ALIASES[:-1]
    + ":\n    metadata:\n      pins:\n        1 (x):\n          role: role.passive\n",
    "flat": OCTAL
    + """board B:
  net.passive x[4][2]
  net.passive y[2]
  Octal o:
    [4, 3, 5..10]: flat(x)
  Octal p:
    10..9: y
    1..8: flat(x)
""",
    "flat_unflattened": flat("1..8: x"),
    "flat_scalar": flat("1: flat(x[0][0])"),
    "flat_no_connect": flat("1: flat(no_connect)"),
    "flat_other_call": flat("1..8: wide(x)"),
    "choice_shorthand": PART
    + """board B:
  metadata:
    erc_choices:
      mode: ["on", "off"]
  net.passive n
  P p(1: n, 2: no_connect):
    metadata:
      pins:
        1: choice(mode, on=role.passive, off=role.passive)
""",
    "choice_malformed": PART
    + BOARD
    + "  P p(1: n, 2: no_connect):\n    metadata:\n      pins:\n        1: choice(on=role.passive)\n",
    "unnamed_parameter": VALUED
    + """extend V<label: "pull"> Pull
extend V<2.2k> Fixed
board B:
  net.passive a, b
  V<4.7k> r(a, b)
  Pull<1k> pull(a, b)
  Fixed fixed(a, b)
  Fixed<10k> override(a, b)
""",
    "unnamed_parameter_ambiguous": VALUED.replace('string label = "x"', "string label")
    + PAIR
    + "  V<4.7k> r(a, b)\n",
    "unnamed_parameter_twice": VALUED + PAIR + "  V<4.7k, value: 1k> r(a, b)\n",
    "unnamed_parameter_none": PART + BOARD + "  P<1> p(1: n, 2: no_connect)\n",
    "unnamed_parameter_late": VALUED + PAIR + '  V<label: "y", 4.7k> r(a, b)\n',
    "net_parameters": PART + "board B:\n  net.passive<1> n\n",
    "net_connections": PART + "board B:\n  net.passive n(x)\n",
    "net_section": PART + "board B:\n  net.passive n:\n    metadata:\n      x: 1\n",
    "global_instance": PART + "board B:\n  global P p\n",
    "instance_names": PART + "board B:\n  P p, q\n",
    "instance_field": PART + BOARD + "  P p:\n    voltage: 5\n",
    "unknown_type": PART + "board B:\n  Missing m:\n    voltage: 5\n",
    "net_type_extension": """extend net.power Rail:
  voltage: 4.75..5.25
extend Rail Rail3:
  voltage: 3.3 ± 5%
bundle Pair:
  Rail a
  Rail3 b[2]
board B:
  Pair pair
  global Rail3 r
""",
    "extend_bundle": "bundle Pair:\n  net.passive a\nextend Pair Other\n" + BOARD,
    "extend_section": PART + "extend P Q:\n  ports:\n    net.passive x\n" + BOARD,
    "extend_cycle": "extend B2 A2\nextend A2 B2\n" + BOARD,
    "declaration_form": "block U:\n  ports:\n    supply: net.power\n" + BOARD,
    "typed_metadata": "component C:\n  metadata:\n    net.power x\n" + BOARD,
}


def nets(circuit):
    return {
        n["name"]: sorted((".".join(map(str, e["instance"])), e["pin"]) for e in n["endpoints"])
        for n in circuit["nets"]
    }


def error_code(tmp_path, name):
    with pytest.raises(DiagnosticError) as error:
        compile_source(tmp_path, SOURCES[name])
    return error.value.diagnostics[0].code


@pytest.mark.parametrize(
    "name,code",
    [
        ("body_after_section", "syntax.invalid"),
        ("wildcard_unmatched", "resolve.wildcard"),
        ("wildcard_twice", "syntax.wildcard"),
        ("wildcard_slash", "syntax.wildcard"),
        ("wildcard_two_names", "resolve.wildcard"),
        ("grouped_duplicate", "syntax.duplicate"),
        ("voltage_plain", "metadata.voltage"),
        ("voltage_both_forms", "metadata.voltage"),
        ("range_elsewhere", "syntax.range-context"),
        ("aliases_on_net", "syntax.net-declaration"),
        ("aliases_in_metadata", "syntax.alias-context"),
        ("flat_unflattened", "connection.shape"),
        ("flat_scalar", "connection.flat"),
        ("flat_no_connect", "resolve.flat"),
        ("flat_other_call", "syntax.target"),
        ("choice_malformed", "metadata.erc"),
        ("unnamed_parameter_ambiguous", "resolve.parameter"),
        ("unnamed_parameter_twice", "resolve.parameter"),
        ("unnamed_parameter_none", "resolve.parameter"),
        ("unnamed_parameter_late", "syntax.parameter-order"),
        ("net_parameters", "syntax.net-declaration"),
        ("net_connections", "syntax.net-declaration"),
        ("net_section", "syntax.net-declaration"),
        ("global_instance", "syntax.instance-declaration"),
        ("instance_names", "syntax.instance-declaration"),
        ("instance_field", "syntax.target"),
        ("unknown_type", "resolve.missing-definition"),
        ("extend_bundle", "syntax.inheritance-context"),
        ("extend_section", "syntax.section-context"),
        ("extend_cycle", "resolve.inheritance-cycle"),
        ("declaration_form", "syntax.declaration-form"),
        ("typed_metadata", "syntax.declaration-form"),
    ],
)
def test_errors(tmp_path, name, code):
    assert error_code(tmp_path, name) == code


def test_unnamed_parameter_sets_the_one_without_a_default(tmp_path):
    parts = compile_source(tmp_path, SOURCES["unnamed_parameter"])["parts"]
    values = {p["identity"]: p["value"]["value"] for p in parts}
    assert values == {
        "r": "4.7kOhm",
        "pull": "1kOhm",
        "fixed": "2.2kOhm",
        "override": "10kOhm",
    }
    labels = {p["identity"]: p["parameters"]["label"]["value"] for p in parts}
    assert labels["pull"] == "pull" and labels["r"] == "x"


def test_net_types_extend_builtin_values_and_each_other(tmp_path):
    nets_ = compile_source(tmp_path, SOURCES["net_type_extension"])["nets"]
    declarations = {n["name"]: n["declarations"][-1] for n in nets_}
    assert declarations["pair.a"]["nettype"] == "main.Rail"
    assert declarations["pair.b[1]"]["nettype"] == "main.Rail3"
    assert declarations["r"]["type"] == "power"


def test_bindings_lead_the_instance_body(tmp_path):
    assert nets(compile_source(tmp_path, SOURCES["body"]))["n"] == [("p", "1")]
    # Section words start sections; a pin with such a name is quoted.
    compile_source(tmp_path, SOURCES["body_quoted_section_word"])


def test_wildcard_binds_same_name_nets_and_ports(tmp_path):
    result = nets(compile_source(tmp_path, SOURCES["wildcard"]))
    assert ("u.q", "1") in result["ground"] and ("v.q", "1") in result["other"]
    assert ("v.p.1", "2") in result["supply"]
    assert result["data[0]"] == [("u.p.0", "1"), ("v.p.0", "1")]


def test_wildcard_uses_aliases_and_ports_of_the_enclosing_block(tmp_path):
    result = nets(compile_source(tmp_path, SOURCES["wildcard_enclosing_ports"]))
    assert result["n"] == [("q", "1"), ("w.p", "1")]
    assert nets(compile_source(tmp_path, SOURCES["wildcard_alias"]))["A"] == [("p", "1")]


def test_grouped_net_declaration_shares_type_and_fields(tmp_path):
    nets_ = compile_source(tmp_path, SOURCES["grouped"])["nets"]
    declarations = {n["name"]: n["declarations"][0] for n in nets_}
    for name in ("a", "b"):
        metadata = declarations[name]["metadata"]
        assert declarations[name]["type"] == "power"
        assert (metadata["min_voltage"]["value"], metadata["max_voltage"]["value"]) == (4.75, 5.25)


@pytest.mark.parametrize(
    "name,bounds",
    [
        ("voltage_percent", (4.75, 5.25)),
        ("voltage_ascii", (4.75, 5.25)),
        ("voltage_absolute", (3.1999999999999997, 3.4)),
        ("voltage_range", (-1, 3)),
    ],
)
def test_voltage_ranges_and_tolerances(tmp_path, name, bounds):
    circuit = compile_source(tmp_path, SOURCES[name])
    net = next(n for n in circuit["nets"] if n["name"] == "u.n")
    metadata = net["declarations"][0]["metadata"]
    assert (metadata["min_voltage"]["value"], metadata["max_voltage"]["value"]) == bounds
    pin = circuit["parts"][0]["pins"]["1"]["metadata"]
    assert (pin["min_voltage"]["value"], pin["max_voltage"]["value"]) == (0, 6)


def test_inline_aliases_name_pins(tmp_path):
    assert nets(compile_source(tmp_path, SOURCES["aliases"]))["n"] == [("p", "1")]


def test_flat_binds_array_elements_in_row_major_order(tmp_path):
    result = nets(compile_source(tmp_path, SOURCES["flat"]))
    assert result["x[0][0]"] == [("o", "4"), ("p", "1")]
    assert result["x[0][1]"] == [("o", "3"), ("p", "2")]
    assert result["x[3][1]"] == [("o", "10"), ("p", "8")]
    assert result["y[0]"] == [("p", "10")] and result["y[1]"] == [("p", "9")]


def test_choice_shorthand_selects_a_role(tmp_path):
    pin = compile_source(tmp_path, SOURCES["choice_shorthand"])["parts"][0]["pins"]["1"]
    # P declares no role, so the instance role describes attached equipment.
    selection = pin["metadata"]["_erc_selections"]["value"]["external_role"]["value"]
    assert selection["choice"]["value"] == "mode"
    assert set(selection["cases"]["value"]) == {"on", "off"}


def test_new_forms_format_canonically():
    source = """component C:
  pins:
    1 (a,b): role.passive
board B:
  net.power v, w:
    voltage: 5±5 %
    custom:
      range: 1
  C c(*, flat(v)):
    1..2: flat( v )
"""
    assert (
        format_source(source)
        == """component C:
  pins:
    1 (a, b): role.passive

board B:
  net.power v, w:
    voltage: 5 ± 5%
    custom:
      range: 1
  C c(*, flat(v)):
    1..2: flat(v)
"""
    )
