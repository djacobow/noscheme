"""Generate immutable-by-convention inputs for manual PCBNew import tests."""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import uuid

from .sexpr import children, dump, form, load

NAMESPACE = uuid.UUID("40e66cbd-7b2c-5d09-8d28-b1fb55e563de")
FOOTPRINTS = {
    "header": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
    "resistor": "Resistor_SMD:R_0603_1608Metric",
    "led": "LED_SMD:LED_0603_1608Metric",
}


def part(identity, ref, kind, value, pins):
    return dict(
        identity=identity,
        source_path=identity,
        ref=ref,
        kind=kind,
        value=value,
        footprint=FOOTPRINTS[kind],
        pins=pins,
        uuid=str(uuid.uuid5(NAMESPACE, identity)),
    )


def variants():
    base = [
        part("input", "J1", "header", "Input", {"1": "v5", "2": "gnd"}),
        part(
            "indicators[0].resistor",
            "R1",
            "resistor",
            "1k",
            {"1": "v5", "2": "/indicators[0]/anode"},
        ),
        part("indicators[0].led", "D1", "led", "Green", {"1": "gnd", "2": "/indicators[0]/anode"}),
        part(
            "indicators[1].resistor",
            "R2",
            "resistor",
            "1k",
            {"1": "v5", "2": "/indicators[1]/anode"},
        ),
        part("indicators[1].led", "D2", "led", "Green", {"1": "gnd", "2": "/indicators[1]/anode"}),
    ]
    result = {
        name: deepcopy(base)
        for name in (
            "baseline",
            "reordered",
            "added",
            "removed",
            "changed_connection",
            "renamed",
            "ref_changed",
            "unspecified_footprint",
            "unknown_footprint",
            "pad_mismatch",
            "names",
        )
    }
    result["reordered"].reverse()
    result["added"].append(part("bleeder", "R3", "resistor", "10k", {"1": "v5", "2": "gnd"}))
    result["removed"] = [p for p in result["removed"] if p["ref"] != "D2"]
    result["changed_connection"][3]["pins"]["1"] = "gnd"
    result["renamed"][1]["source_path"] = "status.resistor"
    result["ref_changed"][1]["ref"] = "R17"
    result["unspecified_footprint"][4]["footprint"] = None
    result["unknown_footprint"][4]["footprint"] = "LED_SMD:NOSCHEME_DOES_NOT_EXIST"
    result["pad_mismatch"][4]["pins"] = {"1": "gnd", "99": "/indicators[1]/anode"}
    names = {
        "v5": "#supply",
        "gnd": "RESET#",
        "/indicators[0]/anode": "/bank[0]/A#",
        "/indicators[1]/anode": "/bank[1]/A#",
    }
    for p in result["names"]:
        p["pins"] = {pin: names[net] for pin, net in p["pins"].items()}
    result["differential_pair"] = [
        part("pair_source", "J1", "header", "Pair source", {"1": "/USB_P", "2": "/USB_N"}),
        part("pair_sink", "J2", "header", "Pair sink", {"1": "/USB_P", "2": "/USB_N"}),
    ]
    return result


def netlist(parts):
    """Emit version E as produced by the installed KiCad 10 exporter.

    Empty-footprint components are omitted from this import artifact, but not
    the manifest. No timestamps or input iteration order leak into output.
    """
    components, nets = [], {}
    for p in sorted(parts, key=lambda p: p["identity"]):
        if p["footprint"] is None:
            continue
        components.append(
            form(
                "comp",
                form("ref", p["ref"]),
                form("value", p["value"]),
                form("footprint", p["footprint"]),
                form("libsource", form("lib", "NoSchemeExperiment"), form("part", p["kind"])),
                form("property", form("name", "noscheme_identity"), form("value", p["identity"])),
                form(
                    "property",
                    form("name", "noscheme_source_path"),
                    form("value", p["source_path"]),
                ),
                form("sheetpath", form("names", "/"), form("tstamps", "/")),
                form("tstamps", p["uuid"]),
            )
        )
        for pin, name in sorted(p["pins"].items()):
            nets.setdefault(name, []).append(form("node", form("ref", p["ref"]), form("pin", pin)))
    libparts = []
    for kind in sorted({p["kind"] for p in parts}):
        pins = sorted({pin for p in parts if p["kind"] == kind for pin in p["pins"]})
        libparts.append(
            form(
                "libpart",
                form("lib", "NoSchemeExperiment"),
                form("part", kind),
                form(
                    "pins",
                    *[
                        form("pin", form("num", pin), form("name", pin), form("type", "passive"))
                        for pin in pins
                    ],
                ),
            )
        )
    return dump(
        form(
            "export",
            form("version", "E"),
            form(
                "design",
                form("source", "noscheme-feasibility.nosch"),
                form("tool", "NoScheme feasibility experiment"),
            ),
            form("components", *components),
            form("libparts", *libparts),
            form(
                "nets",
                *[
                    form("net", form("code", i), form("name", name), *nodes)
                    for i, (name, nodes) in enumerate(sorted(nets.items()), 1)
                ],
            ),
        )
    )


def inspect_footprint(name, library_root):
    library, item = name.split(":", 1)
    path = library_root / (library + ".pretty") / (item + ".kicad_mod")
    if not path.exists():
        return {"name": name, "exists": False, "path": str(path)}
    data = load(path)
    pads = children(data, "pad")
    return {
        "name": name,
        "exists": True,
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "pads": sorted({str(p[1]) for p in pads if str(p[1])}),
        "unnumbered_pads": sum(not str(p[1]) for p in pads),
    }


def validate_parts(parts, records):
    issues = []
    for p in parts:
        name = p["footprint"]
        if name is None:
            issues.append(dict(ref=p["ref"], check="unspecified_footprint"))
            continue
        record = records[name]
        if not record["exists"]:
            issues.append(dict(ref=p["ref"], check="unknown_footprint", footprint=name))
            continue
        pins, pads = set(p["pins"]), set(record["pads"])
        for check, values in (("pin_without_pad", pins - pads), ("pad_without_pin", pads - pins)):
            if values:
                issues.append(dict(ref=p["ref"], check=check, identifiers=sorted(values)))
    return issues


def versions():
    try:
        kicad = subprocess.check_output(["kicad-cli", "version"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        kicad = "unavailable"
    return {
        "python": platform.python_version(),
        "kicad": kicad,
        "packages": {
            name: importlib.metadata.version(name) for name in ("lark", "pytest", "sexpdata")
        },
    }


def generate(output, library_root):
    output.mkdir(parents=True, exist_ok=True)
    designs = variants()
    names = {p["footprint"] for design in designs.values() for p in design if p["footprint"]}
    records = {name: inspect_footprint(name, library_root) for name in sorted(names)}
    write_json(output / "footprints.json", records)
    write_json(output / "versions.json", versions())
    write_json(
        output / "class-membership.json", {"v5": ["Power"], "/indicators[0]/anode": ["Signal"]}
    )
    # Local table avoids modifying the user's global footprint-library settings.
    libs = sorted({name.split(":")[0] for name in names})
    table = form(
        "fp_lib_table",
        form("version", 7),
        *[
            form(
                "lib",
                form("name", lib),
                form("type", "KiCad"),
                form("uri", str(library_root.resolve() / (lib + ".pretty"))),
                form("options", ""),
                form("descr", "NoScheme experiment"),
            )
            for lib in libs
        ],
    )
    (output / "fp-lib-table").write_text(dump(table))
    for name, parts in designs.items():
        (output / (name + ".net")).write_text(netlist(parts))
        write_json(
            output / (name + ".json"),
            {
                "variant": name,
                "components": sorted(parts, key=lambda p: p["identity"]),
                "exported_identities": sorted(p["identity"] for p in parts if p["footprint"]),
                "footprint_observations": validate_parts(parts, records),
                "editor_validation": "pending",
            },
        )
    return designs


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output", type=Path, default=Path("tools/experiments/output/kicad"))
    cli.add_argument("--library-root", type=Path, default=Path("/usr/share/kicad/footprints"))
    args = cli.parse_args()
    designs = generate(args.output, args.library_root)
    print(f"Generated {len(designs)} fixtures in {args.output}. Editor validation is pending.")


if __name__ == "__main__":
    main()
