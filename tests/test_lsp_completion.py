"""Completion must work on unfinished editor buffers without changing source."""

import pytest
from noscheme.lsp_completion import completion_items
from test_circuit import make

PRELUDE = """from noscheme import role, nettype
component Receiver:
  pins:
    01 (signal, rx#):
      role: role.digital_input
    "odd-pin":
      role: role.passive
component Specialized extends Receiver:
  metadata:
    custom:
      note: "Inherited interface"
board B:
"""


def complete(tmp_path, source):
    offset = source.index("|")
    source = source.replace("|", "")
    make(tmp_path, "")
    path = tmp_path / "main.nosch"
    result = completion_items(path, tmp_path, {path: source}, offset)
    return {s.label: s for s in result}


def test_partial_component_type(tmp_path):
    suggestions = complete(tmp_path, PRELUDE + "  inst r: Rec|\n")
    assert set(suggestions) == {"Receiver"}
    assert suggestions["Receiver"].text == "Receiver"


def test_inline_pin_and_parameter_completion(tmp_path):
    source = PRELUDE.replace("  pins:", "  params:\n    value: number = 1\n  pins:", 1)
    suggestions = complete(tmp_path, source + "  inst r: Receiver(si|)")
    assert suggestions["signal"].text == "signal: "
    suggestions = complete(tmp_path, source + "  inst r: Receiver(01: net, va|)")
    assert suggestions["value"].text == "value="
    suggestions = complete(tmp_path, source + "  inst r: Receiver(01: net,\n    va|lue=2)")
    assert suggestions["value"].text == "value"
    assert not complete(tmp_path, source + "  inst r: Receiver(value=si|)")
    assert not complete(tmp_path, source + "  inst r: Receiver(01: net)\n  si|")


@pytest.mark.parametrize("argument", ["bus.", "01: bus.", "bus[0:1]."])
def test_inline_bundle_member_completion(tmp_path, argument):
    source = PRELUDE.replace(
        "board B:",
        """bundle Bus:
  data: nettype.digital
board B:
  net bus: Bus[2]""",
    )
    source += "\n  inst r: Receiver(" + argument + "|)"
    suggestions = complete(tmp_path, source)
    assert suggestions["data"].text == "data"
    assert source[suggestions["data"].start] == "|"


def test_incomplete_connection_section_inherited_aliases(tmp_path):
    suggestions = complete(tmp_path, PRELUDE + "  inst r: Specialized\n    |")
    assert set(suggestions) == {"01", "signal", "rx#", "odd-pin"}
    assert suggestions["odd-pin"].text == '"odd-pin"'
    assert suggestions["01"].text == "01"


def test_pin_replaces_whole_token_and_preserves_colon(tmp_path):
    suggestions = complete(
        tmp_path,
        PRELUDE + "  inst r: Receiver\n    si|gnal: net\n",
    )
    item = suggestions["signal"]
    assert item.end - item.start == len("signal")


def test_array_element_pin_prefix(tmp_path):
    suggestions = complete(tmp_path, PRELUDE + "  inst r[2]: Receiver\n    [0].rx|")
    assert set(suggestions) == {"rx#"}


def test_no_pin_completion_in_params_or_after_closed_instance(tmp_path):
    assert not complete(tmp_path, PRELUDE + "  inst r: Receiver\n    params:\n      si|")
    assert not complete(tmp_path, PRELUDE + "  inst r: Receiver()\n  si|")
    assert not complete(tmp_path, PRELUDE + "  inst r: Receiver\n    signal: si|")


def test_builtin_namespace_uses_registry_and_explicit_import(tmp_path):
    suggestions = complete(tmp_path, PRELUDE + "  net data: nettype.tri|")
    assert set(suggestions) == {"tristate"}
    source = "from noscheme import role\ncomponent C:\n  pins:\n    1:\n      role: role.digital_|"
    source += "\nboard B:\n  net n: nettype.passive\n"
    assert set(complete(tmp_path, source)) == {
        "digital_input",
        "digital_output",
        "digital_bidirectional",
    }
    assert not complete(tmp_path, source.replace("from noscheme import role\n", ""))


def test_comments_and_strings_do_not_offer_completion(tmp_path):
    for line in [
        "  // role.|",
        "  /* role.|",
        "  // inst r: Rec|",
        '  net n: "role.|"',
        '  net n: "role.|',
    ]:
        assert not complete(tmp_path, PRELUDE + line)


def test_imported_components_and_aliases_use_unsaved_library(tmp_path):
    (tmp_path / "lib.nosch").write_text(
        "component Other:\n  pins:\n    old:\n      unused: unused.always\n"
    )
    make(tmp_path, "")
    path = tmp_path / "main.nosch"
    source = "from lib import Other\nboard B:\n  inst r: Other\n    "
    library = (
        "from noscheme import role\ncomponent Other:\n  pins:\n    new:\n      role: role.passive\n"
    )
    buffers = {path: source, tmp_path / "lib.nosch": library}
    result = completion_items(path, tmp_path, buffers, len(source))
    assert {s.label for s in result} == {"new"}
    assert "old" in (tmp_path / "lib.nosch").read_text()


FOOTPRINTS = """component Part:
  pins:
    1:
      role: role.passive
  metadata:
    footprint: "Test:Z_default"
    custom:
      kicad_footprint_candidates: ["Test:A", "Test:Z_default"]
component Derived extends Part:
  metadata:
    custom:
      note: "inherited"
"""


def test_footprint_strings_in_incomplete_instance(tmp_path):
    prelude = "from noscheme import role\n" + FOOTPRINTS + "board B:\n"
    for value in ['"|', '"Test:|"', '"Test:|old"', "|"]:
        source = prelude + "  inst p: Derived\n    metadata:\n      footprint: " + value
        choices = complete(tmp_path, source)
        assert list(choices) == ["Test:Z_default", "Test:A"]
        item = choices["Test:A"]
        original = source.replace("|", "")
        assert original[: item.start] + item.text + original[item.end :] == (
            prelude + '  inst p: Derived\n    metadata:\n      footprint: "Test:A"'
        )


def test_footprint_in_specialization_and_custom_exclusion(tmp_path):
    source = "from noscheme import role\n" + FOOTPRINTS
    source += 'component Final extends Derived:\n  metadata:\n    footprint: "Test:|"\n'
    source += "board B:\n  metadata:\n    custom:\n      note: true\n"
    assert list(complete(tmp_path, source)) == ["Test:Z_default", "Test:A"]
    assert not complete(tmp_path, source.replace("    footprint:", "    custom:\n      footprint:"))
    assert not complete(tmp_path, source.replace("    footprint:", "    // footprint:"))
    assert not complete(tmp_path, source.replace("    footprint:", "    /* footprint:"))


def test_footprint_prefix_and_no_hints_still_allow_strings(tmp_path):
    source = "from noscheme import role\n" + FOOTPRINTS + "board B:\n"
    source += '  inst p: Part\n    metadata:\n      footprint: "Test:A|"\n  )'
    assert list(complete(tmp_path, source)) == ["Test:A"]
    assert not complete(tmp_path, source.replace("Test:A|", "Other:Custom|"))


def test_nested_custom_metadata_does_not_offer_footprints(tmp_path):
    source = "from noscheme import role\n" + FOOTPRINTS + "board B:\n"
    source += (
        '  inst p: Part\n    metadata:\n      custom:\n        metadata:\n          footprint: "|'
    )
    assert not complete(tmp_path, source)


def check_source(call):
    return (
        PRELUDE.replace(
            "from noscheme import role, nettype", "from noscheme import role, nettype, std"
        )
        + "  net bus: nettype.digital\n    checks:\n      drive: "
        + call
        + "\n"
    )


@pytest.mark.parametrize(
    "call,expected",
    [
        ("std.co|", {"std.count"}),
        ("std.count(m|", {"min", "max", "missing"}),
        ("std.count(std.active_|", {"std.active_digital_driver"}),
        ("std.count(std.active_digital_driver, m|", {"min", "max", "missing"}),
        ("std.sum(capa|", {"capacitance"}),
        ("std.sum(capacitance, net=wir|", {"wiring_capacitance"}),
        ("std.sum(capacitance, where=[tri|", {"tristate_capable"}),
        ("std.count(\n        std.active_|", {"std.active_digital_driver"}),
        ("unknown.rule(m|", set()),
    ],
)
def test_erc_rule_call_completion(tmp_path, call, expected):
    assert set(complete(tmp_path, check_source(call))) == expected


def test_erc_completion_offers_keywords_and_scoped_rules(tmp_path):
    suggestions = complete(tmp_path, check_source("std.count(ma|"))
    assert suggestions["max"].text == "max="
    pin = """from noscheme import role, nettype, std
component Part:
  pins:
    1:
      role: role.passive
      checks:
        drive: std.|
board B:
  net bus: nettype.passive
"""
    assert set(complete(tmp_path, pin)) == {
        "std.bias_reference",
        "std.current_groups",
        "std.pin_do_not_connect",
        "std.pin_required_connection",
        "std.pin_unused_pin",
    }
    source = check_source("std.co|")
    assert not complete(tmp_path, source.replace("    checks:", "    custom:"))
