"""Hierarchy diagrams draw nested block instances, optionally with port nets."""

import json
from xml.etree import ElementTree

import pytest

from noscheme.cli import main
from noscheme.hierarchy import RAIL_COLOUR, UNSHARED_COLOUR, common_stem

SVG = "{http://www.w3.org/2000/svg}"
SOURCE = """from noscheme import role, use, net
component Resistor:
  pins:
    1..2: [role.passive, use.optional]
  metadata:
    ref_prefix: "R"
    interchangeable: [1, 2]
bundle Pair:
  net.passive a
  net.passive b
block Cell:
  ports:
    net.power supply
    net.passive tap
    Pair pair
  Resistor load(supply, tap)
  Resistor bridge(pair.a, pair.b)
block Bank:
  ports:
    net.power supply
    net.passive tap
  Pair pair
  Cell cells[3](supply: supply, tap: tap, pair: pair)
block Top:
  net.power vcc
  net.passive shared
  net.passive alone
  Bank left(supply: vcc, tap: shared)
  Bank right(supply: vcc, tap: shared)
  Cell solo(supply: vcc, tap: alone)
  Resistor spare[2]
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
    assert main(["export-hierarchy", "--project", str(project), *options]) == 0
    report = json.loads(capsys.readouterr().out)
    root = ElementTree.parse(report["output"]).getroot()
    return report, root


def texts(root):
    return [element.text for element in root.iter(f"{SVG}text")]


def port_dots(root):
    """Map each port line's text to its dot colour."""
    dots = {}
    for group in root.iter(f"{SVG}g"):
        circle, text = group.find(f"{SVG}circle"), group.find(f"{SVG}text")
        dots.setdefault(text.text, set()).add(circle.get("fill"))
    return dots


def test_boxes_nest_and_arrays_collapse(project, capsys):
    report, root = export(project, capsys)
    assert report == {
        "output": str(project / "build/main/hierarchy.svg"),
        "blocks": 10,
        "parts": 16,
    }
    lines = texts(root)
    assert lines[:2] == ["main.Top", "16 parts"]
    assert lines.count("cells[3]") == 2
    assert "main.Cell  ·  2 parts each, ×3; first shown" in lines
    # Declaration order: left, then right, then solo.
    assert [t for t in lines if t in {"left", "right", "solo"}] == ["left", "right", "solo"]
    assert not any("→" in t for t in lines)


def test_nets_colour_shared_signals_and_mark_rails(project, capsys):
    _, root = export(project, capsys, "--nets")
    dots = port_dots(root)
    assert dots["supply → vcc"] == {RAIL_COLOUR}
    # left and right share `shared`; their cells inherit that colour.
    assert len(dots["tap → shared"]) == 1
    assert dots["tap → shared"] != {UNSHARED_COLOUR}
    assert dots["tap → alone"] == {UNSHARED_COLOUR}
    # Bundle ports summarise their fields and list them on hover.
    assert "pair → left.pair (2 nets)" in dots
    titles = [t.text for t in root.iter(f"{SVG}title")]
    assert "left.pair.a\nleft.pair.b" in titles


def test_depth_and_parts(project, capsys):
    _, root = export(project, capsys, "--depth", "1", "--parts")
    lines = texts(root)
    assert "cells[3]" not in lines
    assert lines.count("not shown: cells[3]") == 2
    assert "spare[2]: main.Resistor" in lines
    _, root = export(project, capsys, "--depth", "0")
    assert "not shown: left, right, solo" in texts(root)


def test_refuses_to_overwrite_inputs(project, capsys):
    args = ["export-hierarchy", "--project", str(project), "--output", str(project / "main.nosch")]
    assert main(args) != 0
    assert "export.overwrite" in capsys.readouterr().err
    assert (project / "main.nosch").read_text() == SOURCE


def test_rejects_negative_depth(project):
    with pytest.raises(SystemExit):
        main(["export-hierarchy", "--project", str(project), "--depth", "-1"])


@pytest.mark.parametrize(
    ("nets", "stem"),
    [
        (["usb.dp", "usb.dm"], "usb"),
        (["d.digits[0].segments[0]", "d.digits[0].segments[1]"], "d.digits[0].segments"),
        (["vcc", "gnd"], ""),
    ],
)
def test_common_stem_cuts_at_name_boundaries(nets, stem):
    assert common_stem(nets) == stem
