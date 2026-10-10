"""SI-prefixed literals, quantity parameters, displayed values, and SI field names."""

import pytest

from noscheme.diagnostics import DiagnosticError
from noscheme.erc_registry import load_registry
from noscheme.kicad.convert import convert_library
from noscheme.kicad.export import part_value
from noscheme.kicad.sexpr import form
from noscheme.parser import parse
from test_circuit import compile_source
from test_convert import library, pin

SOURCE = """component Resistor:
  params:
    resistance value = 4.7k
  pins:
    1..2:
      role: role.passive
      pull_resistance: value
  metadata:
    footprint: "Resistor_SMD:R_0603_1608Metric"
board B:
  net.passive a
  Resistor default:
    1: a
    2: a
  Resistor<value: 22k1> rkm:
    1: a
    2: a
"""


def values(circuit):
    return {p["identity"]: part_value(p) for p in circuit["parts"]}


def test_quantity_parameters_display_with_units_in_the_netlist(tmp_path):
    circuit = compile_source(tmp_path, SOURCE)
    assert values(circuit) == {"default": "4.7kOhm", "rkm": "22.1kOhm"}
    pins = {p["identity"]: p["pins"]["1"]["metadata"] for p in circuit["parts"]}
    assert pins["rkm"]["pull_resistance"]["value"] == 22100


def test_quantity_accepts_verbatim_text_without_numeric_meaning(tmp_path):
    text = SOURCE.replace("<value: 22k1>", '<value: "22k1 1%">')
    with pytest.raises(DiagnosticError, match="expected a number"):
        compile_source(tmp_path, text)
    text = text.replace("      pull_resistance: value\n", "")
    assert values(compile_source(tmp_path, text))["rkm"] == "22k1 1%"


@pytest.mark.parametrize(
    "source,code",
    [
        ("component C:\n  pins:\n    10k:\n      role: role.passive\n", "syntax"),
        ("component C:\n  params:\n    number x = 4.7k7\n", "syntax.number"),
        ("component C:\n  params:\n    number x = 10kOhm\n", "syntax"),
    ],
)
def test_scaled_literal_syntax(source, code):
    with pytest.raises(DiagnosticError) as error:
        parse(source, "main.nosch")
    assert error.value.diagnostics[0].code.startswith(code)


def test_quoted_pin_name_may_look_like_a_scaled_number():
    tree = parse('component C:\n  pins:\n    "10k":\n      role: role.passive\n', "m")
    assert tree.items[0].body[0].entries[0].key.first.text == "10k"


@pytest.mark.parametrize("name", ["leakage_uA", "rail_V", "pull_kOhm", "ripple_mV"])
def test_extension_fields_cannot_name_units(name):
    with pytest.raises(ValueError, match="names a unit"):
        load_registry().fields("pin", {name: "current"})
    load_registry().fields("pin", {"leakage": "current"})


def test_passive_symbols_convert_to_quantity_values(tmp_path):
    def passive(name, reference):
        return form(
            "symbol",
            name,
            form("property", "Reference", reference),
            form("property", "Value", reference),
            form("symbol", name + "_1_1", pin("1", "~"), pin("2", "~")),
        )

    source = library(
        tmp_path / "Device.kicad_sym",
        passive("R", "R"),
        passive("C", "C"),
        passive("L", "L"),
        passive("D", "D"),
    )
    convert_library(source, tmp_path / "out")
    text = (tmp_path / "out/Device.nosch").read_text()
    for name, kind in [("R", "resistance"), ("C", "capacitance"), ("L", "inductance")]:
        assert f"component {name}:\n  params:\n    {kind} value\n" in text
    assert 'component D:\n  params:\n    string value = "D"\n' in text


DISPLAY = """component Part:
  params:
    resistance value = 4.7k
  pins:
    1:
      role: role.passive
      unused: unused.always
  metadata:
    value: value / 1k
block Holder:
  params:
    resistance chosen = 22k
    number count = 3
  Part bare()
  Part caller_quantity:
    metadata:
      value: chosen
  Part caller_number:
    metadata:
      value: count
  Part literal:
    metadata:
      value: 4.7k
board B:
  Holder holder()
"""


def test_only_bare_quantity_parameters_display_with_units(tmp_path):
    shown = {
        k.removeprefix("holder."): v for k, v in values(compile_source(tmp_path, DISPLAY)).items()
    }
    # Computed and literal numbers have no quantity; a caller's quantity parameter does.
    assert shown == {
        "bare": "4.7",
        "caller_quantity": "22kOhm",
        "caller_number": "3",
        "literal": "4700",
    }
    plain = DISPLAY.replace("    value: value / 1k\n", "    value: value\n")
    assert values(compile_source(tmp_path, plain))["holder.bare"] == "4.7kOhm"


def test_evaluate_report_uses_the_same_display_rules(tmp_path):
    from noscheme.evaluate import evaluate_project
    from noscheme.resolve import resolve_project
    from test_circuit import make

    report, _ = evaluate_project(*resolve_project(make(tmp_path, DISPLAY)))
    shown = {r["path"]: r["values"].get("value", {}).get("value") for r in report["instances"]}
    assert shown["B.holder.bare"] == 4.7
    assert shown["B.holder.caller_quantity"] == "22kOhm"
    assert shown["B.holder.literal"] == "4700"  # Integers encode as decimal strings.
