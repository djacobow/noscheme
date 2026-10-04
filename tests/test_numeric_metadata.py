"""Quantities evaluate in their declaration/caller scope before ERC sees them."""

import pytest

from noscheme.diagnostics import DiagnosticError
from noscheme.erc import check_circuit
from test_circuit import compile_source

SOURCE = """from noscheme import bias, f
component Pull:
    params:
        resistance: number = 4700
    pins:
        1:
            role: role.passive
            bias: bias.pull_up
            pull_resistance_ohm: resistance
            capacitance_pF: resistance / 1000
    metadata:
        value: f.kOhm(resistance / 1000)
component Device:
    pins:
        1:
            role: role.open_drain
            bias: bias.none
board B:
    net bus: nettype.open_drain
        checks:
            pull_up:
                checker: "std.reduce"
                params:
                    attribute: "pull_resistance_ohm"
                    reducer: "std.parallel"
                    where:
                        bias: bias.pull_up
                    min: 1000 + 1000
    inst pull: Pull(
        connections:
            1: bus
        params:
            resistance: 3300
    )
    inst device: Device(
        connections:
            1: bus
    )
"""


def test_numeric_parameter_drives_display_and_budget(tmp_path):
    circuit = compile_source(tmp_path, SOURCE)
    pull = next(p for p in circuit["parts"] if p["identity"] == "pull")
    assert pull["value"]["value"] == "3.3kOhm"
    assert pull["pins"]["1"]["metadata"]["pull_resistance_ohm"]["value"] == 3300
    assert pull["pins"]["1"]["metadata"]["capacitance_pF"]["value"] == 3.3
    assert not check_circuit(circuit)


@pytest.mark.parametrize("quantity", ["0", "-1", '"bad"', "1e308 * 1e308"])
def test_invalid_evaluated_quantities_fail_before_erc(tmp_path, quantity):
    source = SOURCE.replace("pull_resistance_ohm: resistance", f"pull_resistance_ohm: ({quantity})")
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
            capacitance_pF: missing + 1
""",
        )
    assert any(d.code == "resolve.expression-name" for d in error.value.diagnostics)


def test_instance_quantities_use_caller_environment(tmp_path):
    source = SOURCE.replace(
        "board B:\n", "block Circuit:\n    params:\n        resistance: number = 2200\n"
    )
    source = source.replace(
        "            resistance: 3300\n",
        """            resistance: 3300
        metadata:
            pins:
                1:
                    pull_resistance_ohm: resistance
""",
    )
    source += "board B:\n    inst circuit: Circuit()\n"
    circuit = compile_source(tmp_path, source)
    pull = next(p for p in circuit["parts"] if p["identity"] == "circuit.pull")
    assert pull["value"]["value"] == "3.3kOhm"
    assert pull["pins"]["1"]["metadata"]["pull_resistance_ohm"]["value"] == 2200


def test_nested_current_load_expression(tmp_path):
    source = """component Load:
    params:
        current: number = 12
    pins:
        1:
            role: role.power_input
    metadata:
        current_loads:
            supply:
                pins: ["1"]
                max_load_current_mA: current * 2
board B:
    net rail: nettype.power
    inst load: Load(
        connections:
            1: rail
    )
"""
    circuit = compile_source(tmp_path, source)
    assert (
        circuit["parts"][0]["metadata"]["current_loads"]["value"]["supply"]["value"][
            "max_load_current_mA"
        ]["value"]
        == 24
    )


def test_overridden_quantity_does_not_evaluate_shadowed_expression(tmp_path):
    source = SOURCE.replace(
        "pull_resistance_ohm: resistance", "pull_resistance_ohm: resistance / 0"
    ).replace(
        "            resistance: 3300\n",
        """            resistance: 3300
        metadata:
            pins:
                1:
                    pull_resistance_ohm: 2200
""",
    )
    circuit = compile_source(tmp_path, source)
    pull = next(p for p in circuit["parts"] if p["identity"] == "pull")
    assert pull["pins"]["1"]["metadata"]["pull_resistance_ohm"]["value"] == 2200
