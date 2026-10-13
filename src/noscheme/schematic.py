"""Single-sheet SVG schematics with every block drawn open.

Each block instance is a box with its ports on the edge and its contents laid
out inside ("white box"), nested to any depth, on one sheet as large as the
design needs. Identical block instances share one layout.

Placement is annealed per block to shorten wires: bundles weigh more than
single nets, and rails only pull weakly. Pin sides belong to the component
definition, so every instance of a part draws alike; they and the block port
sides are re-chosen between placement passes from where each connection lies.

Wires run on a grid in eight directions, so runs are orthogonal or diagonal
and bends are 45 or 90 degrees. A dot marks every junction; crossings carry no
mark. A bundle port is one terminal and its connection one thick bus; a field
leaves the bus through a labelled 45-degree breakout. Rails are not wired:
each rail pin carries a power or ground flag. Wires avoid symbols through a
high cost rather than a ban, except that a net may cross the members of an
instance array it connects to.
"""

from dataclasses import dataclass, field
import heapq
import math
import random
import re
import zlib
from xml.sax.saxutils import escape

from .hierarchy import FILLS, RAIL_TYPES, build_tree, declaration_order, net_types
from .kicad.export import publish, validate_export_paths

G = 10  # routing grid
P = 2 * G  # pin and port pitch
STUB = 2 * G
FONT = 11
SMALL = 9
LINE = FONT + 3
CHAR = 0.6  # monospace advance per unit of font size
SIDES = "LRTB"
NORMAL = {"L": (-1, 0), "R": (1, 0), "T": (0, -1), "B": (0, 1)}
# Eight directions, clockwise from east; a direction's axis is its index mod 4.
DIRS = ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))
OUTWARD = {"R": 0, "B": 2, "L": 4, "T": 6}
WIRE = "#1B7F3B"
BUS = "#2B5797"
BODY = "#A0302A"
BODY_FILL = "#FFFDE8"
POWER = "#B03030"
GROUND = "#444444"
# Anneal weights per connection kind, and routing costs per grid step.
BUS_WEIGHT, NET_WEIGHT, RAIL_WEIGHT, GRAVITY = 3.0, 1.0, 0.1, 0.3
COMPACT = 0.75  # per item, per unit of the contents' half-perimeter
DIAGONAL, TURN_45, TURN_90, CROSSING = 1.8, 0.4, 2.5, 1.5
OVER_SYMBOL, OVER_ARRAY = 80.0, 1.0
FREE, SOFT, HARD = 0, 1, 2
SEARCH_LIMIT = 400_000


def text_width(text, size=FONT):
    return len(plain(text)) * size * CHAR


def up(value, step=G):
    return int(math.ceil(value / step - 1e-9)) * step


def snap(value, step=G):
    return int(round(value / step)) * step


def plain(name):
    """KiCad overbar markup `~{OE}` reads as OE in widths and hover text."""
    return re.sub(r"~\{([^}]*)\}", r"\1", name)


def marked_up(name):
    """SVG text for a pin name, drawing `~{OE}` with an overline."""
    pieces = re.split(r"~\{([^}]*)\}", name)
    return "".join(
        f'<tspan text-decoration="overline">{escape(p)}</tspan>' if i % 2 else escape(p)
        for i, p in enumerate(pieces)
    )


def natural(key):
    return tuple((0, int(t), "") if t.isdigit() else (1, 0, t) for t in re.findall(r"\d+|\D+", key))


def show_path(path):
    out = ""
    for step in path:
        out += f"[{step}]" if isinstance(step, int) else (f".{step}" if out else str(step))
    return out


def array_key(path):
    """Items of one instance array share their path with indices removed."""
    return tuple(step for step in path if not isinstance(step, int))


# --------------------------------------------------------------------------
# Model


@dataclass
class Term:
    """A connection point on an item's edge: one net, or a bundle's fields."""

    key: object
    nets: tuple
    fields: tuple
    side: str
    x: float = 0
    y: float = 0

    @property
    def bundle(self):
        return len(self.nets) > 1


@dataclass
class Symbol:
    """A component definition's drawing; sides and order are per definition."""

    definition: str
    keys: list
    names: dict
    sides: dict = field(default_factory=dict)
    rank: dict = field(default_factory=dict)

    @property
    def named(self):
        """Pin names are drawn on multi-pin bodies; two-pin bodies show the value."""
        return not self.small and any(plain(self.names[k]) != k for k in self.keys)

    @property
    def small(self):
        return len(self.keys) <= 2


@dataclass
class Item:
    name: str
    kind: str  # "part" or "block"
    path: tuple
    w: float = 0
    h: float = 0
    x: float = 0
    y: float = 0
    terms: dict = field(default_factory=dict)
    margin: dict = field(default_factory=dict)
    drawing: dict = field(default_factory=dict)
    template: object = None
    part: dict = None
    rails: list = field(default_factory=list)  # (net, kind, x, y) points for the rail pull
    rotation: int = 0  # quarter turns clockwise, parts only
    turn: object = None  # rotation -> the same part redrawn at that rotation


@dataclass
class Template:
    """The laid-out contents of every block instance with one signature."""

    signature: str
    node: object
    title: str
    definition: str
    rails: str
    items: list = field(default_factory=list)
    ports: dict = field(default_factory=dict)  # name -> Term on the box edge
    lanes: dict = field(default_factory=dict)  # name -> lane end (x, y), where wires start
    w: float = 0
    h: float = 0
    header: tuple = (0, 0)
    uses: int = 0
    routes: dict = field(default_factory=dict)


class Design:
    def __init__(self, circuit, *, seed, effort):
        types = net_types(circuit)
        self.rail = {
            name: "ground" if "ground" in kinds else "power"
            for name, kinds in types.items()
            if kinds & RAIL_TYPES
        }
        self.circuit = circuit
        self.root = build_tree(circuit)
        self.seed, self.effort = seed, effort
        self.symbols = {}
        self.port_sides = {}
        self.port_rank = {}  # signature -> port -> mean offset of its connections
        self.previous = {}
        self.templates = {}
        self.signatures = {}

    # -- signatures -------------------------------------------------------

    def signature(self, node):
        """Equal signatures draw identically: same parts, values, and topology."""
        key = node.path
        if key in self.signatures:
            return self.signatures[key]
        tokens = {}
        for port, entries in node.ports.items():
            for i, entry in enumerate(entries):
                tokens.setdefault(entry["net"], ("port", port, i))

        def token(net):
            if net is None:
                return None
            if net in self.rail:
                return ("rail", net)
            return tokens.setdefault(net, ("local", len(tokens)))

        parts = []
        for part in node.parts:
            pins = sorted((k, token(p["net"]), p["binding"]) for k, p in part["pins"].items())
            parts.append(
                (relative(part["path"], node.path), part["definition"], value_text(part), pins)
            )
        children = []
        for child in node.children:
            ports = sorted(
                (port, tuple(token(e["net"]) for e in entries))
                for port, entries in child.ports.items()
            )
            children.append((relative(child.path, node.path), self.signature(child), ports))
        own = sorted(
            (port, tuple(token(e["net"]) for e in entries)) for port, entries in node.ports.items()
        )
        text = repr((node.definition, own, parts, children))
        digest = f"{node.definition}#{zlib.crc32(text.encode()):08x}"
        self.signatures[key] = digest
        return digest

    # -- symbols ----------------------------------------------------------

    def symbol(self, part):
        definition = part["definition"]
        if definition not in self.symbols:
            names = {}
            for key, pin in part["pins"].items():
                aliases = pin.get("metadata", {}).get("aliases", {}).get("value") or ()
                names[key] = aliases[0] if aliases else key
            keys = sorted(part["pins"], key=natural)
            symbol = Symbol(definition, keys, names)
            self.symbols[definition] = symbol
            initial_sides(symbol, part, self.rail)
        return self.symbols[definition]

    # -- layout -----------------------------------------------------------

    def layout(self, node, name):
        signature = self.signature(node)
        if signature in self.templates:
            return self.templates[signature]
        template = Template(
            signature,
            node,
            name,
            node.definition,
            rail_summary(node, self.rail),
        )
        for kind, child in members(node):
            if kind == "block":
                child_name = show_path(relative(child.path, node.path))
                inner = self.layout(child, child_name)
                template.items.append(block_item(child, child_name, inner, self.rail))
            else:
                template.items.append(part_item(child, node.path, self.symbol(child), self.rail))
        sides = self.port_sides.setdefault(signature, initial_port_sides(node, self.rail))
        for port, entries in node.ports.items():
            nets = tuple(e["net"] for e in entries if e["net"] not in self.rail)
            if nets:
                fields = tuple(
                    show_path(relative(e["path"], node.path))
                    for e in entries
                    if e["net"] not in self.rail
                )
                template.ports[port] = Term(port, nets, fields, sides[port])
        place(template, self.previous.get(signature, {}), self.rng(signature), self.effort)
        frame(template, self.port_rank.get(signature, {}))
        self.previous[signature] = {
            item.name: (item.x + item.w / 2, item.y + item.h / 2, item.rotation)
            for item in template.items
        }
        self.templates[signature] = template
        return template

    def rng(self, signature):
        return random.Random(zlib.crc32(f"{self.seed}:{signature}".encode()))

    def run(self, passes=3):
        board = self.circuit["board"]
        for index in range(passes):
            self.templates, self.signatures = {}, {}
            top = self.layout(self.root, board)
            count_uses(top, self.templates.values())
            if index + 1 < passes:
                refine(self)
        for template in self.templates.values():
            route(template)
        return top


def relative(path, prefix):
    return tuple(path[len(prefix) :])


def value_text(part):
    value = part.get("value")
    if isinstance(value, dict) and value.get("value") is not None:
        return str(value["value"])
    return part["definition"].rsplit(".", 1)[-1]


def members(node):
    """Child parts and blocks in declaration order."""
    entries = [("part", p, declaration_order(p, p["path"])) for p in node.parts]
    entries += [("block", c, c.order) for c in node.children]
    entries.sort(key=lambda e: e[2])
    return [(kind, child) for kind, child, _ in entries]


def rail_summary(node, rail):
    by_net = {}
    for port, entries in node.ports.items():
        for entry in entries:
            if entry["net"] in rail:
                label = show_path(relative(entry["path"], node.path))
                by_net.setdefault(entry["net"], []).append(label)
    return " · ".join(f"{net}: {', '.join(labels)}" for net, labels in by_net.items())


def initial_sides(symbol, part, rail):
    """Power up, ground down, outputs right, the rest split left and right."""
    if symbol.small:
        for key, side in zip(symbol.keys, "LR"):
            symbol.sides[key] = side
        return
    rest = []
    for key in symbol.keys:
        pin = part["pins"][key]
        roles = pin.get("metadata", {}).get("role", {}).get("value") or ()
        kind = rail.get(pin["net"])
        if kind == "power" or (kind is None and "power_input" in roles):
            symbol.sides[key] = "T"
        elif kind == "ground" or "ground" in roles:
            symbol.sides[key] = "B"
        elif any(r.endswith("output") for r in roles):
            symbol.sides[key] = "R"
        else:
            rest.append(key)
    for i, key in enumerate(rest):
        symbol.sides[key] = "L" if i < (len(rest) + 1) // 2 else "R"


def initial_port_sides(node, rail):
    ports = [p for p, es in node.ports.items() if any(e["net"] not in rail for e in es)]
    return {p: "L" if i < (len(ports) + 1) // 2 else "R" for i, p in enumerate(ports)}


# --------------------------------------------------------------------------
# Item geometry


def by_side(keys, sides, rank):
    return {
        s: sorted((k for k in keys if sides[k] == s), key=lambda k: (rank.get(k, 0), natural(k)))
        for s in SIDES
    }


def spread(length, count):
    """Offsets of `count` pins centred on a side of `length`, `P` apart."""
    start = (length - (count - 1) * P) / 2
    return [start + i * P for i in range(count)]


def turned(sides, rotation):
    """A symbol's side lists after `rotation` quarter turns clockwise.

    Left becomes top and right becomes bottom with their order reversed, since
    sides list pins left to right and top to bottom.
    """
    for _ in range(rotation % 4):
        sides = {
            "T": sides["L"][::-1],
            "R": sides["T"],
            "B": sides["R"][::-1],
            "L": sides["B"],
        }
    return sides


def unturn(vector, rotation):
    """Rotate a vector back by `rotation` clockwise quarter turns."""
    x, y = vector
    for _ in range(rotation % 4):
        x, y = y, -x
    return x, y


def part_item(part, parent, symbol, rail, rotation=0):
    """Draw a part; the symbol is per definition, the rotation per instance."""
    name = show_path(relative(part["path"], parent))
    value = value_text(part)
    sides = turned(by_side(symbol.keys, symbol.sides, symbol.rank), rotation)
    side_of = {key: side for side, keys in sides.items() for key in keys}
    count = {s: len(sides[s]) for s in SIDES}
    named = symbol.named

    def longest(side):
        if not named:
            return 0
        return max(
            (
                text_width(symbol.names[k], SMALL)
                for k in sides[side]
                if plain(symbol.names[k]) != k
            ),
            default=0,
        )

    def span(n):
        return n * P + (P if n > 1 else 0)

    vertical = symbol.small and (count["T"] or count["B"])
    if symbol.small:
        inside = up(text_width(value, SMALL) + G, P)
        bw = max(P, span(max(count["T"], count["B"])), 0 if vertical else inside, 2 * P)
        bh = max(P, span(max(count["L"], count["R"])), inside if vertical else 0)
        if vertical:
            bw = P
        else:
            bh = P
        labels = [(name, FONT, True)]
    else:
        # Pins on one side stay clear of the names running in from the
        # neighbouring sides, so the corners never collide.
        across = longest("L") + longest("R") + G
        along = longest("T") + longest("B") + G
        tb, lr = max(count["T"], count["B"]), max(count["L"], count["R"])
        bw = max(span(tb) + (across if tb else 0), across + 2 * G, 2 * P)
        bh = max(span(lr) + (along if lr else 0), along + 2 * G if tb else 0, 2 * P)
        labels = [(name, FONT, True), (value, SMALL, False)]
    if vertical:
        place_label = "beside"  # vertical two-pin part: the name runs up beside it
    else:
        place_label = "top" if not count["T"] else "bottom" if not count["B"] else "inside"
    label_w = max(text_width(t, s) for t, s, _ in labels)
    if place_label == "inside":
        bw = max(bw, label_w + longest("L") + longest("R") + 2 * G)
        bh = max(bh, longest("T") + longest("B") + len(labels) * LINE + 2 * G)
    bw, bh = up(bw, P), up(bh, P)
    band = up(len(labels) * LINE + 4) if place_label in ("top", "bottom") else 0
    stub = {s: STUB if count[s] else 0 for s in SIDES}
    # Rail flags extend past the stubs; reserve room on each side for them.
    flags = {s: 0 for s in SIDES}
    rails, ncs = {}, []
    for key in symbol.keys:
        pin = part["pins"][key]
        if pin["net"] in rail:
            rails[key] = (pin["net"], rail[pin["net"]])
            reach = G + 8 + text_width(pin["net"], SMALL)
            flags[side_of[key]] = max(flags[side_of[key]], up(reach))
        elif pin["binding"] == "no_connect":
            ncs.append(key)
    left, top = (
        stub["L"] + flags["L"],
        stub["T"] + flags["T"] + (band if place_label == "top" else 0),
    )
    width = max(left + bw + stub["R"] + flags["R"], label_w + flags["L"])
    height = top + bh + stub["B"] + flags["B"] + (band if place_label == "bottom" else 0)
    if place_label == "beside":
        # Centre the rotated name on the body; grow the item if it is longer.
        rise = up(max(0, label_w / 2 - (top + bh / 2)))
        top += rise
        width = left + bw + G / 2 + FONT
        height = max(height + rise, top + bh / 2 + label_w / 2)
    body = (left, top, bw, bh)
    points = {}
    for side in SIDES:
        along = bh if side in "LR" else bw
        for key, offset in zip(sides[side], spread(along, count[side])):
            x = {"L": left - STUB, "R": left + bw + STUB}.get(side, left + offset)
            y = {"T": top - STUB, "B": top + bh + STUB}.get(side, top + offset)
            points[key] = (x, y, side)
    if place_label == "top":
        label_at = (flags["L"], stub["T"] + flags["T"] + FONT)
    elif place_label == "bottom":
        label_at = (flags["L"], top + bh + stub["B"] + flags["B"] + FONT + 2)
    elif place_label == "beside":
        label_at = (left + bw + G / 2 + FONT - 1, top + bh / 2)
    else:
        label_at = (left + bw / 2, top + bh / 2 - (len(labels) - 1) * LINE / 2 + FONT / 2 - 1)
    item = Item(
        name,
        "part",
        tuple(part["path"]),
        up(width),
        up(height),
        part=part,
        rotation=rotation % 4,
        turn=lambda r: part_item(part, parent, symbol, rail, r),
    )
    for key, (x, y, side) in points.items():
        net = part["pins"][key]["net"]
        if net is not None and net not in rail:
            item.terms[key] = Term(key, (net,), (key,), side, x, y)
        elif key in rails:
            item.rails.append((rails[key][0], rails[key][1], x, y))
    item.drawing = {
        "symbol": symbol,
        "body": body,
        "points": points,
        "labels": labels,
        "label_at": label_at,
        "label_mode": place_label,
        "value": value,
        "vertical": vertical,
        "rails": rails,
        "ncs": ncs,
    }
    item.margin = clearance(item.terms.values())
    return item


def block_item(node, name, template, rail):
    item = Item(name, "block", node.path, template.w, template.h, template=template)
    for port, term in template.ports.items():
        entries = node.ports[port]
        nets = tuple(e["net"] for e in entries if e["net"] not in rail)
        item.terms[port] = Term(port, nets, term.fields, term.side, term.x, term.y)
    for entries in node.ports.values():
        for entry in entries:
            if entry["net"] in rail:
                item.rails.append((entry["net"], rail[entry["net"]], item.w / 2, item.h / 2))
    item.margin = clearance(item.terms.values())
    return item


def clearance(terms):
    """Space kept clear around an item, growing with the wires leaving each side."""
    count = {s: 0 for s in SIDES}
    for term in terms:
        count[term.side] += 2 if term.bundle else 1
    return {s: G + G * min(2, count[s] // 4) for s in SIDES}


# --------------------------------------------------------------------------
# Placement


def connections(template):
    """Group the container's terminals: net -> [(owner, term)], owner -1 is the box."""
    nets = {}
    for index, item in enumerate(template.items):
        for term in item.terms.values():
            for net in term.nets:
                nets.setdefault(net, []).append((index, term))
    for term in template.ports.values():
        for net in term.nets:
            nets.setdefault(net, []).append((-1, term))
    return nets


def buses(template, nets):
    """Union bundle terminals that share a net; each union is drawn as one bus."""
    owner = {}
    bundles = [
        (index, term)
        for index, item in enumerate(template.items)
        for term in item.terms.values()
        if term.bundle
    ] + [(-1, term) for term in template.ports.values() if term.bundle]
    parent = list(range(len(bundles)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, (_, term) in enumerate(bundles):
        for net in term.nets:
            if net in owner:
                parent[find(i)] = find(owner[net])
            else:
                owner[net] = i
    groups = {}
    for i, entry in enumerate(bundles):
        groups.setdefault(find(i), []).append(entry)
    result = []
    for members_ in groups.values():
        carried = {net for _, term in members_ for net in term.nets}
        singles = [(o, t) for net in sorted(carried) for o, t in nets.get(net, ()) if not t.bundle]
        if len(members_) > 1 or singles:
            result.append({"bundles": members_, "nets": carried, "singles": singles})
    return result


def place(template, previous, rng, effort):
    """Anneal the connected items, then cluster rail-only parts in a corner."""
    items = template.items
    if not items:
        return
    loose = [item for item in items if item.kind == "part" and not item.terms]
    core = [item for item in items if item.kind != "part" or item.terms]
    index = {id(item): i for i, item in enumerate(core)}
    nets = connections(template)
    groups = []
    in_bus = set()
    for bus in buses(template, nets):
        in_bus |= bus["nets"]
        groups.append((BUS_WEIGHT, bus["bundles"] + bus["singles"]))
    for net, terms in nets.items():
        if net not in in_bus and len(terms) > 1:
            groups.append((NET_WEIGHT, terms))
    anneal_nets = []
    for weight, terms in groups:
        points = [(index[id(items[o])], t.key) for o, t in terms if o >= 0]
        anchors = [t.side for o, t in terms if o < 0]
        anneal_nets.append((weight, points, anchors))
    rails = {}
    for i, item in enumerate(core):
        for j, (net, _, _, _) in enumerate(item.rails):
            rails.setdefault(net, []).append((i, ("rail", j)))
    for points in rails.values():
        if len(points) > 1:
            anneal_nets.append((RAIL_WEIGHT, points, []))
    if core:
        annealer = Annealer(core, anneal_nets, previous, rng, effort)
        annealer.run()
        chosen = {id(old): new for old, new in zip(core, annealer.items)}
        template.items = items = [chosen.get(id(item), item) for item in items]
        core = annealer.items
    corner(core, loose)
    # Normalise on the clearance boxes so items keep their clearance from the frame.
    left = min(i.x - i.margin["L"] for i in items)
    top = min(i.y - i.margin["T"] for i in items)
    for item in items:
        item.x, item.y = item.x - left, item.y - top


def corner(core, loose):
    """Shelf-pack rail-only parts, grouped by definition, at the bottom-right.

    The cluster goes below the annealed items or beside them, whichever
    leaves the smaller bounding box.
    """
    if not loose:
        return
    loose = sorted(loose, key=lambda item: (item.part["definition"], natural(item.name)))
    boxes = [
        (item.w + item.margin["L"] + item.margin["R"], item.h + item.margin["T"] + item.margin["B"])
        for item in loose
    ]
    area = sum(w * h for w, h in boxes)
    target = max(max(w for w, _ in boxes), math.sqrt(area * 1.5))
    spots, x, y, row = [], 0, 0, 0
    for w, h in boxes:
        if x and x + w > target:
            x, y, row = 0, y + row, 0
        spots.append((x, y))
        x, row = x + w, max(row, h)
    width = max(sx + w for (sx, _), (w, _) in zip(spots, boxes))
    height = max(sy + h for (_, sy), (_, h) in zip(spots, boxes))
    if core:
        x0 = min(i.x - i.margin["L"] for i in core)
        y0 = min(i.y - i.margin["T"] for i in core)
        x1 = max(i.x + i.w + i.margin["R"] for i in core)
        y1 = max(i.y + i.h + i.margin["B"] for i in core)
        below = (x1 - x0, max(width, x1 - x0) * (y1 - y0 + height))
        beside = (x1 - x0, (x1 - x0 + width) * max(height, y1 - y0))
        if below[1] <= beside[1]:
            ox, oy = max(x0, x1 - width), y1
        else:
            ox, oy = x1, max(y0, y1 - height)
    else:
        ox, oy = 0, 0
    for item, (sx, sy) in zip(loose, spots):
        item.x = snap(ox + sx + item.margin["L"])
        item.y = snap(oy + sy + item.margin["T"])


class Annealer:
    def __init__(self, items, nets, previous, rng, effort):
        self.items, self.nets, self.rng, self.effort = list(items), nets, rng, effort
        for i, item in enumerate(self.items):
            rotation = previous.get(item.name, (0, 0, 0))[2]
            if item.turn is not None and rotation != item.rotation:
                self.items[i] = item.turn(rotation)
        # Two-pin parts turn freely; larger symbols stay upright, power on top,
        # their pin sides already chosen per definition.
        self.turnable = [
            i
            for i, item in enumerate(self.items)
            if item.turn is not None and item.drawing["symbol"].small
        ]
        self.touch = [[] for _ in items]
        for k, (_, points, _) in enumerate(nets):
            for index in {p[0] for p in points}:
                self.touch[index].append(k)
        desired = self.initial(previous)
        self.legalize(desired)
        self.region()

    def inflated(self, i, x=None, y=None):
        item = self.items[i]
        x = item.x if x is None else x
        y = item.y if y is None else y
        m = item.margin
        return (x - m["L"], y - m["T"], x + item.w + m["R"], y + item.h + m["B"])

    def initial(self, previous):
        """Previous centres when known, else shelf rows in declaration order."""
        known = [it.name in previous for it in self.items]
        boxes = [self.inflated(i, 0, 0) for i in range(len(self.items))]
        area = sum((b[2] - b[0]) * (b[3] - b[1]) for b in boxes)
        target = max(max(b[2] - b[0] for b in boxes), math.sqrt(area * 1.6))
        desired, x, y, row = [], 0, 0, 0
        for i, item in enumerate(self.items):
            if known[i]:
                cx, cy, _ = previous[item.name]
                desired.append((snap(cx - item.w / 2), snap(cy - item.h / 2)))
                continue
            bw, bh = boxes[i][2] - boxes[i][0], boxes[i][3] - boxes[i][1]
            if x and x + bw > target:
                x, y, row = 0, y + row, 0
            desired.append((snap(x - boxes[i][0]), snap(y - boxes[i][1])))
            x, row = x + bw, max(row, bh)
        return desired

    def overlaps(self, i, box, skip=()):
        for j in range(len(self.items)):
            if j == i or j in skip or not self.placed[j]:
                continue
            other = self.inflated(j)
            if box[0] < other[2] and other[0] < box[2] and box[1] < other[3] and other[1] < box[3]:
                return True
        return False

    def legalize(self, desired):
        """Greedy: each item takes the free spot nearest its desired spot."""
        n = len(self.items)
        self.placed = [False] * n
        order = sorted(range(n), key=lambda i: (desired[i][1], desired[i][0]))
        for i in order:
            item, (x, y) = self.items[i], desired[i]
            m = item.margin
            candidates = [(x, y)]
            for j in range(n):
                if self.placed[j]:
                    b = self.inflated(j)
                    candidates += [
                        (up(b[2] + m["L"]), y),
                        (b[0] - m["R"] - item.w, y),
                        (x, up(b[3] + m["T"])),
                        (x, b[1] - m["B"] - item.h),
                    ]
            best = None
            for cx, cy in candidates:
                cx, cy = snap(cx), snap(cy)
                if cx != snap(cx, G):
                    continue
                if not self.overlaps(i, self.inflated(i, cx, cy)):
                    d = abs(cx - x) + abs(cy - y)
                    if best is None or d < best[0]:
                        best = (d, cx, cy)
            if best is None:
                right = max((self.inflated(j)[2] for j in range(n) if self.placed[j]), default=0)
                best = (0, up(right + m["L"]), y)
            item.x, item.y = best[1], best[2]
            self.placed[i] = True

    def region(self):
        boxes = [self.inflated(i) for i in range(len(self.items))]
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
        # Room for any item to move round to the far side of another; the
        # spread cost keeps the contents tight regardless.
        widest = max(b[2] - b[0] for b in boxes)
        tallest = max(b[3] - b[1] for b in boxes)
        grow_x = max((x1 - x0) * 0.15, widest)
        grow_y = max((y1 - y0) * 0.15, tallest)
        self.bounds = (
            snap(x0 - grow_x),
            snap(y0 - grow_y),
            snap(x1 + grow_x),
            snap(y1 + grow_y),
        )

    def net_cost(self, k):
        weight, points, anchors = self.nets[k]
        xs, ys = [], []
        for i, key in points:
            x, y = self.point(i, key)
            xs.append(x)
            ys.append(y)
        x0, y0, x1, y1 = self.bounds
        for side in anchors:
            if side == "L":
                xs.append(x0)
            elif side == "R":
                xs.append(x1)
            elif side == "T":
                ys.append(y0)
            else:
                ys.append(y1)
        span = (max(xs) - min(xs) if len(xs) > 1 else 0) + (max(ys) - min(ys) if len(ys) > 1 else 0)
        return weight * span

    def point(self, i, key):
        item = self.items[i]
        if isinstance(key, tuple):
            _, _, x, y = item.rails[key[1]]
        else:
            x, y = item.terms[key].x, item.terms[key].y
        return item.x + x, item.y + y

    def pull(self, i):
        x0, y0, x1, y1 = self.bounds
        item = self.items[i]
        cx, cy = item.x + item.w / 2, item.y + item.h / 2
        return GRAVITY * (abs(cx - (x0 + x1) / 2) + abs(cy - (y0 + y1) / 2))

    def inside(self, box):
        x0, y0, x1, y1 = self.bounds
        return box[0] >= x0 and box[1] >= y0 and box[2] <= x1 and box[3] <= y1

    def spread(self):
        """Half-perimeter of the clearance boxes' extent: the cost of empty space."""
        boxes = [self.inflated(i) for i in range(len(self.items))]
        width = max(b[2] for b in boxes) - min(b[0] for b in boxes)
        height = max(b[3] for b in boxes) - min(b[1] for b in boxes)
        return COMPACT * len(self.items) * (width + height)

    def cost_of(self, moved):
        ks = {k for i in moved for k in self.touch[i]}
        nets = sum(self.net_cost(k) for k in ks)
        return nets + sum(self.pull(i) for i in moved) + self.spread()

    def propose(self, radius):
        """A legal move as [(item, new x, new y)], or None."""
        n = len(self.items)
        i = self.rng.randrange(n)
        item = self.items[i]
        roll = self.rng.random()
        if roll < 0.1 and self.turnable:
            i = self.rng.choice(self.turnable)
            item = self.items[i]
            fresh = item.turn(item.rotation + self.rng.randint(1, 3))
            # Turn about the centre, and sometimes shift too, so a part can
            # turn into a gap that is not centred on it.
            step = max(1, int(radius / G))
            dx, dy = (self.rng.randint(-step, step) * G for _ in range(2))
            if self.rng.random() < 0.5:
                dx = dy = 0
            fresh.x = snap(item.x + (item.w - fresh.w) / 2) + dx
            fresh.y = snap(item.y + (item.h - fresh.h) / 2) + dy
            self.items[i] = fresh
            box = self.inflated(i)
            legal = self.inside(box) and not self.overlaps(i, box)
            self.items[i] = item
            return [(i, fresh.x, fresh.y, fresh)] if legal else None
        if roll < 0.25 and n > 1:
            j = self.rng.randrange(n - 1)
            j += j >= i
            other = self.items[j]
            ix = snap(other.x + other.w / 2 - item.w / 2)
            iy = snap(other.y + other.h / 2 - item.h / 2)
            jx = snap(item.x + item.w / 2 - other.w / 2)
            jy = snap(item.y + item.h / 2 - other.h / 2)
            moves = [(i, ix, iy, None), (j, jx, jy, None)]
        elif roll < 0.35:
            x0, y0, x1, y1 = self.bounds
            moves = [
                (
                    i,
                    snap(self.rng.uniform(x0, max(x0, x1 - item.w))),
                    snap(self.rng.uniform(y0, max(y0, y1 - item.h))),
                    None,
                )
            ]
        else:
            step = max(1, int(radius / G))
            dx = self.rng.randint(-step, step) * G
            dy = self.rng.randint(-step, step) * G
            if not dx and not dy:
                return None
            moves = [(i, item.x + dx, item.y + dy, None)]
        skip = {m[0] for m in moves}
        for k, x, y, _ in moves:
            box = self.inflated(k, x, y)
            if not self.inside(box) or self.overlaps(k, box, skip):
                return None
        if len(moves) == 2:
            a, b = (self.inflated(k, x, y) for k, x, y, _ in moves)
            if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                return None
        return moves

    def apply(self, moves):
        """Apply [(item, x, y, replacement or None)]; return the undoing moves."""
        old = [(k, self.items[k].x, self.items[k].y, self.items[k]) for k, _, _, _ in moves]
        for k, x, y, replacement in moves:
            if replacement is not None:
                self.items[k] = replacement
            self.items[k].x, self.items[k].y = x, y
        return old

    def run(self):
        n = len(self.items)
        if n < 2:
            return
        x0, y0, x1, y1 = self.bounds
        size = max(x1 - x0, y1 - y0)
        steps = int(self.effort * (600 + 250 * n))
        deltas = []
        for _ in range(60):
            moves = self.propose(size / 4)
            if moves:
                moved = [m[0] for m in moves]
                before = self.cost_of(moved)
                old = self.apply(moves)
                deltas.append(abs(self.cost_of(moved) - before))
                self.apply(old)
        start = max(1e-3, 2 * sum(deltas) / max(1, len(deltas)))
        end = start * 2e-3
        cool = (end / start) ** (1 / max(1, steps))
        temperature = start
        for _ in range(steps):
            temperature *= cool
            radius = max(G, size / 2 * math.sqrt(temperature / start))
            moves = self.propose(radius)
            if not moves:
                continue
            moved = [m[0] for m in moves]
            before = self.cost_of(moved)
            old = self.apply(moves)
            delta = self.cost_of(moved) - before
            if delta > 0 and self.rng.random() >= math.exp(-delta / temperature):
                self.apply(old)
        self.settle()

    def settle(self):
        """Slide items towards the middle while the cost does not rise.

        Annealing moves must stay legal, so large items rarely find their way
        into gaps; this greedy pass closes the space they leave.
        """
        n = len(self.items)
        for _ in range(8):
            boxes = [self.inflated(i) for i in range(n)]
            cx = (min(b[0] for b in boxes) + max(b[2] for b in boxes)) / 2
            cy = (min(b[1] for b in boxes) + max(b[3] for b in boxes)) / 2

            def distance(i):
                item = self.items[i]
                return abs(item.x + item.w / 2 - cx) + abs(item.y + item.h / 2 - cy)

            moved_any = False
            for i in sorted(range(n), key=distance, reverse=True):
                item = self.items[i]
                for axis in (0, 1):
                    centre = item.x + item.w / 2 if axis == 0 else item.y + item.h / 2
                    toward = cx - centre if axis == 0 else cy - centre
                    if abs(toward) < G:
                        continue
                    sign = 1 if toward > 0 else -1
                    for size in (32, 16, 8, 4, 2, 1):
                        while True:
                            step = sign * size * G
                            x = item.x + (step if axis == 0 else 0)
                            y = item.y + (step if axis == 1 else 0)
                            if self.overlaps(i, self.inflated(i, x, y)):
                                break
                            before = self.cost_of([i])
                            old = self.apply([(i, x, y, None)])
                            if self.cost_of([i]) > before + 1e-9:
                                self.apply(old)
                                break
                            moved_any = True
            moved_any |= self.turn_in_place()
            if not moved_any:
                return

    def turn_in_place(self):
        """Keep any quarter turn of a part that lowers the cost; True if one did."""
        improved = False
        for i in self.turnable:
            for quarter in (1, 2, 3):
                item = self.items[i]
                fresh = item.turn(item.rotation + quarter)
                x = snap(item.x + (item.w - fresh.w) / 2)
                y = snap(item.y + (item.h - fresh.h) / 2)
                self.items[i] = fresh
                fresh.x, fresh.y = x, y
                legal = not self.overlaps(i, self.inflated(i))
                self.items[i] = item
                if not legal:
                    continue
                before = self.cost_of([i])
                old = self.apply([(i, x, y, fresh)])
                if self.cost_of([i]) < before - 1e-9:
                    improved = True
                else:
                    self.apply(old)
        return improved


# --------------------------------------------------------------------------
# Block boxes


def frame(template, rank):
    """Size the box around the placed contents and set each port on its edge."""
    items = template.items
    cw = max((i.x + i.w + i.margin["R"] for i in items), default=0)
    ch = max((i.y + i.h + i.margin["B"] for i in items), default=0)
    title_w = text_width(template.title, FONT) * 1.1 + text_width(template.definition, SMALL) + 8
    header_w = up(max(title_w, text_width(template.rails, SMALL)) + 3 * G)
    header_h = up(G + LINE * (2 if template.rails else 1))
    lane = {}
    for port, term in template.ports.items():
        lane[port] = up(text_width(" ".join((port,)), SMALL) + G + (G if term.bundle else 0))
    side_ports = {s: [p for p, t in template.ports.items() if t.side == s] for s in SIDES}
    reach = {s: max((lane[p] for p in side_ports[s]), default=0) for s in SIDES}
    margin_l = reach["L"] + G
    margin_r = reach["R"] + G
    margin_t = max(header_h, reach["T"]) + G
    margin_b = reach["B"] + G

    def need(side):
        return (len(side_ports[side]) + 1) * P

    w = max(margin_l + cw + margin_r, header_w + need("T"), need("B"), 4 * P)
    h = max(margin_t + ch + margin_b, header_h + need("L"), header_h + need("R"), 3 * P)
    w, h = up(w, P), up(h, P)
    ox = snap(margin_l + (w - margin_l - margin_r - cw) / 2)
    oy = snap(margin_t + (h - margin_t - margin_b - ch) / 2)
    for item in items:
        item.x, item.y = item.x + ox, item.y + oy
    template.w, template.h, template.header = w, h, (header_w, header_h)
    nets = connections(template)
    for side in SIDES:
        ideal = []
        for port in side_ports[side]:
            term = template.ports[port]
            points = [
                (template.items[o].x + t.x, template.items[o].y + t.y)
                for net in term.nets
                for o, t in nets.get(net, ())
                if o >= 0
            ]
            coordinate = 1 if side in "LR" else 0
            middle = (h if side in "LR" else w) / 2
            mean = sum(p[coordinate] for p in points) / len(points) if points else middle
            if math.isfinite(rank.get(port, math.inf)):
                # Average with the order the outside wiring wants.
                mean = (mean + middle + rank[port]) / 2
            ideal.append((mean, port))
        ideal.sort()
        low = (header_h + P) if side in "LR" else (header_w + P if side == "T" else P)
        high = (h if side in "LR" else w) - P
        slots = line_up([m for m, _ in ideal], low, high)
        for (_, port), along in zip(ideal, slots):
            term = template.ports[port]
            term.x = {"L": 0, "R": w}.get(side, along)
            term.y = {"T": 0, "B": h}.get(side, along)
            nx, ny = NORMAL[side]
            template.lanes[port] = (term.x - nx * lane[port], term.y - ny * lane[port])


def line_up(ideal, low, high):
    """Positions at least `P` apart, in order, near the ideal ones, within bounds."""
    slots, last = [], low - P
    for value in ideal:
        last = max(snap(value), last + P, low)
        slots.append(last)
    nxt = high + P
    for i in range(len(slots) - 1, -1, -1):
        nxt = min(slots[i], nxt - P)
        slots[i] = nxt
    return slots


def count_uses(top, templates):
    """How many block instances draw each template, to weight side votes."""
    for template in templates:
        template.uses = 0
    stack = [top]
    while stack:
        template = stack.pop()
        template.uses += 1
        stack += [item.template for item in template.items if item.template is not None]


# --------------------------------------------------------------------------
# Side refinement


def term_point(template, owner, term):
    if owner < 0:
        return template.lanes[term.key]
    item = template.items[owner]
    return item.x + term.x, item.y + term.y


def pulls(template, nets, owner, term, origin):
    """(unit vector, offset) from `origin` to every other terminal sharing a net.

    Directions choose a side; offsets order pins along it (the barycentre
    heuristic), so connected rows of pins line up instead of crossing.
    """
    vectors = []
    for net in term.nets:
        for o, other in nets.get(net, ()):
            if o == owner and other is term:
                continue
            x, y = term_point(template, o, other)
            dx, dy = x - origin[0], y - origin[1]
            length = math.hypot(dx, dy)
            if length:
                vectors.append(((dx / length, dy / length), (dx, dy)))
    return vectors


def refine(design):
    """Re-choose pin and port sides and order from the current placement.

    Definitions update one at a time, largest first, and their instances are
    redrawn before the next one votes: a part then lines its pins up with the
    order its neighbour has just taken, instead of both swapping at once.
    """
    for symbol in sorted(design.symbols.values(), key=lambda s: (-len(s.keys), s.definition)):
        votes = {}
        for template in design.templates.values():
            nets = connections(template)
            for index, item in enumerate(template.items):
                if item.kind != "part" or item.part["definition"] != symbol.definition:
                    continue
                # Votes are cast in the symbol's own frame, before rotation.
                turn = item.rotation
                centre = (item.x + item.w / 2, item.y + item.h / 2)
                for key, term in item.terms.items():
                    for v, offset in pulls(template, nets, index, term, centre):
                        vote = (unturn(v, turn), unturn(offset, turn), template.uses)
                        votes.setdefault(key, []).append(vote)
                for key, (_, kind) in item.drawing["rails"].items():
                    flag = unturn((0, -1) if kind == "power" else (0, 1), turn)
                    votes.setdefault(key, []).append((flag, None, template.uses))
        if not votes:
            continue
        choose_sides(symbol.keys, symbol.sides, symbol.rank, votes, pair=symbol.small)
        for template in design.templates.values():
            for index, item in enumerate(template.items):
                if item.kind == "part" and item.part["definition"] == symbol.definition:
                    fresh = item.turn(item.rotation)
                    fresh.x = snap(item.x + (item.w - fresh.w) / 2)
                    fresh.y = snap(item.y + (item.h - fresh.h) / 2)
                    template.items[index] = fresh
    port_votes = {}
    for template in design.templates.values():
        nets = connections(template)
        for index, item in enumerate(template.items):
            if item.kind == "block":
                votes = port_votes.setdefault(item.template.signature, {})
                centre = (item.x + item.w / 2, item.y + item.h / 2)
                for key, term in item.terms.items():
                    for v, offset in pulls(template, nets, index, term, centre):
                        votes.setdefault(key, []).append((v, offset, template.uses))
        votes = port_votes.setdefault(template.signature, {})
        centre = (template.w / 2, template.h / 2)
        for key, term in template.ports.items():
            for v, offset in pulls(template, nets, -1, term, centre):
                votes.setdefault(key, []).append((v, offset, template.uses))
    for signature, votes in port_votes.items():
        sides = design.port_sides.get(signature)
        if sides:
            rank = design.port_rank.setdefault(signature, {})
            choose_sides(list(sides), sides, rank, votes, pair=False)


def choose_sides(keys, sides, rank, votes, *, pair):
    """Assign sides by summed pull, then order each side by mean offset.

    A side holds at most 10 pins or 45% of them, so large parts stay square.
    """
    totals = {}
    for key in keys:
        sx = sum(v[0] * w for v, _, w in votes.get(key, ()))
        sy = sum(v[1] * w for v, _, w in votes.get(key, ()))
        totals[key] = (sx, sy)

    def score(key, side):
        nx, ny = NORMAL[side]
        return totals[key][0] * nx + totals[key][1] * ny

    if pair and len(keys) == 2:
        a, b = keys
        options = [("L", "R"), ("R", "L"), ("T", "B"), ("B", "T")]
        current = (sides[a], sides[b])
        best = max(options, key=lambda o: (score(a, o[0]) + score(b, o[1]), o == current))
        sides[a], sides[b] = best
        return
    voted = [k for k in keys if votes.get(k)]
    capacity = max(10, math.ceil(0.45 * len(keys)))
    load = {s: sum(1 for k in keys if k not in voted and sides[k] == s) for s in SIDES}
    choices = sorted(
        ((score(k, s), k, s) for k in voted for s in SIDES),
        key=lambda c: (-c[0], natural(str(c[1])), c[2]),
    )
    assigned = set()
    for _, key, side in choices:
        if key in assigned or load[side] >= capacity:
            continue
        sides[key], load[side] = side, load[side] + 1
        assigned.add(key)
    for key in voted:
        if key in assigned:
            continue
        sides[key] = min(SIDES, key=lambda s: load[s])
        load[sides[key]] += 1
    for key in voted:
        along = 1 if sides[key] in "LR" else 0
        offsets = [(o[along], w) for _, o, w in votes[key] if o is not None]
        if offsets:
            rank[key] = sum(o * w for o, w in offsets) / sum(w for _, w in offsets)
        else:
            rank[key] = math.inf  # rail pins go last, clear of the signal rows


# --------------------------------------------------------------------------
# Routing


class Grid:
    def __init__(self, w, h):
        self.cols, self.rows = int(w // G) + 1, int(h // G) + 1
        n = self.cols * self.rows
        self.state = bytearray(n)  # FREE, SOFT, HARD
        self.over = [None] * n  # array key of the symbol covering a SOFT cell
        self.owner = [[-1] * n for _ in range(4)]
        self.node = [-1] * n
        self.reserved = [-1] * n  # the cell in front of a terminal belongs to its net
        self.edges = set()  # (cell, direction) for every wire leaving a cell

    def index(self, x, y):
        return int(round(y / G)) * self.cols + int(round(x / G))

    def point(self, index):
        y, x = divmod(index, self.cols)
        return x * G, y * G

    def cover(self, x0, y0, x1, y1, state, key=None):
        c0, c1 = max(0, math.floor(x0 / G)), min(self.cols - 1, math.ceil(x1 / G))
        r0, r1 = max(0, math.floor(y0 / G)), min(self.rows - 1, math.ceil(y1 / G))
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                i = r * self.cols + c
                if state > self.state[i]:
                    self.state[i], self.over[i] = state, key
                elif state == SOFT and self.state[i] == SOFT and self.over[i] != key:
                    self.over[i] = None

    def occupied(self, i):
        return any(o[i] != -1 for o in self.owner)


def route(template, attempts=4):
    """Route a layout; on failures, retry with the failed nets routed first.

    Wires routed earlier can box a terminal in. Each retry starts from a clean
    grid with the nets that failed moving ahead, and the attempt with the
    fewest failures is kept.
    """
    first, best = [], None
    for _ in range(attempts):
        routes, failed = route_once(template, first)
        if best is None or routes["failed"] < best["failed"]:
            best = routes
        if not failed:
            break
        first += [key for key in failed if key not in first]
    template.routes = best


def route_once(template, first):
    grid = Grid(template.w, template.h)
    routes = {
        "wires": {},
        "labels": [],
        "breakouts": [],
        "names": {},
        "bus": set(),
        "trees": {},
        "failed": 0,
    }
    terminals = obstacles(template, grid)
    nets = connections(template)
    groups = buses(template, nets)
    ids = {}

    def ident(key):
        return ids.setdefault(key, len(ids))

    names = routes["names"]
    prefix = show_path(template.node.path)
    own = {
        net: f"{t.key}" if not t.bundle else f"{t.fields[t.nets.index(net)]}"
        for t in template.ports.values()
        for net in t.nets
    }

    def display(net):
        if prefix and net.startswith(prefix + "."):
            return net[len(prefix) + 1 :]
        return own.get(net, net)

    def crossable(members_):
        return {array_key(template.items[o].path) for o, _ in members_ if o >= 0}

    def length(members_):
        points = [grid.point(terminals[(o, t.key)][0]) for o, t in members_]
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        return (max(xs) - min(xs)) + (max(ys) - min(ys))

    for k, bus in enumerate(groups):
        for owner, term in bus["bundles"]:
            reserve(grid, terminals[(owner, term.key)], ident(("bus", k)))
    for net, members_ in nets.items():
        for owner, term in members_:
            if not term.bundle:
                reserve(grid, terminals[(owner, term.key)], ident(net))
    bus_of = {net: ident(("bus", k)) for k, bus in enumerate(groups) for net in bus["nets"]}
    # Jobs run in stages: nets that failed before, then buses, then breakouts
    # that failed before, then the rest, shortest first.
    jobs = []
    for k, bus in enumerate(groups):
        key = ("bus", k)
        jobs.append((1, key not in first, length(bus["bundles"] + bus["singles"]), key, bus))
    for net, members_ in nets.items():
        singles = [(o, t) for o, t in members_ if not t.bundle]
        if net in bus_of:
            if singles:
                jobs.append((2 if net in first else 3, False, length(singles), net, singles))
        elif len(singles) > 1:
            jobs.append((0 if net in first else 3, False, length(singles), net, singles))
        elif singles and singles[0][0] >= 0:
            o, t = singles[0]
            routes["labels"].append((terminals[(o, t.key)], display(net)))
    failed = []
    for _, _, _, key, job in sorted(jobs, key=lambda j: (j[0], j[1], j[2], str(j[3]))):
        me = ident(key)
        before = routes["failed"]
        if isinstance(key, tuple):
            routes["bus"].add(me)
            names[me] = ", ".join(sorted({str(t.key) for _, t in job["bundles"]}))
            ends = [terminals[(o, t.key)] for o, t in job["bundles"]]
            allowed = crossable(job["bundles"] + job["singles"])
            tree = connect(grid, me, ends, allowed, routes)
            if len(job["bundles"]) == 1 and job["singles"]:
                points = [grid.point(terminals[(o, t.key)][0]) for o, t in job["singles"]]
                tree |= spine(grid, me, ends[0], points, allowed, routes)
            routes["trees"][me] = tree
        else:
            names[me] = display(key)
            ends = [terminals[(o, t.key)] for o, t in job]
            allowed = crossable(job)
            if key in bus_of:
                field_name = field_label(groups, bus_of[key], ids, key)
                breakout(grid, me, ends, bus_of[key], routes, allowed, field_name)
            else:
                connect(grid, me, ends, allowed, routes, label=display(key))
        if routes["failed"] > before:
            failed.append(key)
    return routes, failed


def reserve(grid, terminal, me):
    cell, direction = terminal
    y, x = divmod(cell, grid.cols)
    dx, dy = DIRS[direction]
    if 0 <= x + dx < grid.cols and 0 <= y + dy < grid.rows:
        grid.reserved[(y + dy) * grid.cols + x + dx] = me


def field_label(groups, bus_id, ids, net):
    for k, bus in enumerate(groups):
        if ids.get(("bus", k)) != bus_id:
            continue
        for _, term in bus["bundles"]:
            if net in term.nets:
                label = term.fields[term.nets.index(net)]
                return label.rsplit(".", 1)[-1] if "." in label else label
    return net


def obstacles(template, grid):
    """Mark symbols, frames and labels; return {(owner, key): (cell, outward dir)}."""
    terminals = {}
    w, h = template.w, template.h
    grid.cover(0, 0, w, 0, HARD)
    grid.cover(0, h, w, h, HARD)
    grid.cover(0, 0, 0, h, HARD)
    grid.cover(w, 0, w, h, HARD)
    grid.cover(0, 0, template.header[0], template.header[1], HARD)
    for port, term in template.ports.items():
        lx, ly = template.lanes[port]
        grid.cover(min(term.x, lx), min(term.y, ly), max(term.x, lx), max(term.y, ly), HARD)
        label_w = text_width(port, SMALL)
        if term.side in "LR":
            x0 = min(term.x, lx)
            grid.cover(x0, ly - G, x0 + max(abs(lx - term.x), label_w), ly - G, SOFT)
        cell = grid.index(lx, ly)
        terminals[(-1, port)] = (cell, (OUTWARD[term.side] + 4) % 8)
        grid.state[cell] = HARD
    for index, item in enumerate(template.items):
        key = array_key(item.path)
        x, y = item.x, item.y
        if item.kind == "block":
            grid.cover(x, y, x + item.w, y + item.h, SOFT, key)
            for term in item.terms.values():
                cell = grid.index(x + term.x, y + term.y)
                grid.state[cell] = HARD
                terminals[(index, term.key)] = (cell, OUTWARD[term.side])
            continue
        d = item.drawing
        bx, by, bw, bh = d["body"]
        grid.cover(x + bx, y + by, x + bx + bw, y + by + bh, SOFT, key)
        lx, ly = d["label_at"]
        label_w = max(text_width(t, s) for t, s, _ in d["labels"])
        if d["label_mode"] == "beside":
            grid.cover(x + lx - FONT, y + ly - label_w / 2, x + lx, y + ly + label_w / 2, SOFT)
        elif d["label_mode"] != "inside":
            grid.cover(
                x + lx,
                y + ly - FONT,
                x + lx + label_w,
                y + ly + LINE * (len(d["labels"]) - 1),
                SOFT,
            )
        for pin, (px, py, side) in d["points"].items():
            nx, ny = NORMAL[side]
            # Stub from the body edge to the terminal.
            grid.cover(
                x + min(px, px - nx * STUB),
                y + min(py, py - ny * STUB),
                x + max(px, px - nx * STUB),
                y + max(py, py - ny * STUB),
                HARD,
            )
            cell = grid.index(x + px, y + py)
            if pin in item.terms:
                terminals[(index, pin)] = (cell, OUTWARD[side])
            if pin in d["rails"]:
                net = d["rails"][pin][0]
                reach = G + 8 + text_width(net, SMALL)
                ex, ey = px + nx * reach, py + ny * reach
                grid.cover(x + min(px, ex), y + min(py, ey), x + max(px, ex), y + max(py, ey), SOFT)
    return terminals


def heuristic(x, y, box):
    dx = max(box[0] - x, 0, x - box[2])
    dy = max(box[1] - y, 0, y - box[3])
    return dx + dy - (2 - DIAGONAL) * min(dx, dy)


def search(grid, me, start, targets, allowed):
    """A* from a terminal to any target cell; returns [(cell, dir)] or None.

    `targets` maps cell -> predicate(direction) for an acceptable arrival.
    """
    cols, rows = grid.cols, grid.rows
    cells = [grid.point(c) for c in targets]
    box = (
        min(p[0] for p in cells) / G,
        min(p[1] for p in cells) / G,
        max(p[0] for p in cells) / G,
        max(p[1] for p in cells) / G,
    )
    origin, first = start
    sy, sx = divmod(origin, cols)
    best = {(origin, first): 0.0}
    parent = {}
    heap = [(heuristic(sx, sy, box), 0.0, origin, first)]
    owner, node, state, over = grid.owner, grid.node, grid.state, grid.over
    reserved = grid.reserved
    expanded = 0
    while heap:
        _, cost, cell, d = heapq.heappop(heap)
        if best.get((cell, d), math.inf) < cost:
            continue
        expanded += 1
        if expanded > SEARCH_LIMIT:
            return None
        y, x = divmod(cell, cols)
        options = (
            (d,) if cell == origin else (d, (d + 1) % 8, (d - 1) % 8, (d + 2) % 8, (d - 2) % 8)
        )
        for nd in options:
            turn = min((nd - d) % 8, (d - nd) % 8)
            if turn and any(o[cell] not in (-1, me) for o in owner):
                continue
            dx, dy = DIRS[nd]
            nx, ny = x + dx, y + dy
            if not (0 <= nx < cols and 0 <= ny < rows):
                continue
            nxt = ny * cols + nx
            step = DIAGONAL if dx and dy else 1.0
            if dx and dy:
                corner_a, corner_b = y * cols + nx, ny * cols + x
                if state[corner_a] == HARD or state[corner_b] == HARD:
                    continue
            if nxt in targets:
                if targets[nxt](nd):
                    parent[(nxt, nd)] = (cell, d)
                    return trace(parent, (nxt, nd), origin, first)
                continue
            if state[nxt] == HARD:
                continue
            axis = nd % 4
            if owner[axis][nxt] != -1 or node[nxt] != -1:
                continue
            if reserved[nxt] not in (-1, me):
                continue
            if any(o[nxt] == me for o in owner):
                continue
            step += TURN_45 if turn == 1 else TURN_90 if turn == 2 else 0
            if any(o[nxt] != -1 for o in owner):
                step += CROSSING
            if state[nxt] == SOFT:
                step += (
                    OVER_ARRAY if over[nxt] is not None and over[nxt] in allowed else OVER_SYMBOL
                )
            total = cost + step
            if total < best.get((nxt, nd), math.inf):
                best[(nxt, nd)] = total
                parent[(nxt, nd)] = (cell, d)
                heapq.heappush(heap, (total + heuristic(nx, ny, box), total, nxt, nd))
    return None


def trace(parent, end, origin, first):
    path = [end]
    while path[-1] != (origin, first):
        path.append(parent[path[-1]])
    path.reverse()
    return path


def commit(grid, me, path):
    for (a, _), (b, d) in zip(path, path[1:]):
        grid.owner[d % 4][a] = me
        grid.owner[d % 4][b] = me
        grid.edges.add((a, d))
        grid.edges.add((b, (d + 4) % 8))
    for i, (cell, d) in enumerate(path):
        if i in (0, len(path) - 1) or path[i + 1][1] != d:
            grid.node[cell] = me
    return [cell for cell, _ in path]


def inward(direction):
    return lambda nd: nd == (direction + 4) % 8


def tree_entry(grid, cell, *, diagonal):
    """How a wire may join a tree at `cell`.

    A bus entry arrives at 45 degrees across a straight run. A wire joining
    its own net arrives square on from any side no wire leaves by, so a corner
    can become a T; four-way junctions are refused, keeping every dot clear.
    """

    def accept(nd):
        if (nd % 2 == 1) != diagonal:
            return False
        if diagonal:
            return grid.owner[nd % 4][cell] == -1
        degree = sum((cell, d) in grid.edges for d in range(8))
        return degree < 3 and (cell, (nd + 4) % 8) not in grid.edges

    return accept


def tree_targets(grid, me, tree, *, diagonal):
    """Cells of a routed tree that a new wire may join, and how it must arrive."""
    targets = {}
    for cell in tree:
        if grid.state[cell] == HARD:
            continue
        if any(o[cell] not in (-1, me) for o in grid.owner):
            continue
        targets[cell] = tree_entry(grid, cell, diagonal=diagonal)
    return targets


def connect(grid, me, ends, allowed, routes, label=None):
    """Grow a tree over terminals, nearest first; unreachable ends get labels."""
    wires = routes["wires"].setdefault(me, [])
    tree = set()
    if len(ends) < 2:
        return tree
    points = [grid.point(c) for c, _ in ends]

    def gap(i, j):
        return abs(points[i][0] - points[j][0]) + abs(points[i][1] - points[j][1])

    pairs = sorted((gap(i, j), i, j) for i in range(len(ends)) for j in range(i + 1, len(ends)))
    _, a, b = pairs[0]
    done = {a}
    pending = [i for i in range(len(ends)) if i != a]
    while pending:
        pending.sort(key=lambda i: (min(gap(i, j) for j in done), i))
        i = pending.pop(0)
        cell, direction = ends[i]
        targets = tree_targets(grid, me, tree, diagonal=False)
        if not targets:
            j = min(done, key=lambda j: gap(i, j))
            targets = {ends[j][0]: inward(ends[j][1])}
        path = search(grid, me, ends[i], targets, allowed)
        if path is None:
            routes["failed"] += 1
            routes["labels"].append((ends[i], routes["names"].get(me, label or "")))
            if not tree:
                j = min(done, key=lambda j: gap(i, j))
                routes["labels"].append((ends[j], routes["names"].get(me, label or "")))
            done.add(i)
            continue
        cells = commit(grid, me, path)
        wires.append(cells)
        tree |= {c for c in cells if grid.state[c] != HARD}
        done.add(i)
    return tree


def spine(grid, me, end, points, allowed, routes):
    """A bus leaving its only bundle terminal, run towards its fields' pins."""
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    for radius in (3, 6, 12, 24):
        targets = {}
        c0, r0 = int(cx / G), int(cy / G)
        for r in range(max(0, r0 - radius), min(grid.rows, r0 + radius + 1)):
            for c in range(max(0, c0 - radius), min(grid.cols, c0 + radius + 1)):
                i = r * grid.cols + c
                if (
                    grid.state[i] == FREE
                    and grid.node[i] == -1
                    and grid.reserved[i] in (-1, me)
                    and not grid.occupied(i)
                ):
                    targets[i] = lambda nd: nd % 2 == 0
        if targets:
            path = search(grid, me, end, targets, allowed)
            if path:
                cells = commit(grid, me, path)
                routes["wires"].setdefault(me, []).append(cells)
                return {c for c in cells if grid.state[c] != HARD}
    return set()


def breakout(grid, me, ends, bus, routes, allowed, label):
    """Join each field terminal to its bus with a 45-degree entry, or to its own wire.

    Where no 45-degree entry is free, as on a short, crowded bus, the field
    enters square on, still labelled; net labels are the last resort.
    """
    bus_tree = routes["trees"].get(bus, set())
    wires = routes["wires"].setdefault(me, [])
    tree = set()
    for end in sorted(ends):
        taken = routes.setdefault("entries", set())
        own = tree_targets(grid, me, tree, diagonal=False)
        path = None
        for diagonal in (True, False):
            targets = {
                cell: accept
                for cell, accept in tree_targets(grid, bus, bus_tree, diagonal=diagonal).items()
                if cell not in taken and grid.node[cell] == -1
            }
            targets.update(own)
            path = search(grid, me, end, targets, allowed) if targets else None
            if path is not None:
                break
        if path is None:
            routes["failed"] += 1
            routes["labels"].append((end, label))
            continue
        cells = commit(grid, me, path)
        last, d = path[-1]
        if last in bus_tree:
            grid.node[last] = bus
            y, x = divmod(last, grid.cols)
            taken.update((y + dy) * grid.cols + x + dx for dx in (-1, 0, 1) for dy in (-1, 0, 1))
            prev = path[-2][0] if len(path) > 1 else last
            routes["breakouts"].append((prev, last, label))
            cells = cells[:-1] + [last]
        wires.append(cells)
        tree |= {c for c in cells[:-1] if grid.state[c] != HARD}


# --------------------------------------------------------------------------
# Rendering


def render(template, name, depth, out):
    fill = FILLS[depth % len(FILLS)]
    w, h = template.w, template.h
    out.append(
        f'<rect width="{w}" height="{h}" rx="4" fill="{fill}" stroke="#666" stroke-width="1.2"/>'
    )
    definition = ""
    if template.definition != name:
        definition = (
            f'<tspan dx="8" font-weight="normal" font-size="{SMALL}" fill="#666">'
            f"{escape(template.definition)}</tspan>"
        )
    out.append(f'<text x="{G}" y="{G + FONT}" font-weight="bold">{escape(name)}{definition}</text>')
    if template.rails:
        out.append(
            f'<text x="{G}" y="{G + FONT + LINE}" font-size="{SMALL}" fill="{POWER}">'
            f"{escape(template.rails)}</text>"
        )
    for port, term in template.ports.items():
        lx, ly = template.lanes[port]
        colour, stroke = (BUS, 4) if term.bundle else (WIRE, 1.4)
        out.append(
            f'<line x1="{term.x}" y1="{term.y}" x2="{lx}" y2="{ly}" stroke="{colour}" stroke-width="{stroke}"/>'
        )
        out.append(
            f'<rect x="{term.x - 4}" y="{term.y - 4}" width="8" height="8" fill="{colour}">'
            f"<title>{escape(', '.join(term.fields))}</title></rect>"
        )
        if term.side in "LR":
            anchor = "start" if term.side == "L" else "end"
            tx = term.x + (5 if term.side == "L" else -5)
            out.append(
                f'<text x="{tx}" y="{term.y - 3}" font-size="{SMALL}" text-anchor="{anchor}">{escape(port)}</text>'
            )
        else:
            ty = term.y + (5 if term.side == "T" else -5)
            anchor = "end" if term.side == "T" else "start"
            out.append(
                f'<text transform="translate({term.x - 3} {ty}) rotate(-90)" font-size="{SMALL}" '
                f'text-anchor="{anchor}">{escape(port)}</text>'
            )
    for item in template.items:
        out.append(f'<g transform="translate({item.x} {item.y})">')
        if item.kind == "block":
            render(item.template, item.name, depth + 1, out)
        else:
            draw_part(item, out)
        out.append("</g>")
    draw_routes(template, out)


def draw_part(item, out):
    d = item.drawing
    symbol = d["symbol"]
    bx, by, bw, bh = d["body"]
    out.append(
        f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" fill="{BODY_FILL}" stroke="{BODY}" stroke-width="1.2"/>'
    )
    if symbol.small:
        cx, cy = bx + bw / 2, by + bh / 2
        rotate = f" rotate(-90 {cx} {cy})" if d["vertical"] else ""
        out.append(
            f'<text x="{cx}" y="{cy + 3}" font-size="{SMALL}" text-anchor="middle" '
            f'transform="{rotate.strip()}">{escape(d["value"])}</text>'
            if rotate
            else f'<text x="{cx}" y="{cy + 3}" font-size="{SMALL}" text-anchor="middle">{escape(d["value"])}</text>'
        )
    lx, ly = d["label_at"]
    anchor = ' text-anchor="middle"' if d["label_mode"] in ("inside", "beside") else ""
    if d["label_mode"] == "beside":
        anchor += f' transform="rotate(-90 {lx} {ly})"'
    for i, (text, size, bold) in enumerate(d["labels"]):
        weight = ' font-weight="bold"' if bold else ' fill="#555"'
        out.append(
            f'<text x="{lx}" y="{ly + i * LINE}" font-size="{size}"{weight}{anchor}>{escape(text)}</text>'
        )
    for key, (px, py, side) in d["points"].items():
        nx, ny = NORMAL[side]
        ex, ey = px - nx * STUB, py - ny * STUB
        out.append(
            f'<line x1="{px}" y1="{py}" x2="{ex}" y2="{ey}" stroke="{BODY}" stroke-width="1.2"/>'
        )
        name = symbol.names[key]
        if symbol.named and plain(name) != key:
            if side in "LR":
                tx = ex + (4 if side == "L" else -4)
                anchor = "start" if side == "L" else "end"
                out.append(
                    f'<text x="{tx}" y="{ey + 3}" font-size="{SMALL}" text-anchor="{anchor}">{marked_up(name)}</text>'
                )
            else:
                ty = ey + (4 if side == "T" else -4)
                anchor = "end" if side == "T" else "start"
                out.append(
                    f'<text transform="translate({ex + 3} {ty}) rotate(-90)" font-size="{SMALL}" '
                    f'text-anchor="{anchor}">{marked_up(name)}</text>'
                )
        if not symbol.small:
            mx, my = (px + ex) / 2, (py + ey) / 2
            if side in "LR":
                out.append(
                    f'<text x="{mx}" y="{my - 2}" font-size="7" fill="#777" text-anchor="middle">{escape(key)}</text>'
                )
            else:
                out.append(
                    f'<text x="{mx - 2}" y="{my + 2}" font-size="7" fill="#777" text-anchor="end">{escape(key)}</text>'
                )
        if key in d["rails"]:
            draw_rail(px, py, side, *d["rails"][key], out)
        elif key in d["ncs"]:
            out.append(
                f'<path d="M{px - 3} {py - 3}L{px + 3} {py + 3}M{px - 3} {py + 3}L{px + 3} {py - 3}" '
                f'stroke="#1F4E9C" stroke-width="1.2"/>'
            )


def draw_rail(px, py, side, net, kind, out):
    nx, ny = NORMAL[side]
    ex, ey = px + nx * G, py + ny * G
    colour = GROUND if kind == "ground" else POWER
    out.append(f"<g><title>{escape(net)}</title>")
    out.append(
        f'<line x1="{px}" y1="{py}" x2="{ex}" y2="{ey}" stroke="{colour}" stroke-width="1.2"/>'
    )
    qx, qy = -ny, nx  # across the stub
    if kind == "ground":
        for i, half in enumerate((5, 3.2, 1.4)):
            cx, cy = ex + nx * 2.2 * i, ey + ny * 2.2 * i
            out.append(
                f'<line x1="{cx - qx * half}" y1="{cy - qy * half}" x2="{cx + qx * half}" '
                f'y2="{cy + qy * half}" stroke="{colour}" stroke-width="1.2"/>'
            )
    else:
        out.append(
            f'<line x1="{ex - qx * 5}" y1="{ey - qy * 5}" x2="{ex + qx * 5}" y2="{ey + qy * 5}" '
            f'stroke="{colour}" stroke-width="1.6"/>'
        )
    tx, ty = ex + nx * 8, ey + ny * 8
    if side in "LR":
        anchor = "start" if side == "R" else "end"
        out.append(
            f'<text x="{tx}" y="{ty + 3}" font-size="{SMALL}" fill="{colour}" text-anchor="{anchor}">{escape(net)}</text>'
        )
    else:
        anchor = "start" if side == "T" else "end"
        out.append(
            f'<text transform="translate({tx + 3} {ty}) rotate(-90)" font-size="{SMALL}" fill="{colour}" '
            f'text-anchor="{anchor}">{escape(net)}</text>'
        )
    out.append("</g>")


def polyline(grid_cols, cells):
    points = []
    for cell in cells:
        y, x = divmod(cell, grid_cols)
        point = (x * G, y * G)
        if len(points) >= 2:
            (ax, ay), (bx, by) = points[-2], points[-1]
            if (bx - ax) * (point[1] - by) == (by - ay) * (point[0] - bx):
                points[-1] = point
                continue
        points.append(point)
    return points


def draw_routes(template, out):
    routes = template.routes
    cols = int(template.w // G) + 1
    names = routes["names"]
    for me, paths in routes["wires"].items():
        if not paths:
            continue
        is_bus = me in routes["bus"]
        colour, stroke = (BUS, 4) if is_bus else (WIRE, 1.4)
        out.append(f"<g><title>{escape(names.get(me, ''))}</title>")
        degree = {}
        for cells in paths:
            for a, b in zip(cells, cells[1:]):
                degree[a] = degree.get(a, 0) + 1
                degree[b] = degree.get(b, 0) + 1
            points = polyline(cols, cells)
            coords = " ".join(f"{x},{y}" for x, y in points)
            out.append(
                f'<polyline points="{coords}" fill="none" stroke="{colour}" stroke-width="{stroke}" '
                f'stroke-linejoin="round"/>'
            )
        for cell, count in degree.items():
            if count >= 3:
                y, x = divmod(cell, cols)
                out.append(
                    f'<circle cx="{x * G}" cy="{y * G}" r="{5 if is_bus else 3}" fill="{colour}"/>'
                )
        out.append("</g>")
    for before, at, label in routes["breakouts"]:
        # Run the label along the entry stub, from the bus outwards, reading
        # left to right or upwards; neighbouring entries are two grid steps apart.
        y, x = divmod(before, cols)
        ay, ax = divmod(at, cols)
        dx, dy = x - ax, y - ay
        if dx and dy:
            angle, outward = (45 if dx * dy > 0 else -45), dx > 0
        elif dx:
            angle, outward = 0, dx > 0
        else:
            angle, outward = -90, dy < 0
        anchor = "start" if outward else "end"
        out.append(
            f'<text transform="translate({ax * G} {ay * G}) rotate({angle})" x="{4 if outward else -4}" '
            f'y="-3" font-size="{SMALL}" font-style="italic" fill="{BUS}" '
            f'text-anchor="{anchor}">{escape(label)}</text>'
        )
    for (cell, direction), label in routes["labels"]:
        draw_tag(cell, cols, direction, label, out)


def draw_tag(cell, cols, direction, label, out):
    """A net tag: a stub, then the name in a pointed outline.

    Tags with the same name are connected; they stand in for wires that are
    not drawn, at single-terminal nets and where routing failed. Vertical tags
    read upwards.
    """
    y, x = divmod(cell, cols)
    dx, dy = DIRS[direction]
    ex, ey = x * G + dx * G, y * G + dy * G
    sign = 1 if dx > 0 or dy < 0 else -1  # along the reading direction
    width = text_width(label, SMALL) + 6
    tip = width + 6
    outline = " ".join(
        f"{sign * px},{py}" for px, py in ((0, -6), (width, -6), (tip, 0), (width, 6), (0, 6))
    )
    rotate = " rotate(-90)" if dy else ""
    anchor = "start" if sign > 0 else "end"
    out.append(
        f"<g><title>{escape(label)}</title>"
        f'<line x1="{x * G}" y1="{y * G}" x2="{ex}" y2="{ey}" stroke="{WIRE}" stroke-width="1.4"/>'
        f'<g transform="translate({ex} {ey}){rotate}">'
        f'<polygon points="{outline}" fill="#FFFFFF" stroke="{WIRE}" stroke-width="1"/>'
        f'<text x="{sign * 3}" y="3" font-size="{SMALL}" fill="{WIRE}" text-anchor="{anchor}">'
        f"{escape(label)}</text></g></g>"
    )


def schematic_svg(circuit, *, seed=0, effort=1.0):
    """Lay out, route, and draw the whole design as standalone SVG text."""
    design = Design(circuit, seed=seed, effort=effort)
    top = design.run()
    width, height = top.w + 4 * G, top.h + 4 * G
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" font-family="DejaVu Sans Mono, Menlo, '
        f'Consolas, monospace" font-size="{FONT}">',
        f"<title>{escape(circuit['board'])} schematic</title>",
        f'<rect width="{width:.0f}" height="{height:.0f}" fill="#FFFFFF"/>',
        f'<g transform="translate({2 * G} {2 * G})">',
    ]
    render(top, circuit["board"], 0, out)
    out.append("</g></svg>")
    stats = {
        "layouts": len(design.templates),
        "unrouted": sum(t.routes["failed"] for t in design.templates.values()),
    }
    return "\n".join(out) + "\n", stats


def export_schematic(project, circuit, output=None, **options):
    """Publish the SVG atomically, refusing to overwrite project inputs."""
    output = output or project.config.output_dir / "schematic.svg"
    svg, stats = schematic_svg(circuit, **options)
    validate_export_paths(project, [output])
    publish({output: svg})
    return {
        "output": str(output),
        "blocks": len(circuit["blocks"]),
        "parts": len(circuit["parts"]),
        **stats,
    }
