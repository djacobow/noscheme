"""Compare the array-based example to independently extracted Eagle connectivity."""

from collections import defaultdict
from dataclasses import replace
import json
import shutil
from pathlib import Path

import pytest

from noscheme.circuit import elaborate_project
from noscheme.erc import apply_erc
from noscheme.kicad.export import export_project
from noscheme.kicad.sexpr import children, load, scalar
from noscheme.resolve import resolve_project

PROJECT = Path(__file__).resolve().parents[1] / "examples/rulos_6matrix"


def copy_example(tmp_path):
    destination = tmp_path / "rulos"
    shutil.copytree(PROJECT, destination, ignore=shutil.ignore_patterns("build", "__pycache__"))
    return destination


def test_source_connectivity_values_and_export(tmp_path):
    source = json.loads((PROJECT / "upstream/connectivity.json").read_text())
    # Deliberate correction: the XTR package has an exposed pad absent in Eagle.
    # Keep the on-disk evidence intact and require each new pad on its GND net.
    drivers = [ref for ref, part in source["parts"].items() if part["value"] == "STP16CPC26XTR"]
    assert len(drivers) == 4
    for ref in drivers:
        source["parts"][ref]["pads"].append("25")
        ground = next(nodes for nodes in source["nets"].values() if f"{ref}.1" in nodes)
        ground.append(f"{ref}.25")
    # Approved regulator substitutions preserve rail functions, not pad numbers.
    remaps = {"IC3.1": "IC3.2", "IC3.2": "IC3.1", "IC4.1": "IC4.3", "IC4.3": "IC4.1"}
    remaps.update({"U$4.P$1": "U$4.1", "U$4.P$2": "U$4.2"})
    source["parts"]["U$4"]["pads"] = ["1", "2"]
    # New jack has three contacts and no separate shield pads.
    remaps.update({"U$5.P$1": "U$5.1", "U$5.P$2": "U$5.2", "U$5.P$3": "U$5.3"})
    source["parts"]["U$5"]["pads"] = ["1", "2", "3"]
    source["parts"]["U$5"]["value"] = "PJ-036AH-SMT-TR"
    source["nets"] = {
        name: [remaps.get(node, node) for node in nodes] for name, nodes in source["nets"].items()
    }
    for ref, value in {
        "IC3": "TLV1117LV33DCYR",
        "IC4": "LM2937IMP-10/NOPB",
        "C2": "4.7uF",
        "C4": "22uF",
        "C9": "47uF",
    }.items():
        source["parts"][ref]["value"] = value
    # Original R_EXT values were blank; the selected LED current now specifies 1k.
    for part in source["parts"].values():
        if part["value"] == "" and part["package"] == "R0603":
            part["value"] = "1kOhm"
    # The source's isolated SWO header pad is now explicitly unconnected.
    swo = next(name for name, nodes in source["nets"].items() if nodes == ["PROG.6"])
    del source["nets"][swo]
    mapping = json.loads((PROJECT / "upstream/instance_map.json").read_text())
    overrides = json.loads((PROJECT / "footprint_overrides.json").read_text())
    project, definitions = resolve_project(PROJECT)
    circuit, warnings = elaborate_project(project, definitions)
    assert not warnings
    # All six configurations must pass with the documented demo budgets.
    assert not apply_erc(circuit)
    assert len(circuit["parts"]) == 93
    assert len(circuit["nets"]) == 106
    assert set(mapping.values()) == set(source["parts"])
    assert len(set(mapping.values())) == len(mapping)
    assert {p["identity"] for p in circuit["parts"]} == set(mapping)

    actual_nets = defaultdict(set)
    disconnected = set()
    for part in circuit["parts"]:
        original = mapping[part["identity"]]
        expected = source["parts"][original]
        assert set(part["pins"]) == set(expected["pads"])
        # Quantity parameters normalize display strings without changing values.
        displayed = {"0.1uF": "100nF", "2k": "2kOhm", "10k": "10kOhm", "22.6k": "22.6kOhm"}.get(
            expected["value"], expected["value"]
        )
        assert part["value"]["value"] == displayed
        assert part["metadata"]["footprint"]["value"] == overrides[original]
        for pad, pin in part["pins"].items():
            endpoint = f"{original}.{pad}"
            if pin["net"] is None:
                disconnected.add(endpoint)
            else:
                actual_nets[pin["net"]].add(endpoint)

    expected_nets = {frozenset(endpoints) for endpoints in source["nets"].values()}
    assert {frozenset(endpoints) for endpoints in actual_nets.values()} == expected_nets
    all_pads = {f"{ref}.{pad}" for ref, p in source["parts"].items() for pad in p["pads"]}
    assert disconnected == all_pads - set().union(*expected_nets)

    project = replace(
        project,
        config=replace(
            project.config, path=tmp_path / "noscheme.toml", output_dir=tmp_path / "build"
        ),
    )
    result = export_project(project, circuit, PROJECT / "kicad_lib/footprints.json")
    assert result["omitted_without_footprints"] == []
    exported = load(tmp_path / "build/board.net")
    components = children(children(exported, "components")[0], "comp")
    assert len(components) == 93
    # Resolve export references back to original Eagle instances using saved state.
    state = json.loads((tmp_path / "noscheme.state.json").read_text())
    refs = {
        p["reference"]: mapping[identity]
        for identity, p in state["entries"].items()
        if identity in mapping
    }
    exported_nets = set()
    exported_disconnected = set()
    for net in children(children(exported, "nets")[0], "net"):
        nodes = children(net, "node")
        endpoints = frozenset(
            f"{refs[scalar(node, 'ref')]}.{scalar(node, 'pin')}" for node in children(net, "node")
        )
        if any(scalar(node, "pintype") == "no_connect" for node in nodes):
            assert len(nodes) == 1
            exported_disconnected.update(endpoints)
        else:
            exported_nets.add(endpoints)
    assert exported_nets == expected_nets
    assert exported_disconnected == disconnected


def test_standard_footprints_cover_design():
    snapshot = json.loads((PROJECT / "kicad_lib/footprints.json").read_text())
    project, definitions = resolve_project(PROJECT)
    circuit, warnings = elaborate_project(project, definitions)
    assert not warnings
    selected = {p["metadata"]["footprint"]["value"] for p in circuit["parts"]}
    assert set(snapshot["footprints"]) == selected
    assert len(selected) == 18
    assert all(not name.startswith("Rulos_RevB:") for name in selected)
    for part in circuit["parts"]:
        record = snapshot["footprints"][part["metadata"]["footprint"]["value"]]
        assert record["exists"]
        assert set(part["pins"]) <= set(record["pads"])


def test_passive_policy_and_stm32_swd_footprint():
    project, definitions = resolve_project(PROJECT)
    circuit, warnings = elaborate_project(project, definitions)
    assert not warnings
    mapping = json.loads((PROJECT / "upstream/instance_map.json").read_text())
    source = json.loads((PROJECT / "upstream/connectivity.json").read_text())
    by_reference = {mapping[p["identity"]]: p for p in circuit["parts"]}
    for ref, part in by_reference.items():
        original = source["parts"][ref]
        footprint = part["metadata"]["footprint"]["value"]
        if ref in {"C2", "C4", "C8", "C9"}:
            expected = {
                "C2": "Capacitor_SMD:C_0805_2012Metric",
                "C4": "Capacitor_SMD:CP_Elec_5x5.8",
                "C8": "Capacitor_SMD:CP_Elec_6.3x5.8",
                "C9": "Capacitor_SMD:CP_Elec_6.3x5.8",
            }
            assert footprint == expected[ref]
        elif original["package"] == "R0603":
            assert footprint == "Resistor_SMD:R_0603_1608Metric"
        elif original["package"].startswith("C") and ref.split(":")[-1].startswith("C"):
            expected = {
                "0.1uF": "Capacitor_SMD:C_0603_1608Metric",
                "1uF": "Capacitor_SMD:C_0603_1608Metric",
                "10uF": "Capacitor_SMD:C_0805_2012Metric",
            }
            assert footprint == expected[original["value"]]
    stm, definitions = resolve_project(PROJECT.parent / "stm32h523")
    other, _ = elaborate_project(stm, definitions)
    header = next(p for p in other["parts"] if p["identity"] == "swd.header")
    assert (
        by_reference["PROG"]["metadata"]["footprint"]["value"]
        == header["metadata"]["footprint"]["value"]
    )
    assert set(by_reference["PROG"]["pins"]) == set(header["pins"])


def configuration(supply_5v, port):
    """The name of a configuration generated from the board's two supply choices."""
    return f"supply_12v=port{port},supply_5v={supply_5v}"


def test_power_configuration_detects_two_external_sources(tmp_path):
    destination = copy_example(tmp_path)
    main = destination / "main.nosch"
    text = main.read_text()
    start = text.index("  inst power_header:")
    end = text.index("  inst logic_regulator:", start)
    header = text[start:end].replace("jack=role.power_input", "jack=role.power_output")
    main.write_text(text[:start] + header + text[end:])
    project, definitions = resolve_project(destination)
    circuit, warnings = elaborate_project(project, definitions)
    assert not warnings
    diagnostics = apply_erc(circuit)
    conflicts = [d for d in diagnostics if d.code == "erc.multiple_drivers"]
    assert len(conflicts) == 3
    assert {d.configuration for d in conflicts} == {configuration("jack", i) for i in range(3)}
    assert all(d.application == "net:v5:multiple_drivers" for d in conflicts)


@pytest.mark.parametrize(
    ("old", "new", "code"),
    [
        (
            '"2": vcc\n    params:\n      value: 2k',
            '"2": gnd\n    params:\n      value: 2k',
            "erc.bias_reference",
        ),
        ("value: 2k", "value: 1k", "erc.std.parallel"),
        ("voltage: 5 ± 5%", "voltage: 4.75..7", "erc.voltage_limits"),
    ],
)
def test_electrical_limits_detect_faults(tmp_path, old, new, code):
    destination = copy_example(tmp_path)
    main = destination / "main.nosch"
    source = main.read_text()
    assert source.count(old) == 1
    main.write_text(source.replace(old, new))
    project, definitions = resolve_project(destination)
    circuit, warnings = elaborate_project(project, definitions)
    assert not warnings
    faults = [d for d in apply_erc(circuit) if d.code == code and d.severity == "error"]
    assert faults
    assert {d.configuration for d in faults} == {
        configuration(source, port) for source in ("jack", "header") for port in range(3)
    }


@pytest.mark.parametrize(
    ("filename", "old", "new", "code", "application"),
    [
        (
            "components.nosch",
            "max_source_current: 3",
            "max_source_current: 1",
            "erc.matrix.shared_sink_budget",
            "net:led_power:display_load",
        ),
        (
            "main.nosch",
            "64 * 20m * 1.10 + 1m",
            "64 * 50m * 1.10 + 1m",
            "erc.matrix.shared_sink_budget",
            "net:led_power:display_load",
        ),
        (
            "main.nosch",
            "net scl, sda: LocalI2C\n    wiring_capacitance: 50p\n",
            "net sda: LocalI2C\n    wiring_capacitance: 50p\n    voltage: 0..3.465\n"
            "  net scl: LocalI2C\n    wiring_capacitance: 500p\n",
            "erc.std.sum",
            "net:scl:capacitance",
        ),
        (
            "main.nosch",
            "net scl_hv, sda_hv: BufferedI2C\n    wiring_capacitance: 100p",
            "net scl_hv: BufferedI2C\n    wiring_capacitance: 100p\n"
            "  net sda_hv: BufferedI2C\n    wiring_capacitance: 5n",
            "erc.std.sum",
            "net:sda_hv:capacitance",
        ),
        (
            "components.nosch",
            "      // Demo allowance at 10V, including bus activity; see README.\n      max_load_current: 20m",
            "      max_load_current: 600m",
            "erc.current_overload",
            "net:v10:current_overload",
        ),
        (
            "components.nosch",
            "      // Demo allocation for logic supply, not LED sink current.\n      max_load_current: 20m",
            "      max_load_current: 300m",
            "erc.current_overload",
            "net:vcc:current_overload",
        ),
    ],
)
def test_quantitative_budget_overloads(tmp_path, filename, old, new, code, application):
    destination = copy_example(tmp_path)
    path = destination / filename
    source = path.read_text()
    assert source.count(old) == 1
    path.write_text(source.replace(old, new))
    project, definitions = resolve_project(destination)
    circuit, warnings = elaborate_project(project, definitions)
    assert not warnings
    diagnostics = apply_erc(circuit)
    faults = [d for d in diagnostics if d.code == code and d.severity == "error"]
    assert len(faults) == 6
    assert {d.application for d in faults} == {application}
    assert {d.configuration for d in faults} == {
        configuration(supply, port) for supply in ("jack", "header") for port in range(3)
    }


def test_buck_budget_requires_capacity(tmp_path):
    destination = copy_example(tmp_path)
    path = destination / "components.nosch"
    path.write_text(path.read_text().replace("      max_source_current: 3\n", ""))
    project, definitions = resolve_project(destination)
    circuit, warnings = elaborate_project(project, definitions)
    assert not warnings
    diagnostics = [d for d in apply_erc(circuit) if d.application == "net:led_power:display_load"]
    assert len(diagnostics) == 6
    assert all(d.severity == "warning" and "one source" in d.message for d in diagnostics)


@pytest.mark.parametrize(("capacity", "overloads"), [("190m", 0), ("180m", 6)])
def test_logic_budget_counts_group_once_and_includes_pullups(tmp_path, capacity, overloads):
    destination = copy_example(tmp_path)
    path = destination / "components.nosch"
    source = path.read_text()
    assert source.count("max_source_current: 1 - 1m") == 1
    path.write_text(source.replace("max_source_current: 1 - 1m", f"max_source_current: {capacity}"))
    project, definitions = resolve_project(destination)
    circuit, warnings = elaborate_project(project, definitions)
    assert not warnings
    diagnostics = apply_erc(circuit)
    assert not any(d.code == "erc.incomplete_budget" for d in diagnostics)
    faults = [d for d in diagnostics if d.application == "net:vcc:current_overload"]
    assert len(faults) == overloads


@pytest.mark.parametrize(
    ("filename", "old", "new", "severity", "nets"),
    [
        (
            "main.nosch",
            "          capacitance: 1n",
            "          capacitance: 2n",
            "error",
            ("sda_hv", "scl_hv"),
        ),
        (
            "main.nosch",
            "          capacitance: 1n\n",
            "",
            "warning",
            ("sda_hv", "scl_hv"),
        ),
        (
            "components.nosch",
            "      capacitance: 10p\n  metadata:",
            "  metadata:",
            "warning",
            ("sda", "scl"),
        ),
        ("main.nosch", "    wiring_capacitance: 50p\n", "", "warning", ("sda", "scl")),
    ],
)
def test_capacitance_budget_contributions(tmp_path, filename, old, new, severity, nets):
    destination = copy_example(tmp_path)
    path = destination / filename
    source = path.read_text()
    assert old in source
    path.write_text(source.replace(old, new))
    project, definitions = resolve_project(destination)
    circuit, warnings = elaborate_project(project, definitions)
    assert not warnings
    diagnostics = apply_erc(circuit)
    assert len(diagnostics) == 12
    assert all(d.code == "erc.std.sum" and d.severity == severity for d in diagnostics)
    assert {(d.configuration, d.application) for d in diagnostics} == {
        (configuration(supply, port), f"net:{net}:capacitance")
        for supply in ("jack", "header")
        for port in range(3)
        for net in nets
    }
