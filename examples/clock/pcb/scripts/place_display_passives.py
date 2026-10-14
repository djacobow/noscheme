"""Place each digit's R-EXT and decoupling cap on the back, relative to its parts.

R-EXT (digits[k].current_resistor) goes just below the digit's bottom pin
row, its R-EXT pad under the gap between cathode pins 1 and 2 so the trace
from driver pin 15 runs straight down. The decoupling cap (digits[k].decouple)
goes just above the driver's top pin row, its ground pad over driver pin 1.

Run it in KiCad's PCB editor scripting console, with the board open:
    exec(open("examples/clock/pcb/scripts/place_display_passives.py").read())
or standalone on a closed board:
    python3 place_display_passives.py honest_clock.kicad_pcb
"""

import re
import sys

import pcbnew

MM = pcbnew.FromMM
MIL = 0.05  # spacing between neighbouring courtyards and pads, mm


def mm(v):
    return pcbnew.ToMM(v)


def by_path(board):
    parts = {}
    for fp in board.GetFootprints():
        path = fp.GetFieldText("noscheme_path") if fp.HasField("noscheme_path") else ""
        match = re.fullmatch(r"time_display\.digits\[(\d+)\]\.(\w+)", path)
        if match:
            parts[(int(match[1]), match[2])] = fp
    return parts


def put(fp, x, y=None, below=None, above=None):
    """Put a two-pad part on the back, pad 1 at the right, centred on x.

    With `below` (`above`), its courtyard's top (bottom) edge sits just past
    that y.
    """
    if not fp.IsFlipped():
        fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
    fp.SetOrientationDegrees(180)
    fp.SetPosition(pcbnew.VECTOR2I(MM(x), 0))
    yard = fp.GetCourtyard(pcbnew.B_CrtYd).BBox()
    half = mm(yard.GetHeight()) / 2
    y = below + MIL + half if below is not None else above - MIL - half
    fp.SetPosition(pcbnew.VECTOR2I(MM(x), MM(y)))


def place(board):
    parts = by_path(board)
    digits = sorted({k for k, _ in parts})
    for k in digits:
        driver, display = parts[(k, "driver")], parts[(k, "display")]
        pin1 = next(p for p in driver.Pads() if p.GetNumber() == "1")
        cathodes = {p.GetNumber(): p for p in display.Pads()}
        gap_x = (mm(cathodes["1"].GetPosition().x) + mm(cathodes["2"].GetPosition().x)) / 2
        # R-EXT pad (1) under the cathode 1-2 gap, ground pad (2) to its left.
        put(
            parts[(k, "current_resistor")],
            gap_x - 0.775,
            below=mm(cathodes["1"].GetBoundingBox().GetBottom()),
        )
        # Ground pad (2) over driver pin 1, logic pad (1) to its right.
        top = mm(driver.GetCourtyard(pcbnew.B_CrtYd).BBox().GetTop())
        put(parts[(k, "decouple")], mm(pin1.GetPosition().x) + 0.775, above=top)
    return len(digits)


if __name__ == "__main__":
    board = pcbnew.LoadBoard(sys.argv[1])
    print("placed", place(board), "digits")
    pcbnew.SaveBoard(sys.argv[1], board)
else:
    print("placed", place(pcbnew.GetBoard()), "digits")
    pcbnew.Refresh()
