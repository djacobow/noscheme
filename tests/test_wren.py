"""Independent connectivity reference for the repeated WREN output subsystem."""

from collections import defaultdict
import json
from pathlib import Path

from noscheme.circuit import elaborate_project
from noscheme.erc import apply_erc
from noscheme.resolve import resolve_project

PROJECT = Path(__file__).resolve().parents[1] / "examples/wren"


def test_wren_subsystem_matches_upstream_values_pins_and_connectivity():
    source = json.loads((PROJECT / "upstream/connectivity.json").read_text())
    mapping = json.loads((PROJECT / "upstream/instance_map.json").read_text())
    project, definitions = resolve_project(PROJECT)
    circuit, warnings = elaborate_project(project, definitions)
    assert not warnings
    assert len(circuit["parts"]) == 189
    assert len(circuit["nets"]) == 164
    assert len(source["external_endpoints"]) == 68
    assert set(mapping.values()) == set(source["parts"])
    assert len(set(mapping.values())) == len(mapping)
    assert {part["identity"] for part in circuit["parts"]} == set(mapping)
    nets = defaultdict(set)
    for part in circuit["parts"]:
        ref = mapping[part["identity"]]
        original = source["parts"][ref]
        assert part["value"]["value"] == original["value"]
        assert part["metadata"]["footprint"]["value"] == original["footprint"]
        assert set(part["pins"]) == set(original["pins"])
        for pin, data in part["pins"].items():
            assert data["net"] is not None
            nets[data["net"]].add(f"{ref}.{pin}")
    assert {frozenset(v) for v in nets.values()} == {frozenset(v) for v in source["nets"].values()}
    # This connectivity pilot explicitly defers electrical policy checks.
    diagnostics = apply_erc(circuit)
    assert diagnostics
    assert all(d.severity == "warning" and d.code == "erc.unchecked_net" for d in diagnostics)


def test_full_wren_matches_upstream_connectivity_and_no_connects():
    root = PROJECT / "full"
    source = json.loads((root / "reference.json").read_text())
    mapping = json.loads((root / "instance_map.json").read_text())
    project, definitions = resolve_project(root)
    circuit, warnings = elaborate_project(project, definitions)
    assert not warnings
    assert len(circuit["parts"]) == 1507
    assert len(circuit["nets"]) == 1309
    assert len(mapping) == len(set(mapping.values())) == len(source["parts"])
    assert set(mapping.values()) == set(source["parts"])
    assert {part["identity"] for part in circuit["parts"]} == set(mapping)
    assert any("[" in path for path in mapping)
    nets = defaultdict(set)
    no_connect = set()
    for part in circuit["parts"]:
        ref = mapping[part["identity"]]
        original = source["parts"][ref]
        assert part["value"]["value"] == original["value"]
        assert part["metadata"]["footprint"]["value"] == original["footprint"]
        assert set(part["pins"]) == set(original["pins"])
        for pin, data in part["pins"].items():
            if data["net"] is None:
                assert data["binding"] == "no_connect"
                no_connect.add((ref, pin))
            else:
                nets[data["net"]].add((ref, pin))
    assert len(nets) == len(circuit["nets"])
    assert {frozenset(v) for v in nets.values()} == {
        frozenset(tuple(endpoint) for endpoint in v) for v in source["nets"].values()
    }
    assert no_connect == {tuple(endpoint) for endpoint in source["no_connect"]}
    assert len(no_connect) == 491
    diagnostics = apply_erc(circuit)
    assert len(diagnostics) == 1309
    assert all(d.severity == "warning" and d.code == "erc.unchecked_net" for d in diagnostics)
