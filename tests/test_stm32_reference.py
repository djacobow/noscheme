"""Reference-board wiring checks independent of installed KiCad libraries."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from noscheme.circuit import elaborate_project
from noscheme.erc import apply_erc
from noscheme.kicad.export import export_project
from noscheme.kicad.sexpr import load, children, scalar
from noscheme.resolve import resolve_project

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "examples/stm32h523"


@pytest.fixture
def board():
    project, definitions = resolve_project(PROJECT)
    circuit, warnings = elaborate_project(project, definitions)
    assert not warnings
    return project, circuit


def parts(circuit):
    return {p["identity"]: p for p in circuit["parts"]}


def test_all_parts_are_physical_and_both_power_modes_are_clean(board):
    _, c = board
    assert len(c["parts"]) == 58
    assert len(c["nets"]) == 52
    assert all(p["metadata"]["footprint"]["value"] for p in c["parts"])
    assert not any(
        d["metadata"].get("placeholder", {}).get("value")
        for n in c["nets"]
        for d in n["declarations"]
    )
    assert apply_erc(c) == ()
    assert {r["name"] for r in c["erc"]["configurations"]} == {"usb_powered", "header_5v_powered"}
    assert all(
        w["check"] == "multiple_drivers" and w["net"] == "mcu.vcap" for w in c["erc"]["waivers"]
    )


def test_mcu_supply_and_decoupling_connections(board):
    _, c = board
    p = parts(c)
    chip = p["mcu.chip"]["pins"]
    assert len(chip) == 100
    for number in [6, 11, 21, 22, 28, 50, 73, 75, 100]:
        assert chip[str(number)]["net"] == "v3v3"
        cap = p[f"mcu.decouple_pin_{number}"]
        assert [cap["pins"][pin]["net"] for pin in ("1", "2")] == ["v3v3", "gnd"]
        assert cap["value"]["value"] == "100nF"
    for number in [10, 19, 20, 27, 49, 74, 99]:
        assert chip[str(number)]["net"] == "gnd"
    for number in [48, 98]:
        assert chip[str(number)]["net"] == "mcu.vcap"
        cap = p[f"mcu.vcap_pin_{number}"]
        assert cap["pins"]["1"]["net"] == "mcu.vcap"
        assert cap["pins"]["2"]["net"] == "gnd"
        assert cap["value"]["value"] == "2.2uF"


def test_peripheral_pin_allocation_and_clock(board):
    _, c = board
    p = parts(c)
    expected = {
        70: "usb.dm",
        71: "usb.dp",
        72: "debug.swdio",
        76: "debug.swclk",
        89: "debug.swo",
        94: "boot0",
        14: "debug.reset#",
        29: "sd.cs#",
        30: "sd.sck",
        31: "sd.miso",
        32: "sd.mosi",
        92: "oled.scl",
        93: "oled.sda",
        67: "expansion_i2c.scl",
        66: "expansion_i2c.sda",
        42: "expansion_spi.cs#",
        43: "expansion_spi.sck",
        44: "expansion_spi.miso",
        45: "expansion_spi.mosi",
        25: "uart_tx[0]",
        26: "uart_rx[0]",
        55: "uart_tx[1]",
        56: "uart_rx[1]",
        12: "mcu.osc_in",
        13: "mcu.osc_out",
    }
    for number, net in expected.items():
        assert p["mcu.chip"]["pins"][str(number)]["net"] == net
    assert [p["mcu.crystal"]["pins"][pin]["net"] for pin in ("1", "2")] == [
        "mcu.osc_in",
        "mcu.osc_out",
    ]
    for suffix in ["in", "out"]:
        cap = p[f"mcu.crystal_load_{suffix}"]
        assert cap["pins"]["1"]["net"] == f"mcu.osc_{suffix}"
        assert cap["pins"]["2"]["net"] == "gnd"


def test_usb_contacts_and_sd_spi_mapping(board):
    _, c = board
    p = parts(c)
    usb = p["usb_c.connector"]["pins"]
    for pin in ["A4", "A9", "B4", "B9"]:
        assert usb[pin]["net"] == "v5"
    for pin in ["A6", "B6"]:
        assert usb[pin]["net"] == "usb.dp"
    for pin in ["A7", "B7"]:
        assert usb[pin]["net"] == "usb.dm"
    for i, pin in enumerate(["A5", "B5"]):
        resistor = p[f"usb_c.cc_pulldowns[{i}]"]
        assert resistor["pins"]["1"]["net"] == usb[pin]["net"]
        assert resistor["pins"]["2"]["net"] == "gnd"
        assert resistor["value"]["value"] == "5.1kOhm"
    sd = p["micro_sd.socket"]["pins"]
    for pin, net in {
        "2": "sd.cs#",
        "3": "sd.mosi",
        "4": "v3v3",
        "5": "sd.sck",
        "6": "gnd",
        "7": "sd.miso",
        "9": "gnd",
        "10": "sd_detect#",
    }.items():
        assert sd[pin]["net"] == net
    for identity in ["micro_sd.decouple", "micro_sd.bulk", "oled_decouple", "oled_bulk"]:
        assert [p[identity]["pins"][pin]["net"] for pin in ("1", "2")] == ["v3v3", "gnd"]


def test_reset_button_and_surface_mount_parts(board):
    _, c = board
    p = parts(c)
    reset = p["reset_button"]
    assert [reset["pins"][pin]["net"] for pin in ("1", "2")] == ["debug.reset#", "gnd"]
    assert p["mcu.reset_cap"]["pins"]["1"]["net"] == reset["pins"]["1"]["net"]
    records = json.loads((PROJECT / "footprints.json").read_text())["footprints"]
    for part in c["parts"]:
        if part["identity"] == "usb_c.connector":
            continue  # USB shield anchors are through-hole.
        footprint = records[part["metadata"]["footprint"]["value"]]
        assert all(pad["technology"] == "smd" for pad in footprint["pad_records"])
    for i in range(2):
        assert p[f"uart_headers[{i}]"]["pins"]["MP"]["net"] is None
    switch = records[reset["metadata"]["footprint"]["value"]]
    assert sorted(pad["number"] for pad in switch["pad_records"]) == ["1", "1", "2", "2"]


def test_reference_exports_every_part_and_repeats_without_changes(board, tmp_path):
    project, c = board
    project = replace(
        project,
        config=replace(
            project.config, path=tmp_path / "noscheme.toml", output_dir=tmp_path / "out"
        ),
    )
    export_project(project, c, PROJECT / "footprints.json")
    path = tmp_path / "out" / "board.net"
    before = path.read_bytes()
    netlist = load(path)
    components = children(
        next(x for x in netlist if isinstance(x, list) and str(x[0]) == "components"), "comp"
    )
    assert len(components) == 58
    assert all(scalar(comp, "footprint") for comp in components)
    export_project(project, c, PROJECT / "footprints.json")
    assert path.read_bytes() == before
    report = json.loads((tmp_path / "out" / "export.json").read_text())
    assert report["omitted_without_footprints"] == []
    assert report["changes"] == {"added": [], "removed": [], "changed": []}
