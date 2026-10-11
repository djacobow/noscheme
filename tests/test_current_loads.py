import pytest

from noscheme.diagnostics import DiagnosticError
from noscheme.erc import check_circuit
from test_circuit import compile_source


SOURCE = """component Supply:
  pins:
    1:
      role: role.power_output
      max_source_current: 150m
component MCU:
  pins:
    1 (vdd):
      role: role.power_input
    2:
      role: role.power_input
  metadata:
    current_loads:
      core:
        pins: [vdd, "2"]
        max_load_current: 100m
block B:
  net.power rail
  Supply supply:
    1: rail
  MCU mcu:
    1: rail
    2: rail
"""


def diagnostics(tmp_path, source=SOURCE):
    return check_circuit(compile_source(tmp_path, source))


def test_group_counted_once_and_aliases_resolved(tmp_path):
    circuit = compile_source(tmp_path, SOURCE)
    part = next(p for p in circuit["parts"] if p["path"] == ["mcu"])
    assert part["metadata"]["current_loads"]["value"]["core"]["value"]["pins"]["value"] == [
        "1",
        "2",
    ]
    assert check_circuit(circuit) == ()


def test_arrays_count_each_instance_once(tmp_path):
    errors = diagnostics(tmp_path, SOURCE.replace("MCU mcu:", "MCU mcu[2]:"))
    assert [d.code for d in errors] == ["erc.current_overload"]


@pytest.mark.parametrize("connection", ["other", "no_connect"])
def test_group_rejects_split_or_disconnected_pins(tmp_path, connection):
    source = SOURCE.replace("  net.power rail\n", "  net.power other\n  net.power rail\n")
    source = source.replace("    2: rail", f"    2: {connection}")
    errors = diagnostics(tmp_path, source)
    group = next(d for d in errors if d.code == "erc.current_load_group")
    assert group.severity == "error"
    assert group.instance_path == "mcu"
    assert len(group.related) == 2
    assert group.span.filename.endswith("main.nosch")
    assert any(d.code == "erc.incomplete_budget" for d in errors)


@pytest.mark.parametrize(
    "old,new",
    [
        ('[vdd, "2"]', "[]"),
        ('[vdd, "2"]', '[vdd, "1"]'),
        ('[vdd, "2"]', "[missing]"),
        ("max_load_current: 100m", "max_load_current: -1m"),
        ("max_load_current: 100m", "max_load_current: true"),
        ("max_load_current: 100m", "unexpected: 100"),
        ("        max_load_current: 100m\n", ""),
        ("role: role.power_input", "role: role.passive"),
        ("    1 (vdd):", "    1 (vdd):\n      max_load_current: 50m"),
        (
            "max_load_current: 100m",
            "max_load_current: 100m\n      extra:\n        pins: [vdd]\n        max_load_current: 10m",
        ),
    ],
)
def test_invalid_group_metadata(tmp_path, old, new):
    with pytest.raises(DiagnosticError):
        compile_source(tmp_path, SOURCE.replace(old, new))


def test_group_and_individual_loads_sum(tmp_path):
    source = SOURCE.replace(
        "block B:",
        """component Extra:
  pins:
    1:
      role: role.power_input
      max_load_current: 60m
block B:
  Extra extra:
    1: rail
""",
    )
    assert [d.code for d in diagnostics(tmp_path, source)] == ["erc.current_overload"]


def test_inherited_group_amount_override(tmp_path):
    source = SOURCE.replace(
        "block B:",
        """extend MCU Bigger:
  metadata:
    current_loads:
      core:
        max_load_current: 160m
block B:""",
    ).replace("MCU mcu", "Bigger mcu")
    assert [d.code for d in diagnostics(tmp_path, source)] == ["erc.current_overload"]


def test_group_requires_capacity_even_without_pin_annotations(tmp_path):
    source = SOURCE.replace("      max_source_current: 150m\n", "")
    assert [d.code for d in diagnostics(tmp_path, source)] == ["erc.incomplete_budget"]


def test_effective_role_override_does_not_silently_drop_group(tmp_path):
    source = SOURCE.replace(
        "    2:\n      role: role.power_input\n",
        "    2:\n      role: [role.power_input, role.passive]\n",
    ).replace(
        "    2: rail",
        """    2: rail
    metadata:
      pins:
        2:
          role: role.passive
""",
    )
    errors = diagnostics(tmp_path, source)
    assert any(d.code == "erc.current_load_group" for d in errors)
    assert any(d.code == "erc.incomplete_budget" for d in errors)


def test_independent_supply_groups_on_separate_rails(tmp_path):
    source = (
        SOURCE.replace('[vdd, "2"]', "[vdd]")
        .replace(
            "        max_load_current: 100m",
            """        max_load_current: 100m
      analog:
        pins: ["2"]
        max_load_current: 20m""",
        )
        .replace(
            "  net.power rail\n",
            """  net.power analog
  Supply analog_supply:
    1: analog
  net.power rail
""",
        )
        .replace("    2: rail", "    2: analog")
    )
    assert diagnostics(tmp_path, source) == ()


def test_configurations_check_effective_group_roles(tmp_path):
    from noscheme.erc import apply_erc

    source = SOURCE.replace("role: role.power_input", "role: [role.power_input, role.passive]")
    source = source.replace(
        "block B:",
        '''block B:
  metadata:
    erc_choices:
      mode: ["powered", "absent"]
    configurations:
      powered:
        mode: "powered"
      absent:
        mode: "absent"''',
    )
    source = source.replace(
        "    2: rail",
        """    2: rail
    metadata:
      pins:
        1..2: choice(mode, powered=role.power_input, absent=role.passive)
""",
    )
    circuit = compile_source(tmp_path, source)
    errors = apply_erc(circuit)
    group = [d for d in errors if d.code == "erc.current_load_group"]
    assert len(group) == 1 and group[0].configuration == "absent"
    assert not [d for d in errors if d.configuration == "powered"]
