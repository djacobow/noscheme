"""Public CLI parity and cross-implementation export-state compatibility."""

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil

import pytest

from noscheme.cli import main
from noscheme.circuit import elaborate_project
from noscheme.resolve import resolve_project

ROOT = Path(__file__).resolve().parents[2]
TOOL = os.environ.get("NOSCHEME_NATIVE_TOOL")
pytestmark = pytest.mark.skipif(not TOOL, reason="native tool supplied by Meson")
NATIVE = ["--implementation", "native", "--native-tool", TOOL or ""]


def invoke(capsys, args, native):
    code = main([*args, *(NATIVE if native else [])])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def json_output(result):
    code, out, err = result
    return code, json.loads(out) if out.strip() else None, json.loads(err) if err.strip() else None


def copy_project(tmp_path, example="blink"):
    shutil.copytree(
        ROOT / "examples" / example,
        tmp_path,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(
            "build", ".history", "__pycache__", "*.kicad_*", "noscheme.state.json"
        ),
    )
    loaded, definitions = resolve_project(tmp_path)
    circuit, _ = elaborate_project(loaded, definitions)
    (tmp_path / "footprints.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "footprints": {
                    p["metadata"]["footprint"]["value"]: {"exists": True, "pads": list(p["pins"])}
                    for p in circuit["parts"]
                },
            }
        )
    )
    (tmp_path / "noscheme.state.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "namespace": "e7470314-8517-4cfa-af45-4ef937843c87",
                "entries": {},
                "previous_parts": {},
            }
        )
    )
    return tmp_path


@pytest.fixture
def project(tmp_path):
    return copy_project(tmp_path)


@pytest.mark.parametrize("command", ["load", "resolve", "lint", "evaluate", "check", "compile"])
def test_project_commands(project, capsys, command):
    args = [command, "--project", str(project), "--diagnostics", "json"]
    expected = json_output(invoke(capsys, args, False))
    before = (
        json.loads((project / "build/circuit.json").read_text()) if command == "compile" else None
    )
    assert json_output(invoke(capsys, args, True)) == expected
    if before:
        assert json.loads((project / "build/circuit.json").read_text()) == before


@pytest.mark.parametrize(
    "source",
    [
        "board B:\n  T n\n",
        "component P:\n  pins:\n    1: role.passive\n",
        "board B\n",
        "// Ω\nboard B:\n  T n\n",
    ],
)
def test_parse(tmp_path, capsys, source):
    path = tmp_path / "a.nosch"
    path.write_text(source)
    args = ["parse", str(path), "--diagnostics", "json"]
    actual = json_output(invoke(capsys, args, True))
    expected = json_output(invoke(capsys, args, False))
    if source == "board B\n":
        # ANTLR and Lark report syntax failures differently; neither may accept it.
        assert actual[0] == expected[0] == 1
        assert actual[2]["diagnostics"][0]["code"] == expected[2]["diagnostics"][0]["code"]
    else:
        assert actual == expected


def test_format(tmp_path, capsys):
    path = tmp_path / "a.nosch"
    path.write_text("board B:\n    T a\n")
    for flags in (["--check"], ["--diff"]):
        args = ["format", str(path), *flags]
        assert invoke(capsys, args, True) == invoke(capsys, args, False)
    assert invoke(capsys, ["format", str(path)], True)[0] == 0
    assert path.read_text() == "board B:\n  T a\n"


def artifacts(project):
    result = {}
    for path in [*sorted((project / "build").glob("*")), project / "noscheme.state.json"]:
        if path.is_file():
            result[path.name] = (
                json.loads(path.read_text()) if path.suffix == ".json" else path.read_text()
            )
    return result


def export_compare(project, capsys):
    args = [
        "export-kicad",
        "--project",
        str(project),
        "--footprints",
        str(project / "footprints.json"),
        "--diagnostics",
        "json",
    ]
    state = (project / "noscheme.state.json").read_text()
    expected = json_output(invoke(capsys, args, False))
    outputs = artifacts(project)
    (project / "noscheme.state.json").write_text(state)
    assert json_output(invoke(capsys, args, True)) == expected
    assert artifacts(project) == outputs
    return outputs


def test_export_repeat_rename_and_reservations(project, capsys):
    first = export_compare(project, capsys)
    again = export_compare(project, capsys)
    assert again["export.json"]["changes"] == {"added": [], "removed": [], "changed": []}
    assert first["board.net"] == again["board.net"]
    source = project / "src/main.nosch"
    source.write_text(
        source.read_text()
        .replace("Header input", "Header connector")
        .replace("      pins:", '      identity: "input"\n      ref: "J17"\n      pins:')
    )
    renamed = export_compare(project, capsys)
    assert (
        renamed["noscheme.state.json"]["entries"]["input"]["uuid"]
        == first["noscheme.state.json"]["entries"]["input"]["uuid"]
    )
    source.write_text(source.read_text().replace("indicators[2]", "indicators[1]"))
    removed = export_compare(project, capsys)
    assert len(removed["export.json"]["changes"]["removed"]) == 2
    source.write_text(source.read_text().replace("indicators[1]", "indicators[3]"))
    export_compare(project, capsys)


@pytest.mark.parametrize("failure", ["footprint", "pads", "state"])
def test_export_failure_preserves_files(project, capsys, failure):
    export_compare(project, capsys)
    if failure == "state":
        state = json.loads((project / "noscheme.state.json").read_text())
        state["schema_version"] = 99
        (project / "noscheme.state.json").write_text(json.dumps(state))
    else:
        records = json.loads((project / "footprints.json").read_text())
        record = next(iter(records["footprints"].values()))
        record["exists"] = failure != "footprint"
        if failure == "pads":
            record["pads"] = ["999"]
        (project / "footprints.json").write_text(json.dumps(records))
    before = deepcopy(artifacts(project))
    args = [
        "export-kicad",
        "--project",
        str(project),
        "--footprints",
        str(project / "footprints.json"),
        "--diagnostics",
        "json",
    ]
    assert json_output(invoke(capsys, args, True)) == json_output(invoke(capsys, args, False))
    assert artifacts(project) == before


def test_leading_hash_and_no_connect(project, capsys):
    path = project / "lib/parts.nosch"
    path.write_text(path.read_text().replace('ref_prefix: "R"', 'ref_prefix: "#R"'))
    path = project / "src/main.nosch"
    path.write_text(path.read_text().replace("1: global.v5", "1: no_connect"))
    outputs = export_compare(project, capsys)
    assert "unconnected-(" in outputs["board.net"]
    assert outputs["export.json"]["warnings"]


@pytest.mark.parametrize(
    "example",
    [
        "power_modes",
        "erc_extensions",
        "parameterized_erc",
        "tristate",
        "stm32h523",
        "rulos_6matrix",
        "wren",
    ],
)
@pytest.mark.parametrize("command", ["check", "evaluate", "lint", "resolve"])
def test_examples(capsys, example, command):
    args = [command, "--project", str(ROOT / "examples" / example), "--diagnostics", "json"]
    assert json_output(invoke(capsys, args, True)) == json_output(invoke(capsys, args, False))


@pytest.mark.parametrize("native", [False, True])
def test_electrical_errors_allow_compile_but_fail_check(project, capsys, native):
    path = project / "src/main.nosch"
    path.write_text(path.read_text().replace("role.power_output", "role.power_input"))
    args = ["--project", str(project), "--diagnostics", "json"]
    check = json_output(invoke(capsys, ["check", *args], native))
    compile_result = json_output(invoke(capsys, ["compile", *args], native))
    assert check[0] == 1
    assert compile_result[0] == 0
    assert check[1]["erc_errors"] > 0
    assert (project / "build/circuit.json").exists()


def test_publication_rollback(project, capsys, monkeypatch):
    export_compare(project, capsys)
    before = artifacts(project)
    path = project / "lib/parts.nosch"
    path.write_text(path.read_text().replace('"Green"', '"Blue"'))
    replace = os.replace
    count = 0

    def fail_once(source, target):
        nonlocal count
        count += 1
        if count == 4:
            raise OSError("injected publication failure")
        return replace(source, target)

    monkeypatch.setattr(os, "replace", fail_once)
    args = [
        "export-kicad",
        "--project",
        str(project),
        "--footprints",
        str(project / "footprints.json"),
    ]
    assert invoke(capsys, args, True)[0] == 2
    assert artifacts(project) == before
    assert not list(project.rglob(".noscheme-export-*"))


def test_template_elements_and_lint(project, capsys):
    (project / "src/main.nosch").write_text("""from noscheme import role, net
component P:
  params:
    resistance value = 1k
    number unused_parameter = 9
  pins:
    1: role.passive
block G:
  params:
    number n = 2
    resistance r = 2k
  ports:
    net.passive p[n]
  P<value: r> parts[n]():
    1: p[0:n]
    elements:
      [1]:
        params:
          value: 4.7k
        metadata:
          value: r
board Blink:
  net.passive p[2]
  G g(p: p)
""")
    for command in ("evaluate", "lint"):
        args = [command, "--project", str(project), "--diagnostics", "json"]
        assert json_output(invoke(capsys, args, True)) == json_output(invoke(capsys, args, False))


@pytest.mark.parametrize("command", ["check", "compile", "resolve", "evaluate", "lint"])
def test_structural_errors(project, capsys, command):
    (project / "src/main.nosch").write_text("board Blink:\n  Unknown x\n")
    args = [command, "--project", str(project), "--diagnostics", "json"]
    expected = json_output(invoke(capsys, args, False))
    assert expected[0] == 1
    assert json_output(invoke(capsys, args, True)) == expected
    assert not (project / "build").exists()


@pytest.mark.parametrize("example", ["stm32h523", "rulos_6matrix"])
def test_large_example_exports(tmp_path, capsys, example):
    project = copy_project(tmp_path, example)
    outputs = export_compare(project, capsys)
    assert outputs["export.json"]["changes"]["added"]
    export_compare(project, capsys)


def test_unicode_source_artifacts_and_crlf_records(project, capsys):
    source = project / "src/main.nosch"
    source.write_text("// Ω and 😀\n" + source.read_text())
    records = project / "footprints.json"
    records.write_bytes(records.read_bytes() + b"\r\n")
    export_compare(project, capsys)
    args = ["compile", "--project", str(project), "--diagnostics", "json"]
    expected = json_output(invoke(capsys, args, False))
    before = json.loads((project / "build/circuit.json").read_text())
    assert json_output(invoke(capsys, args, True)) == expected
    assert json.loads((project / "build/circuit.json").read_text()) == before


def test_unavailable_native_tool_does_not_fall_back(project, capsys):
    assert (
        main(
            [
                "check",
                "--project",
                str(project),
                "--implementation",
                "native",
                "--native-tool",
                str(project / "missing"),
            ]
        )
        == 2
    )
    assert "io.error" in capsys.readouterr().err
    assert not (project / "build").exists()


@pytest.mark.parametrize(
    "configurations", [["usb_powered"], ["header_powered", "usb_powered"], ["missing"]]
)
def test_configuration_selection(capsys, configurations):
    args = ["check", "--project", str(ROOT / "examples/power_modes"), "--diagnostics", "json"]
    for name in configurations:
        args.extend(["--erc-configuration", name])
    assert json_output(invoke(capsys, args, True)) == json_output(invoke(capsys, args, False))


def test_export_protects_footprint_input(project, capsys):
    out = project / "build"
    out.mkdir()
    records = out / "export.json"
    records.write_bytes((project / "footprints.json").read_bytes())
    before = artifacts(project)
    args = [
        "export-kicad",
        "--project",
        str(project),
        "--footprints",
        str(records),
        "--diagnostics",
        "json",
    ]
    expected = json_output(invoke(capsys, args, False))
    assert expected[0] == 1
    assert json_output(invoke(capsys, args, True)) == expected
    assert artifacts(project) == before


@pytest.mark.parametrize("command", ["load", "resolve", "lint", "evaluate", "check", "compile"])
@pytest.mark.parametrize("target", ["single", "double", "missing"])
def test_named_targets(tmp_path, capsys, command, target):
    from tests.test_targets import make_targets

    make_targets(tmp_path)
    args = [command, "--project", str(tmp_path), "--target", target, "--diagnostics", "json"]
    expected = json_output(invoke(capsys, args, False))
    assert json_output(invoke(capsys, args, True)) == expected
    if command == "compile" and target != "missing":
        circuit = json.loads((tmp_path / "build" / target / "circuit.json").read_text())
        assert len(circuit["parts"]) == (1 if target == "single" else 2)


def test_named_target_export_state_isolation(project, capsys):
    config = project / "noscheme.toml"
    config.write_text(
        config.read_text().replace('board = "main.Blink"', 'default_target = "one"')
        + '\n[targets.one]\nroot = "main.Blink"\n[targets.two]\nroot = "main.Blink"\n'
    )
    footprints = project / "footprints.json"
    for target in ("one", "two"):
        state = project / f"noscheme.{target}.state.json"
        state.write_bytes((project / "noscheme.state.json").read_bytes())
        args = [
            "export-kicad",
            "--project",
            str(project),
            "--target",
            target,
            "--footprints",
            str(footprints),
            "--diagnostics",
            "json",
        ]
        expected = json_output(invoke(capsys, args, False))
        assert expected[0] == 0, expected
        before = state.read_bytes()
        native = json_output(invoke(capsys, args, True))
        assert native[0] == 0, native
        assert state.read_bytes() == before
        assert native[1]["state"] == str(state)
        assert native[1]["changes"] == {"added": [], "removed": [], "changed": []}
        assert (project / "build" / target / "board.net").is_file()
        if target == "one":
            first_state = before
        else:
            assert (project / "noscheme.one.state.json").read_bytes() == first_state


@pytest.mark.parametrize("case", ["required", "ports", "globals", "configurations"])
def test_root_constraint_parity(tmp_path, capsys, case):
    from tests.test_targets import CONFIG, SOURCE, make_targets

    source, config = SOURCE, CONFIG
    if case == "required":
        source = source.replace("number count = 1", "number count")
    elif case == "ports":
        source = source.replace("  params:", "  ports:\n    net.passive external\n  params:")
    else:
        if case == "configurations":
            source = source.replace(
                "  global net.passive common",
                """  metadata:
    erc_choices:
      mode: ["a", "b"]
    configurations:
      selected:
        mode: "a"
  global net.passive common""",
            )
        source += "block Outer:\n  Double child\n"
        config = config.replace("main.Circuit", "main.Outer")
    make_targets(tmp_path, source, config)
    args = ["check", "--project", str(tmp_path), "--diagnostics", "json"]
    expected = json_output(invoke(capsys, args, False))
    assert expected[0] == 1
    assert json_output(invoke(capsys, args, True)) == expected


@pytest.mark.parametrize(
    "replacement",
    [
        "root = 42",
        'root = "main.Double"\nparams = {}',
        'root = "main.Double"\noutput_dir = ""',
    ],
)
def test_target_config_error_parity(tmp_path, capsys, replacement):
    from tests.test_targets import CONFIG, make_targets

    make_targets(tmp_path, config=CONFIG.replace('root = "main.Double"', replacement))
    args = ["load", "--project", str(tmp_path), "--diagnostics", "json"]
    expected = json_output(invoke(capsys, args, False))
    assert expected[0] == 2
    assert json_output(invoke(capsys, args, True)) == expected


def test_selected_root_diagnostic_parity(tmp_path, capsys):
    from tests.test_targets import CONFIG, make_targets

    make_targets(tmp_path, config=CONFIG.replace('root = "main.Double"', 'root = "main.P"'))
    args = ["load", "--project", str(tmp_path), "--target", "double", "--diagnostics", "json"]
    expected = json_output(invoke(capsys, args, False))
    assert expected[0] == 1
    assert json_output(invoke(capsys, args, True)) == expected
