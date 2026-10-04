from copy import deepcopy
import json
from pathlib import Path
import pytest

from noscheme.circuit import elaborate_project
from noscheme.resolve import resolve_project
from noscheme.kicad.export import export_project, KiCadNames
from noscheme.kicad.sexpr import load, children, scalar
from noscheme.diagnostics import DiagnosticError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def setup(tmp_path):
    import shutil

    shutil.copytree(ROOT / "examples/blink/src", tmp_path / "src")
    shutil.copytree(ROOT / "examples/blink/lib", tmp_path / "lib")
    shutil.copyfile(ROOT / "examples/blink/noscheme.toml", tmp_path / "noscheme.toml")
    project, definitions = resolve_project(tmp_path)
    circuit, _ = elaborate_project(project, definitions)
    records = tmp_path / "footprints.json"
    records.write_text(
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
    return project, circuit, records


def state(project):
    return json.loads((project.config.path.parent / "noscheme.state.json").read_text())


def test_export_and_repeatability(setup):
    project, circuit, records = setup
    report = export_project(*setup)
    assert len(report["changes"]["added"]) == 5
    before = (project.config.output_dir / "board.net").read_bytes()
    entries = state(project)["entries"]
    netlist = load(project.config.output_dir / "board.net")
    components = children(children(netlist, "components")[0], "comp")
    assert len(components) == 5
    assert {scalar(c, "ref") for c in components} == {"J1", "D1", "D2", "R1", "R2"}
    assert len(children(children(netlist, "nets")[0], "net")) == 4
    again = export_project(*setup)
    assert again["changes"] == {"added": [], "removed": [], "changed": []}
    assert (project.config.output_dir / "board.net").read_bytes() == before
    assert state(project)["entries"] == entries


def test_stable_identity_and_reference_override(setup):
    project, circuit, records = setup
    export_project(*setup)
    original = state(project)["entries"]["indicators[0].resistor"]
    moved = deepcopy(circuit)
    part = next(p for p in moved["parts"] if p["identity"] == "indicators[0].resistor")
    part["path"] = ["renamed", "resistor"]
    part["metadata"]["ref"] = {"value": "R17"}
    export_project(project, moved, records)
    current = state(project)["entries"][part["identity"]]
    assert current["uuid"] == original["uuid"]
    assert current["reference"] == "R17"
    assert current["reserved_references"] == ["R1", "R17"]


def test_deleted_references_reserved_and_explicit_conflicts_block(setup):
    project, circuit, records = setup
    export_project(*setup)
    removed = deepcopy(circuit)
    removed["parts"] = [p for p in removed["parts"] if p["identity"] != "indicators[0].resistor"]
    export_project(project, removed, records)
    assert not state(project)["entries"]["indicators[0].resistor"]["active"]
    added = deepcopy(removed)
    extra = deepcopy(next(p for p in added["parts"] if p["identity"] == "indicators[1].resistor"))
    extra["identity"] = "new"
    extra["path"] = ["new"]
    added["parts"].append(extra)
    export_project(project, added, records)
    assert state(project)["entries"]["new"]["reference"] == "R3"
    extra["metadata"]["ref"] = {"value": "R1"}
    before = (project.config.path.parent / "noscheme.state.json").read_bytes()
    with pytest.raises(DiagnosticError, match="reserved"):
        export_project(project, added, records)
    assert (project.config.path.parent / "noscheme.state.json").read_bytes() == before


@pytest.mark.parametrize("failure", ["unknown", "pad", "bad-state"])
def test_validation_failure_preserves_outputs(setup, failure):
    project, circuit, records = setup
    export_project(*setup)
    net = project.config.output_dir / "board.net"
    before = net.read_bytes()
    if failure == "bad-state":
        (project.config.path.parent / "noscheme.state.json").write_text("{bad")
    else:
        snapshots = json.loads(records.read_text())
        record = next(iter(snapshots["footprints"].values()))
        if failure == "unknown":
            record["exists"] = False
        else:
            record["pads"].append("99")
        records.write_text(json.dumps(snapshots))
    state_before = (project.config.path.parent / "noscheme.state.json").read_bytes()
    with pytest.raises(DiagnosticError):
        export_project(*setup)
    assert net.read_bytes() == before
    assert (project.config.path.parent / "noscheme.state.json").read_bytes() == state_before


def test_missing_footprint_retained_in_state_but_omitted_from_netlist(setup):
    project, circuit, records = setup
    part = circuit["parts"][0]
    part["metadata"].pop("footprint")
    report = export_project(*setup)
    assert report["omitted_without_footprints"] == [part["identity"]]
    assert part["identity"] in state(project)["entries"]
    netlist = load(project.config.output_dir / "board.net")
    assert len(children(children(netlist, "components")[0], "comp")) == 4


def test_publication_io_failure_rolls_back(setup, monkeypatch):
    import noscheme.kicad.export as exporter

    project, circuit, records = setup
    export_project(*setup)
    files = {p: p.read_bytes() for p in project.config.output_dir.iterdir() if p.is_file()}
    statefile = project.config.path.parent / "noscheme.state.json"
    files[statefile] = statefile.read_bytes()
    real_replace = exporter.os.replace
    calls = 0

    def replace(src, dst):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("injected publication failure")
        return real_replace(src, dst)

    monkeypatch.setattr(exporter.os, "replace", replace)
    circuit["parts"][0]["value"] = {"type": "string", "value": "changed"}
    with pytest.raises(OSError, match="injected"):
        export_project(*setup)
    assert all(p.read_bytes() == content for p, content in files.items())


def test_name_adapter_reverse_map():
    adapter = KiCadNames()
    for path, expected in [
        (["#supply"], "#supply"),
        (["bank", 0, "A#"], "/bank[0]/A#"),
        (["RESET#"], "RESET#"),
    ]:
        net = {"name": expected, "path": path}
        assert adapter.encode(net) == expected
        assert adapter.reverse[expected]["path"] == path


def test_class_merge_preserves_layout_settings_and_inputs(tmp_path):
    from noscheme.kicad.classes import update_classes

    project = tmp_path / "layout.kicad_pro"
    data = {
        "net_settings": {
            "classes": [{"name": "Power", "track_width": 0.5, "clearance": 0.3}],
            "netclass_assignments": {"other": ["Power"]},
            "netclass_patterns": [{"netclass": "Power", "pattern": "V*"}],
        },
        "board": {"design_settings": {"custom": 123}},
    }
    project.write_text(json.dumps(data))
    memberships = tmp_path / "memberships.json"
    memberships.write_text(json.dumps({"v5": ["Power"]}))
    output = tmp_path / "updated.kicad_pro"
    before = project.read_bytes()
    update_classes(project, memberships, output)
    result = json.loads(output.read_text())
    assert result["board"] == data["board"]
    assert result["net_settings"]["classes"] == data["net_settings"]["classes"]
    assert result["net_settings"]["netclass_patterns"] == data["net_settings"]["netclass_patterns"]
    assert result["net_settings"]["netclass_assignments"] == {"other": ["Power"], "v5": ["Power"]}
    assert project.read_bytes() == before
    with pytest.raises(FileExistsError):
        update_classes(project, memberships, output)
    memberships.write_text('{"v5":["Missing"]}')
    with pytest.raises(DiagnosticError):
        update_classes(project, memberships, tmp_path / "bad.kicad_pro")
    assert not (tmp_path / "bad.kicad_pro").exists()
