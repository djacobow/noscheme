"""Fixture/reader tests. Synthetic boards here do not prove PCBNew imports."""

from copy import deepcopy
import json
from pathlib import Path

import pytest
import sexpdata

from experiments.kicad.classes import merge_membership
from experiments.kicad.fixtures import generate, netlist, variants
from experiments.kicad.inspect_board import compare, snapshot, validate
from experiments.kicad.sexpr import children, dump, form, scalar


def manifest(parts):
    return {"components": parts}


def synthetic_board(parts, net_offset=0):
    names = sorted({n for p in parts for n in p["pins"].values()})
    codes = {n: i + 1 + net_offset for i, n in enumerate(names)}
    footprints = []
    for i, p in enumerate(parts):
        if p["footprint"] is None:
            continue
        footprints.append(
            form(
                "footprint",
                p["footprint"],
                form("property", "Reference", p["ref"]),
                form("property", "Value", p["value"]),
                form("property", "noscheme_identity", p["identity"]),
                form("uuid", "board-" + p["uuid"]),
                form("path", "/" + p["uuid"]),
                form("at", 10 + i * 5, 20, 90),
                form("layer", "F.Cu"),
                *[
                    form(
                        "pad",
                        pin,
                        sexpdata.Symbol("smd"),
                        sexpdata.Symbol("rect"),
                        form("net", codes[net], net),
                    )
                    for pin, net in p["pins"].items()
                ],
            )
        )
    n = "/indicators[0]/anode"
    track = form(
        "segment",
        form("start", 10, 20),
        form("end", 15, 20),
        form("width", 0.25),
        form("layer", "F.Cu"),
        form("net", codes[n]),
        form("uuid", "track-uuid"),
    )
    return form(
        "kicad_pcb",
        form("version", 20260101),
        *[form("net", code, name) for name, code in codes.items()],
        *footprints,
        track,
    )


def saved(tmp_path, parts, filename="board.kicad_pcb", offset=0):
    path = tmp_path / filename
    path.write_text(dump(synthetic_board(parts, offset)))
    return snapshot(path)


def test_generation_is_deterministic_and_reordering_is_identical():
    cases = variants()
    assert netlist(cases["baseline"]) == netlist(cases["reordered"])
    assert netlist(cases["baseline"]) == netlist(variants()["baseline"])
    parsed = sexpdata.loads(netlist(cases["baseline"]))
    assert len(children(children(parsed, "components")[0], "comp")) == 5
    assert scalar(parsed, "version") == "E"


def test_identity_survives_rename_and_reference_change():
    cases = variants()
    for name in ("renamed", "ref_changed"):
        assert [p["uuid"] for p in cases[name]] == [p["uuid"] for p in cases["baseline"]]
    assert cases["renamed"][1]["source_path"] != cases["baseline"][1]["source_path"]
    assert cases["ref_changed"][1]["ref"] == "R17"


def test_omitted_footprint_is_retained_in_manifest_not_netlist():
    parts = variants()["unspecified_footprint"]
    assert len(parts) == 5
    parsed = sexpdata.loads(netlist(parts))
    refs = [scalar(p, "ref") for p in children(children(parsed, "components")[0], "comp")]
    assert set(refs) == {"J1", "R1", "R2", "D1"}


@pytest.mark.parametrize("variant", ["baseline", "renamed", "ref_changed"])
def test_compare_semantics_ignores_net_code_renumbering(tmp_path, variant):
    cases = variants()
    before = saved(tmp_path, cases["baseline"], "before.kicad_pcb")
    after = saved(tmp_path, cases[variant], "after.kicad_pcb", offset=10)
    result = compare(before, after, manifest(cases["baseline"]), manifest(cases[variant]))
    assert result["status"] == "pass", result
    assert result["baseline_copper_items_checked"] == 1


@pytest.mark.parametrize(
    "change", ["placement", "rotation", "layer", "uuid", "net", "duplicate", "missing_pad", "track"]
)
def test_comparator_detects_layout_or_connectivity_regressions(tmp_path, change):
    parts = variants()["baseline"]
    before = saved(tmp_path, parts)
    after = deepcopy(before)
    f = after["footprints"][0]
    if change == "placement":
        f["at"][0] += 1
    elif change == "rotation":
        f["at"][2] += 90
    elif change == "layer":
        f["layer"] = "B.Cu"
    elif change == "uuid":
        f["uuid"] = "replacement"
    elif change == "net":
        f["pads"][0]["net"] = "wrong"
    elif change == "duplicate":
        after["footprints"].append(deepcopy(f))
    elif change == "missing_pad":
        f["pads"].clear()
    elif change == "track":
        after["copper"].clear()
    result = compare(before, after, manifest(parts), manifest(parts))
    assert result["status"] == "fail", result


def test_no_route_does_not_claim_routing_preservation(tmp_path):
    parts = variants()["baseline"]
    before = saved(tmp_path, parts)
    before["copper"] = []
    result = compare(before, before, manifest(parts), manifest(parts))
    assert result["status"] == "fail"
    assert "not exercised" in result["errors"][-1]


def test_pin_pad_check_uses_exact_strings_and_all_repeated_pads(tmp_path):
    parts = variants()["baseline"]
    board = saved(tmp_path, parts)
    board["footprints"][0]["pads"].append({"number": "1", "net": "wrong"})
    errors, _ = validate(board, manifest(parts))
    assert any("pad 1" in e for e in errors)


def test_membership_merge_preserves_all_other_settings():
    project = {
        "board": {"settings": "kept"},
        "net_settings": {
            "classes": [
                {"name": "Power", "clearance": 0.31, "track_width": 0.8},
                {"name": "Signal", "track_width": 0.2},
            ],
            "netclass_patterns": [{"netclass": "Signal", "pattern": "other*"}],
            "netclass_assignments": {"other": ["Signal"]},
        },
    }
    original = deepcopy(project)
    result = merge_membership(project, {"v5": ["Power"]})
    assert project == original
    assert result["net_settings"]["netclass_assignments"]["v5"] == ["Power"]
    del result["net_settings"]["netclass_assignments"]["v5"]
    assert result == original
    with pytest.raises(ValueError, match="undefined classes"):
        merge_membership(project, {"v5": ["Unknown"]})


def test_local_footprint_conversion_and_invalid_cases(tmp_path):
    library = Path("/usr/share/kicad/footprints")
    if not library.exists():
        pytest.skip("KiCad footprint libraries not installed")
    cases = generate(tmp_path / "fixtures", library)
    for name, expected_checks in {
        "baseline": set(),
        "unspecified_footprint": {"unspecified_footprint"},
        "unknown_footprint": {"unknown_footprint"},
        "pad_mismatch": {"pin_without_pad", "pad_without_pin"},
    }.items():
        data = json.loads((tmp_path / "fixtures" / (name + ".json")).read_text())
        assert {d["check"] for d in data["footprint_observations"]} == expected_checks
        assert data["editor_validation"] == "pending"
    assert len(cases) == 12


def test_reads_real_kicad_demo_board():
    path = Path("/usr/share/kicad/demos/ecc83/ecc83-pp.kicad_pcb")
    if not path.exists():
        pytest.skip("KiCad demos not installed")
    data = snapshot(path)
    assert data["footprints"] and data["copper"]
    assert all(f["reference"] for f in data["footprints"])
