"""Instance scaffolding retains source and never infers nets or overrides defaults."""

from noscheme.lsp_code_actions import instance_actions
from noscheme.parser import parse
from test_circuit import make

SOURCE = """from noscheme import role, unused, nettype
component Part:
  params:
    required: number
    optional: number = 10
  pins:
    1 (signal):
      role: role.passive
      unused: unused.never
    2:
      role: role.passive
      unused: unused.always
board B:
  net wire: nettype.passive
  inst p: Part
"""


def actions(tmp_path, source=SOURCE):
    make(tmp_path, source)
    path = tmp_path / "main.nosch"
    return {
        a["title"]: a["edits"]
        for a in instance_actions(path, tmp_path, {path: source}, source.index("inst p") + 6)
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
        "inst p: Part",
        'inst p: Part(signal: wire, required=4) // keep\n    metadata:\n      value: "original"',
    )
    result = actions(tmp_path, source)
    assert "Add required connections and parameters" not in result
    assert "Add metadata template" not in result
    expanded = apply(source, result["Add all connections and parameters"])
    assert "signal: wire, required=4" in expanded
    assert "// keep" in expanded
    assert "1: TODO_" not in expanded
    assert 'value: "original"' in expanded
    assert "// params:" in expanded


def test_existing_parameter_section_and_next_instance(tmp_path):
    source = SOURCE.replace(
        "inst p: Part", "inst p: Part\n    params:\n      required: 3\n  inst next: Part"
    )
    result = actions(tmp_path, source)
    expanded = apply(source, result["Add all connections and parameters"])
    assert expanded.count("    params:") == 1
    assert "required: 3" in expanded
    assert expanded.index("// optional:") < expanded.index("inst next:")


def test_no_actions_on_malformed_source_or_other_declarations(tmp_path):
    source = SOURCE.replace("inst p: Part", "inst p: Missing")
    assert actions(tmp_path, source) == {}
    source = SOURCE.replace("inst p: Part", "inst p: Part(")
    assert actions(tmp_path, source) == {}


def test_existing_section_required_parameter_and_idempotence(tmp_path):
    source = SOURCE.replace("inst p: Part", "inst p: Part\n    params:\n      optional: 3")
    result = actions(tmp_path, source)
    expanded = apply(source, result["Add all connections and parameters"])
    assert "      required: TODO_parameter_required" in expanded
    assert "Add all connections and parameters" not in actions(tmp_path, expanded)
    metadata = apply(expanded, actions(tmp_path, expanded)["Add metadata template"])
    assert actions(tmp_path, metadata) == {}


def test_forbidden_pin_is_not_scaffolded(tmp_path):
    source = SOURCE.replace(
        "role: role.passive\n      unused: unused.always",
        "role: role.do_not_connect\n      unused: unused.always",
    )
    result = actions(tmp_path, source)
    expanded = apply(source, result["Add all connections and parameters"])
    assert "2: TODO_" not in expanded
