"""SVG diagrams of an elaborated design's block hierarchy.

Each block instance is drawn as a box nested inside its parent. Elements of an
instance array share one box, labelled with the array shape, whose contents
show the first element. With nets, each box lists its ports and the flattened
net each binds to; a net shared by two or more sibling boxes gets the same
colour in each, so connectivity reads without drawn wires.
"""

from dataclasses import dataclass, field
import os
from xml.sax.saxutils import escape

from .kicad.export import publish, validate_export_paths

FONT_SIZE = 11
CHAR_WIDTH = FONT_SIZE * 0.6  # monospace advance
LINE = FONT_SIZE + 5
PAD = 8
GAP = 10
# Okabe-Ito colours first (colour-blind safe), then darker tones once those
# run out. Each shared signal net keeps one colour across the whole diagram.
PALETTE = (
    "#0072B2",
    "#D55E00",
    "#009E73",
    "#CC79A7",
    "#E69F00",
    "#56B4E9",
    "#7B3294",
    "#A6611A",
    "#1B7837",
    "#B2182B",
    "#2166AC",
    "#8C510A",
)
RAIL_COLOUR = "#333333"
UNSHARED_COLOUR = "#BBBBBB"
FILLS = ("#F7F7F7", "#EEF3F8", "#F6F1EA", "#EEF6F0", "#F5EEF4")
RAIL_TYPES = {"power", "ground"}


@dataclass
class Node:
    path: tuple
    definition: str
    ports: dict = field(default_factory=dict)
    order: tuple = ()
    children: list = field(default_factory=list)
    parts: list = field(default_factory=list)


def instance_name(path):
    """Strip trailing array indices: ('display', 'digits', 3) -> ('display', 'digits')."""
    end = len(path)
    while end and isinstance(path[end - 1], int):
        end -= 1
    return tuple(path[:end]), tuple(path[end:])


def build_tree(circuit):
    nodes = {
        tuple(b["path"]): Node(
            tuple(b["path"]), b["definition"], b["ports"], declaration_order(b, b["path"])
        )
        for b in circuit["blocks"]
    }
    for path, node in nodes.items():
        if path:
            name, _ = instance_name(path)
            nodes[name[:-1]].children.append(node)
    for part in circuit["parts"]:
        name, _ = instance_name(tuple(part["path"]))
        nodes[name[:-1]].parts.append(part)
    for node in nodes.values():
        node.children.sort(key=lambda child: child.order)
        node.parts.sort(key=lambda part: declaration_order(part, part["path"]))
    return nodes[()]


def declaration_order(item, path):
    """Source order of the declaring statement, then array index order."""
    span = item.get("source", {}).get("declaration") or {}
    _, index = instance_name(tuple(path))
    return (span.get("filename", ""), span.get("start_pos", 0), index)


def array_label(name, indices):
    """`digits[9]` for one dimension, `grid[4][2]` for two."""
    if not indices or not indices[0]:
        return name
    shape = [max(index[d] for index in indices) + 1 for d in range(len(indices[0]))]
    return name + "".join(f"[{n}]" for n in shape)


def grouped(items, definition):
    """Group array elements: [(label, count, representative)] in declaration order."""
    groups = {}
    for item in items:
        name, index = instance_name(tuple(item["path"] if isinstance(item, dict) else item.path))
        key = (name[-1], definition(item))
        groups.setdefault(key, []).append((index, item))
    return [
        (array_label(str(key[0]), [index for index, _ in members]), len(members), members[0][1])
        for key, members in groups.items()
    ]


def part_total(node):
    return len(node.parts) + sum(part_total(child) for child in node.children)


def net_types(circuit):
    return {
        net["name"]: {d.get("type") for d in net.get("declarations", ())} for net in circuit["nets"]
    }


def port_rows(node):
    """(port label, displayed net, net names) per port; bundles summarise their fields."""
    rows = []
    for port, entries in node.ports.items():
        nets = [entry["net"] for entry in entries]
        if len(nets) == 1:
            shown = nets[0]
        else:
            stem = common_stem(nets)
            shown = f"{stem} ({len(nets)} nets)" if stem else f"{len(nets)} nets"
        rows.append((port, shown, tuple(nets)))
    return rows


def common_stem(nets):
    """Shared prefix cut at a name boundary: usb.dp, usb.dm -> usb."""
    prefix = os.path.commonprefix(nets)
    cut = max(prefix.rfind("."), prefix.rfind("["))
    return prefix[:cut] if cut > 0 else ""


@dataclass
class Line:
    kind: str  # "port", "part", or "note"
    text: str
    colour: str = UNSHARED_COLOUR
    bold: bool = False
    hover: str = ""


@dataclass
class Box:
    label: str
    detail: str
    lines: list
    children: list
    width: float = 0
    height: float = 0
    x: float = 0
    y: float = 0
    depth: int = 0
    rows: list = field(default_factory=list)


def text_width(text):
    return len(text) * CHAR_WIDTH


def make_box(node, label, count, depth, options, colours, types):
    total = part_total(node)
    detail = "" if node.definition == label else node.definition + "  ·  "
    detail += f"{total} part{'s' if total != 1 else ''}"
    if count > 1:
        detail += f" each, ×{count}; first shown"
    lines = []
    if options["nets"]:
        for port, shown, nets in port_rows(node):
            rail = any(types.get(net, set()) & RAIL_TYPES for net in nets)
            colour = next((colours[net] for net in nets if net in colours), UNSHARED_COLOUR)
            lines.append(
                Line(
                    "port",
                    f"{port} → {shown}",
                    RAIL_COLOUR if rail else colour,
                    rail,
                    "\n".join(nets),
                )
            )
    children = grouped(node.children, lambda n: n.definition)
    if options["depth"] is not None and depth >= options["depth"]:
        if children:
            hidden = ", ".join(child_label for child_label, _, _ in children)
            lines.append(Line("note", f"not shown: {hidden}"))
        children = []
    if options["parts"]:
        for part_label, part_count, part in grouped(node.parts, lambda p: p["definition"]):
            lines.append(Line("part", f"{part_label}: {part['definition']}"))
    box = Box(label, detail, lines, [], depth=depth)
    box.children = [
        make_box(child, child_label, child_count, depth + 1, options, colours, types)
        for child_label, child_count, child in children
    ]
    return box


def net_colours(root, types):
    """One colour per signal net bound by two or more sibling blocks anywhere."""
    shared = {}

    def visit(node):
        users = {}
        for child in node.children:
            for entries in child.ports.values():
                for entry in entries:
                    users.setdefault(entry["net"], set()).add(instance_name(child.path)[0])
        for net, paths in users.items():
            if len(paths) > 1 and not types.get(net, set()) & RAIL_TYPES:
                shared.setdefault(net, None)
        for child in node.children:
            visit(child)

    visit(root)
    return {net: PALETTE[i % len(PALETTE)] for i, net in enumerate(shared)}


def measure(box):
    for child in box.children:
        measure(child)
    header = max(text_width(box.label) * 1.1, text_width(box.detail))
    lines = max((text_width(line.text) + 12 for line in box.lines), default=0)
    rows = pack(box.children)
    inner = max((sum(c.width for c in row) + GAP * (len(row) - 1) for row in rows), default=0)
    box.width = max(header, lines, inner) + 2 * PAD
    box.height = 2 * PAD + 2 * LINE + LINE * len(box.lines)
    if rows:
        box.height += GAP + sum(max(c.height for c in row) for row in rows) + GAP * (len(rows) - 1)
    box.rows = rows


def pack(children):
    """Greedy rows near a square aspect: wrap once a row passes the target width."""
    if not children:
        return []
    area = sum(c.width * c.height for c in children)
    target = max(max(c.width for c in children), (area * 1.6) ** 0.5)
    rows, row, width = [], [], 0
    for child in children:
        if row and width + GAP + child.width > target:
            rows.append(row)
            row, width = [], 0
        width += (GAP if row else 0) + child.width
        row.append(child)
    rows.append(row)
    return rows


def place(box, x, y):
    box.x, box.y = x, y
    top = y + PAD + 2 * LINE + LINE * len(box.lines) + GAP
    for row in box.rows:
        left = x + PAD
        for child in row:
            place(child, left, top)
            left += child.width + GAP
        top += max(c.height for c in row) + GAP


def render(box, out):
    fill = FILLS[box.depth % len(FILLS)]
    out.append(
        f'<rect x="{box.x:.1f}" y="{box.y:.1f}" width="{box.width:.1f}" height="{box.height:.1f}" '
        f'rx="6" fill="{fill}" stroke="#555" stroke-width="1"/>'
    )
    tx, ty = box.x + PAD, box.y + PAD + FONT_SIZE
    out.append(f'<text x="{tx:.1f}" y="{ty:.1f}" font-weight="bold">{escape(box.label)}</text>')
    out.append(f'<text x="{tx:.1f}" y="{ty + LINE:.1f}" fill="#555">{escape(box.detail)}</text>')
    for i, line in enumerate(box.lines):
        ly = ty + LINE * (i + 2)
        hover = f"<title>{escape(line.hover)}</title>" if line.hover else ""
        if line.kind == "port":
            weight = ' font-weight="bold"' if line.bold else ""
            out.append(
                f'<g>{hover}<circle cx="{tx + 4:.1f}" cy="{ly - 4:.1f}" r="3.5" '
                f'fill="{line.colour}"/><text x="{tx + 12:.1f}" y="{ly:.1f}"{weight}>'
                f"{escape(line.text)}</text></g>"
            )
        else:
            style = ' font-style="italic" fill="#777"' if line.kind == "note" else ' fill="#333"'
            out.append(f'<text x="{tx:.1f}" y="{ly:.1f}"{style}>{escape(line.text)}</text>')
    for child in box.children:
        render(child, out)


def hierarchy_svg(circuit, *, nets=False, parts=False, depth=None):
    """Render the elaborated circuit's block hierarchy as standalone SVG text."""
    options = {"nets": nets, "parts": parts, "depth": depth}
    tree, types = build_tree(circuit), net_types(circuit)
    colours = net_colours(tree, types) if nets else {}
    root = make_box(tree, circuit["board"], 1, 0, options, colours, types)
    measure(root)
    place(root, GAP, GAP)
    width, height = root.width + 2 * GAP, root.height + 2 * GAP
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" font-family="DejaVu Sans Mono, Menlo, '
        f'Consolas, monospace" font-size="{FONT_SIZE}">',
        f"<title>{escape(circuit['board'])} hierarchy</title>",
        f'<rect width="{width:.0f}" height="{height:.0f}" fill="#FFFFFF"/>',
    ]
    render(root, out)
    out.append("</svg>")
    return "\n".join(out) + "\n"


def export_hierarchy(project, circuit, output=None, **options):
    """Publish the SVG atomically, refusing to overwrite project inputs."""
    output = output or project.config.output_dir / "hierarchy.svg"
    svg = hierarchy_svg(circuit, **options)
    validate_export_paths(project, [output])
    publish({output: svg})
    return {
        "output": str(output),
        "blocks": len(circuit["blocks"]),
        "parts": len(circuit["parts"]),
    }
