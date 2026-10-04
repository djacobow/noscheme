import json
from pathlib import Path

import pytest

from noscheme.cli import main
from noscheme.diagnostics import DiagnosticError
from noscheme.project import ProjectError, load_project, read_config, project_summary


def project(tmp_path, modules=None, **overrides):
    values = {
        "language_version": "0.1",
        "board": "main.Blink",
        "source_paths": ["src"],
        "library_paths": ["lib"],
        "output_dir": "build",
    }
    values.update(overrides)
    for name in ["src", "lib"]:
        (tmp_path / name).mkdir(exist_ok=True)
    config = tmp_path / "noscheme.toml"
    config.write_text("\n".join(f"{key} = {json.dumps(value)}" for key, value in values.items()))
    for relative, text in (modules or {"src/main.nosch": "board Blink:\n    net a: T\n"}).items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return config


def test_load_graph_imports_reexports_and_config_relative_paths(tmp_path, monkeypatch):
    config = project(
        tmp_path,
        {
            "src/main.nosch": "from parts import R\nfrom noscheme import nettype\nboard Blink:\n    net a: nettype.passive\n    inst r: R()\n",
            "lib/parts.nosch": "from vendor.passives import R\n",
            "lib/vendor/passives.nosch": "component R:\n    pins:\n        01:\n            aliases: [one]\n",
            "lib/unused.nosch": "invalid source must not be loaded",
        },
    )
    monkeypatch.chdir(tmp_path.parent)
    loaded = load_project(config)
    assert list(loaded.modules) == ["main", "parts", "vendor.passives"]
    assert loaded.modules["main"].symbols["R"].module == "vendor.passives"
    assert loaded.modules["main"].symbols["nettype"].declaration is None
    assert loaded.config.output_dir == tmp_path / "build"
    assert not (tmp_path / "build").exists()
    assert project_summary(loaded) == project_summary(load_project(tmp_path))


@pytest.mark.parametrize(
    "overrides",
    [
        {"language_version": 0.1},
        {"language_version": "9"},
        {"board": "Blink"},
        {"board": "../main.Blink"},
        {"board": "noscheme.Blink"},
        {"source_paths": "src"},
        {"source_paths": ["absent"]},
        {"source_paths": [], "library_paths": []},
        {"source_paths": ["src", "./src"]},
        {"library_paths": ["src"]},
        {"source_paths": [1]},
        {"output_dir": ""},
        {"typo": "value"},
        {"output_dir": "\0"},
        {"source_paths": ["\0"]},
    ],
)
def test_invalid_config(tmp_path, overrides):
    config = project(tmp_path, **overrides)
    with pytest.raises(ProjectError) as exc:
        read_config(config)
    assert exc.value.diagnostics[0].code == "project.config"
    assert exc.value.diagnostics[0].span.filename == str(config)


def test_missing_keys_and_malformed_toml(tmp_path):
    config = project(tmp_path)
    config.write_text('board = "main.Blink"\n')
    with pytest.raises(ProjectError, match="missing required"):
        read_config(config)
    config.write_text("board = [")
    with pytest.raises(ProjectError) as exc:
        read_config(config)
    assert exc.value.diagnostics[0].code == "project.toml"


@pytest.mark.parametrize(
    "modules,code",
    [
        (
            {"src/main.nosch": "from missing import R\nboard Blink:\n    net n: T\n"},
            "import.missing-module",
        ),
        (
            {
                "src/main.nosch": "board Blink:\n    net n: T\n",
                "lib/main.nosch": "board Blink:\n    net n: T\n",
            },
            "import.ambiguous-module",
        ),
        (
            {"src/main.nosch": "from noscheme import typo\nboard Blink:\n    net n: T\n"},
            "import.missing-name",
        ),
        (
            {
                "src/main.nosch": "from parts import Unknown\nboard Blink:\n    net n: T\n",
                "lib/parts.nosch": 'component R:\n    metadata:\n        value: "R"\n',
            },
            "import.missing-name",
        ),
        (
            {"src/main.nosch": "from noscheme import f, f\nboard Blink:\n    net n: T\n"},
            "import.duplicate-name",
        ),
        (
            {
                "src/main.nosch": "from parts import Blink\nboard Blink:\n    net n: T\n",
                "lib/parts.nosch": "board Blink:\n    net n: T\n",
            },
            "import.duplicate-name",
        ),
        (
            {"src/main.nosch": "from main import Blink\nboard Blink:\n    net n: T\n"},
            "import.cycle",
        ),
        (
            {
                "src/main.nosch": "from other import X\nboard Blink:\n    net n: T\n",
                "lib/other.nosch": "from main import Blink\n",
            },
            "import.cycle",
        ),
        (
            {"src/main.nosch": 'component Blink:\n    metadata:\n        value: "x"\n'},
            "project.board",
        ),
        ({"src/main.nosch": "board Other:\n    net n: T\n"}, "project.board"),
        ({"src/main.nosch": "board Blink:\n    net n = T\n"}, "syntax.invalid"),
        (
            {"src/main.nosch": "from noscheme.extra import X\nboard Blink:\n    net n: T\n"},
            "import.module",
        ),
    ],
)
def test_load_errors(tmp_path, modules, code):
    project(tmp_path, modules)
    with pytest.raises(DiagnosticError) as exc:
        load_project(tmp_path)
    assert exc.value.diagnostics[0].code == code
    if code == "import.duplicate-name":
        assert exc.value.diagnostics[0].related


def test_diamond_graph_is_not_a_cycle(tmp_path):
    project(
        tmp_path,
        {
            "src/main.nosch": "from left import L\nfrom right import R\nboard Blink:\n    net n: T\n",
            "src/left.nosch": 'from shared import S\ncomponent L:\n    metadata:\n        value: "L"\n',
            "src/right.nosch": 'from shared import S\ncomponent R:\n    metadata:\n        value: "R"\n',
            "src/shared.nosch": 'component S:\n    metadata:\n        value: "S"\n',
        },
    )
    loaded = load_project(tmp_path)
    assert len(loaded.modules) == 4
    assert loaded.modules["left"].symbols["S"] is loaded.modules["right"].symbols["S"]


def test_symlink_cannot_escape_search_root(tmp_path):
    project(tmp_path)
    external = tmp_path / "external.nosch"
    external.write_text("board Blink:\n    net n: T\n")
    (tmp_path / "src/main.nosch").unlink()
    (tmp_path / "src/main.nosch").symlink_to(external)
    with pytest.raises(DiagnosticError) as exc:
        load_project(tmp_path)
    assert exc.value.diagnostics[0].code == "import.escape"


def test_no_implicit_working_directory_search(tmp_path, monkeypatch):
    project(tmp_path)
    (tmp_path / "src/main.nosch").rename(tmp_path / "main.nosch")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(DiagnosticError) as exc:
        load_project()
    assert exc.value.diagnostics[0].code == "import.missing-module"


def test_cli_exit_codes_and_diagnostics(tmp_path, capsys, monkeypatch):
    config = project(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["load"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["stage"] == "project-loading"
    assert report["board"] == "main.Blink"
    (tmp_path / "src/main.nosch").write_text("from absent import X\nboard Blink:\n    net n: T\n")
    assert main(["load", "--project", str(config), "--diagnostics", "json"]) == 1
    report = json.loads(capsys.readouterr().err)
    assert report["diagnostics"][0]["span"]["line"] == 1
    config.write_text("board = 3")
    assert main(["load", "--diagnostics", "json"]) == 2
    assert json.loads(capsys.readouterr().err)["diagnostics"][0]["code"] == "project.config"
    assert main(["load", "--project", str(tmp_path / "absent.toml")]) == 2


def test_blink_example():
    root = Path(__file__).resolve().parents[1]
    loaded = load_project(root / "examples/blink")
    assert list(loaded.modules) == ["indicator", "main", "parts"]
    assert loaded.board.name == "Blink"
    assert loaded.modules["indicator"].symbols["Resistor"].declaration.kind == "component"
