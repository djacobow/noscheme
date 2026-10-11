"""Hover shows resolved defaults, actual instances, and metadata provenance."""

from pathlib import Path
import pytest

from noscheme.lsp_analysis import analyze
from noscheme.lsp_hover import BUILTIN_HELP, format_hover
from noscheme.metadata import ROLES, NETTYPES, USE_POLICIES
from test_resolve import make_project

LIBRARY = """from noscheme import role, use
component Resistor:
  params:
    resistance value
  pins:
    1 (terminal):
      role: role.passive
      use: use.required
  metadata:
    ref_prefix: "R"
    footprint: "Test:0603"
    custom:
      kicad_footprint_candidates: ["Test:0603", "Test:0805"]
extend Resistor<value: 4.7k> Pullup
"""

SOURCE = """import parts
from noscheme import net, role
block B:
  net.passive power
  parts.Pullup<value: "10kOhm"> resistors[2]:
    terminal: power
    metadata:
      footprint: "Test:0805"
"""


def setup(tmp_path, source=SOURCE, library=LIBRARY):
    make_project(tmp_path, source, {"parts": library})
    path = tmp_path / "main.nosch"
    return path, analyze(path, tmp_path, {path: source})


def hover(result, path, source, needle):
    ref = result.reference_at(path, source.index(needle))
    assert ref, needle
    return ref.description


def test_inherited_definition_defaults_and_metadata(tmp_path):
    path, result = setup(tmp_path)
    text = hover(result, path, SOURCE, "parts.Pullup")
    assert "component parts.Pullup" in text
    assert "Extends: Resistor" in text
    assert "Default value: 4.7kOhm" in text
    assert "footprint: Test:0603 (from parts, parts.nosch:" in text
    assert "2 saved candidates" in text
    assert "10kOhm" not in text
    required = hover(result, tmp_path / "parts.nosch", LIBRARY, "Resistor:")
    assert "Required; no default." in required


def test_instance_value_override_and_array_expansion(tmp_path):
    path, result = setup(tmp_path)
    text = hover(result, path, SOURCE, "resistors[")
    assert "parts.Pullup resistors" in text
    assert "Elaborated instances: 2" in text
    assert "Instance: resistors[0]" in text
    assert "Instance: resistors[1]" in text
    assert "Value: 10kOhm" in text
    assert "Footprint: Test:0805" in text
    assert "Parameter value: 10kOhm" in text


def test_pin_alias_roles_unused_and_net_details(tmp_path):
    path, result = setup(tmp_path)
    text = hover(result, path, SOURCE, "terminal:")
    assert "component Pullup: 1" in text
    assert "role: passive (from parts," in text
    assert "use: required" in text
    assert "aliases: terminal" in text
    assert "Effective role: passive; net: power" in text
    net = hover(result, path, SOURCE, "power\n")
    assert "Type: net.passive" in net
    assert "power: 2 endpoints" in net


def test_effective_pin_role_is_distinct_from_declared_options(tmp_path):
    library = LIBRARY.replace(
        "role: role.passive", "role: [role.digital_input, role.digital_output]"
    )
    source = SOURCE.replace(
        'footprint: "Test:0805"',
        """footprint: "Test:0805"
      pins:
        terminal:
          role: role.digital_input""",
    )
    path, result = setup(tmp_path, source, library)
    text = hover(result, path, source, "terminal:")
    assert "role: digital_input, digital_output" in text
    assert "Effective role: digital_input" in text


def test_unsaved_inherited_metadata_and_bounded_custom_lists(tmp_path):
    path, _ = setup(tmp_path)
    library = LIBRARY.replace("Test:0603", "Test:New").replace(
        '["Test:New", "Test:0805"]', "[" + ", ".join(f'"Test:F{i}"' for i in range(500)) + "]"
    )
    result = analyze(path, tmp_path, {path: SOURCE, tmp_path / "parts.nosch": library})
    text = hover(result, path, SOURCE, "parts.Pullup")
    assert "footprint: Test:New" in text
    assert "500 saved candidates" in text
    assert len(text) < 2000
    assert "Test:New" not in (tmp_path / "parts.nosch").read_text()


def test_evaluation_failure_keeps_declared_hover_without_claiming_actual_values(tmp_path):
    source = SOURCE.replace('value: "10kOhm"', "value: 1 / 0")
    path, result = setup(tmp_path, source)
    assert any(d.severity == "error" for d in result.diagnostics)
    text = hover(result, path, source, "resistors[")
    assert "Instance values unavailable; definition defaults follow." in text
    assert "Default value: 4.7kOhm" in text
    assert "Value: 2" not in text


def test_reference_board_led_resistor_instance_value():
    path = Path(__file__).resolve().parents[1] / "examples/stm32h523/components.nosch"
    source = path.read_text()
    result = analyze(path, path.parent, {path: source})
    text = hover(result, path, source, "resistor(drive")
    assert "Value: 1kOhm" in text
    assert "Footprint: Resistor_SMD:R_0603_1608Metric" in text
    assert "Elaborated instances: 3" in text


def test_custom_hint_convention_is_not_required_to_be_a_list(tmp_path):
    library = LIBRARY.replace('["Test:0603", "Test:0805"]', "42")
    path, result = setup(tmp_path, library=library)
    assert "custom.kicad_footprint_candidates: 42" in hover(result, path, SOURCE, "parts.Pullup")


def test_builtin_help_covers_enum_registries():
    for namespace, members in (("role", ROLES), ("net", NETTYPES), ("use", USE_POLICIES)):
        assert set(BUILTIN_HELP[namespace]) == members


@pytest.mark.parametrize(
    "import_line,prefix",
    [
        ("from noscheme import role", "role"),
        ("from noscheme import role as electrical", "electrical"),
        ("import noscheme", "noscheme.role"),
    ],
)
def test_builtin_hover_respects_imports_without_navigation(tmp_path, import_line, prefix):
    library = LIBRARY.replace(
        "from noscheme import role, use", import_line + "\nfrom noscheme import use"
    ).replace("role.passive", prefix + ".passive")
    _, result = setup(tmp_path, library=library)
    path = tmp_path / "parts.nosch"
    offset = library.index(prefix + ".passive") + len(prefix) + 1
    assert "role.passive\nPassive contact" in result.hover_at(path, offset).description
    assert result.reference_at(path, offset) is None
    assert result.references_at(path, offset) == []
    unused_offset = library.index("use.required")
    assert "Must be connected" in result.hover_at(path, unused_offset).description


def test_nettype_hover_and_non_code_text(tmp_path):
    source = SOURCE.replace("net.passive", "net.tristate") + "// net.tristate\n"
    path, result = setup(tmp_path, source)
    assert (
        "tristate_capable: true" in result.hover_at(path, source.index("net.tristate")).description
    )
    assert result.hover_at(path, source.rindex("net.tristate")) is None
    library = LIBRARY.replace('"Test:0603"', '"role.passive"')
    _, result = setup(tmp_path, library=library)
    assert result.hover_at(tmp_path / "parts.nosch", library.rindex("role.passive")) is None


def test_unimported_builtin_has_no_hover(tmp_path):
    source = SOURCE.replace("from noscheme import net, role\n", "")
    path, result = setup(tmp_path, source)
    assert result.hover_at(path, source.index("net.passive")) is None


def test_hover_formatting_escapes_source_text():
    description = "Pullup resistors[0]\nValue: *text* <b>\n\nInstance: second"
    text = format_hover(description, markdown=True)
    assert text.startswith("**Pullup resistors\\[0\\]**\n\n")
    assert "- Value: \\*text\\* \\<b\\>" in text
    assert "\n\n- Instance: second" in text
    assert format_hover(description).startswith("Pullup resistors[0]\n\nValue:")
