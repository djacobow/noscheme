"""Quantities evaluate in their declaration/caller scope before ERC sees them."""

import pytest

from noscheme.diagnostics import DiagnosticError
from noscheme.erc import check_circuit
from test_circuit import compile_source

SOURCE = """from noscheme import bias, std
component Pull:
  params:
    resistance resistance = 4.7k
  pins:
    1:
      role: role.passive
      bias: bias.pull_up
      pull_resistance: resistance
      capacitance: resistance * 1f
  metadata:
    value: resistance
component Device:
  pins:
    1:
      role: role.open_drain
      bias: bias.none
board B:
  net.open_drain bus:
    checks:
      pull_up: std.parallel(pull_resistance, where=bias.pull_up, min=1k + 1k)
  Pull<resistance: 3.3k> pull:
    1: bus
  Device device:
    1: bus
"""


def test_numeric_parameter_drives_display_and_budget(tmp_path):
    circuit = compile_source(tmp_path, SOURCE)
    pull = next(p for p in circuit["parts"] if p["identity"] == "pull")
    assert pull["value"]["value"] == "3.3kOhm"
    assert pull["pins"]["1"]["metadata"]["pull_resistance"]["value"] == 3300
    assert pull["pins"]["1"]["metadata"]["capacitance"]["value"] == pytest.approx(3.3e-12)
    assert not check_circuit(circuit)


@pytest.mark.parametrize("quantity", ["0", "-1", '"bad"', "1e308 * 1e308"])
def test_invalid_evaluated_quantities_fail_before_erc(tmp_path, quantity):
    source = SOURCE.replace("pull_resistance: resistance", f"pull_resistance: ({quantity})")
    with pytest.raises(DiagnosticError):
        compile_source(tmp_path, source)


def test_metadata_expression_names_are_resolved_even_for_uninstantiated_parts(tmp_path):
    with pytest.raises(DiagnosticError) as error:
        compile_source(
            tmp_path,
            SOURCE
            + """component Unused:
  pins:
    1:
      capacitance: missing + 1
""",
        )
    assert any(d.code == "resolve.expression-name" for d in error.value.diagnostics)


def test_instance_quantities_use_caller_environment(tmp_path):
    source = SOURCE.replace(
        "board B:\n", "block Circuit:\n  params:\n    number resistance = 2200\n"
    )
    source = source.replace(
        "    1: bus\n  Device",
        """    1: bus
    metadata:
      pins:
        1:
          pull_resistance: resistance
  Device""",
    )
    source += "board B:\n  Circuit circuit()\n"
    circuit = compile_source(tmp_path, source)
    pull = next(p for p in circuit["parts"] if p["identity"] == "circuit.pull")
    assert pull["value"]["value"] == "3.3kOhm"
    assert pull["pins"]["1"]["metadata"]["pull_resistance"]["value"] == 2200


def test_nested_current_load_expression(tmp_path):
    source = """component Load:
  params:
    current current = 12m
  pins:
    1:
      role: role.power_input
  metadata:
    current_loads:
      supply:
        pins: ["1"]
        max_load_current: current * 2
board B:
  net.power rail
  Load load:
    1: rail
"""
    circuit = compile_source(tmp_path, source)
    assert circuit["parts"][0]["metadata"]["current_loads"]["value"]["supply"]["value"][
        "max_load_current"
    ]["value"] == pytest.approx(0.024)


def test_overridden_quantity_does_not_evaluate_shadowed_expression(tmp_path):
    source = SOURCE.replace(
        "pull_resistance: resistance", "pull_resistance: resistance / 0"
    ).replace(
        "    1: bus\n  Device",
        """    1: bus
    metadata:
      pins:
        1:
          pull_resistance: 2200
  Device""",
    )
    circuit = compile_source(tmp_path, source)
    pull = next(p for p in circuit["parts"] if p["identity"] == "pull")
    assert pull["pins"]["1"]["metadata"]["pull_resistance"]["value"] == 2200
