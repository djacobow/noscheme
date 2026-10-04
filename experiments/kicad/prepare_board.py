"""Create an EMPTY scratch board with an outline, using KiCad's Python runtime.

Run with /usr/bin/python3, not the experiment venv. This does not import a netlist
and must never be treated as proof of the editor import path.
"""

import argparse
import json
from pathlib import Path


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("output", type=Path)
    args = cli.parse_args()
    path = args.output.resolve()
    project = path.with_suffix(".kicad_pro")
    if path.exists() or project.exists():
        cli.error("refusing to overwrite an existing board or project")
    path.parent.mkdir(parents=True, exist_ok=True)
    import pcbnew

    board = pcbnew.BOARD()
    corners = [(30, 30), (80, 30), (80, 60), (30, 60)]
    for a, b in zip(corners, corners[1:] + corners[:1]):
        edge = pcbnew.PCB_SHAPE()
        edge.SetShape(pcbnew.SHAPE_T_SEGMENT)
        edge.SetLayer(pcbnew.Edge_Cuts)
        edge.SetWidth(pcbnew.FromMM(0.05))
        edge.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(a[0]), pcbnew.FromMM(a[1])))
        edge.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(b[0]), pcbnew.FromMM(b[1])))
        board.Add(edge)
    if not pcbnew.SaveBoard(str(path), board):
        raise RuntimeError("KiCad did not save the board")
    # The editor owns the eventual detailed settings; no schematic is created.
    project.write_text(
        json.dumps({"meta": {"filename": project.name, "version": 1}}, indent=2) + "\n"
    )
    print(f"Created empty scratch board {path}; zero footprints, no import performed.")


if __name__ == "__main__":
    main()
