"""Single-sheet schematics: open blocks, annealed placement, eight-way routing."""

import json
from xml.etree import ElementTree

import pytest
from types import SimpleNamespace

from noscheme.cli import main
from noscheme.schematic import (
    BUS,
    HARD,
    OVER_ARRAY,
    SOFT,
    WIRE,
    Design,
    Grid,
    commit,
    draw_routes,
    inward,
    line_up,
    search,
    turned,
)

SVG = "{http://www.w3.org/2000/svg}"
SOURCE = """from noscheme import role, use, net
component Resistor:
  pins:
    1..2: [role.passive, use.optional]
  metadata:
    ref_prefix: "R"
    interchangeable: [1, 2]
component Buffer:
  pins:
    1 (A): role.digital_input
    2 (Y): role.digital_output
    3 (VCC): role.power_input
    4 (GND): role.ground
  metadata:
    ref_prefix: "U"
bundle Pair:
  net.digital a
  net.digital b
block Stage:
  ports:
    net.power vcc
    net.ground gnd
    net.digital input
    net.digital output
  Buffer buffer(A: input, Y: output, VCC: vcc, GND: gnd)
block Sender:
  ports:
    net.power vcc
    net.ground gnd
    Pair pair
  net.digital start
  Stage stages[2](vcc: vcc, gnd: gnd):
    [0].input: start
    [0].output: pair.a
    [1].input: pair.a
    [1].output: pair.b
  Resistor bias(vcc, start)
block Top:
  net.power vcc
  net.ground gnd
  Pair link
  Sender sender(vcc: vcc, gnd: gnd, pair: link)
  Sender receiver(vcc: vcc, gnd: gnd, pair: link)
  Resistor load(link.a, link.b)
"""
CONFIG = """language_version = "0.1"
source_paths = ["."]
library_paths = []
output_dir = "build"
[targets.main]
root = "main.Top"
"""


@pytest.fixture
def project(tmp_path):
    (tmp_path / "main.nosch").write_text(SOURCE)
    (tmp_path / "noscheme.toml").write_text(CONFIG)
    return tmp_path


def export(project, capsys, *options):
    assert main(["export-schematic", "--project", str(project), *options]) == 0
    report = json.loads(capsys.readouterr().out)
    return report, ElementTree.parse(report["output"]).getroot()


def circuit(project):
    from noscheme.cli import elaborate_project, resolve_project

    loaded, definitions = resolve_project(project)
    return elaborate_project(loaded, definitions)[0]


def test_exports_every_block_open(project, capsys):
    report, root = export(project, capsys)
    assert report["output"] == str(project / "build/main/schematic.svg")
    assert (report["blocks"], report["parts"], report["unrouted"]) == (7, 7, 0)
    # sender and receiver share one layout, as do the four stages.
    assert report["layouts"] == 3
    texts = [t.text for t in root.iter(f"{SVG}text") if t.text]
    for name in ("sender", "receiver", "stages[0]", "stages[1]", "buffer", "load"):
        assert name in texts


def test_same_seed_same_sheet(project, capsys):
    _, first = export(project, capsys, "--seed", "7")
    _, second = export(project, capsys, "--seed", "7")
    assert ElementTree.tostring(first) == ElementTree.tostring(second)


def test_bundle_travels_as_one_bus_with_labelled_breakouts(project, capsys):
    _, root = export(project, capsys)
    buses = [p for p in root.iter(f"{SVG}polyline") if p.get("stroke") == BUS]
    assert buses
    breakout_labels = {
        t.text
        for t in root.iter(f"{SVG}text")
        if t.get("fill") == BUS and "rotate(" in (t.get("transform") or "")
    }
    # The root's load resistor taps both fields of the link bus.
    assert {"a", "b"} <= breakout_labels


def test_rails_are_flagged_not_wired(project, capsys):
    _, root = export(project, capsys)
    wired = {
        g.find(f"{SVG}title").text
        for g in root.iter(f"{SVG}g")
        if g.find(f"{SVG}title") is not None and g.find(f"{SVG}polyline") is not None
    }
    assert not wired & {"vcc", "gnd"}
    flags = [g for g in root.iter(f"{SVG}g") if (g.findtext(f"{SVG}title") or "") in {"vcc", "gnd"}]
    assert len(flags) >= 8  # VCC and GND on each of four buffers


def test_pin_arrangement_is_shared_by_every_instance(project):
    """Instances may turn, but unturned they all draw their pins alike."""
    design = Design(circuit(project), seed=0, effort=0.5)
    design.run()
    arrangements = {}
    for template in design.templates.values():
        for item in template.items:
            if item.kind != "part":
                continue
            points = item.drawing["points"]
            drawn = {
                side: [
                    k
                    for k, _ in sorted(
                        ((k, p) for k, p in points.items() if p[2] == side),
                        key=lambda kp: kp[1][1] if side in "LR" else kp[1][0],
                    )
                ]
                for side in "LRTB"
            }
            base = turned(drawn, 4 - item.rotation)
            arrangements.setdefault(item.part["definition"], []).append(base)
    assert arrangements
    for drawings in arrangements.values():
        assert all(d == drawings[0] for d in drawings)


def test_dots_mark_junctions_and_not_crossings():
    cols = 11

    def cells(*points):
        return [y * cols + x for x, y in points]

    routes = {
        "wires": {
            # Net 0: a T, west-east with a branch down from (5, 2).
            0: [cells((0, 2), (5, 2), (10, 2)), cells((5, 2), (5, 6))],
            # Net 1: crosses net 0's trunk at (8, 2) without joining it.
            1: [cells((8, 0), (8, 2), (8, 5))],
        },
        "names": {0: "tee", 1: "cross"},
        "bus": set(),
        "breakouts": [],
        "labels": [],
    }
    out = []
    draw_routes(SimpleNamespace(w=100, routes=routes), out)
    dots = [line for line in out if line.startswith("<circle")]
    assert dots == [f'<circle cx="50" cy="20" r="3" fill="{WIRE}"/>']


def test_refuses_to_overwrite_inputs(project, capsys):
    args = ["export-schematic", "--project", str(project), "--output", str(project / "main.nosch")]
    assert main(args) != 0
    assert "export.overwrite" in capsys.readouterr().err
    assert (project / "main.nosch").read_text() == SOURCE


def test_rejects_non_positive_effort(project):
    with pytest.raises(SystemExit):
        main(["export-schematic", "--project", str(project), "--effort", "0"])


def test_line_up_keeps_order_pitch_and_bounds():
    assert line_up([50, 50, 50], 20, 200) == [50, 70, 90]
    assert line_up([190, 195], 20, 200) == [180, 200]
    assert line_up([0], 40, 200) == [40]


def route_across(over, allowed):
    """Route east across a 3-cell-high soft block; return the path's cost proxy."""
    grid = Grid(200, 100)
    grid.cover(80, 30, 120, 70, SOFT, over)
    start = grid.index(20, 50)
    end = grid.index(180, 50)
    grid.state[start] = grid.state[end] = HARD
    path = search(grid, 0, (start, 0), {end: inward(4)}, allowed)
    assert path is not None
    cells = commit(grid, 0, path)
    return [grid.point(c) for c in cells]


def test_wires_cross_their_own_array_but_detour_around_other_symbols():
    straight = route_across(("digits",), {("digits",)})
    assert all(y == 50 for _, y in straight)
    detour = route_across(("other",), {("digits",)})
    assert any(y != 50 for _, y in detour)
    assert OVER_ARRAY < 10


def test_crossings_are_allowed_but_bends_on_them_are_not():
    grid = Grid(100, 100)
    for y in range(0, 101, 10):
        grid.owner[2][grid.index(50, y)] = 9  # a foreign vertical wire at x=50
    start, end = grid.index(10, 50), grid.index(90, 50)
    grid.state[start] = grid.state[end] = HARD
    path = search(grid, 0, (start, 0), {end: inward(4)}, set())
    assert path is not None
    crossing = grid.index(50, 50)
    i = [c for c, _ in path].index(crossing)
    assert path[i][1] == path[i + 1][1] == 0  # straight through the crossing


def test_rail_only_parts_cluster_in_a_corner(project):
    design = Design(circuit(project), seed=0, effort=0.5)
    design.run()
    for template in design.templates.values():
        loose = [i for i in template.items if i.kind == "part" and not i.terms]
        core = [i for i in template.items if i not in loose]
        if not loose or not core:
            continue
        right = max(i.x + i.w for i in core)
        bottom = max(i.y + i.h for i in core)
        for item in loose:
            assert item.x + item.w >= right or item.y + item.h >= bottom


def test_pins_wired_pin_for_pin_line_up():
    """Two eight-pin parts wired 1:1 take matching orders, so wires run parallel."""
    from noscheme.schematic import Symbol, choose_sides

    keys = [str(k) for k in range(1, 9)]
    left = Symbol("Left", keys, {k: k for k in keys}, {k: "R" for k in keys})
    # Pin k's partner sits at height (9 - k) * 20 to the right.
    votes = {k: [((1.0, 0.0), (100.0, (9 - int(k)) * 20.0), 1)] for k in keys}
    choose_sides(left.keys, left.sides, left.rank, votes, pair=False)
    order = sorted(keys, key=lambda k: left.rank[k])
    assert order == list(reversed(keys))
    assert all(left.sides[k] == "R" for k in keys)


def test_quarter_turns_keep_pin_order_around_the_body():
    sides = {"L": ["1", "2"], "T": ["3"], "R": ["4", "5"], "B": ["6"]}
    once = turned(sides, 1)
    # Clockwise: left reads bottom-to-top along the top, so its order reverses.
    assert once == {"T": ["2", "1"], "R": ["3"], "B": ["5", "4"], "L": ["6"]}
    assert turned(sides, 4) == sides
