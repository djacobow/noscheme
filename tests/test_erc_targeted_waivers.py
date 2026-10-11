import pytest

from noscheme.diagnostics import DiagnosticError
from noscheme.erc import apply_erc, report_files
from test_circuit import compile_source
from test_erc import component


def design(pins='["p[0].1"]'):
    return (
        component("power_input")
        + f"""block B:
  net.ground gnd:
    waivers:
      incompatible_role:
        reason: "Ground the disabled power domain."
        pins: {pins}
  P p[2]:
    1: gnd
"""
    )


def test_pin_waiver_preserves_other_grounded_supply_error(tmp_path):
    circuit = compile_source(tmp_path, design())
    diagnostics = apply_erc(circuit)
    assert [(d.code, d.instance_path) for d in diagnostics] == [("erc.incompatible_role", "p[1].1")]
    waiver = circuit["erc"]["waivers"][0]
    assert waiver["pins"] == ["p[0].1"]
    assert waiver["valid_targets"]
    assert [d["instance_path"] for d in waiver["diagnostics"]] == ["p[0].1"]
    text = report_files(circuit, tmp_path)[tmp_path / "erc.txt"]
    assert "pins p[0].1" in text and "Ground the disabled power domain" in text
    assert apply_erc(circuit) == diagnostics


@pytest.mark.parametrize("pins", ['["p[7].1"]', '["p[0].2"]', '["p[0].1", "missing.1"]'])
def test_invalid_target_cannot_suppress_any_errors(tmp_path, pins):
    circuit = compile_source(tmp_path, design(pins))
    diagnostics = apply_erc(circuit)
    assert sum(d.code == "erc.incompatible_role" for d in diagnostics) == 2
    assert any(d.code == "erc.waiver_target" for d in diagnostics)
    assert not circuit["erc"]["waivers"][0]["valid_targets"]


@pytest.mark.parametrize("pins", ["[]", '[""]', '["p[0].1", "p[0].1"]', "[42]", '"p[0].1"'])
def test_malformed_target_lists_fail_validation(tmp_path, pins):
    with pytest.raises(DiagnosticError) as error:
        compile_source(tmp_path, design(pins))
    assert error.value.diagnostics[0].code == "metadata.waiver"


def test_targeted_waiver_is_relative_to_each_block_instance(tmp_path):
    source = design('["p[0].1", "p[1].1"]').replace("block B:", "block W:")
    source += "block B:\n  W w[2]()\n"
    circuit = compile_source(tmp_path, source)
    assert apply_erc(circuit) == ()
    assert [w["pins"] for w in circuit["erc"]["waivers"]] == [
        ["w[0].p[0].1", "w[0].p[1].1"],
        ["w[1].p[0].1", "w[1].p[1].1"],
    ]


def test_ambiguous_power_role_is_not_selected_by_ground_connection(tmp_path):
    source = design().replace("role: role.power_input", "role: [role.power_input, role.ground]")
    circuit = compile_source(tmp_path, source)
    diagnostics = apply_erc(circuit)
    assert any(d.code == "erc.incomplete_roles" for d in diagnostics)
    assert not circuit["erc"]["waivers"][0]["diagnostics"]


def test_selected_ground_role_is_allowed(tmp_path):
    source = design().replace("role: role.power_input", "role: [role.power_input, role.ground]")
    # The instance is last in the design, so a new section can follow it.
    source += "    metadata:\n      pins:\n        1: role.ground\n"
    assert apply_erc(compile_source(tmp_path, source)) == ()


def test_target_must_be_connected_to_the_waived_net(tmp_path):
    source = (
        design('["other.1"]')
        + """  net.passive other_net
  P other[2]:
    1: other_net
"""
    )
    source = source.replace('["other.1"]', '["other[0].1"]')
    diagnostics = apply_erc(compile_source(tmp_path, source))
    assert any(d.code == "erc.waiver_target" for d in diagnostics)
    assert sum(d.code == "erc.incompatible_role" for d in diagnostics) == 2


def test_pin_target_cannot_waive_net_wide_diagnostic(tmp_path):
    source = (
        design().replace("net.ground", "net.power").replace("incompatible_role:", "missing_driver:")
    )
    circuit = compile_source(tmp_path, source)
    assert any(d.code == "erc.missing_driver" for d in apply_erc(circuit))
    assert circuit["erc"]["waivers"][0]["diagnostics"] == []


def test_targeted_waiver_on_inner_port_still_has_invalid_scope(tmp_path):
    source = (
        component("power_input")
        + """block W:
  ports:
    net.ground supply:
      waivers:
        incompatible_role:
          reason: "Disabled domain"
          pins: ["p.1"]
  P p:
    1: supply
block B:
  net.ground gnd
  W w[2]:
    supply: gnd
"""
    )
    diagnostics = apply_erc(compile_source(tmp_path, source))
    assert sum(d.code == "erc.waiver_scope" for d in diagnostics) == 2
    assert sum(d.code == "erc.incompatible_role" for d in diagnostics) == 2
