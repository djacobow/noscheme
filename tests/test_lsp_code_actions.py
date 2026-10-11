"""Instance scaffolding retains source and never infers nets or overrides defaults."""

import json
from pathlib import Path

import pytest

from noscheme.lsp_code_actions import instance_actions
from noscheme.parser import parse
from test_circuit import make

SOURCE = """from noscheme import role, use, net
component Part:
  params:
    number required
    number optional = 10
  pins:
    1 (signal):
      role: role.passive
      use: use.required
    2:
      role: role.passive
      use: use.optional
block B:
  net.passive wire
  Part p
"""


def instance_offset(source):
    """The offset of the instance named p, whatever its type and parameters."""
    return source.index(" p", source.index("block B:")) + 1


def actions(tmp_path, source=SOURCE):
    make(tmp_path, source)
    path = tmp_path / "main.nosch"
    return {
        a["title"]: a["edits"]
        for a in instance_actions(path, tmp_path, {path: source}, instance_offset(source))
    }


def apply(source, edits):
    for at, text in reversed(edits):
        source = source[:at] + text + source[at:]
    parse(source)
    return source


def test_required_and_expanded(tmp_path):
    result = actions(tmp_path)
    required = apply(SOURCE, result["Add required connections and parameters"])
    assert "1: TODO_connection_1" in required
    assert "2: TODO_" not in required
    assert "required: TODO_parameter_required" in required
    expanded = apply(SOURCE, result["Add all connections and parameters"])
    assert "2: TODO_connection_2" in expanded
    assert "// optional: TODO_parameter_optional" in expanded
    assert "// metadata:" in apply(SOURCE, result["Add metadata template"])


def test_existing_bindings_and_parameters_are_preserved(tmp_path):
    source = SOURCE.replace(
        "Part p",
        'Part<required: 4> p(signal: wire):  // keep\n    metadata:\n      value: "original"',
    )
    result = actions(tmp_path, source)
    assert "Add required connections and parameters" not in result
    assert "Add metadata template" not in result
    expanded = apply(source, result["Add all connections and parameters"])
    assert "Part<required: 4> p(signal: wire):  // keep" in expanded
    assert "// keep" in expanded
    assert "1: TODO_" not in expanded
    assert 'value: "original"' in expanded
    assert "// params:" in expanded


def test_existing_parameter_section_and_next_instance(tmp_path):
    source = SOURCE.replace("Part p", "Part p:\n    params:\n      required: 3\n  Part next")
    result = actions(tmp_path, source)
    expanded = apply(source, result["Add all connections and parameters"])
    assert expanded.count("    params:") == 1
    assert "required: 3" in expanded
    assert expanded.index("// optional:") < expanded.index("Part next")


def test_no_actions_on_malformed_source_or_other_declarations(tmp_path):
    source = SOURCE.replace("Part p", "Missing p")
    assert actions(tmp_path, source) == {}
    source = SOURCE.replace("Part p", "Part p(")
    assert actions(tmp_path, source) == {}


def test_existing_section_required_parameter_and_idempotence(tmp_path):
    source = SOURCE.replace("Part p", "Part<optional: 3> p\n")
    result = actions(tmp_path, source)
    expanded = apply(source, result["Add all connections and parameters"])
    assert "      required: TODO_parameter_required" in expanded
    assert "Add all connections and parameters" not in actions(tmp_path, expanded)
    metadata = apply(expanded, actions(tmp_path, expanded)["Add metadata template"])
    assert actions(tmp_path, metadata) == {}


def test_forbidden_pin_is_not_scaffolded(tmp_path):
    source = SOURCE.replace(
        "role: role.passive\n      use: use.optional",
        "use: use.disallowed",
    )
    result = actions(tmp_path, source)
    expanded = apply(source, result["Add all connections and parameters"])
    assert "2: TODO_" not in expanded


@pytest.mark.parametrize(
    "case",
    json.loads((Path(__file__).parent / "fixtures/lsp_code_actions.json").read_text()),
    ids=lambda case: case["name"],
)
def test_shared_scaffolding_fixtures_stay_parseable(tmp_path, case):
    source = case["source"]
    for edits in actions(tmp_path, source).values():
        apply(source, edits)
