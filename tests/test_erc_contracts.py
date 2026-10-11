"""Behavioral cases for electrical domains and hierarchical port boundaries."""

import pytest

from noscheme.diagnostics import DiagnosticError

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
        ("idle_high", "pull_low", True),
        ("idle_high", "digital_input", True),
        ("idle_high", "digital_output", False),
        ("idle_high", "digital_bidirectional", False),
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
    "nettype", ["digital", "analog", "idle_high", "bidirectional", "power", "ground", "passive"]
)
def test_passive_contacts_do_not_impose_a_domain(tmp_path, nettype):
    assert "incompatible_role" not in codes(pair(tmp_path, "passive", "passive", nettype))


def test_pull_low_does_not_conflict_with_other_pull_low(tmp_path):
    assert codes(pair(tmp_path, "pull_low", "pull_low", "idle_high")) == ["missing_pull"]
    assert "multiple_drivers" in codes(pair(tmp_path, "digital_output", "pull_low"))


def test_selectable_roles_are_not_guessed(tmp_path):
    source = component("digital_input").replace(
        "role: role.digital_input", "role: [role.digital_input, role.analog_input]"
    )
    c = compile_source(
        tmp_path,
        source
        + """block B:
  net.digital n
  P p[2]:
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
    net.{inner_type} signal:
      role: role.{contract}
      use: use.required
  {internal} p:
    1: signal
block Forward:
  ports:
    net.{inner_type} signal:
      role: role.{contract}
      use: use.required
  W child:
    signal: signal
block B:
  net.{outer_type} n
  Forward w:
    signal: n
  Receiver receiver:
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
    source = hierarchy(internal="Receiver").replace("Receiver receiver", "Driver receiver")
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
    assert "unchecked_net" not in codes(c)


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
        ("", "optional", None),
        ("signal: no_connect", "explicit", None),
        ("signal: no_connect", "required", "required_port_connection"),
        ("signal: n", "required", "required_port_connection"),
    ],
)
def test_port_connection_contracts_need_an_outside_endpoint(tmp_path, binding, policy, expected):
    clause = "    " + binding + "\n" if binding else ""
    source = (
        component("passive")
        + f"""block W:
  ports:
    net.passive signal:
      use: use.{policy}
  P p[2]:
    1: signal
block B:
  net.passive n:
    placeholder: true
  W w{":" if clause else ""}
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
    net.digital signal
block B:
  net.digital n
  External e:
    signal: n
    metadata:
      pins:
        signal:
          external_role: role.digital_output
  P p:
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
    source = hierarchy().replace("Forward w", "Forward w[2]")
    c = compile_source(tmp_path, source)
    assert codes(c) == ["multiple_drivers"]
    d = check_circuit(c)[0]
    assert len(d.related) == 2
    assert {r.message for r in d.related} == {"w[0].child.p.1", "w[1].child.p.1"}


def test_bundle_array_port_contracts_are_checked_per_leaf(tmp_path):
    source = (
        PARTS
        + """bundle Bus:
  net.digital signal:
    role: role.digital_output
    use: use.required
block W:
  ports:
    Bus bus[2]
  Driver driver[2]:
    1: bus[0:2].signal
block B:
  Bus bus[2]
  W w:
    bus: bus
  Receiver receiver[2]:
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
        "role: role.digital_output\n      use: use.explicit",
        "role: [role.digital_output, role.digital_input]\n      use: use.explicit",
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
                f"""block B:
  net.digital n:
    waivers:
      {check}: "Not allowed"
""",
            )
        assert exc.value.diagnostics[0].code == "metadata.waiver"


@pytest.mark.parametrize("rail,source_role", [("power", "power_output"), ("ground", "ground")])
def test_explicit_input_ports_accept_rail_ties_through_nested_blocks(tmp_path, rail, source_role):
    source = hierarchy(contract="digital_input", internal="Receiver", outer_type=rail)
    source = source.replace("Receiver receiver", "Driver receiver")
    source = source.replace("role: role.digital_output", f"role: role.{source_role}")
    c = compile_source(tmp_path, source)
    assert codes(c) == []


def test_rail_tie_requires_explicit_input_contract_at_every_public_boundary(tmp_path):
    source = hierarchy(contract="digital_input", internal="Receiver", outer_type="ground")
    source = source.replace("Receiver receiver", "Driver receiver")
    source = source.replace("role: role.digital_output", "role: role.ground")
    source = source.replace(
        "net.digital signal:\n      role: role.digital_input\n",
        "net.digital signal:\n",
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


def test_unchecked_report_counts_nets_once_across_configs_and_declarations(tmp_path):
    from noscheme.erc import report_files

    source = hierarchy(outer_type="unchecked").replace(
        "block B:",
        '''block B:
  metadata:
    erc_choices:
      mode: ["first", "second"]
    configurations:
      first:
        mode: "first"
      second:
        mode: "second"''',
    )
    circuit = compile_source(tmp_path, source)
    assert len(circuit["nets"][0]["declarations"]) > 1
    diagnostics = apply_erc(circuit)
    assert not diagnostics
    report = circuit["erc"]
    assert len(report["configurations"]) == 2
    assert report["unchecked_nets"] == 1
    assert report["errors"] == report["warnings"] == 0
    assert "Explicitly unchecked nets: 1\n" in report_files(circuit, tmp_path)[tmp_path / "erc.txt"]


def test_inner_unchecked_is_not_counted_as_an_unchecked_outer_net(tmp_path):
    circuit = compile_source(tmp_path, hierarchy(inner_type="unchecked", internal="Receiver"))
    diagnostics = apply_erc(circuit)
    assert any(d.code == "erc.missing_driver" for d in diagnostics)
    assert circuit["erc"]["unchecked_nets"] == 0


@pytest.mark.parametrize(
    "binding,forbidden", [("", False), ("signal: no_connect", False), ("signal: n", True)]
)
def test_disallowed_port_policy(tmp_path, binding, forbidden):
    source = """block W:
  ports:
    net.passive signal:
      use: use.disallowed
      placeholder: true
block B:
  net.passive n:
    placeholder: true
  W w""" + (f":\n    {binding}\n" if binding else "\n")
    found = codes(compile_source(tmp_path, source))
    assert ("do_not_connect" in found) is forbidden
    assert "unused_port" not in found


@pytest.mark.parametrize("policy", ["use.optional", "use.explicit", "use.required"])
def test_instance_cannot_override_disallowed_policy(tmp_path, policy):
    source = (
        component(use="disallowed")
        + f"""block B:
  P p:
    metadata:
      pins:
        1:
          use: {policy}
"""
    )
    with pytest.raises(DiagnosticError):
        compile_source(tmp_path, source)
