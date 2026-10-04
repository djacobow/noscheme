"""Refresh source evidence and footprints from a downloaded Rev B directory.

Run with the NoScheme environment. Footprint import also requires KiCad 10's
pcbnew module available to /usr/bin/python3. This does not rewrite .nosch files.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from noscheme.kicad.convert import footprint_record
from noscheme.kicad.sexpr import children, dump, form, load, tag

ROOT = Path(__file__).resolve().parents[1]


def clean_footprint(node, name, *, root=False):
    """Remove board associations; retain package geometry in local coordinates."""
    if not isinstance(node, list):
        return node
    result = []
    for item in node:
        if tag(item) in {"uuid", "net", "path", "sheetname", "sheetfile"}:
            continue
        if root and tag(item) == "at":
            continue
        if tag(item) == "property":
            item = item.copy()
            if item[1] == "Reference":
                item[2] = "REF**"
            elif item[1] == "Value":
                item[2] = name
        result.append(clean_footprint(item, name))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="directory containing 6matrix.sch/.brd/.pdf")
    parser.add_argument("--revision", required=True, help="upstream Git commit for these files")
    args = parser.parse_args()
    board_path = args.source / "6matrix.brd"
    board = ET.parse(board_path).find(".//board")
    elements = {e.get("name"): e for e in board.findall("elements/element")}
    pads = {}
    for library in board.findall("libraries/library"):
        for package in library.findall("packages/package"):
            pads[library.get("name"), package.get("name")] = sorted(
                p.get("name") for p in package if p.tag in {"pad", "smd"}
            )
    snapshot = {
        "revision": args.revision,
        "sha256": {
            name: hashlib.sha256((args.source / name).read_bytes()).hexdigest()
            for name in ("6matrix.sch", "6matrix.brd", "6matrix.pdf")
        },
        "parts": {
            name: {
                "value": e.get("value", ""),
                "package": e.get("package"),
                "pads": pads[e.get("library"), e.get("package")],
            }
            for name, e in elements.items()
        },
        "nets": {
            n.get("name"): sorted(
                p.get("element") + "." + p.get("pad") for p in n.findall("contactref")
            )
            for n in board.findall("signals/signal")
        },
    }
    packages = {e.get("package") for e in elements.values()}
    # Import with KiCad, then normalize back-side and rotated placements before
    # extracting library footprints. No board routing/placement is retained.
    with tempfile.TemporaryDirectory() as temporary:
        normalized = Path(temporary) / "normalized.kicad_pcb"
        subprocess.run(
            [
                "/usr/bin/python3",
                "-c",
                """import sys, pcbnew
board = pcbnew.LoadBoard(sys.argv[1], pcbnew.PCB_IO_MGR.EAGLE)
for fp in board.GetFootprints():
    if fp.IsFlipped():
        fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
    fp.SetOrientationDegrees(0)
    fp.SetPosition(pcbnew.VECTOR2I(0, 0))
pcbnew.SaveBoard(sys.argv[2], board)
""",
                str(board_path),
                str(normalized),
            ],
            check=True,
        )
        footprints = {}
        for fp in children(load(normalized), "footprint"):
            name = fp[1]
            # The importer adds board mounting holes as dummy footprints; they
            # are mechanical board features, not schematic components.
            if name not in packages or name in footprints:
                continue
            clean = clean_footprint(fp, name, root=True)
            clean.insert(2, form("version", 20260206))
            clean.insert(3, form("generator", "pcbnew"))
            footprints[name] = dump(clean)
        if set(footprints) != packages:
            raise RuntimeError("Eagle import did not produce every expected package")
    destination = ROOT / "footprints/Rulos_RevB.pretty"
    destination.mkdir(parents=True, exist_ok=True)
    for name, text in footprints.items():
        (destination / f"{name}.kicad_mod").write_text(text)
    records = {}
    for name in sorted(packages):
        key = "Rulos_RevB:" + name
        record = footprint_record(key, [ROOT / "footprints"])
        record["path"] = f"footprints/Rulos_RevB.pretty/{name}.kicad_mod"
        records[key] = record
    # Keep reviewed KiCad substitutions when refreshing the original packages.
    standard = ROOT / "kicad_lib/footprints.json"
    if standard.exists():
        records.update(json.loads(standard.read_text())["footprints"])
    (ROOT / "footprints/footprints.json").write_text(
        json.dumps({"schema_version": 1, "footprints": records}, indent=2) + "\n"
    )
    (ROOT / "upstream/connectivity.json").write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    main()
