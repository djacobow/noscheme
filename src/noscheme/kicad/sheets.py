"""Hierarchical KiCad schematics generated from the elaborated design.

The schematic is an export, like the netlist: KiCad's PCB editor updates a
board from it with Tools > Update PCB from Schematic (F8), which keeps
placement and routing and applies footprint, value, and net changes the KiCad
way. Edits made to the generated sheets are overwritten by the next export.

Each NoScheme block instance is a KiCad sheet; the root is the project's root
sheet. Block instances with equal contents share one sheet file, so an
instance array such as nine display digits is one file used nine times, and
KiCad's per-instance references and multichannel tools apply. Contents are
equal when the structure, values, footprints, and BOM fields of every part,
and the rails each port ties to, agree.

Symbols are generated boxes rather than KiCad library art, so the export reads
no CAD libraries and has no hidden power pins. Pins connect through labels
placed on their ends, never wires:

- Rails, and nets declared at the root with a plain name, use global labels.
- A net declared in a block uses a local label in that block's sheet; KiCad
  then names it with the sheet path, the same name the netlist exporter gives.
- A net reaching a block through a port uses a hierarchical label named for
  the port field, and the block's sheet symbol carries a matching sheet pin.

A deliberate no-connect pin gets a no-connect flag.
"""

import math
from uuid import UUID, uuid5
import zlib

from ..hierarchy import build_tree, declaration_order
from ..schematic import Design, natural, relative, show_path
from .export import KiCadNames, attribute, net_classes, part_value, pin_kind

VERSION = 20250114  # KiCad 9 formats; KiCad 10 reads them
LIB_VERSION = 20241209
LIBRARY = "noscheme.kicad_sym"
FONT = 1.27
CHAR = 1.05  # approximate advance per character at FONT
PITCH = 2.54
PIN = 2.54
MARGIN = 25.4
TITLE_BLOCK = 50.8  # room below the contents for KiCad's title block
GAP = 7.62  # between items, and between rows
ROW_WIDTH = 360.0
TEXT = 0.6  # label text offset from its anchor
LABEL_EXTRA = {"label": 1.0, "hierarchical_label": 3.0, "global_label": 4.0}
BOM_FIELDS = (
    ("manufacturer", "Manufacturer"),
    ("mpn", "MPN"),
    ("supplier", "Supplier"),
    ("supplier_part_number", "Supplier Part Number"),
)


def up(value, step=PITCH):
    return math.ceil(value / step - 1e-9) * step


def mm(value):
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


class Raw(str):
    """An unquoted token: a keyword or a number."""


def q(text):
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def node(name, *items):
    return [Raw(name), *items]


def at(x, y, angle=None):
    return node("at", Raw(mm(x)), Raw(mm(y)), *([] if angle is None else [Raw(mm(angle))]))


def effects(justify=None, hide=False):
    out = node("effects", node("font", node("size", Raw(mm(FONT)), Raw(mm(FONT)))))
    if justify:
        out.append(node("justify", *[Raw(j) for j in justify.split()]))
    if hide:
        out.append(node("hide", Raw("yes")))
    return out


def prop(name, value, x, y, *, hide=False, justify=None, angle=0):
    return node("property", q(name), q(value), at(x, y, angle), effects(justify, hide))


def write(value, indent=0):
    """KiCad-style layout: one form per line, atoms inline."""
    if isinstance(value, Raw):
        return str(value)
    if isinstance(value, str):
        return value
    head, *rest = value
    flat = [r for r in rest if not isinstance(r, list)]
    nested = [r for r in rest if isinstance(r, list)]
    text = "(" + " ".join([str(head), *(write(r) for r in flat)])
    if not nested:
        return text + ")"
    pad = "\t" * (indent + 1)
    for r in nested:
        text += "\n" + pad + write(r, indent + 1)
    return text + "\n" + "\t" * indent + ")"


# --------------------------------------------------------------------------
# Symbols


class Box:
    """A component definition's generated symbol: pins left, then right."""

    def __init__(self, part):
        self.definition = part["definition"]
        keys = sorted(part["pins"], key=natural)
        self.names = {}
        for key in keys:
            aliases = attribute(part["pins"][key], "aliases", [])
            self.names[key] = aliases[0] if aliases else key
        self.small = len(keys) <= 2
        self.pins = {}  # key -> (x, y, side) in symbol coordinates, y up
        if self.small:
            self.half = PITCH
            for key, side in zip(keys, "LR"):
                self.pins[key] = ((-1 if side == "L" else 1) * (self.half + PIN), 0.0, side)
            self.top, self.bottom = 1.27, -1.27
            return
        left = keys[: (len(keys) + 1) // 2]
        right = keys[len(left) :]
        widest = max(len(self.names[k]) for k in keys)
        self.half = max(up((widest * 2 * CHAR + 2 * PITCH) / 2), 2 * PITCH)
        for column, side in ((left, "L"), (right, "R")):
            for i, key in enumerate(column):
                x = (-1 if side == "L" else 1) * (self.half + PIN)
                self.pins[key] = (x, -i * PITCH, side)
        self.top, self.bottom = PITCH, -len(left) * PITCH

    def lib_symbol(self, kinds, prefix, lib=""):
        """The symbol, named `lib` + definition (`noscheme:` when embedded)."""
        name = self.definition
        body = node(
            "rectangle",
            node("start", Raw(mm(-self.half)), Raw(mm(self.top))),
            node("end", Raw(mm(self.half)), Raw(mm(self.bottom))),
            node("stroke", node("width", Raw("0.254")), node("type", Raw("default"))),
            node("fill", node("type", Raw("background"))),
        )
        pins = []
        for key, (x, y, side) in self.pins.items():
            pins.append(
                node(
                    "pin",
                    Raw(kinds[key]),
                    Raw("line"),
                    at(x, y, 0 if side == "L" else 180),
                    node("length", Raw(mm(PIN))),
                    node("name", q(self.names[key]), effects()),
                    node("number", q(key), effects()),
                )
            )
        hidden = [node("hide", Raw("yes"))] if self.small else []
        numbers = [node("pin_numbers", *hidden)] if self.small else []
        return node(
            "symbol",
            q(f"{lib}{name}"),
            *numbers,
            node("pin_names", node("offset", Raw("1.016")), *hidden),
            node("exclude_from_sim", Raw("no")),
            node("in_bom", Raw("yes")),
            node("on_board", Raw("yes")),
            prop("Reference", prefix, 0, self.top + 1.27, justify="left"),
            prop("Value", name.rsplit(".", 1)[-1], 0, self.bottom - 1.27, justify="left"),
            prop("Footprint", "", 0, 0, hide=True),
            prop("Datasheet", "", 0, 0, hide=True),
            prop("Description", f"NoScheme {name}", 0, 0, hide=True),
            node("symbol", q(f"{name}_0_1"), body),
            node("symbol", q(f"{name}_1_1"), *pins),
        )


# --------------------------------------------------------------------------
# KiCad's default pin-conflict matrix: pairs it reports as an error or warning.

CONFLICTS = {
    frozenset(pair)
    for pair in (
        ("output", "output"),
        ("output", "tri_state"),
        ("output", "power_out"),
        ("output", "open_collector"),
        ("output", "open_emitter"),
        ("bidirectional", "power_out"),
        ("bidirectional", "open_emitter"),
        ("tri_state", "power_in"),
        ("tri_state", "power_out"),
        ("tri_state", "open_collector"),
        ("tri_state", "open_emitter"),
        ("power_out", "power_out"),
        ("power_out", "open_collector"),
        ("power_out", "open_emitter"),
    )
}
# Which pin of a conflicting pair is drawn passive: the earlier in this list.
DEMOTE = ("output", "tri_state", "open_collector", "open_emitter", "bidirectional", "power_out")


def conflict(pins):
    """(definition, pin) to demote for the first conflicting pair, or None."""
    for i, (d1, k1, a) in enumerate(pins):
        for d2, k2, b in pins[i + 1 :]:
            if frozenset((a, b)) in CONFLICTS:
                rank = {kind: n for n, kind in enumerate(DEMOTE)}
                return (d1, k1) if rank.get(a, 99) <= rank.get(b, 99) else (d2, k2)
    return None


# --------------------------------------------------------------------------
# Power flag


def power_flag(name):
    """KiCad's PWR_FLAG: tells KiCad's ERC that NoScheme accepted the supply."""
    pin = node(
        "pin",
        Raw("power_out"),
        Raw("line"),
        at(0, 0, 90),
        node("length", Raw("0")),
        node("name", q("pwr"), effects()),
        node("number", q("1"), effects()),
    )
    flag = node(
        "polyline",
        node(
            "pts",
            *[
                node("xy", Raw(mm(x)), Raw(mm(y)))
                for x, y in (
                    (0, 0),
                    (0, 1.27),
                    (-1.016, 1.905),
                    (0, 2.54),
                    (1.016, 1.905),
                    (0, 1.27),
                )
            ],
        ),
        node("stroke", node("width", Raw("0")), node("type", Raw("default"))),
        node("fill", node("type", Raw("none"))),
    )
    return node(
        "symbol",
        q(name),
        node("power"),
        node("pin_numbers", node("hide", Raw("yes"))),
        node("pin_names", node("offset", Raw("0")), node("hide", Raw("yes"))),
        node("exclude_from_sim", Raw("no")),
        node("in_bom", Raw("yes")),
        node("on_board", Raw("yes")),
        prop("Reference", "#FLG", 0, 1.905, hide=True),
        prop("Value", "PWR_FLAG", 0, 3.81),
        prop("Footprint", "", 0, 0, hide=True),
        prop("Datasheet", "", 0, 0, hide=True),
        prop(
            "Description", "Special symbol for telling ERC where power comes from", 0, 0, hide=True
        ),
        node("symbol", q("PWR_FLAG_0_0"), pin),
        node("symbol", q("PWR_FLAG_0_1"), flag),
    )


# --------------------------------------------------------------------------
# Sheets


class Sheets:
    def __init__(self, circuit, state, project):
        self.circuit, self.state, self.project = circuit, state, project
        self.design = Design(circuit, seed=0, effort=1.0)
        self.names = KiCadNames(b["path"] for b in circuit["blocks"])
        self.exported = {net["name"]: self.names.encode(net) for net in circuit["nets"]}
        # Rails with a bare root name are global labels on every sheet; a rail
        # declared inside a block travels through ports like any other net.
        self.rail = {n for n in self.design.rail if not self.exported[n].startswith("/")}
        self.paths = {net["name"]: tuple(net["path"]) for net in circuit["nets"]}
        self.classes = net_classes(circuit)
        self.namespace = UUID(state["namespace"])
        self.root = build_tree(circuit)
        self.keys, self.used_ports, self.anchor_ids = {}, {}, {}
        self.files = {}  # sheet key -> file name
        self.instances = {}  # sheet key -> [(node, instance path)]
        self.pages = {}  # instance path of a sheet -> page number
        self.plan = {}  # (sheet key, anchor) -> decorations
        self.flags = 0
        self.symbols()

    def uid(self, *parts):
        return str(uuid5(self.namespace, "kicad-schematic:" + "\x1f".join(map(str, parts))))

    def fitted(self, part):
        return bool(attribute(part, "footprint"))

    # -- symbols and the supplies KiCad's ERC expects --------------------------

    def symbols(self):
        """Boxes and KiCad pin types per definition, and nets needing a PWR_FLAG.

        NoScheme's own ERC has already judged every connection, so the sheets are
        drawn for KiCad's ERC to agree: an ambiguous pin type is passive, a pin
        that would trip KiCad's pin-conflict matrix is demoted to passive, and a
        net with power inputs but no power output gets a PWR_FLAG.
        """
        parts = [p for p in self.circuit["parts"] if self.fitted(p)]
        self.boxes, self.kinds = {}, {}
        for part in parts:
            definition = part["definition"]
            if definition not in self.boxes:
                same = [p for p in parts if p["definition"] == definition]
                self.boxes[definition] = Box(part)
                self.kinds[definition] = {
                    key: pin_kind([p["pins"][key] for p in same]) for key in part["pins"]
                }
        for kinds in self.kinds.values():
            for key, kind in kinds.items():
                if kind == "unspecified":  # KiCad warns on every unspecified pin
                    kinds[key] = "passive"
        by_net = {}
        for part in parts:
            for key, pin in part["pins"].items():
                if pin["net"] is not None:
                    by_net.setdefault(pin["net"], []).append((part["definition"], key))
        changed = True
        while changed:
            changed = False
            for pins in by_net.values():
                pair = conflict([(d, k, self.kinds[d][k]) for d, k in pins])
                if pair:
                    d, k = pair
                    self.kinds[d][k] = "passive"
                    changed = True
        self.needs_flag = set()
        for net, pins in by_net.items():
            kinds = {self.kinds[d][k] for d, k in pins}
            if "power_in" in kinds and "power_out" not in kinds:
                self.needs_flag.add(net)
        # A net reaching a single pin has nothing to label: KiCad flags a label
        # with one pin, so the pin is drawn as a no-connect instead.
        self.single = {net for net, pins in by_net.items() if len(pins) == 1}

    def prefix(self, definition):
        part = next(p for p in self.circuit["parts"] if p["definition"] == definition)
        return attribute(part, "ref_prefix", "U")

    def lib_symbols(self, definitions, prefix=""):
        out = [
            self.boxes[d].lib_symbol(self.kinds[d], self.prefix(d), prefix)
            for d in sorted(definitions)
        ]
        return [*out, power_flag(f"{prefix}PWR_FLAG")]

    def library(self):
        """The `noscheme` symbol library: every generated symbol, as embedded."""
        return (
            write(
                [
                    Raw("kicad_symbol_lib"),
                    node("version", Raw(str(LIB_VERSION))),
                    node("generator", q("noscheme")),
                    node("generator_version", q("0.1")),
                    *self.lib_symbols(self.boxes),
                ]
            )
            + "\n"
        )

    # -- sheet files and instances ---------------------------------------------

    def key(self, node):
        """Equal keys can share a sheet file: same structure and same fields."""
        if node.path not in self.keys:
            own = []
            for part in node.parts:
                bom = attribute(part, "bom", {})
                own.append(
                    (
                        relative(part["path"], node.path),
                        attribute(part, "footprint"),
                        sorted((k, str(v.get("value"))) for k, v in bom.items()),
                    )
                )
            children = [(relative(c.path, node.path), self.key(c)) for c in node.children]
            text = repr((self.design.signature(node), own, children))
            self.keys[node.path] = f"{node.definition}#{zlib.crc32(text.encode()):08x}"
        return self.keys[node.path]

    def file_for(self, node, stem):
        key = self.key(node)
        if key not in self.files:
            base = stem if not node.path else node.definition.replace(".", "_")
            name, n = f"{base}.kicad_sch", 1
            while name in self.files.values():
                n += 1
                name = f"{base}_{n}.kicad_sch"
            self.files[key] = name
        return self.files[key]

    def walk(self, node, path, stem):
        file = self.file_for(node, stem)
        self.instances.setdefault(self.key(node), []).append((node, path))
        self.pages[path] = len(self.pages) + 1
        for child in node.children:
            sheet = self.uid(file, relative(child.path, node.path))
            self.walk(child, f"{path}/{sheet}", stem)

    # -- labels -------------------------------------------------------------

    def ports(self, node):
        """Non-rail net -> port field label, first field wins."""
        out = {}
        for port, entries in node.ports.items():
            for entry in entries:
                net = entry["net"]
                if net is not None and net not in self.rail:
                    out.setdefault(net, show_path(relative(entry["path"], node.path)))
        return out

    def label(self, net, node, ports):
        """(label kind, text) that makes KiCad give `net` its exported name."""
        exported = self.exported[net]
        if net in self.rail:
            return "global_label", exported
        if net in ports:
            return "hierarchical_label", ports[net]
        block, _ = self.names.split(self.paths[net])
        if block != node.path:
            raise ValueError(f"net {net!r} is neither local to nor a port of {node.path}")
        if not node.path and not exported.startswith("/"):
            return "global_label", exported
        return "label", self.names.local(self.paths[net])

    def members(self, node):
        entries = [("part", p, declaration_order(p, p["path"])) for p in node.parts]
        entries = [e for e in entries if self.fitted(e[1])]
        entries += [("block", c, c.order) for c in node.children]
        entries.sort(key=lambda e: e[2])
        return [(kind, member) for kind, member, _ in entries]

    def anchors(self, node):
        """Every connection point on a block's sheet, with its net here: (anchor, net).

        The anchors belong to the sheet file, so every instance sharing it has
        the same ones; only their nets differ.
        """
        return [(anchor, self.anchor_net(node, anchor)) for anchor in self.sheet_anchors(node)]

    def sheet_anchors(self, node):
        key = self.key(node)
        if key not in self.anchor_ids:
            instances = [n for n, _ in self.instances[key]]
            ids = []
            for kind, member in self.members(instances[0]):
                path = member["path"] if kind == "part" else member.path
                rel = relative(path, instances[0].path)
                if kind == "part":
                    for pin in self.boxes[member["definition"]].pins:
                        anchor = ("pin", rel, pin)
                        nets = [self.anchor_net(n, anchor) for n in instances]
                        if not all(n is None or n in self.single for n in nets):
                            ids.append(anchor)
                else:
                    ids += [("sheet", rel, field) for field, _ in self.sheet_pins(member)]
            self.anchor_ids[key] = ids
        return self.anchor_ids[key]

    def used(self, node):
        """Port fields with a hierarchical label on the block's sheet."""
        key = self.key(node)
        if key not in self.used_ports:
            node0 = self.instances[key][0][0]
            ports = self.ports(node0)
            self.used_ports[key] = {
                ports[net]
                for _, net in self.anchors(node0)
                if net is not None and self.label(net, node0, ports)[0] == "hierarchical_label"
            }
        return self.used_ports[key]

    def sheet_pins(self, child):
        """(field, net) for each port field the child's sheet labels.

        Rails are global labels, so they need no sheet pin; a pin for a field
        the child never labels would dangle.
        """
        used = self.used(child)
        pins = []
        for port, entries in child.ports.items():
            for entry in entries:
                net, field = entry["net"], show_path(relative(entry["path"], child.path))
                if net is not None and net not in self.rail and field in used:
                    pins.append((field, net))
        return pins

    def anchor_net(self, node, anchor):
        kind, rel, key = anchor
        if kind == "pin":
            part = next(p for p in node.parts if relative(p["path"], node.path) == rel)
            return part["pins"][key]["net"]
        child = next(c for c in node.children if relative(c.path, node.path) == rel)
        return dict(self.sheet_pins(child)).get(key)

    def decorate(self):
        """Place each needed PWR_FLAG and net-class directive once per net.

        A decoration in a shared sheet appears in every instance, so it goes on
        an anchor only where every instance's net needs exactly it. Sheets with
        fewer instances are tried first.
        """
        flagged, classed = set(), set()
        order = sorted(self.instances, key=lambda k: len(self.instances[k]))
        for key in order:
            instances = self.instances[key]
            for anchor, _ in self.anchors(instances[0][0]):
                nets = [self.anchor_net(n, anchor) for n, _ in instances]
                marks = set()
                if all(n in self.needs_flag for n in nets) and not flagged & set(nets):
                    marks.add(("flag",))
                    flagged.update(nets)
                kind = self.classes.get(nets[0])
                same = kind and all(self.classes.get(n) == kind for n in nets)
                if same and not classed & set(nets):
                    marks.add(("class", kind))
                    classed.update(nets)
                if marks:
                    self.plan[(key, anchor)] = marks
        self.unplaced = sorted(
            (self.needs_flag - flagged) | (set(self.classes) - classed - self.unfitted())
        )

    def unfitted(self):
        """Nets with no fitted pin: nothing in the schematic carries them."""
        carried = {
            pin["net"]
            for part in self.circuit["parts"]
            if self.fitted(part)
            for pin in part["pins"].values()
        }
        return set(self.classes) - carried

    # -- rendering ----------------------------------------------------------

    def render(self, stem):
        root_uuid = self.uid("root")
        self.walk(self.root, f"/{root_uuid}", stem)
        self.decorate()
        out = {}
        for key, instances in self.instances.items():
            out[self.files[key]] = self.sheet(key, instances, root_uuid)
        out[LIBRARY] = self.library()
        return out

    def sheet(self, key, instances, root_uuid):
        file = self.files[key]
        node0 = instances[0][0]
        ports = self.ports(node0)
        items = []  # (kind, object, width, height, left extent, right extent)
        for kind, member in self.members(node0):
            if kind == "part":
                box = self.boxes[member["definition"]]
                ext = {"L": 0.0, "R": 0.0}
                for pin, (_, _, side) in box.pins.items():
                    net = member["pins"][pin]["net"]
                    if net is not None:
                        how, text = self.label(net, node0, ports)
                        ext[side] = max(ext[side], len(text) * CHAR + LABEL_EXTRA[how] + 1)
                items.append(
                    (
                        "part",
                        member,
                        2 * (box.half + PIN),
                        box.top - box.bottom,
                        up(ext["L"]),
                        up(ext["R"]),
                    )
                )
            else:
                pins = self.sheet_pins(member)
                width = up(
                    max(
                        [len(show_path(relative(member.path, node0.path))) * CHAR + PITCH]
                        + [len(p) * CHAR + 2 * PITCH for p, _ in pins]
                        + [len(self.files[self.key(member)]) * CHAR]
                    )
                )
                ext = 0.0
                for _, net in pins:
                    how, text = self.label(net, node0, ports)
                    ext = max(ext, len(text) * CHAR + LABEL_EXTRA[how] + 1)
                items.append(
                    ("block", member, width, (len(pins) + 1) * PITCH, up(ext) + PITCH, 0.0)
                )
        body, used = [], {}
        x, y, row_h, right = MARGIN, MARGIN, 0.0, MARGIN
        for kind, member, w, h, left, rext in items:
            span = left + w + rext
            if x > MARGIN and x + span > MARGIN + ROW_WIDTH:
                x, y, row_h = MARGIN, y + row_h + GAP, 0.0
            ox = x + left
            if kind == "part":
                box = self.boxes[member["definition"]]
                cx, cy = ox + box.half + PIN, y + up(box.top)
                body += self.symbol(member, box, cx, cy, node0, ports, key, file, used)
            else:
                body += self.sheet_symbol(member, ox, y + 2 * PITCH, w, node0, ports, key, file)
            x += span + GAP
            row_h = max(row_h, h + 3 * PITCH)
            right = max(right, x)
        width = max(up(right + MARGIN, 10), 297)
        height = max(up(y + row_h + TITLE_BLOCK, 10), 210)
        head = [
            Raw("kicad_sch"),
            node("version", Raw(str(VERSION))),
            node("generator", q("noscheme")),
            node("generator_version", q("0.1")),
            node("uuid", q(root_uuid if not node0.path else self.uid("file", file))),
            node("paper", q("User"), Raw(mm(width)), Raw(mm(height))),
            node(
                "title_block",
                node("title", q(self.title(node0))),
                node("comment", Raw("1"), q("Generated by NoScheme; edits are overwritten")),
            ),
            node(
                "lib_symbols",
                *self.lib_symbols(used, "noscheme:"),
            ),
        ]
        tail = []
        if not node0.path:
            tail.append(node("sheet_instances", node("path", q("/"), node("page", q("1")))))
        tail.append(node("embedded_fonts", Raw("no")))
        return write([*head, *body, *tail]) + "\n"

    def title(self, node):
        """The block definition; KiCad shows each instance's sheet path itself."""
        return self.circuit["board"] if not node.path else node.definition

    def decorations(self, key, anchor, x, y, file):
        """A planned PWR_FLAG (above the anchor) and net-class directive (below)."""
        out = []
        for mark in sorted(self.plan.get((key, anchor), ())):
            tag = self.uid(file, "mark", anchor, mark)
            if mark[0] == "class":
                out.append(
                    node(
                        "netclass_flag",
                        q(""),
                        node("length", Raw(mm(PITCH))),
                        node("shape", Raw("round")),
                        at(x, y, 270),
                        node("effects", node("font", node("size", Raw("1.27"), Raw("1.27")))),
                        node("uuid", q(tag)),
                        prop("Netclass", mark[1], x + 0.7, y + PITCH + 1.27, justify="left"),
                    )
                )
                continue
            refs = []
            for _, path in self.instances[key]:
                self.flags += 1
                refs.append((path, f"#FLG{self.flags}"))
            out.append(
                node(
                    "symbol",
                    node("lib_id", q("noscheme:PWR_FLAG")),
                    at(x, y, 0),
                    node("unit", Raw("1")),
                    node("exclude_from_sim", Raw("no")),
                    node("in_bom", Raw("yes")),
                    node("on_board", Raw("yes")),
                    node("dnp", Raw("no")),
                    node("uuid", q(tag)),
                    prop("Reference", refs[0][1], x, y - 1.905, hide=True),
                    prop("Value", "PWR_FLAG", x, y - 3.81),
                    prop("Footprint", "", x, y, hide=True),
                    prop("Datasheet", "", x, y, hide=True),
                    node("pin", q("1"), node("uuid", q(self.uid(file, "mark-pin", anchor)))),
                    node(
                        "instances",
                        node(
                            "project",
                            q(self.project),
                            *[
                                node("path", q(p), node("reference", q(r)), node("unit", Raw("1")))
                                for p, r in refs
                            ],
                        ),
                    ),
                )
            )
        return out

    def labels(self, how, text, x, y, angle, file, tag):
        justify = "left" if angle == 0 else "right"
        uuid = node("uuid", q(self.uid(file, "label", tag)))
        if how == "label":
            return [node("label", q(text), at(x, y, angle), effects(justify + " bottom"), uuid)]
        out = node(
            how,
            q(text),
            node("shape", Raw("passive")),
            at(x, y, angle),
        )
        if how == "global_label":
            out.append(node("fields_autoplaced", Raw("yes")))
        out += [effects(justify), uuid]
        if how == "global_label":
            out.append(prop("Intersheetrefs", "${INTERSHEET_REFS}", x, y, hide=True))
        return [out]

    def symbol(self, part, box, cx, cy, node0, ports, key, file, used):
        instances = self.instances[key]
        used[part["definition"]] = True
        rel = relative(part["path"], node0.path)
        refs = []
        for node_i, path_i in instances:
            match = next(p for p in node_i.parts if relative(p["path"], node_i.path) == rel)
            refs.append((path_i, self.state["entries"][match["identity"]]["reference"]))
        bom = attribute(part, "bom", {})
        dnp = bool(bom.get("dnp", {}).get("value", False))
        fields = [
            prop("Reference", refs[0][1], cx, cy - box.top - 1.27, justify="left"),
            prop("Value", part_value(part), cx, cy - box.bottom + 1.27, justify="left"),
            prop("Footprint", attribute(part, "footprint"), cx, cy, hide=True),
            prop("Datasheet", "", cx, cy, hide=True),
            prop("noscheme_path", show_path(rel), cx, cy, hide=True),
        ]
        for field, title in BOM_FIELDS:
            if field in bom:
                fields.append(prop(title, str(bom[field]["value"]), cx, cy, hide=True))
        out = [
            node(
                "symbol",
                node("lib_id", q(f"noscheme:{part['definition']}")),
                at(cx, cy, 0),
                node("unit", Raw("1")),
                node("exclude_from_sim", Raw("no")),
                node("in_bom", Raw("yes")),
                node("on_board", Raw("yes")),
                node("dnp", Raw("yes" if dnp else "no")),
                node("uuid", q(self.uid(file, rel))),
                *fields,
                *[
                    node("pin", q(k), node("uuid", q(self.uid(file, rel, "pin", k))))
                    for k in box.pins
                ],
                node(
                    "instances",
                    node(
                        "project",
                        q(self.project),
                        *[
                            node("path", q(p), node("reference", q(r)), node("unit", Raw("1")))
                            for p, r in refs
                        ],
                    ),
                ),
            )
        ]
        for pin_key, (px, py, side) in box.pins.items():
            x, y = cx + px, cy - py
            net = part["pins"][pin_key]["net"]
            if ("pin", rel, pin_key) not in self.sheet_anchors(node0):
                # NoScheme's ERC accepted the open pin; tell KiCad's too.
                tag = self.uid(file, rel, "nc", pin_key)
                out.append(node("no_connect", at(x, y), node("uuid", q(tag))))
                continue
            how, text = self.label(net, node0, ports)
            angle = 180 if side == "L" else 0
            out += self.labels(how, text, x, y, angle, file, (rel, pin_key))
            out += self.decorations(key, ("pin", rel, pin_key), x, y, file)
        return out

    def sheet_symbol(self, child, x, y, w, node0, ports, key, file):
        rel = relative(child.path, node0.path)
        pins = self.sheet_pins(child)
        h = (len(pins) + 1) * PITCH
        child_file = self.files[self.key(child)]
        sheet_uuid = self.uid(file, rel)
        out = node(
            "sheet",
            at(x, y),
            node("size", Raw(mm(w)), Raw(mm(h))),
            node("exclude_from_sim", Raw("no")),
            node("in_bom", Raw("yes")),
            node("on_board", Raw("yes")),
            node("dnp", Raw("no")),
            node("fields_autoplaced", Raw("yes")),
            node("stroke", node("width", Raw("0.1524")), node("type", Raw("solid"))),
            node("fill", node("color", Raw("0"), Raw("0"), Raw("0"), Raw("0.0000"))),
            node("uuid", q(sheet_uuid)),
            prop("Sheetname", show_path(rel), x, y - 0.7, justify="left bottom"),
            prop("Sheetfile", child_file, x, y + h + 0.6, justify="left top"),
        )
        labels = []
        for i, (field, net) in enumerate(pins):
            py = y + (i + 1) * PITCH
            out.append(
                node(
                    "pin",
                    q(field),
                    Raw("passive"),
                    at(x, py, 180),
                    node("uuid", q(self.uid(file, rel, "sheet-pin", field))),
                    effects("left"),
                )
            )
            if net in self.single and net not in ports:
                # Its one pin is inside the block: nothing on this sheet joins it.
                tag = self.uid(file, rel, "sheet-nc", field)
                labels.append(node("no_connect", at(x, py), node("uuid", q(tag))))
                continue
            how, text = self.label(net, node0, ports)
            labels += self.labels(how, text, x, py, 180, file, (rel, "sheet-pin", field))
            labels += self.decorations(key, ("sheet", rel, field), x, py, file)
        out.append(
            node(
                "instances",
                node(
                    "project",
                    q(self.project),
                    *[
                        node("path", q(path_i), node("page", q(str(self.pages[child_path]))))
                        for _, path_i in self.instances[self.key(node0)]
                        for child_path in [f"{path_i}/{sheet_uuid}"]
                    ],
                ),
            )
        )
        return [out, *labels]


def render_schematic(circuit, state, project, stem):
    """{file name: text} for the root sheet `stem`.kicad_sch and its sub-sheets."""
    return Sheets(circuit, state, project).render(stem)


def generated(path):
    """Whether a .kicad_sch is an untouched NoScheme export.

    KiCad rewrites the generator when it saves a sheet, so a sheet someone
    saved from KiCad is never treated as ours.
    """
    with open(path, encoding="utf-8", errors="replace") as f:
        return '(generator "noscheme")' in f.read(400)


def library_table(text):
    """The project's sym-lib-table with the `noscheme` library registered."""
    entry = (
        '  (lib (name "noscheme")(type "KiCad")(uri "${KIPRJMOD}/'
        + LIBRARY
        + '")(options "")(descr "Generated by NoScheme"))\n'
    )
    if not text or not text.strip():
        return "(sym_lib_table\n  (version 7)\n" + entry + ")\n"
    if '(name "noscheme")' in text:
        return text
    end = text.rstrip().rfind(")")
    return text[:end].rstrip() + "\n" + entry + ")\n"
