"""Instance bodies, `*`, grouped nets, voltage ranges, inline aliases, flat(), and descending ranges.

SOURCES also drive the native parity tests.
"""

import pytest

from noscheme.diagnostics import DiagnosticError
from noscheme.formatter import format_source
from test_circuit import PART, compile_source

BLOCK = """block Unit:
  ports:
    supply: nettype.power
    ground: nettype.ground
    data: nettype.passive[2]
  inst p[2]: P
    1: data
    2: supply
  inst q: P
    1: ground
    2: no_connect
"""
OCTAL = """component Octal:
  pins:
    1..10: role.passive
"""
BOARD = "board B:\n  net n: nettype.passive\n"


def wildcard(instance):
    return PART + BLOCK + f"board B:\n  net unrelated: nettype.passive\n  inst u: {instance}\n"


def voltage(value):
    return (
        PART
        + f"""block Unit:
  params:
    supply: number = 3
  ports:
    rail: nettype.power
      unused: unused.always
  net n: nettype.power
    voltage: {value}
  inst p: P(1: n, 2: rail)
    metadata:
      pins:
        1:
          voltage: 0..supply * 2
board B:
  net rail: nettype.power
  inst u: Unit(rail: rail)
"""
    )


def voltage_fields(fields):
    return PART + f"board B:\n  net n: nettype.power\n{fields}  inst p: P(1: n, 2: n)\n"


def flat(binding):
    return OCTAL + f"board B:\n  net x: nettype.passive[4][2]\n  inst o: Octal\n    {binding}\n"


ALIASES = PART + BOARD + "  inst p: P(A: n, 2: no_connect)\n"
GROUPED = (
    PART + "board B:\n  net a, b: nettype.power\n    voltage: 4.75..5.25\n  inst p: P(1: a, 2: b)\n"
)
BODY = PART + BOARD + "  inst p: P\n    1: n\n    2: no_connect\n"
SOURCES = {
    "body": BODY,
    "body_after_section": BODY.replace(
        "    1: n\n", "    metadata:\n      custom:\n        x: 1\n    1: n\n"
    ),
    "body_quoted_section_word": PART.replace("1 (A):", '"params":')
    + 'board B:\n  inst p: P\n    "params": no_connect\n    2: no_connect\n',
    "wildcard": PART
    + BLOCK
    + """board B:
  net supply: nettype.power
  net ground, other: nettype.ground
  net data: nettype.passive[2]
  inst u: Unit(*)
  inst v: Unit(*, ground: other)
""",
    "wildcard_enclosing_ports": PART
    + """block Wrapper:
  ports:
    A: nettype.passive
  inst p: P(*, 2: no_connect)
board B:
  net n: nettype.passive
  inst w: Wrapper(A: n)
  inst q: P(1: n, 2: no_connect)
""",
    "wildcard_alias": PART
    + "board B:\n  net A, n1: nettype.passive\n  inst p: P(*, 2: no_connect)\n",
    "wildcard_two_names": PART.replace("1 (A):", "n1 (A):")
    + "board B:\n  net A, n1: nettype.passive\n  inst p: P(*, 2: no_connect)\n",
    "wildcard_unmatched": wildcard("Unit(*)"),
    "wildcard_twice": wildcard("Unit(*, *)"),
    "wildcard_slash": wildcard("Unit(/)"),
    "grouped": GROUPED,
    "grouped_duplicate": GROUPED.replace("net a, b:", "net a, a:"),
    "voltage_percent": voltage("5 ± 5%"),
    "voltage_ascii": voltage("5 +/- 5%"),
    "voltage_absolute": voltage("3.3 ± 0.1"),
    "voltage_range": voltage("-1..supply"),
    "voltage_plain": voltage_fields("    voltage: 5\n"),
    "voltage_both_forms": voltage_fields("    voltage: 4..6\n    min_voltage: 4\n"),
    "range_elsewhere": voltage_fields("    wiring_capacitance: 1..2\n"),
    "aliases": ALIASES,
    "aliases_on_net": ALIASES.replace("  inst p:", "  net m (x): nettype.passive\n  inst p:"),
    "aliases_in_metadata": ALIASES
    + "    metadata:\n      pins:\n        1 (x):\n          role: role.passive\n",
    "flat": OCTAL
    + """board B:
  net x: nettype.passive[4][2]
  net y: nettype.passive[2]
  inst o: Octal
    [4, 3, 5..10]: flat(x)
  inst p: Octal
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
  net n: nettype.passive
  inst p: P(1: n, 2: no_connect)
    metadata:
      pins:
        1: choice(mode, on=role.passive, off=role.passive)
""",
    "choice_malformed": PART
    + BOARD
    + "  inst p: P(1: n, 2: no_connect)\n    metadata:\n      pins:\n        1: choice(on=role.passive)\n",
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
        ("aliases_on_net", "syntax.invalid"),
        ("aliases_in_metadata", "syntax.alias-context"),
        ("flat_unflattened", "connection.shape"),
        ("flat_scalar", "connection.flat"),
        ("flat_no_connect", "resolve.flat"),
        ("flat_other_call", "syntax.target"),
        ("choice_malformed", "metadata.erc"),
    ],
)
def test_errors(tmp_path, name, code):
    assert error_code(tmp_path, name) == code


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
  net v,w: nettype.power
    voltage: 5±5 %
    custom:
      range: 1
  inst c: C(*,flat(v))
    1..2: flat( v )
"""
    assert (
        format_source(source)
        == """component C:
  pins:
    1 (a, b): role.passive

board B:
  net v, w: nettype.power
    voltage: 5 ± 5%
    custom:
      range: 1
  inst c: C(*, flat(v))
    1..2: flat(v)
"""
    )
