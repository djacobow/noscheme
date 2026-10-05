import pytest

from noscheme.diagnostics import DiagnosticError
from noscheme.erc import check_circuit
from test_circuit import compile_source

SOURCE = """component Input:
    params:
        maximum: number = 3.6
    pins:
        1:
            role: role.power_input
            min_voltage: -0.3
            max_voltage: maximum
board B:
    net supply: nettype.power
        min_voltage: 3.3 * 0.95
        max_voltage: 3.3 * 1.05
    inst input: Input(
        connections:
            1: supply
    )
"""


def checks(tmp_path, source=SOURCE):
    return [
        d for d in check_circuit(compile_source(tmp_path, source)) if d.code == "erc.voltage_limits"
    ]


def test_explicit_voltage_range_with_parameter_limits(tmp_path):
    assert not checks(tmp_path)
    assert not checks(tmp_path, SOURCE.replace("3.3 * 0.95", "-0.3").replace("3.3 * 1.05", "3.6"))


@pytest.mark.parametrize("old,new", [("3.3 * 1.05", "5"), ("3.3 * 0.95", "-1")])
def test_outside_pin_limit_reports_pin_and_range(tmp_path, old, new):
    result = checks(tmp_path, SOURCE.replace(old, new))
    assert len(result) == 1 and result[0].severity == "error"
    assert result[0].instance_path == "input.1"
    assert result[0].related


def test_unannotated_pins_are_not_required_to_supply_limits(tmp_path):
    source = SOURCE.replace("            min_voltage: -0.3\n", "").replace(
        "            max_voltage: maximum\n", ""
    )
    assert not checks(tmp_path, source)


def test_pin_limits_without_range_warn(tmp_path):
    source = SOURCE.replace("        min_voltage: 3.3 * 0.95\n", "").replace(
        "        max_voltage: 3.3 * 1.05\n", ""
    )
    result = checks(tmp_path, source)
    assert len(result) == 1 and result[0].severity == "warning"


def test_invalid_intervals_report_errors(tmp_path):
    assert any(d.severity == "error" for d in checks(tmp_path, SOURCE.replace("3.3 * 0.95", "4")))
    assert any(
        d.severity == "error"
        for d in checks(tmp_path, SOURCE.replace("        max_voltage: 3.3 * 1.05\n", ""))
    )
    assert any(
        "min_voltage exceeds" in d.message
        for d in checks(tmp_path, SOURCE.replace("min_voltage: -0.3", "min_voltage: 4"))
    )


def test_nonfinite_and_boolean_voltage_values_are_source_errors(tmp_path):
    for value in ("1e309", "true", '"3.3"'):
        with pytest.raises(DiagnosticError):
            compile_source(
                tmp_path, SOURCE.replace("max_voltage: 3.3 * 1.05", "max_voltage: " + value)
            )


def test_voltage_check_waiver(tmp_path):
    source = SOURCE.replace(
        "max_voltage: 3.3 * 1.05",
        """max_voltage: 5
        waivers:
            voltage_limits:
                reason: "External protection accounted for separately"
                pins: ["input.1"]""",
    )
    assert not checks(tmp_path, source)
