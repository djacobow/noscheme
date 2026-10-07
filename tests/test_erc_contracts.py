"""Behavioral cases for electrical domains and hierarchical port boundaries."""

import pytest

from noscheme.erc import apply_erc, check_circuit
from test_circuit import compile_source
from test_erc import component, pair, codes


@pytest.mark.parametrize(
    "nettype,role,allowed",
    [
        ("digital", "digital_input", True),
        ("digital", "analog_input", False),
        ("digital", "power_output", False),
        ("analog", "analog_bidirectional", True),
        ("analog", "digital_output", False),
        ("open_drain", "open_drain", True),
        ("open_drain", "digital_input", True),
        ("open_drain", "digital_output", False),
        ("open_drain", "digital_bidirectional", False),
        ("bidirectional", "digital_output", True),
        ("bidirectional", "analog_input", False),
        ("power", "power_input", True),
        ("power", "digital_input", True),
        ("power", "analog_input", True),
        ("power", "digital_output", False),
        ("ground", "ground", True),
        ("ground", "power_input", False),
        ("ground", "digital_input", True),
        ("ground", "power_output", False),
        ("passive", "analog_output", True),
        ("unchecked", "analog_output", True),
    ],
)
def test_electrical_domain_cases(tmp_path, nettype, role, allowed):
    c = pair(tmp_path, role, "passive", nettype)
    assert ("incompatible_role" not in codes(c)) == allowed
    if not allowed:
        d = next(d for d in check_circuit(c) if d.code == "erc.incompatible_role")
        assert d.instance_path == "a.1" and d.related


@pytest.mark.parametrize(
    "nettype", ["digital", "analog", "open_drain", "bidirectional", "power", "ground", "passive"]
)
def test_passive_contacts_do_not_impose_a_domain(tmp_path, nettype):
    assert "incompatible_role" not in codes(pair(tmp_path, "passive", "passive", nettype))


def test_open_drain_does_not_conflict_with_other_open_drain(tmp_path):
    assert codes(pair(tmp_path, "open_drain", "open_drain", "open_drain")) == []
    assert "multiple_drivers" in codes(pair(tmp_path, "digital_output", "open_drain"))


def test_selectable_roles_are_not_guessed(tmp_path):
    source = component("digital_input").replace(
        "role: role.digital_input", "role: [role.digital_input, role.analog_input]"
    )
    c = compile_source(
        tmp_path,
        source
        + """board B:
    net n: nettype.digital
    inst p[2]: P
        connections:
            1: n
""",
    )
    assert codes(c) == ["incomplete_roles"]
    for part in c["parts"]:
        part["pins"]["1"]["metadata"]["role"]["value"] = ["analog_input", "analog_output"]
    assert codes(c).count("incompatible_role") == 2


PARTS = component("digital_output").replace("component P:", "component Driver:") + component(
    "digital_input"
).replace("component P:", "component Receiver:")


def hierarchy(
    contract="digital_output", internal="Driver", inner_type="digital", outer_type="digital"
):
    return (
        PARTS
        + f"""block W:
    ports:
        signal: nettype.{inner_type}
            role: role.{contract}
            unused: unused.never
    inst p: {internal}
        connections:
            1: signal
block Forward:
    ports:
        signal: nettype.{inner_type}
            role: role.{contract}
            unused: unused.never
    inst child: W
        connections:
            signal: signal
board B:
    net n: nettype.{outer_type}
    inst w: Forward
        connections:
            signal: n
    inst receiver: Receiver
        connections:
            1: n
"""
    )


def test_forwarded_output_ports_count_one_physical_driver(tmp_path):
    c = compile_source(tmp_path, hierarchy())
    assert codes(c) == []
    assert len(c["nets"][0]["endpoints"]) == 2
    assert all("scope" in d and "binding" in d for d in c["nets"][0]["declarations"])


def test_input_contract_cannot_hide_an_internal_driver(tmp_path):
    c = compile_source(tmp_path, hierarchy(contract="digital_input"))
    assert codes(c).count("port_role") == 2
    assert "multiple_drivers" not in codes(c)


def test_output_contract_does_not_invent_a_driver(tmp_path):
    c = compile_source(tmp_path, hierarchy(internal="Receiver"))
    assert codes(c).count("port_role") == 2
    assert codes(c).count("missing_driver") == 1


def test_caller_driver_does_not_satisfy_inner_output_contract(tmp_path):
    source = hierarchy(internal="Receiver").replace(
        "inst receiver: Receiver", "inst receiver: Driver"
    )
    c = compile_source(tmp_path, source)
    assert codes(c).count("port_role") == 2
    assert "missing_driver" not in codes(c)


def test_inner_unchecked_does_not_disable_caller_checks(tmp_path):
    c = compile_source(tmp_path, hierarchy(inner_type="unchecked", internal="Receiver"))
    assert "missing_driver" in codes(c)
    assert "unchecked_net" not in codes(c)


def test_outer_unchecked_does_not_disable_inner_contracts(tmp_path):
    c = compile_source(tmp_path, hierarchy(contract="digital_input", outer_type="unchecked"))
    assert codes(c).count("port_role") == 2
    assert "unchecked_net" in codes(c)


def test_all_type_constraints_share_one_role_selection(tmp_path):
    source = hierarchy(contract="passive", internal="Receiver", outer_type="analog")
    source = source.replace(
        "role: role.digital_input", "role: [role.digital_input, role.analog_input]"
    )
    c = compile_source(tmp_path, source)
    assert codes(c).count("incompatible_role") == 2


@pytest.mark.parametrize(
    "binding,policy,expected",
    [
        ("", "explicit", "unused_port"),
        ("", "always", None),
        ("signal: no_connect", "explicit", None),
        ("signal: no_connect", "never", "required_port_connection"),
        ("signal: n", "never", "required_port_connection"),
    ],
)
def test_port_connection_contracts_need_an_outside_endpoint(tmp_path, binding, policy, expected):
    clause = "        connections:\n            " + binding + "\n" if binding else ""
    source = (
        component("passive")
        + f"""block W:
    ports:
        signal: nettype.passive
            unused: unused.{policy}
    inst p[2]: P
        connections:
            1: signal
board B:
    net n: nettype.passive
        placeholder: true
    inst w: W
{clause}
"""
    )
    found = codes(compile_source(tmp_path, source))
    assert found == ([expected] if expected else [])


def test_explicit_external_port_supplies_real_endpoint_once(tmp_path):
    c = compile_source(
        tmp_path,
        component("digital_input")
        + """block External:
    ports:
        signal: nettype.digital
board B:
    net n: nettype.digital
    inst e: External
        connections:
            signal: n
        metadata:
            pins:
                signal:
                    external_role: role.digital_output
    inst p: P
        connections:
            1: n
""",
    )
    assert codes(c) == []
    assert len(c["nets"][0]["endpoints"]) == 1  # Only physical pins go to CAD.


def test_outer_waiver_suppresses_only_compatibility_check(tmp_path):
    c = pair(tmp_path, "digital_output", "digital_output", "analog")
    origin = c["nets"][0]["declarations"][0]
    origin["metadata"]["waivers"] = {
        "value": {
            "incompatible_role": {
                "value": "Mixed signal interface reviewed",
                "span": origin["span"],
            }
        }
    }
    assert [d.code for d in apply_erc(c)] == ["erc.multiple_drivers"]
    assert len(c["erc"]["waivers"][0]["diagnostics"]) == 2


def test_arrayed_ports_count_only_their_own_internal_driver(tmp_path):
    source = hierarchy().replace("inst w: Forward", "inst w[2]: Forward")
    c = compile_source(tmp_path, source)
    assert codes(c) == ["multiple_drivers"]
    d = check_circuit(c)[0]
    assert len(d.related) == 2
    assert {r.message for r in d.related} == {"w[0].child.p.1", "w[1].child.p.1"}


def test_bundle_array_port_contracts_are_checked_per_leaf(tmp_path):
    source = (
        PARTS
        + """bundle Bus:
    signal: nettype.digital
        role: role.digital_output
        unused: unused.never
block W:
    ports:
        bus: Bus[2]
    inst driver[2]: Driver
        connections:
            1: bus[0:2].signal
board B:
    net bus: Bus[2]
    inst w: W
        connections:
            bus: bus
    inst receiver[2]: Receiver
        connections:
            1: bus[0:2].signal
"""
    )
    c = compile_source(tmp_path, source)
    assert codes(c) == []
    assert len(c["nets"]) == 2


def test_unselected_internal_roles_cannot_conceal_definite_contract_violation(tmp_path):
    source = hierarchy(contract="digital_input").replace(
        "role: role.digital_output", "role: [role.digital_output, role.analog_input]"
    )
    c = compile_source(tmp_path, source)
    assert codes(c).count("port_role") == 2


def test_port_contract_uncertainty_is_reported_without_inventing_driver(tmp_path):
    source = hierarchy().replace(
        "role: role.digital_output\n            unused: unused.explicit",
        "role: [role.digital_output, role.digital_input]\n            unused: unused.explicit",
    )
    c = compile_source(tmp_path, source)
    assert codes(c).count("incomplete_port_role") == 2
    assert "incomplete_roles" in codes(c)


def test_port_policies_cannot_be_waived(tmp_path):
    from noscheme.diagnostics import DiagnosticError

    for check in ("required_port_connection", "unused_port"):
        with pytest.raises(DiagnosticError) as exc:
            compile_source(
                tmp_path,
                f"""board B:
    net n: nettype.digital
        waivers:
            {check}: "Not allowed"
""",
            )
        assert exc.value.diagnostics[0].code == "metadata.waiver"


@pytest.mark.parametrize("rail,source_role", [("power", "power_output"), ("ground", "ground")])
def test_explicit_input_ports_accept_rail_ties_through_nested_blocks(tmp_path, rail, source_role):
    source = hierarchy(contract="digital_input", internal="Receiver", outer_type=rail)
    source = source.replace("inst receiver: Receiver", "inst receiver: Driver")
    source = source.replace("role: role.digital_output", f"role: role.{source_role}")
    c = compile_source(tmp_path, source)
    assert codes(c) == []


def test_rail_tie_requires_explicit_input_contract_at_every_public_boundary(tmp_path):
    source = hierarchy(contract="digital_input", internal="Receiver", outer_type="ground")
    source = source.replace("inst receiver: Receiver", "inst receiver: Driver")
    source = source.replace("role: role.digital_output", "role: role.ground")
    source = source.replace(
        "signal: nettype.digital\n            role: role.digital_input",
        "signal: nettype.digital",
        1,
    )
    found = codes(compile_source(tmp_path, source))
    assert "incompatible_role" in found
    assert "missing_driver" in found


def test_input_port_rail_tie_does_not_legalize_an_internal_output(tmp_path):
    source = hierarchy(contract="digital_input", outer_type="power")
    c = compile_source(tmp_path, source)
    assert codes(c).count("port_role") == 2
    assert "incompatible_role" in codes(c)


def test_passive_caller_does_not_establish_a_fixed_logic_level(tmp_path):
    c = compile_source(
        tmp_path, hierarchy(contract="digital_input", internal="Receiver", outer_type="passive")
    )
    assert "missing_driver" in codes(c)


def test_blink_example_has_no_erc_errors_or_warnings():
    from noscheme.circuit import elaborate_project
    from noscheme.resolve import resolve_project
    from test_circuit import ROOT

    circuit, warnings = elaborate_project(*resolve_project(ROOT / "examples/blink"))
    assert not warnings
    assert check_circuit(circuit) == ()
