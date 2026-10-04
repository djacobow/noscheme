import json

import pytest

from noscheme.cli import main
from noscheme.diagnostics import DiagnosticError
from noscheme.erc import apply_erc, check_circuit
from test_circuit import compile_source, make


SOURCE = """component Supply:
    pins:
        1:
            role: role.power_output
            max_source_current_mA: 110
component Load:
    pins:
        1:
            role: role.power_input
            max_load_current_mA: 80
block Module:
    ports:
        power: nettype.power
            role: role.power_input
            aggregate_load:
                max_load_current_mA: 100
    inst load: Load(
        connections:
            1: power
    )
board B:
    net rail: nettype.power
    inst supply: Supply(
        connections:
            1: rail
    )
    inst module: Module(
        connections:
            power: rail
    )
"""


def errors(tmp_path, source=SOURCE):
    return check_circuit(compile_source(tmp_path, source))


def codes(tmp_path, source=SOURCE):
    return [d.code for d in errors(tmp_path, source)]


def test_aggregate_replaces_internal_load(tmp_path):
    assert errors(tmp_path) == ()
    # Parent sees 100, not the internal 80 or their combined 180.
    source = SOURCE.replace("max_source_current_mA: 110", "max_source_current_mA: 90")
    assert codes(tmp_path, source) == ["erc.current_overload"]


def test_internal_overload_keeps_declared_parent_budget(tmp_path):
    source = SOURCE.replace("max_load_current_mA: 80", "max_load_current_mA: 120")
    diagnostics = errors(tmp_path, source)
    assert [d.code for d in diagnostics] == ["erc.aggregate_current_overload"]
    assert diagnostics[0].instance_path == "module.power"
    assert diagnostics[0].related and diagnostics[0].span.filename.endswith("main.nosch")


def test_missing_internal_annotation_remains_visible(tmp_path):
    source = SOURCE.replace("            max_load_current_mA: 80\n", "")
    diagnostics = errors(tmp_path, source)
    assert [d.code for d in diagnostics] == ["erc.incomplete_aggregate_load"]
    assert diagnostics[0].severity == "warning"


def test_array_instances_have_independent_aggregate_budgets(tmp_path):
    source = SOURCE.replace("inst module:", "inst module[2]:")
    assert codes(tmp_path, source) == ["erc.current_overload"]


def test_nested_boundaries_check_each_level(tmp_path):
    source = SOURCE.replace(
        "board B:",
        """block Outer:
    ports:
        power: nettype.power
            role: role.power_input
            aggregate_load:
                max_load_current_mA: 90
    inst inner: Module(
        connections:
            power: power
    )
board B:""",
    ).replace("inst module: Module", "inst module: Outer")
    source = source.replace("max_load_current_mA: 80", "max_load_current_mA: 120")
    diagnostics = errors(tmp_path, source)
    assert [d.code for d in diagnostics] == ["erc.aggregate_current_overload"] * 2
    assert {d.instance_path for d in diagnostics} == {"module.inner.power", "module.power"}


def test_siblings_and_outside_loads_are_not_hidden(tmp_path):
    source = SOURCE.replace(
        "    inst module: Module(",
        """    inst outside: Load(
        connections:
            1: rail
    )
    inst module: Module(""",
    )
    assert codes(tmp_path, source) == ["erc.current_overload"]


def test_grouped_physical_loads_inside_aggregate(tmp_path):
    source = SOURCE.replace(
        "            max_load_current_mA: 80",
        """        2:
            role: role.power_input
    metadata:
        current_loads:
            pair:
                pins: ["1", "2"]
                max_load_current_mA: 80""",
    ).replace("            1: power", "            1: power\n            2: power")
    assert errors(tmp_path, source) == ()


def test_same_scope_overlapping_boundaries_are_rejected(tmp_path):
    source = SOURCE.replace(
        "    inst load: Load(",
        """        second: nettype.power
            role: role.power_input
            aggregate_load:
                max_load_current_mA: 100
    inst load2: Load(
        connections:
            1: second
    )
    inst load: Load(""",
    ).replace("            power: rail", "            power: rail\n            second: rail")
    result = codes(tmp_path, source)
    assert result.count("erc.aggregate_load_boundary") == 2
    assert "erc.current_overload" in result  # Retain the two raw 80 mA loads.


def test_internal_source_cannot_be_hidden_by_aggregate(tmp_path):
    source = SOURCE.replace(
        "    inst load: Load(",
        """    inst generator: Supply(
        connections:
            1: power
    )
    inst load: Load(""",
    )
    result = codes(tmp_path, source)
    assert "erc.aggregate_load_boundary" in result
    assert "erc.multiple_drivers" in result


@pytest.mark.parametrize("amount", ["-1", "true", '"100"'])
def test_invalid_amounts_are_source_errors(tmp_path, amount):
    with pytest.raises(DiagnosticError):
        compile_source(
            tmp_path, SOURCE.replace("max_load_current_mA: 100", f"max_load_current_mA: {amount}")
        )


def test_internal_check_survives_unchecked_outer_net(tmp_path):
    source = SOURCE.replace("net rail: nettype.power", "net rail: nettype.unchecked").replace(
        "max_load_current_mA: 80", "max_load_current_mA: 120"
    )
    assert "erc.aggregate_current_overload" in codes(tmp_path, source)


def test_compile_publishes_source_located_aggregate_error(tmp_path):
    make(tmp_path, SOURCE.replace("max_load_current_mA: 80", "max_load_current_mA: 120"))
    assert main(["compile", "--project", str(tmp_path)]) == 0
    reports = list(tmp_path.rglob("erc.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert "erc.aggregate_current_overload" in json.dumps(report)


def test_rechecking_preserves_original_circuit(tmp_path):
    circuit = compile_source(tmp_path, SOURCE)
    before = json.dumps(circuit["nets"], sort_keys=True)
    assert apply_erc(circuit) == apply_erc(circuit) == ()
    assert json.dumps(circuit["nets"], sort_keys=True) == before


def test_inherited_port_budget_override(tmp_path):
    source = SOURCE.replace(
        "board B:",
        """block Smaller extends Module:
    ports:
        power:
            aggregate_load:
                max_load_current_mA: 70
board B:""",
    ).replace("inst module: Module", "inst module: Smaller")
    assert codes(tmp_path, source) == ["erc.aggregate_current_overload"]


def test_configuration_role_selection_checks_each_internal_budget(tmp_path):
    source = SOURCE.replace(
        "            role: role.power_input\n            max_load_current_mA: 80",
        "            role: [role.power_input, role.passive]\n            max_load_current_mA: 120",
    )
    source = source.replace(
        "    inst load: Load(",
        """    metadata:
        erc_choices:
            mode: ["on", "off"]
    inst load: Load(""",
    ).replace(
        "            1: power\n    )",
        """            1: power
        metadata:
            pins:
                1:
                    role:
                        choice: "mode"
                        cases:
                            on: role.power_input
                            off: role.passive
    )""",
    )
    source = source.replace(
        "board B:",
        '''board B:
    metadata:
        erc_choices:
            mode: ["on", "off"]
        configurations:
            powered:
                mode: "on"
            off:
                mode: "off"''',
    ).replace(
        "            power: rail\n    )",
        """            power: rail
        metadata:
            erc_choices:
                mode:
                    choice: "mode"
                    cases:
                        on: "on"
                        off: "off"
    )""",
    )
    circuit = compile_source(tmp_path, source)
    overloads = [d for d in apply_erc(circuit) if d.code == "erc.aggregate_current_overload"]
    assert len(overloads) == 1 and overloads[0].configuration == "powered"
