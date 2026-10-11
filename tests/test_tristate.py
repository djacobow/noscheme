"""Tri-state ERC checks capabilities without modeling bus arbitration."""

import pytest

from noscheme.diagnostics import DiagnosticError
from noscheme.erc import check_circuit
from test_circuit import compile_source
from test_erc import pair, codes

CAPABLE = "      tristate_capable: true\n"


@pytest.mark.parametrize(
    "role", ["digital_output", "digital_bidirectional", "pull_low", "pull_high"]
)
@pytest.mark.parametrize("capable", [True, False])
def test_shared_bus_requires_explicit_capability(tmp_path, role, capable):
    c = pair(tmp_path, role, "digital_input", "tristate", CAPABLE if capable else "")
    assert codes(c) == ([] if capable else ["tristate_capability"])
    if not capable:
        d = check_circuit(c)[0]
        assert d.instance_path == "a.1"
        assert d.related and d.span.filename.endswith("main.nosch")


@pytest.mark.parametrize(
    "role", ["digital_output", "digital_bidirectional", "pull_low", "pull_high"]
)
def test_multiple_capable_drivers_are_allowed(tmp_path, role):
    assert codes(pair(tmp_path, "digital_output", role, "tristate", CAPABLE, CAPABLE)) == []


@pytest.mark.parametrize("role", ["passive", "digital_input"])
def test_inputs_and_passive_attachments_need_no_capability(tmp_path, role):
    assert codes(pair(tmp_path, role, "digital_input", "tristate")) == []


@pytest.mark.parametrize("role", ["analog_input", "analog_output", "power_output", "ground"])
def test_flag_does_not_allow_other_domains(tmp_path, role):
    assert "incompatible_role" in codes(pair(tmp_path, role, "digital_input", "tristate", CAPABLE))


def test_capability_does_not_relax_ordinary_digital_net(tmp_path):
    assert codes(
        pair(tmp_path, "digital_output", "digital_output", "digital", CAPABLE, CAPABLE)
    ) == ["multiple_drivers"]


SOURCE = """component Contact:
  pins:
    1:
      role: role.passive
      use: use.required
block B:
  net.tristate bus
  Contact connectors[2]:
    1: bus
    metadata:
      pins:
        1:
          role: role.digital_output
          tristate_capable: true
"""


def test_arrayed_external_drivers_use_instance_capability(tmp_path):
    assert codes(compile_source(tmp_path, SOURCE)) == []
    c = compile_source(
        tmp_path, SOURCE.replace("tristate_capable: true", "tristate_capable: false")
    )
    assert codes(c).count("tristate_capability") == 2


def test_capability_requires_boolean(tmp_path):
    with pytest.raises(DiagnosticError):
        compile_source(
            tmp_path, SOURCE.replace("tristate_capable: true", 'tristate_capable: "yes"')
        )


def test_public_tristate_port_checks_internal_driver(tmp_path):
    source = """component Driver:
  pins:
    1:
      role: role.digital_output
      tristate_capable: true
block Device:
  ports:
    net.tristate bus
  Driver output:
    1: bus
block B:
  net.tristate bus
  Device devices[2]:
    bus: bus
"""
    assert codes(compile_source(tmp_path, source)) == []
    assert (
        codes(
            compile_source(
                tmp_path, source.replace("tristate_capable: true", "tristate_capable: false")
            )
        ).count("tristate_capability")
        == 2
    )


def test_unknown_roles_warn_instead_of_becoming_passive(tmp_path):
    assert codes(pair(tmp_path, "unchecked", "digital_input", "tristate")) == ["incomplete_roles"]


def test_capability_inheritance_and_override(tmp_path):
    source = (
        SOURCE.replace(
            "block B:",
            """extend Contact TriContact:
  pins:
    1:
      tristate_capable: true
block B:""",
        )
        .replace("Contact connectors[2]:", "TriContact connectors[2]:")
        .replace("          tristate_capable: true\n", "")
    )
    assert codes(compile_source(tmp_path, source)) == []
    source = source.replace(
        "role: role.digital_output",
        "role: role.digital_output\n          tristate_capable: false",
    )
    assert codes(compile_source(tmp_path, source)).count("tristate_capability") == 2


def test_capability_waiver_is_reported(tmp_path):
    from noscheme.erc import apply_erc

    source = SOURCE.replace("tristate_capable: true", "tristate_capable: false").replace(
        "  net.tristate bus\n",
        '  net.tristate bus:\n    waivers:\n      tristate_capability: "Reviewed external equipment."\n',
    )
    c = compile_source(tmp_path, source)
    apply_erc(c)
    waiver = next(w for w in c["erc"]["waivers"] if w["check"] == "tristate_capability")
    assert len(waiver["diagnostics"]) == 2


def test_named_configurations_use_selected_roles(tmp_path):
    from noscheme.erc import apply_erc

    source = (
        SOURCE.replace(
            "block B:",
            '''block B:
  metadata:
    erc_choices:
      mode: ["input", "output"]
    configurations:
      receiving:
        mode: "input"
      transmitting:
        mode: "output"''',
        )
        .replace(
            "role: role.digital_output",
            "role: choice(mode, input=role.digital_input, output=role.digital_output)",
        )
        .replace("tristate_capable: true", "tristate_capable: false")
    )
    c = compile_source(tmp_path, source)
    apply_erc(c)
    results = {r["name"]: r for r in c["erc"]["configurations"]}
    assert results["receiving"]["errors"] == 0
    assert results["transmitting"]["errors"] >= 2
