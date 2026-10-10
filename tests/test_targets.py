"""Named build targets share source definitions, not output or CAD identity state."""

import json

import pytest

from noscheme.analysis_cache import AnalysisCache
from noscheme.circuit import elaborate_project
from noscheme.cli import main
from noscheme.diagnostics import DiagnosticError
from noscheme.project import load_project, read_config
from noscheme.resolve import resolve_project

SOURCE = """from noscheme import net, role, unused
component P:
  pins:
    1:
      role: role.passive
      unused: unused.always
  metadata:
    footprint: "Test:P"
block Circuit:
  params:
    number count = 1
  global net.passive common
  P parts[count](1: global.common)
extend Circuit<2> Double
"""
CONFIG = """language_version = "0.1"
source_paths = ["."]
library_paths = []
output_dir = "build"
default_target = "single"
[targets.single]
root = "main.Circuit"
[targets.double]
root = "main.Double"
"""


def make_targets(path, source=SOURCE, config=CONFIG):
    (path / "main.nosch").write_text(source)
    (path / "noscheme.toml").write_text(config)
    return path


def test_defaults_extensions_and_isolated_artifacts(tmp_path, capsys):
    make_targets(tmp_path)
    for target, count in (("single", 1), ("double", 2)):
        assert main(["compile", "--project", str(tmp_path), "--target", target]) == 0
        capsys.readouterr()
        circuit = json.loads((tmp_path / "build" / target / "circuit.json").read_text())
        assert len(circuit["parts"]) == count
        assert len(circuit["nets"]) == 1
        assert len(circuit["nets"][0]["endpoints"]) == count
        assert (
            read_config(tmp_path, target=target).state_path
            == tmp_path / f"noscheme.{target}.state.json"
        )
    assert load_project(tmp_path).board.name == "Circuit"
    with AnalysisCache().activate():
        assert load_project(tmp_path, target="single").board.name == "Circuit"
        assert load_project(tmp_path, target="double").board.name == "Double"


@pytest.mark.parametrize(
    "config,target,message",
    [
        (CONFIG, "missing", "unknown target"),
        (CONFIG.replace('default_target = "single"\n', ""), None, "select a target"),
        (
            CONFIG.replace('default_target = "single"', 'default_target = "missing"'),
            "double",
            "default_target",
        ),
        (CONFIG.replace('root = "main.Double"', 'root = "Double"'), None, "qualified root"),
        (CONFIG.replace("[targets.double]", '[targets."../double"]'), None, "invalid target name"),
        (
            CONFIG.replace('root = "main.Double"', 'root = "main.Double"\nparams = {}'),
            None,
            "allows only",
        ),
        ('board = "main.Circuit"\n' + CONFIG, None, "cannot be combined"),
    ],
)
def test_invalid_target_configuration(tmp_path, config, target, message):
    make_targets(tmp_path, config=config)
    with pytest.raises(DiagnosticError, match=message):
        read_config(tmp_path, target=target)


def test_sole_target_and_output_override(tmp_path):
    config = CONFIG[: CONFIG.index("[targets.double]")].replace('default_target = "single"\n', "")
    make_targets(tmp_path, config=config + 'output_dir = "custom"\n')
    assert read_config(tmp_path).output_dir == tmp_path / "custom"


@pytest.mark.parametrize(
    "source,code",
    [
        (SOURCE.replace("number count = 1", "number count"), "parameter.required"),
        (
            SOURCE.replace("  params:\n", "  ports:\n    net.passive external\n  params:\n"),
            "project.root-ports",
        ),
        (SOURCE + "block Outer:\n  Circuit child\n", "project.root-only"),
    ],
)
def test_root_requirements(tmp_path, source, code):
    config = CONFIG.replace("main.Circuit", "main.Outer") if "block Outer" in source else CONFIG
    make_targets(tmp_path, source, config)
    with pytest.raises(DiagnosticError) as error:
        elaborate_project(*resolve_project(tmp_path))
    assert error.value.diagnostics[0].code == code


def test_inherited_globals_have_editor_navigation(tmp_path):
    from noscheme.lsp_analysis import analyze

    make_targets(
        tmp_path, config=CONFIG.replace('default_target = "single"', 'default_target = "double"')
    )
    path = tmp_path / "main.nosch"
    result = analyze(path, tmp_path, {}, run_erc=False)
    assert not result.diagnostics
    offset = SOURCE.index("global.common") + len("global.")
    reference = result.reference_at(path, offset)
    assert reference is not None
    assert reference.target.start_pos == SOURCE.index("common\n")


def test_named_configurations_inherited_by_root_only(tmp_path):
    source = SOURCE.replace(
        "  global net.passive common",
        """  metadata:
    erc_choices:
      mode: ["a", "b"]
    configurations:
      selected:
        mode: "a"
  global net.passive common""",
    )
    make_targets(tmp_path, source)
    circuit, _ = elaborate_project(*resolve_project(tmp_path, target="double"))
    assert "selected" in circuit["erc_configurations"]
    make_targets(
        tmp_path,
        source + "block Outer:\n  Double child\n",
        CONFIG.replace("main.Circuit", "main.Outer"),
    )
    with pytest.raises(DiagnosticError) as error:
        resolve_project(tmp_path)
    diagnostic = error.value.diagnostics[0]
    assert diagnostic.code == "project.root-only"
    assert diagnostic.span.line == len((source + "block Outer:\n").splitlines()) + 1


def test_targets_load_independent_module_graphs(tmp_path):
    make_targets(tmp_path, config=CONFIG.replace("main.Double", "other.Double"))
    (tmp_path / "other.nosch").write_text("invalid syntax")
    project = load_project(tmp_path)
    assert set(project.modules) == {"main"}
    with pytest.raises(DiagnosticError):
        load_project(tmp_path, target="double")
    (tmp_path / "other.nosch").write_text("from main import Circuit\nextend Circuit<3> Double\n")
    project, definitions = resolve_project(tmp_path, target="double")
    circuit, _ = elaborate_project(project, definitions)
    assert len(circuit["parts"]) == 3
    assert set(project.modules) == {"main", "other"}


def test_root_diagnostic_points_to_selected_target(tmp_path):
    config = CONFIG.replace('root = "main.Double"', 'root = "main.P"')
    make_targets(tmp_path, config=config)
    with pytest.raises(DiagnosticError) as error:
        load_project(tmp_path, target="double")
    diagnostic = error.value.diagnostics[0]
    assert diagnostic.code == "project.board"
    assert diagnostic.span.line == len(config.splitlines())
