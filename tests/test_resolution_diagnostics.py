"""Collect only independent checks; never publish a partially resolved design."""

import json

import pytest

from noscheme.cli import main
from noscheme.diagnostics import DiagnosticError
from noscheme.lsp_analysis import analyze
from noscheme.resolve import resolve_project
from test_circuit import make


SOURCE = """component R:
  params:
    string value = "1k"
  pins:
    1..2:
      role: role.passive
block B:
  R r:
    1: missing1
    2: missing2
  Nope q:
    unknown_pin: unknown_net
"""


def resolve_errors(path):
    with pytest.raises(DiagnosticError) as error:
        resolve_project(path)
    return error.value.diagnostics


def test_multiple_bad_bindings_and_unknown_sibling_instance(tmp_path):
    make(tmp_path, SOURCE)
    errors = resolve_errors(tmp_path)
    assert [d.code for d in errors] == ["resolve.net", "resolve.net", "resolve.missing-definition"]
    assert "missing1" in errors[0].message
    assert "missing2" in errors[1].message
    assert "Nope" in errors[2].message
    assert len({d.span.line for d in errors}) == 3
    assert resolve_errors(tmp_path) == errors


def test_arguments_and_bindings_are_independent_but_each_binding_stops(tmp_path):
    source = SOURCE.replace("1: missing1", "absent: missing1").replace(
        "    2: missing2\n",
        "    2: missing2\n    params:\n      missing_arg: 1\n      value: missing_value\n",
    )
    make(tmp_path, source)
    errors = resolve_errors(tmp_path)
    assert [d.code for d in errors] == [
        "resolve.argument",
        "resolve.expression-name",
        "resolve.private",
        "resolve.net",
        "resolve.missing-definition",
    ]
    assert not any("missing1" in d.message for d in errors)


def test_independent_definitions_and_shared_body_deduplication(tmp_path):
    source = (
        SOURCE.replace("block B:", "block W:")
        + """extend W Derived:
  metadata:
    custom:
      note: "Same body"
component BadDefault:
  params:
    string value = unknown_default
block B:
  W w()
"""
    )
    make(tmp_path, source)
    errors = resolve_errors(tmp_path)
    assert len(errors) == 4
    assert sum("missing1" in d.message for d in errors) == 1
    assert any("unknown_default" in d.message for d in errors)


def test_bad_net_type_stops_its_body_but_not_an_independent_definition(tmp_path):
    source = (
        SOURCE
        + """block BadNet:
  MissingBundle n
  R dependent:
    1: n.nonexistent
"""
    )
    make(tmp_path, source)
    errors = resolve_errors(tmp_path)
    assert len(errors) == 4
    assert any("MissingBundle" in d.message for d in errors)
    assert not any("nonexistent" in d.message for d in errors)


@pytest.mark.parametrize("command", ["check", "compile", "export-kicad"])
def test_cli_reports_all_errors_without_touching_artifacts(tmp_path, capsys, command):
    make(tmp_path, SOURCE)
    out = tmp_path / "build"
    out.mkdir()
    files = [out / "circuit.json", out / "board.net", tmp_path / "noscheme.default.state.json"]
    for path in files:
        path.write_text("previous artifact")
    args = [command, "--project", str(tmp_path), "--diagnostics", "json"]
    if command == "export-kicad":
        args += ["--footprints", str(tmp_path / "unused-footprints.json")]
    assert main(args) == 1
    diagnostics = json.loads(capsys.readouterr().err)["diagnostics"]
    assert len(diagnostics) == 3
    assert all(path.read_text() == "previous artifact" for path in files)
    assert sorted(p.name for p in out.iterdir()) == ["board.net", "circuit.json"]


def test_lsp_publishes_multiple_resolution_errors_from_unsaved_source(tmp_path):
    make(tmp_path, SOURCE.replace("missing1", "no_connect"))
    path = tmp_path / "main.nosch"
    source = path.read_text().replace("1: no_connect", "1: missing1")
    result = analyze(path, tmp_path, {path: source})
    assert [d.code for d in result.diagnostics] == [
        "resolve.net",
        "resolve.net",
        "resolve.missing-definition",
    ]
    assert "1: no_connect" in path.read_text()
