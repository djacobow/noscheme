"""Cluster placement for the Honest Clock board.

Each cluster has an anchor part and satellites. The anchor goes to the
nearest free spot to a target (board-relative mm, front view). Each satellite
goes as close as possible to the anchor pin it shares a net with (decoupling
caps first, one power pin each), or else to an already-placed part it shares
a signal net with. Placement checks courtyards, through-hole pads, mounting
hole screw heads, the board outline and the stand band.

    python3 place_clusters.py board.kicad_pcb
    python3 place_clusters.py board.kicad_pcb --only C30 R13 ...

With --only, just the named parts move: a named anchor goes to its target,
and a named satellite is re-placed around its anchor where the anchor now
sits. Every other part stays put. Adding --here nudges the named anchors to
the nearest free spot around where they already are instead.

    python3 place_clusters.py board.kicad_pcb --only J8 J10 --here
"""

import math
import re
import sys
from collections import defaultdict

import pcbnew

MM, TO = pcbnew.FromMM, pcbnew.ToMM
GROUND = {"gnd"}
RAILS = {"gnd", "v3v3", "v5", "vbus", "v3v3_quiet"}
CLEAR = 0.1  # courtyard-to-obstacle clearance
EDGE = 0.5  # courtyard to board edge
HOLE_MARGIN = {"H": 3.3, "NPTH": 1.0}  # screw heads and washers around holes
STEP = 0.25

# Clusters, in placement order. side: "B" or "F". at: target for the anchor
# (board-relative, front view); None means "near what it connects to". An
# anchor listed again only adds satellites. Satellites are placed in order.
MCU_PASSIVES = [
    "C15",
    "C16",
    "C17",
    "C18",
    "C19",
    "C20",
    "C21",
    "C22",
    "C25",
    "C26",
    "C24",
    "C12",
    "Y1",
    "C13",
    "C14",
    "R4",
    "C23",
]
# Line terminations first: they matter most near the PHY's line pins, which
# share one side with the regulator feedback pin and the reserved pins.
PHY_PASSIVES = [
    "R8",
    "R9",
    "R10",
    "R11",
    "C35",
    "C36",
    "C37",
    "C34",
    "C30",
    "C31",
    "C32",
    "C29",
    "R5",
    "R12",
    "R7",
    "R13",
    "R14",
    "R15",
]
# The jack's centre taps and LED anodes are its own pins, not the PHY's.
JACK_PASSIVES = ["C27", "C28", "R6", "R16"]
CLUSTERS = [
    # USB-C power entry: PD sink and ESD next to the receptacle.
    ("U25", "B", (17, 5), ["C72", "R46", "R45", "R44"]),
    ("U24", "B", (14, 12), []),
    ("D11", "B", (11, 1.5), ["C70", "C71"]),
    # Bucks: IC, inductor on SW, bootstrap, input caps on VIN/GND, output caps.
    ("U2", "B", (35, 5), ["C8", "C7", "C6", "L2", "C9", "C10"]),
    ("U1", "B", (54, 5), ["C3", "C2", "C1", "L1", "C4", "C5"]),
    # MCU on the front: supply decoupling beside its pins on the front (see
    # FRONT); the rest of its passives on the back, under the pins they serve.
    ("U3", "F", None, MCU_PASSIVES),
    # Buttons on the front, left of the MCU; pull-ups behind them.
    ("SW1", "F", (103.5, 45), ["R1"]),
    ("SW2", "F", (115, 45), ["R2"]),
    ("SW3", "F", (126.5, 45), []),
    # Ethernet right of the MCU: PHY, then the vertical jack beyond it, then
    # the PHY's passives between them.
    ("U4", "B", (158, 48), PHY_PASSIVES[:8]),
    ("J2", "B", (184, 48), []),
    ("U4", "B", None, PHY_PASSIVES[8:]),
    ("J2", "B", None, JACK_PASSIVES),
    ("Y2", "B", (153, 57), ["C33"]),
    # Through-hole parts stay out from behind the OLED, where their pins would
    # reach the module, so both SMAs sit in the strip left of it.
    # GPS beside its antenna SMA.
    ("J5", "B", (20, 24), []),
    ("U6", "B", (35, 24), ["L3", "R17", "C43", "C42"]),
    # Timebase: 10 MHz SMA and its buffers, oscillators by the OCXO, trim
    # chain toward the OCXO's EFC pin.
    ("J6", "B", (20, 44), ["R39"]),
    ("U20", "B", (31, 41), ["R38", "C61"]),
    ("U17", "B", (31, 49), ["C54", "C55", "R32", "R33"]),
    ("U19", "B", (39, 45), ["C58", "R31"]),
    ("U18", "B", (60, 36), ["C57", "C56", "R34", "R35"]),
    ("Y4", "B", (66, 37), ["C69", "C68", "R43"]),
    ("U5", "B", (60, 26), ["C40", "C41"]),
    ("Y3", "B", None, ["C59", "C60", "R36", "R37"]),
    ("U21", "B", (72, 44), ["C62", "R41", "R42"]),
    ("U23", "B", (58, 52), ["C66", "C67"]),
    ("U22", "B", (66, 52), ["C64", "C63", "R40", "C65"]),
    ("JP1", "B", (78, 50), []),
    ("JP2", "B", (84, 50), []),
    # Debug and probe headers on the back.
    ("J1", "B", (124, 52), []),
    # Probe headers (surface mount) beside their signals' sources.
    ("J10", "B", (6, 17), []),  # power: by the USB-C and the PD sink
    ("J8", "B", (37, 33), []),  # GPS UART and PPS: under the GPS
    ("J11", "B", (63, 48), []),  # EFC, direction, quiet rail: among the trim parts
    ("J9", "B", (100, 52), []),  # LED chain: by the MCU and the first driver
    # OLED supply caps at its header.
    ("J3", "F", None, ["C39", "C38"]),
    # Light sensor faces the front; its load and filter go behind it.
    ("Q1", "F", (206, 46), ["R3", "C11"]),
]
SATELLITE_SIDE = "B"
# Satellites that sit on the front instead: the MCU is on the front, so its
# supply decoupling (VDD, VDDA, bulk, VCAP) goes beside its pins there.
FRONT = {"C15", "C16", "C17", "C18", "C19", "C20", "C21", "C22", "C24", "C12", "C25", "C26"}
KEEP = {"U3", "Y3", "J3"}  # anchors already placed by hand


class Board:
    def __init__(self, path, moving):
        self.board = pcbnew.LoadBoard(path)
        box = self.board.GetBoardEdgesBoundingBox()
        self.x0, self.y0 = TO(box.GetX()), TO(box.GetY())
        self.x1, self.y1 = TO(box.GetRight()), TO(box.GetBottom())
        band = next(z for z in self.board.Zones() if z.GetZoneName() == "stand band")
        self.floor = TO(band.GetBoundingBox().GetY())
        self.fps = {f.GetReference(): f for f in self.board.GetFootprints()}
        self.moving = moving
        self.blocks = {"F": [], "B": []}
        self.shapes = {}
        for ref, fp in self.fps.items():
            if (ref not in self.moving or ref in KEEP) and self.on_board(fp):
                self.block(fp)

    def on_board(self, fp):
        p = fp.GetPosition()
        return self.x0 <= TO(p.x) <= self.x1 and self.y0 <= TO(p.y) <= self.y1

    def block(self, fp):
        side = "B" if fp.IsFlipped() else "F"
        yard = fp.GetCourtyard(pcbnew.B_CrtYd if side == "B" else pcbnew.F_CrtYd)
        if yard.OutlineCount():
            b = yard.BBox()
            self.blocks[side].append(
                (TO(b.GetX()), TO(b.GetY()), TO(b.GetRight()), TO(b.GetBottom()))
            )
        for pad in fp.Pads():
            attr = pad.GetAttribute()
            if attr not in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
                continue
            b = pad.GetBoundingBox()
            m = 0.3
            if attr == pcbnew.PAD_ATTRIB_NPTH:
                m = HOLE_MARGIN["NPTH"]
            if re.fullmatch(r"H\d+", fp.GetReference()):
                m = HOLE_MARGIN["H"]
            r = (TO(b.GetX()) - m, TO(b.GetY()) - m, TO(b.GetRight()) + m, TO(b.GetBottom()) + m)
            self.blocks["F"].append(r)
            self.blocks["B"].append(r)

    def shape(self, ref, side, rot):
        """Courtyard box and pad centres relative to the position, per side and rotation."""
        key = (ref, side, rot)
        if key not in self.shapes:
            fp = self.fps[ref]
            if fp.IsFlipped() != (side == "B"):
                fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
            fp.SetOrientationDegrees(rot)
            fp.SetPosition(pcbnew.VECTOR2I(0, 0))
            yard = fp.GetCourtyard(pcbnew.B_CrtYd if side == "B" else pcbnew.F_CrtYd).BBox()
            box = (TO(yard.GetX()), TO(yard.GetY()), TO(yard.GetRight()), TO(yard.GetBottom()))
            pads = defaultdict(list)
            for pad in fp.Pads():
                p = pad.GetPosition()
                pads[pad.GetNetname()].append((pad.GetNumber(), TO(p.x), TO(p.y)))
            tht = [
                (
                    TO(pd.GetBoundingBox().GetX()),
                    TO(pd.GetBoundingBox().GetY()),
                    TO(pd.GetBoundingBox().GetRight()),
                    TO(pd.GetBoundingBox().GetBottom()),
                )
                for pd in fp.Pads()
                if pd.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH)
            ]
            self.shapes[key] = (box, pads, tht)
        return self.shapes[key]

    def free(self, side, box, tht=()):
        x0, y0, x1, y1 = box
        if (
            x0 < self.x0 + EDGE
            or x1 > self.x1 - EDGE
            or y0 < self.y0 + EDGE
            or y1 > self.floor - EDGE
        ):
            return False
        for a in self.blocks[side]:
            if x0 < a[2] + CLEAR and a[0] < x1 + CLEAR and y0 < a[3] + CLEAR and a[1] < y1 + CLEAR:
                return False
        other = "F" if side == "B" else "B"
        for t in tht:  # through-hole pins of the part also land on the other side
            for a in self.blocks[other]:
                if t[0] < a[2] and a[0] < t[2] and t[1] < a[3] and a[1] < t[3]:
                    return False
        return True

    def commit(self, ref, side, rot, x, y):
        fp = self.fps[ref]
        if fp.IsFlipped() != (side == "B"):
            fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
        fp.SetOrientationDegrees(rot)
        fp.SetPosition(pcbnew.VECTOR2I(MM(x), MM(y)))
        self.block(fp)
        self.placed[ref] = side

    def settled(self):
        """Parts with final positions: placed by this run, or not moving at all."""
        fixed = [r for r, fp in self.fps.items() if r not in self.moving and self.on_board(fp)]
        return [*self.placed, *fixed]

    def pads_at(self, ref):
        fp = self.fps[ref]
        out = defaultdict(list)
        for pad in fp.Pads():
            p = pad.GetPosition()
            out[pad.GetNetname()].append((pad.GetNumber(), TO(p.x), TO(p.y)))
        return out


def rings(radius, step=STEP):
    pts = [
        (dx * step, dy * step)
        for dx in range(-int(radius / step), int(radius / step) + 1)
        for dy in range(-int(radius / step), int(radius / step) + 1)
    ]
    pts = [p for p in pts if math.hypot(*p) <= radius]
    pts.sort(key=lambda p: math.hypot(*p))
    return pts


NEAR = rings(9.0)
FAR = rings(30.0, 0.5)


def best(board, ref, side, score, centre, margin=0.0, search=NEAR, rots=(0, 90, 180, 270)):
    """Lowest-score free spot near `centre` for each rotation."""
    choice = None
    for dx, dy in search:
        if choice and math.hypot(dx, dy) > choice[0] + 3:
            break
        for rot in rots:
            box, pads, tht = board.shape(ref, side, rot)
            x, y = centre[0] + dx, centre[1] + dy
            b = (box[0] + x - margin, box[1] + y - margin, box[2] + x + margin, box[3] + y + margin)
            t = [(a + x, c + y, e + x, g + y) for a, c, e, g in tht]
            if not board.free(side, b, t):
                continue
            s = score(pads, x, y)
            if choice is None or s < choice[0]:
                choice = (s, rot, x, y)
    return choice


def main(path, only=None, here=False):
    clusters = CLUSTERS
    if only:
        # Named anchors go to their targets; other anchors stay where they are.
        # Only the named parts move.
        clusters = [
            (a, s, at if a in only else None, [r for r in sats if r in only])
            for a, s, at, sats in CLUSTERS
        ]
        clusters = [c for c in clusters if c[0] in only or c[3]]
        moving = set(only)
    else:
        moving = {r for a, _, _, sats in CLUSTERS for r in (a, *sats)}
    board = Board(path, moving)
    board.placed = {}
    if only:
        for anchor, *_ in clusters:
            if anchor not in only:
                board.placed[anchor] = "B" if board.fps[anchor].IsFlipped() else "F"
    claimed = defaultdict(set)  # anchor -> pins already served by a satellite
    report = []
    for anchor, side, target, sats in clusters:
        if anchor in board.placed:
            place_satellites(board, anchor, sats, claimed, report)
            continue
        # Anchor: near its target, oriented so its signal pins face what they connect to.
        links = []
        for net, pins in board.pads_at(anchor).items():
            if net in RAILS or not net or net.startswith("unconnected"):
                continue
            for ref in board.settled():
                if ref == anchor:
                    continue
                for _, px, py in board.pads_at(ref).get(net, []):
                    links.append((net, px, py))
        rots = (0, 90, 180, 270)
        if here:
            # Nudge: nearest free spot to where the part is now, same rotation
            # if possible.
            fp = board.fps[anchor]
            p, current = fp.GetPosition(), round(fp.GetOrientationDegrees()) % 360
            target = (TO(p.x) - board.x0, TO(p.y) - board.y0)
            rots = (current, *[r for r in rots if r != current])
            links = []
        if target is None:
            if anchor in KEEP:
                board.placed[anchor] = "B" if board.fps[anchor].IsFlipped() else "F"
                report.append((anchor, "kept"))
                place_satellites(board, anchor, sats, claimed, report)
                continue
            pts = [(x, y) for _, x, y in links]
            centre = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        else:
            centre = (board.x0 + target[0], board.y0 + target[1])

        def anchor_score(pads, x, y, centre=centre):
            s = math.hypot(x - centre[0], y - centre[1])
            for net, lx, ly in links:
                for _, px, py in pads.get(net, []):
                    s += 0.15 * math.hypot(px + x - lx, py + y - ly)
            return s

        margin = 1.6 if sats and not here else 0.3
        choice = best(board, anchor, side, anchor_score, centre, margin, FAR, rots)
        if choice is None:
            report.append((anchor, "NO ROOM"))
            continue
        _, rot, x, y = choice
        board.commit(anchor, side, rot, x, y)
        report.append((anchor, f"{side} {rot:3d} ({x - board.x0:.1f}, {y - board.y0:.1f})"))
        place_satellites(board, anchor, sats, claimed, report)
    pcbnew.SaveBoard(path, board.board)
    for line in report:
        print(*line)


def place_satellites(board, anchor, sats, claimed, report):
    for ref in sats:
        side = "F" if ref in FRONT else SATELLITE_SIDE
        mine = board.shape(ref, side, 0)[1]
        nets = [n for n in mine if n and n not in GROUND]
        apads = board.pads_at(anchor)
        targets = []  # (my net, x, y, weight)
        shared = [n for n in nets if n in apads]
        if shared:
            signal = [n for n in shared if n not in RAILS]
            net = (signal or shared)[0]
            pins = apads[net]
            free = [p for p in pins if p[0] not in claimed[anchor]] or pins
            # Nearest unclaimed pin to the anchor's other placed satellites is arbitrary;
            # take them in pad order so decouplers spread around the part.
            pin = sorted(free, key=lambda p: (len(p[0]), p[0]))[0]
            if net in RAILS:
                claimed[anchor].add(pin[0])
            targets.append((net, pin[1], pin[2], 1.0))
            # Ground pad toward the anchor's nearest ground pin.
            if "gnd" in mine and "gnd" in apads:
                g = min(apads["gnd"], key=lambda q: math.hypot(q[1] - pin[1], q[2] - pin[2]))
                targets.append(("gnd", g[1], g[2], 0.3))
        else:
            for net in nets:
                if net in RAILS:
                    continue
                for other in board.settled():
                    for _, px, py in board.pads_at(other).get(net, []):
                        targets.append((net, px, py, 1.0))
            if not targets:
                p = board.fps[anchor].GetPosition()
                targets.append((None, TO(p.x), TO(p.y), 1.0))
        centre = (
            sum(t[1] for t in targets) / len(targets),
            sum(t[2] for t in targets) / len(targets),
        )

        def score(pads, x, y, targets=targets):
            s = 0.0
            for net, tx, ty, w in targets:
                if net is None:
                    s += math.hypot(x - tx, y - ty)
                    continue
                d = min(
                    math.hypot(px + x - tx, py + y - ty) for _, px, py in pads.get(net, [(0, 0, 0)])
                )
                s += w * d
            return s

        choice = best(board, ref, side, score, targets[0][1:3] if targets[0][0] else centre)
        if choice is None:
            choice = best(board, ref, side, score, centre, search=FAR)
        if choice is None:
            report.append((ref, "NO ROOM"))
            continue
        s, rot, x, y = choice
        board.commit(ref, side, rot, x, y)
        report.append(
            (ref, f"  -> {anchor} {rot:3d} ({x - board.x0:.1f}, {y - board.y0:.1f}) score {s:.1f}")
        )


if __name__ == "__main__":
    args = sys.argv[1:]
    here = "--here" in args
    args = [a for a in args if a != "--here"]
    main(args[0], set(args[2:]) if args[1:2] == ["--only"] else None, here)
