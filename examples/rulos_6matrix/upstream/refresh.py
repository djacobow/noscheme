"""Refresh original connectivity and source hashes from a downloaded Rev B directory.

Uses Python's standard library; does not rewrite designs or KiCad footprints.
"""

import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


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
    (ROOT / "upstream/connectivity.json").write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    main()
