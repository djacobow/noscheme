from copy import deepcopy
import json

import pytest

from noscheme.circuit import elaborate_project
from noscheme.cli import main
from noscheme.diagnostics import DiagnosticError, render_text
from noscheme.erc import apply_erc, report_files
from noscheme.erc_config import configuration_views
from noscheme.kicad.export import export_project
from noscheme.resolve import resolve_project
from test_circuit import ROOT, make

SOURCE = (
    (ROOT / "examples/power_modes/main.nosch").read_text().replace("board PowerModes:", "board B:")
)


def project(tmp_path, source=SOURCE):
    make(tmp_path, "")
    (tmp_path / "main.nosch").write_text(source)
    resolved = resolve_project(tmp_path)
    return resolved[0], elaborate_project(*resolved)[0]


def snapshot(tmp_path):
    path = tmp_path / "footprints.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "footprints": {
                    "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical": {
                        "exists": True,
                        "pads": ["1", "2"],
                    }
                },
            }
        )
    )
    return path


def test_checks_every_configuration_without_mutating_connectivity(tmp_path):
    _, circuit = project(tmp_path)
    before = deepcopy(circuit)
    assert apply_erc(circuit) == ()
    assert [c["name"] for c in circuit["erc"]["configurations"]] == [
        "header_powered",
        "usb_powered",
    ]
    for key in ("parts", "nets", "blocks", "erc_choice_scopes", "erc_configurations"):
        assert circuit[key] == before[key]
    for name, view, _ in configuration_views(circuit):
        selected = {
            p["path"][0]: p["pins"]["1"]["metadata"]["external_role"]["value"]
            for p in view["parts"]
        }
        assert selected["usb"] == (["power_output"] if name == "usb_powered" else ["passive"])
        assert selected["expansion"] == (
            ["power_input"] if name == "usb_powered" else ["power_output"]
        )
    first = deepcopy(circuit["erc"])
    apply_erc(circuit)
    assert circuit["erc"] == first


def test_subset_selection_and_unknown_configuration(tmp_path):
    _, circuit = project(tmp_path)
    assert apply_erc(circuit, ["usb_powered", "usb_powered"]) == ()
    assert [c["name"] for c in circuit["erc"]["configurations"]] == ["usb_powered"]
    with pytest.raises(DiagnosticError) as exc:
        apply_erc(circuit, ["typo"])
    assert exc.value.diagnostics[0].code == "erc.unknown_configuration"


def test_arrays_keep_separate_local_choices_and_report_conflicts_per_configuration(tmp_path):
    _, circuit = project(tmp_path, SOURCE.replace("Supply expansion", "Supply expansion[2]"))
    diagnostics = apply_erc(circuit)
    conflict = next(d for d in diagnostics if d.code == "erc.multiple_drivers")
    assert conflict.configuration == "header_powered"
    assert len(conflict.related) == 2
    assert "[header_powered]" in render_text(diagnostics)
    configurations = circuit["erc"]["configurations"]
    assert configurations[1]["errors"] == 0
    assert len(configurations[0]["choices"]) == 4
    text = report_files(circuit, tmp_path)[tmp_path / "erc.txt"]
    assert "Configuration usb_powered: 0 errors" in text
    assert '"power_mode": "source"' in text


def test_unknown_choice_is_not_guessed_when_forwarding_missing(tmp_path):
    # Remove just the USB instance choice binding; the block retains its public
    # alternatives, so both configurations must report incomplete source roles.
    start = SOURCE.index("    metadata:\n      erc_choices:", SOURCE.index("  Supply usb:"))
    end = SOURCE.index("  Supply expansion:", start)
    _, circuit = project(tmp_path, SOURCE[:start] + SOURCE[end:])
    diagnostics = apply_erc(circuit)
    assert {d.configuration for d in diagnostics if d.code == "erc.incomplete_roles"} == {
        "header_powered",
        "usb_powered",
    }


@pytest.mark.parametrize(
    "old,new,code",
    [
        ('power_source: "usb"', 'power_source: "invalid"', "erc.choice_value"),
        ('power_source: "usb"', 'private_connector: "usb"', "erc.unknown_choice"),
        ("choice(power_mode,", "choice(private.power_mode,", "metadata.erc"),
        ("choice(power_mode,", "choice(typo,", "erc.unknown_choice"),
        ("absent=role.passive", "typo=role.passive", "erc.choice_cases"),
        ('header="absent"', 'header="invalid"', "erc.choice_value"),
        ('["usb", "header"]', '["usb", "usb"]', "metadata.erc"),
        ('["usb", "header"]', "[]", "metadata.erc"),
        ("usb_powered:", '"usb.private":', "metadata.erc"),
    ],
)
def test_invalid_choice_definitions_are_source_errors(tmp_path, old, new, code):
    with pytest.raises(DiagnosticError) as exc:
        project(tmp_path, SOURCE.replace(old, new, 1))
    assert exc.value.diagnostics[0].code == code


def test_conditional_role_cannot_override_component_allowed_roles(tmp_path):
    # A pin that declares active roles selects among them; a choice cannot add one.
    with pytest.raises(DiagnosticError) as exc:
        project(
            tmp_path,
            SOURCE.replace(
                "1..2: [role.passive, unused.never]",
                "1..2: [role.ground, role.passive, unused.never]",
            ),
        )
    assert exc.value.diagnostics[0].code == "metadata.constraint"


def test_nested_public_choice_forwarding(tmp_path):
    wrapper = """block Wrapper:
  ports:
    net.power supply
  metadata:
    erc_choices:
      power_mode: ["source", "load", "absent"]
  Supply inner:
    supply: supply
    metadata:
      erc_choices:
        power_mode: choice(power_mode, source="source", load="load", absent="absent")

"""
    source = SOURCE.replace("board B:", wrapper + "board B:").replace("Supply usb", "Wrapper usb")
    _, circuit = project(tmp_path, source)
    assert apply_erc(circuit) == ()
    scopes = circuit["erc"]["configurations"][0]["choices"]
    assert (
        next(s for s in scopes if s["scope"] == ["usb", "inner"])["selections"]["power_mode"]
        == "absent"
    )


def test_export_preserves_netlist_and_state_across_configurations(tmp_path):
    loaded, circuit = project(tmp_path)
    records = snapshot(tmp_path)
    export_project(loaded, circuit, records, configurations=["usb_powered"])
    netlist = (tmp_path / "build/board.net").read_bytes()
    state = (tmp_path / "noscheme.state.json").read_bytes()
    report = export_project(loaded, circuit, records, configurations=["header_powered"])
    assert (tmp_path / "build/board.net").read_bytes() == netlist
    assert (tmp_path / "noscheme.state.json").read_bytes() == state
    assert report["changes"] == {"added": [], "removed": [], "changed": []}
    assert [c["name"] for c in report["erc_configurations"]] == ["header_powered"]


def test_cli_configuration_errors_preserve_existing_artifacts(tmp_path, capsys):
    project(tmp_path)
    args = ["compile", "--project", str(tmp_path)]
    assert main(args) == 0
    before = (tmp_path / "build/erc.json").read_bytes()
    assert main(args + ["--erc-configuration", "typo"]) == 1
    assert (tmp_path / "build/erc.json").read_bytes() == before
    assert main(args + ["--erc-configuration", "usb_powered"]) == 0
    report = json.loads((tmp_path / "build/erc.json").read_text())
    assert [c["name"] for c in report["configurations"]] == ["usb_powered"]


def test_fixed_child_selection_does_not_depend_on_board_configuration(tmp_path):
    start = SOURCE.index("        power_mode:", SOURCE.index("  Supply usb:"))
    end = SOURCE.index("  Supply expansion:", start)
    source = SOURCE[:start] + '        power_mode: "source"\n' + SOURCE[end:]
    _, circuit = project(tmp_path, source)
    diagnostics = apply_erc(circuit)
    assert any(
        d.code == "erc.multiple_drivers" and d.configuration == "header_powered"
        for d in diagnostics
    )
    assert not any(d.configuration == "usb_powered" for d in diagnostics)


def test_unnamed_configurations_check_every_combination(tmp_path):
    start = SOURCE.index("    configurations:")
    end = SOURCE.index("  global net.ground gnd", start)
    _, circuit = project(tmp_path, SOURCE[:start] + SOURCE[end:])
    _, named = project(tmp_path, SOURCE)
    diagnostics = apply_erc(circuit)
    assert [c["name"] for c in circuit["erc"]["configurations"]] == [
        "power_source=header",
        "power_source=usb",
    ]
    assert [(d.code, d.configuration) for d in diagnostics] == [
        (d.code, d.configuration.replace("header_powered", "power_source=header"))
        for d in apply_erc(named)
    ]


def test_no_choices_run_one_unconfigured_check(tmp_path):
    start = SOURCE.index("board B:")
    board = SOURCE[start:]
    board = (
        board[: board.index("    erc_choices:")] + board[board.index("  global net.ground gnd") :]
    )
    board = board.replace(
        'power_mode: choice(power_source, usb="source", header="absent")', 'power_mode: "source"'
    ).replace('power_mode: choice(power_source, usb="load", header="source")', 'power_mode: "load"')
    _, circuit = project(tmp_path, SOURCE[:start] + board.replace("  metadata:\n", "", 1))
    apply_erc(circuit)
    assert [c["name"] for c in circuit["erc"]["configurations"]] == [None]


def test_too_many_combinations_require_named_configurations(tmp_path):
    start = SOURCE.index("    configurations:")
    end = SOURCE.index("  global net.ground gnd", start)
    choices = "".join(f'      extra{i}: ["a", "b"]\n' for i in range(6))
    source = SOURCE[:start] + choices + SOURCE[end:]
    with pytest.raises(DiagnosticError) as exc:
        project(tmp_path, source)
    assert exc.value.diagnostics[0].code == "erc.configurations"


def test_named_configuration_can_leave_choices_unselected(tmp_path):
    source = SOURCE.replace(
        'power_source: ["usb", "header"]',
        'power_source: ["usb", "header"]\n      other: ["x", "y"]',
    )
    source = source.replace('power_source: "usb"', 'other: "x"', 1)
    _, circuit = project(tmp_path, source)
    diagnostics = apply_erc(circuit)
    assert {d.configuration for d in diagnostics if d.code == "erc.incomplete_roles"} == {
        "usb_powered"
    }


def test_conditional_pin_role_selects_only_definition_permitted_roles(tmp_path):
    source = """from noscheme import role, net, unused
component Pin:
  pins:
    1:
      role: [role.digital_input, role.digital_output]
      unused: unused.never
board B:
  metadata:
    erc_choices:
      direction: ["input", "output"]
    configurations:
      receive:
        direction: "input"
      transmit:
        direction: "output"
  net.digital n
  Pin p:
    1: n
    metadata:
      pins:
        1: choice(direction, input=role.digital_input, output=role.digital_output)
  Pin load:
    1: n
    metadata:
      pins:
        1:
          role: role.digital_input
"""
    _, circuit = project(tmp_path, source)
    diagnostics = apply_erc(circuit)
    assert [(d.code, d.configuration) for d in diagnostics] == [("erc.missing_driver", "receive")]


def test_waived_diagnostics_retain_configuration_names(tmp_path):
    source = SOURCE.replace("Supply expansion", "Supply expansion[2]")
    source = source.replace(
        "  net.power v5\n",
        '  net.power v5:\n    waivers:\n      multiple_drivers: "Bench supply arrangement explicitly reviewed"\n',
    )
    _, circuit = project(tmp_path, source)
    assert apply_erc(circuit) == ()
    waived = [w for w in circuit["erc"]["waivers"] if w["diagnostics"]]
    assert len(waived) == 1
    assert waived[0]["configuration"] == "header_powered"
    assert waived[0]["diagnostics"][0]["configuration"] == "header_powered"


def test_cli_checks_all_but_can_select_a_clean_configuration(tmp_path, capsys):
    project(tmp_path, SOURCE.replace("Supply expansion", "Supply expansion[2]"))
    assert main(["check", "--project", str(tmp_path)]) == 1
    assert main(["check", "--project", str(tmp_path), "--erc-configuration", "usb_powered"]) == 0
    records = snapshot(tmp_path)
    assert main(["export-kicad", "--project", str(tmp_path), "--footprints", str(records)]) == 0
    report = json.loads((tmp_path / "build/export.json").read_text())
    assert report["erc_errors"] == 1
    assert len(report["erc_configurations"]) == 2


def test_configuration_selects_public_port_contract_as_well_as_private_roles(tmp_path):
    # The public promise is selected in board scope; private connector behavior
    # is selected independently through the block's public choice binding.
    source = SOURCE.replace(
        """        power_mode: choice(power_source, usb="source", header="absent")
""",
        """        power_mode: choice(power_source, usb="source", header="absent")
      pins:
        supply: choice(power_source, usb=role.power_output, header=role.power_output)
""",
        1,
    )
    _, circuit = project(tmp_path, source)
    diagnostics = apply_erc(circuit)
    assert [(d.code, d.configuration) for d in diagnostics] == [("erc.port_role", "header_powered")]
    _, corrected = project(
        tmp_path, source.replace("header=role.power_output)", "header=role.passive)")
    )
    assert apply_erc(corrected) == ()


def test_specialized_blocks_preserve_choice_bindings_in_inherited_body(tmp_path):
    source = SOURCE.replace(
        "board B:",
        """extend Supply SpecializedSupply:
  metadata:
    custom:
      note: "Same choice contract and private implementation"

board B:""",
    ).replace("Supply usb", "SpecializedSupply usb")
    _, circuit = project(tmp_path, source)
    assert apply_erc(circuit) == ()


def test_configuration_plugins_cannot_change_shared_data_and_next_run_sees_edits(tmp_path):
    from noscheme.erc_registry import Application, load_registry

    _, circuit = project(tmp_path)
    registry = load_registry()
    seen = []

    def inspect(ctx):
        if ctx.pin.label == "usb.connector.1":
            seen.append((ctx.circuit["erc_configuration"], ctx.pin.attributes["external_role"]))
        # Test nested records, including unchanged records shared by configurations.
        with pytest.raises(TypeError):
            ctx.pin["metadata"]["role"]["value"][0] = "power_output"
        with pytest.raises(TypeError):
            ctx.pin["metadata"]["extra"] = {}
        return ()

    registry.pin_rule("test.inspect", inspect)
    registry.rule_set(
        "test.inspect", lambda ctx: [Application("inspect", "test.inspect")] if ctx.pin else []
    )
    circuit["erc_rule_sets"].append("test.inspect")
    before = deepcopy(circuit)
    assert apply_erc(circuit, registry=registry) == ()
    assert seen == [("header_powered", ("passive",)), ("usb_powered", ("power_output",))]
    assert circuit["parts"] == before["parts"]
    # Changing the source after ERC must not hit an old freeze memo.
    source = circuit["erc_configurations"]["usb_powered"]["value"]["power_source"]
    source["value"] = "header"
    seen.clear()
    assert apply_erc(circuit, registry=registry) == ()
    assert seen == [("header_powered", ("passive",)), ("usb_powered", ("passive",))]
