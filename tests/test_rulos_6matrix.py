"""Compare the array-based example to independently extracted Eagle connectivity."""

from collections import defaultdict
from dataclasses import replace
import hashlib
import json
from pathlib import Path

from noscheme.circuit import elaborate_project
from noscheme.erc import apply_erc
from noscheme.kicad.export import export_project
from noscheme.kicad.sexpr import children, load, scalar
from noscheme.resolve import resolve_project

PROJECT = Path(__file__).resolve().parents[1] / "examples/rulos_6matrix"


def test_source_connectivity_values_and_export(tmp_path):
    source = json.loads((PROJECT / "upstream/connectivity.json").read_text())
    mapping = json.loads((PROJECT / "upstream/instance_map.json").read_text())
    overrides = json.loads((PROJECT / "footprint_overrides.json").read_text())
    project, definitions = resolve_project(PROJECT)
    circuit, warnings = elaborate_project(project, definitions)
    assert not warnings
    assert apply_erc(circuit) == ()
    assert len(circuit["parts"]) == 93
    assert len(circuit["nets"]) == 107
    assert set(mapping.values()) == set(source["parts"])
    assert len(set(mapping.values())) == len(mapping)
    assert {p["identity"] for p in circuit["parts"]} == set(mapping)

    actual_nets = defaultdict(set)
    disconnected = set()
    for part in circuit["parts"]:
        original = mapping[part["identity"]]
        expected = source["parts"][original]
        assert set(part["pins"]) == set(expected["pads"])
        assert part["value"]["value"] == expected["value"]
        assert part["metadata"]["footprint"]["value"] == overrides.get(
            original, "Rulos_RevB:" + expected["package"]
        )
        for pad, pin in part["pins"].items():
            endpoint = f"{original}.{pad}"
            if pin["net"] is None:
                disconnected.add(endpoint)
            else:
                actual_nets[pin["net"]].add(endpoint)

    expected_nets = {frozenset(endpoints) for endpoints in source["nets"].values()}
    assert {frozenset(endpoints) for endpoints in actual_nets.values()} == expected_nets
    all_pads = {f"{ref}.{pad}" for ref, p in source["parts"].items() for pad in p["pads"]}
    assert disconnected == all_pads - set().union(*expected_nets)

    project = replace(
        project,
        config=replace(
            project.config, path=tmp_path / "noscheme.toml", output_dir=tmp_path / "build"
        ),
    )
    result = export_project(project, circuit, PROJECT / "footprints/footprints.json")
    assert result["omitted_without_footprints"] == []
    exported = load(tmp_path / "build/board.net")
    components = children(children(exported, "components")[0], "comp")
    assert len(components) == 93
    # Resolve export references back to original Eagle instances using saved state.
    state = json.loads((tmp_path / "noscheme.state.json").read_text())
    refs = {
        p["reference"]: mapping[identity]
        for identity, p in state["entries"].items()
        if identity in mapping
    }
    exported_nets = {
        frozenset(
            f"{refs[scalar(node, 'ref')]}.{scalar(node, 'pin')}" for node in children(net, "node")
        )
        for net in children(children(exported, "nets")[0], "net")
    }
    assert exported_nets == expected_nets


def test_local_footprints_match_snapshot():
    snapshot = json.loads((PROJECT / "footprints/footprints.json").read_text())
    assert len(snapshot["footprints"]) == 29
    assert sum(n.startswith("Rulos_RevB:") for n in snapshot["footprints"]) == 18
    for name, record in snapshot["footprints"].items():
        if not name.startswith("Rulos_RevB:"):
            continue
        path = PROJECT / "footprints/Rulos_RevB.pretty" / (name.split(":")[1] + ".kicad_mod")
        assert hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"]
        footprint = load(path)
        assert scalar(footprint, "layer") == "F.Cu"
        assert {p[1] for p in children(footprint, "pad") if p[1]} == set(record["pads"])
        assert all(not children(p, "net") for p in children(footprint, "pad"))


def test_passive_policy_and_stm32_swd_footprint():
    project, definitions = resolve_project(PROJECT)
    circuit, warnings = elaborate_project(project, definitions)
    assert not warnings
    mapping = json.loads((PROJECT / "upstream/instance_map.json").read_text())
    source = json.loads((PROJECT / "upstream/connectivity.json").read_text())
    by_reference = {mapping[p["identity"]]: p for p in circuit["parts"]}
    for ref, part in by_reference.items():
        original = source["parts"][ref]
        footprint = part["metadata"]["footprint"]["value"]
        if original["package"] == "R0603":
            assert footprint == "Resistor_SMD:R_0603_1608Metric"
        elif original["package"].startswith("C") and ref.split(":")[-1].startswith("C"):
            expected = {
                "0.1uF": "Capacitor_SMD:C_0603_1608Metric",
                "1uF": "Capacitor_SMD:C_0603_1608Metric",
                "10uF": "Capacitor_SMD:C_0805_2012Metric",
                "47uF": "Rulos_RevB:C1210",
                "": "Rulos_RevB:C0603",
            }
            assert footprint == expected[original["value"]]
    stm, definitions = resolve_project(PROJECT.parent / "stm32h523")
    other, _ = elaborate_project(stm, definitions)
    header = next(p for p in other["parts"] if p["identity"] == "swd.header")
    assert (
        by_reference["PROG"]["metadata"]["footprint"]["value"]
        == header["metadata"]["footprint"]["value"]
    )
    assert set(by_reference["PROG"]["pins"]) == set(header["pins"])
