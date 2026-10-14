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
    return json.loads((project.config.path.parent / "noscheme.default.state.json").read_text())


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
    properties = [
        {scalar(p, "name"): scalar(p, "value") for p in children(c, "property")} for c in components
    ]
    assert {p["noscheme_path"] for p in properties} == {
        "input",
        "indicators[0].resistor",
        "indicators[0].led",
        "indicators[1].resistor",
        "indicators[1].led",
    }
    assert all(p["noscheme_identity"] == p["noscheme_path"] for p in properties)
    for component, expected in zip(components, properties, strict=True):
        fields = children(children(component, "fields")[0], "field")
        assert {scalar(f, "name"): f[2] for f in fields} == expected
    assert len(children(children(netlist, "nets")[0], "net")) == 4
    again = export_project(*setup)
    assert again["changes"] == {"added": [], "removed": [], "changed": []}
    assert (project.config.output_dir / "board.net").read_bytes() == before
    assert state(project)["entries"] == entries


@pytest.mark.parametrize("key,value", [("ref", "#R42"), ("ref_prefix", "#R")])
def test_leading_hash_reference_warns_but_exports_and_repeats(setup, key, value):
    project, circuit, records = setup
    part = circuit["parts"][0]
    part["metadata"][key] = {"value": value, "span": part["source"]["declaration"]}
    warnings = []
    report = export_project(*setup, warnings=warnings)
    assert len(warnings) == 1 and warnings[0].code == "reference.leading-hash"
    assert warnings[0].severity == "warning"
    assert warnings[0].instance_path == part["identity"]
    assert report["warnings"][0]["span"] == part["source"]["declaration"]
    assert (
        json.loads((project.config.output_dir / "export.json").read_text())["warnings"]
        == report["warnings"]
    )
    assert state(project)["entries"][part["identity"]]["reference"].startswith("#R")
    assert export_project(*setup)["warnings"] == report["warnings"]


def test_internal_hash_in_reference_has_no_warning(setup):
    project, circuit, records = setup
    circuit["parts"][0]["metadata"]["ref"] = {"value": "R#1"}
    assert export_project(*setup)["warnings"] == []


def test_leading_hash_warning_on_cli_stderr_and_json_report(setup, capsys):
    from noscheme.cli import main

    project, _, records = setup
    source = project.config.path.parent / "lib/parts.nosch"
    source.write_text(source.read_text().replace('ref_prefix: "R"', 'ref_prefix: "#R"'))
    assert (
        main(
            [
                "export-kicad",
                "--project",
                str(project.config.path.parent),
                "--footprints",
                str(records),
                "--diagnostics",
                "json",
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert len(report["warnings"]) == 2
    diagnostics = json.loads(output.err)["diagnostics"]
    assert [d for d in diagnostics if d["code"] == "reference.leading-hash"] == report["warnings"]


def test_source_path_updates_without_changing_identity_or_connectivity(setup):
    project, circuit, records = setup
    export_project(*setup)
    original_state = state(project)["entries"]
    original_netlist = load(project.config.output_dir / "board.net")
    moved = deepcopy(circuit)
    part = next(p for p in moved["parts"] if p["identity"] == "indicators[0].resistor")
    part["path"] = ["channels#", 1, 2, "renamed", "resistor"]
    export_project(project, moved, records)
    netlist = load(project.config.output_dir / "board.net")
    components = children(children(netlist, "components")[0], "comp")
    original = original_state[part["identity"]]
    component = next(c for c in components if scalar(c, "ref") == original["reference"])
    properties = {scalar(p, "name"): scalar(p, "value") for p in children(component, "property")}
    assert properties == {
        "noscheme_identity": "indicators[0].resistor",
        "noscheme_path": "channels#[1][2].renamed.resistor",
    }
    assert scalar(component, "tstamps") == original["uuid"]
    fields = children(children(component, "fields")[0], "field")
    assert {scalar(f, "name"): f[2] for f in fields} == properties
    assert state(project)["entries"] == original_state
    assert children(netlist, "nets") == children(original_netlist, "nets")


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
    before = (project.config.path.parent / "noscheme.default.state.json").read_bytes()
    with pytest.raises(DiagnosticError, match="reserved"):
        export_project(project, added, records)
    assert (project.config.path.parent / "noscheme.default.state.json").read_bytes() == before


@pytest.mark.parametrize("failure", ["unknown", "pad", "bad-state"])
def test_validation_failure_preserves_outputs(setup, failure):
    project, circuit, records = setup
    export_project(*setup)
    net = project.config.output_dir / "board.net"
    before = net.read_bytes()
    if failure == "bad-state":
        (project.config.path.parent / "noscheme.default.state.json").write_text("{bad")
    else:
        snapshots = json.loads(records.read_text())
        record = next(iter(snapshots["footprints"].values()))
        if failure == "unknown":
            record["exists"] = False
        else:
            record["pads"].append("99")
        records.write_text(json.dumps(snapshots))
    state_before = (project.config.path.parent / "noscheme.default.state.json").read_bytes()
    with pytest.raises(DiagnosticError):
        export_project(*setup)
    assert net.read_bytes() == before
    assert (project.config.path.parent / "noscheme.default.state.json").read_bytes() == state_before


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
    statefile = project.config.path.parent / "noscheme.default.state.json"
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
    """`/` separates block instances (KiCad sheets); `.` joins steps within one."""
    adapter = KiCadNames([("bank", 0)])
    for path, expected in [
        (["#supply"], "#supply"),
        (["bank", 0, "A#"], "/bank[0]/A#"),
        (["bank", 0, "pair", "a"], "/bank[0]/pair.a"),
        (["link", "a"], "/link.a"),
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


def test_text_changes_describe_value_footprint_and_connections(setup):
    project, circuit, records = setup
    export_project(*setup)
    part = circuit["parts"][0]
    part["value"] = {"type": "string", "value": "changed value"}
    old_footprint = part["metadata"]["footprint"]["value"]
    snapshot = json.loads(records.read_text())
    snapshot["footprints"]["Test:Replacement"] = snapshot["footprints"][old_footprint]
    records.write_text(json.dumps(snapshot))
    part["metadata"]["footprint"]["value"] = "Test:Replacement"
    connected = next(pin for pin in part["pins"].values() if pin["net"] is not None)
    old_net = connected["net"]
    connected["net"] = None
    report = export_project(*setup)
    text = (project.config.output_dir / "changes.txt").read_text()
    assert "value:" in text and '"changed value"' in text
    assert f'"{old_footprint}" -> "Test:Replacement"' in text
    assert f"{json.dumps(old_net)} -> unconnected" in text
    assert len(report["changes"]["changed"]) == 1
    export_project(*setup)
    assert (project.config.output_dir / "changes.txt").read_text() == "\n"


def test_libpart_pin_names_and_role_consensus(setup):
    project, circuit, _ = setup
    led_parts = [p for p in circuit["parts"] if p["definition"] == "parts.Led"]
    for part in led_parts:
        part["pins"]["1"]["metadata"]["drive"] = dict(
            part["pins"]["1"]["metadata"]["role"], value="high"
        )
    export_project(*setup)
    netlist = load(project.config.output_dir / "board.net")
    libparts = children(children(netlist, "libparts")[0], "libpart")
    led = next(p for p in libparts if scalar(p, "part") == "parts.Led")
    pins = children(children(led, "pins")[0], "pin")
    assert (scalar(pins[0], "num"), scalar(pins[0], "name"), scalar(pins[0], "type")) == (
        "1",
        "cathode",
        "open_emitter",
    )
    del led_parts[0]["pins"]["1"]["metadata"]["drive"]
    led_parts[0]["pins"]["1"]["metadata"]["role"]["value"] = ["digital_input"]
    export_project(*setup)
    netlist = load(project.config.output_dir / "board.net")
    led = next(
        p
        for p in children(children(netlist, "libparts")[0], "libpart")
        if scalar(p, "part") == "parts.Led"
    )
    assert scalar(children(children(led, "pins")[0], "pin")[0], "type") == "unspecified"


def test_probable_rename_is_advisory_and_never_reuses_identity(setup):
    project, circuit, _ = setup
    export_project(*setup)
    part = circuit["parts"][0]
    old_identity = part["identity"]
    old_uuid = state(project)["entries"][old_identity]["uuid"]
    part["identity"] = "renamed_led"
    report = export_project(*setup)
    assert report["probable_renames"] == [{"before": old_identity, "after": "renamed_led"}]
    assert state(project)["entries"]["renamed_led"]["uuid"] != old_uuid
    assert "identity was not preserved" in (project.config.output_dir / "changes.txt").read_text()


def test_probable_rename_requires_unique_exact_match():
    from noscheme.kicad.export import probable_renames

    part = {
        "footprint": "Test:R",
        "value": {"type": "string", "value": "1k"},
        "pins": {"1": "vcc", "2": "bus"},
    }
    assert probable_renames({"old": part}, {"new": part}) == [{"before": "old", "after": "new"}]
    assert probable_renames({"old": part}, {"new": part, "other": part}) == []
    assert probable_renames({"old": part, "other": part}, {"new": part}) == []
    for changed in ({"footprint": "Test:C"}, {"value": None}, {"pins": {"1": "gnd", "2": "bus"}}):
        assert probable_renames({"old": part}, {"new": part | changed}) == []


def test_no_connect_nodes_are_isolated_stable_and_collision_safe(setup):
    import sexpdata
    from noscheme.kicad.export import render

    project, circuit, records = setup
    export_project(*setup)
    allocated = state(project)
    part = circuit["parts"][0]
    pins = list(part["pins"])
    assert len(pins) >= 2
    for pin in pins[:2]:
        part["pins"][pin]["net"] = None
    part["pins"][pins[0]]["binding"] = "no_connect"
    part["pins"][pins[1]]["binding"] = "omitted"
    part["pins"][pins[1]]["metadata"]["use"] = {"value": "disallowed"}
    original = deepcopy(circuit)

    def exported_nets():
        text, _, _ = render(circuit, allocated)
        return children(children(sexpdata.loads(text), "nets")[0], "net")

    nets = exported_nets()
    nc = [n for n in nets if any(scalar(p, "pintype") == "no_connect" for p in children(n, "node"))]
    assert len(nc) == 2
    assert len({scalar(n, "name") for n in nc}) == 2
    assert all(len(children(n, "node")) == 1 for n in nc)
    assert {scalar(children(n, "node")[0], "pin") for n in nc} == set(pins[:2])
    assert circuit == original  # Export-only nets must not leak into the circuit/ERC model.
    part["path"] = ["renamed"]
    assert exported_nets() == nets

    # A real source net can use the generated name: never merge it with an NC.
    collision = scalar(nc[0], "name")
    circuit["nets"][0]["name"] = collision
    circuit["nets"][0]["path"] = [collision]
    old_name = original["nets"][0]["name"]
    for p in circuit["parts"]:
        for data in p["pins"].values():
            if data["net"] == old_name:
                data["net"] = collision
    collision_nets = exported_nets()
    assert len({scalar(n, "name") for n in collision_nets}) == len(collision_nets)
    assert any(scalar(n, "name") == collision + "_1" for n in collision_nets)


def test_unbound_pin_is_not_silently_marked_no_connect(setup):
    import sexpdata
    from noscheme.kicad.export import render

    project, circuit, records = setup
    export_project(*setup)
    part = circuit["parts"][0]
    pin = next(iter(part["pins"].values()))
    pin["net"] = None
    pin["binding"] = "omitted"
    text, _, _ = render(circuit, state(project))
    nets = children(children(sexpdata.loads(text), "nets")[0], "net")
    assert not any(
        scalar(node, "pintype") == "no_connect" for net in nets for node in children(net, "node")
    )
