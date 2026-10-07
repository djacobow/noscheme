"""Build a hierarchical connectivity benchmark from KiCad's exported XML netlist.

The XML export resolves wires, buses, global labels, and multi-unit symbols.
This importer preserves physical topology; it does not infer operating modes.
"""

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from noscheme.formatter import format_source


def digest(value):
    return sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


def identifier(value):
    return re.sub(r"[^A-Za-z0-9_]", "_", value)


def extract(netlist, source, output):
    root = ET.parse(netlist).getroot()
    libraries = {(p.get("lib"), p.get("part")): p for p in root.findall("./libparts/libpart")}
    sheets = {s.get("name"): s for s in root.findall("./design/sheet")}
    components = {}
    for c in root.findall("./components/comp"):
        lib = c.find("libsource")
        pins = {
            p.get("num"): dict(p.attrib)
            for p in libraries[(lib.get("lib"), lib.get("part"))].findall("./pins/pin")
        }
        components[c.get("ref")] = {
            "value": c.findtext("value"),
            "footprint": c.findtext("footprint"),
            "sheet": c.find("sheetpath").get("names"),
            "uuid": c.findtext("tstamps"),
            "pins": pins,
            "fields": {f.get("name"): f.text for f in c.findall("./fields/field")},
        }
    nets, no_connect, pin_net = {}, set(), {}
    for net in root.findall("./nets/net"):
        nodes = [(n.get("ref"), n.get("pin")) for n in net.findall("node")]
        if any("no_connect" in (n.get("pintype") or "") for n in net.findall("node")):
            if len(nodes) != 1:
                raise ValueError(f"Non-isolated no-connect net: {net.get('name')}")
            no_connect.update(nodes)
            continue
        nets[net.get("name")] = nodes
        for endpoint in nodes:
            if endpoint in pin_net:
                raise ValueError(f"Duplicate net membership: {endpoint}")
            pin_net[endpoint] = net.get("name")
    all_pins = {(ref, pin) for ref, c in components.items() for pin in c["pins"]}
    if set(pin_net) | no_connect != all_pins:
        raise ValueError(f"Missing/extra pins: {all_pins ^ (set(pin_net) | no_connect)}")

    # Reference data is derived directly from XML, before hierarchy reconstruction.
    sources = sorted({s.findtext("./title_block/source") for s in sheets.values()})
    reference = {
        "source": "KiCad 10.0.6 bundled VME-WREN demo",
        "files": {name: sha256((source / name).read_bytes()).hexdigest() for name in sources},
        "parts": components,
        "nets": nets,
        "no_connect": sorted(no_connect),
        "sheet_instances": len(sheets),
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "reference.json").write_text(json.dumps(reference, indent=2, sort_keys=True) + "\n")

    # A sheet boundary is the set of nets with physical pins on both sides.
    # Multi-unit symbols are assigned to the owning sheet selected by KiCad.
    tree = {}
    for name, sheet in sheets.items():
        refs = {ref for ref, c in components.items() if c["sheet"].startswith(name)}
        if not refs:
            continue
        stamp = sheet.get("tstamps")
        local_nets = {}
        for net, endpoints in nets.items():
            inside = [(ref, pin) for ref, pin in endpoints if ref in refs]
            if not inside:
                continue
            relative = sorted(
                (
                    sheets[components[ref]["sheet"]].get("tstamps")[len(stamp) :],
                    components[ref]["uuid"],
                    pin,
                )
                for ref, pin in inside
            )
            local_nets[net] = {
                "key": "n_" + digest(relative),
                "port": len(inside) != len(endpoints),
            }
        if len({n["key"] for n in local_nets.values()}) != len(local_nets):
            raise ValueError(f"Net identifier collision in {name}")
        tree[name] = {"refs": refs, "nets": local_nets, "sheet": sheet, "children": []}
    for name in sorted(tree):
        if name == "/":
            continue
        parent = name.rstrip("/").rsplit("/", 1)[0] + "/"
        tree[parent]["children"].append(name)

    roles = {
        "input": "digital_input",
        "output": "digital_output",
        "bidirectional": "digital_bidirectional",
        "tri_state": "digital_output",
        "passive": "passive",
        "power_in": "power_input",
        "power_out": "power_output",
        "open_collector": "open_drain",
    }
    component_types, ref_types = {}, {}
    for ref, c in sorted(components.items()):
        signature = [
            c["value"],
            c["footprint"],
            c["pins"],
            sorted(
                pin
                for pin in c["pins"]
                if (ref, pin) in pin_net and len(nets[pin_net[ref, pin]]) == 1
            ),
        ]
        key = "Part_" + digest(signature)
        component_types.setdefault(key, (c, signature[-1]))
        ref_types[ref] = key
    part_lines = [
        "// Generated from KiCad's XML netlist; see ../README.md.",
        "from noscheme import role, unused",
        "",
    ]
    for key, (c, singletons) in sorted(component_types.items()):
        part_lines += [f"component {key}:"]
        if c["pins"]:
            part_lines += ["    pins:"]
        for num, pin in sorted(c["pins"].items()):
            values = [f"role.{roles[pin['type']]}"] + (
                ["unused.always"] if num in singletons else []
            )
            shown = values[0] if len(values) == 1 else "[" + ", ".join(values) + "]"
            part_lines += [f"        {json.dumps(num)}: {shown}"]
            if pin["type"] == "tri_state":
                part_lines += ["            tristate_capable: true"]
        part_lines += [
            "    metadata:",
            f"        value: {json.dumps(c['value'])}",
            f"        footprint: {json.dumps(c['footprint'])}",
            "",
        ]
    (output / "parts.nosch").write_text(format_source("\n".join(part_lines)))

    # Structural signatures allow repeated source sheets to share definitions.
    definitions, mapping = {}, {}
    for name in sorted(tree, key=lambda n: (-n.count("/"), n)):
        node = tree[name]
        items = sorted(
            (components[ref]["uuid"], ref)
            for ref in node["refs"]
            if components[ref]["sheet"] == name
        )
        local_names = {ref: "part_" + identifier(ref) for _, ref in items}
        pin_bindings = {
            ref: {
                pin: node["nets"][pin_net[ref, pin]]["key"]
                if (ref, pin) in pin_net
                else "no_connect"
                for pin in c["pins"]
            }
            for ref in local_names
            for c in [components[ref]]
        }
        child_items = []
        for child in node["children"]:
            ch = tree[child]
            links = {
                n["key"]: node["nets"][net]["key"] for net, n in ch["nets"].items() if n["port"]
            }
            child_items.append(
                (ch["sheet"].get("tstamps").rstrip("/").rsplit("/", 1)[-1], child, links)
            )
        signature = [
            sorted((n["key"], n["port"]) for n in node["nets"].values()),
            [(uid, ref_types[ref], pin_bindings[ref]) for uid, ref in items],
            [(uid, tree[ch]["type"], links) for uid, ch, links in sorted(child_items)],
        ]
        type_name = (
            "Sheet_"
            + identifier(Path(node["sheet"].findtext("./title_block/source")).stem)
            + "_"
            + digest(signature)
        )
        node["type"] = type_name
        if type_name in definitions:
            canonical = definitions[type_name]
            node["part_names"] = {ref: canonical["uuid_names"][uid] for uid, ref in items}
            node["child_names"] = {
                ch: canonical["child_uuid_names"][uid] for uid, ch, _ in child_items
            }
            continue
        node["part_names"] = local_names
        child_names = {}
        groups = defaultdict(list)
        for uid, ch, links in sorted(child_items):
            groups[tree[ch]["type"]].append((uid, ch, links))
        lines = (
            ["    ports:"] if name != "/" and any(n["port"] for n in node["nets"].values()) else []
        )
        for n in sorted(node["nets"].values(), key=lambda n: n["key"]):
            if n["port"]:
                lines += [f"        {n['key']}: nettype.unchecked"]
        for n in sorted(node["nets"].values(), key=lambda n: n["key"]):
            # Nets wholly contained in a child are declared in that child.
            used_here = any(n["key"] in pins.values() for pins in pin_bindings.values()) or any(
                n["key"] in links.values() for _, _, links in child_items
            )
            if not n["port"] and used_here:
                lines += [f"    net {n['key']}: nettype.unchecked"]
        for _, ref in items:
            lines += [f"    inst {local_names[ref]}: {ref_types[ref]}"]
            if pin_bindings[ref]:
                lines += ["        connections:"]
            lines += [
                f"            {json.dumps(pin)}: {net}"
                for pin, net in sorted(pin_bindings[ref].items())
            ]
        for group_index, (kind, children) in enumerate(sorted(groups.items())):
            label = "sub_" + str(group_index)
            array = len(children) > 1
            lines += [f"    inst {label}{'[' + str(len(children)) + ']' if array else ''}: {kind}"]
            if any(links for _, _, links in children):
                lines += ["        connections:"]
            for i, (_, ch, links) in enumerate(children):
                child_names[ch] = label + (f"[{i}]" if array else "")
                for port, net in sorted(links.items()):
                    lines += [f"            {'[' + str(i) + '].' if array else ''}{port}: {net}"]
        node["child_names"] = child_names
        definitions[type_name] = {
            "lines": lines,
            "uuid_names": {uid: local_names[ref] for uid, ref in items},
            "child_uuid_names": {uid: child_names[ch] for uid, ch, _ in child_items},
        }

    def walk(sheet, prefix):
        node = tree[sheet]
        for ref, local in node["part_names"].items():
            mapping[prefix + local] = ref
        for child, local in node["child_names"].items():
            walk(child, prefix + local + ".")

    walk("/", "")
    imports = "from parts import " + ", ".join(sorted(component_types))
    lines = [
        "// Generated hierarchical benchmark. See ../README.md.",
        "from noscheme import nettype",
        imports,
        "",
    ]
    # Definitions are already stored child-first.
    for kind, definition in definitions.items():
        lines += (
            [("board WrenFull:" if kind == tree["/"]["type"] else f"block {kind}:")]
            + definition["lines"]
            + [""]
        )
    (output / "main.nosch").write_text(format_source("\n".join(lines)))
    (output / "instance_map.json").write_text(json.dumps(mapping, indent=2, sort_keys=True) + "\n")
    (output / "noscheme.toml").write_text(
        'language_version = "0.1"\nboard = "main.WrenFull"\nsource_paths = ["."]\nlibrary_paths = []\noutput_dir = "build"\n'
    )
    print(
        f"{len(components)} parts; {len(nets)} connected nets; {len(no_connect)} no-connect pins; {len(definitions)} sheet definitions"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("netlist", type=Path)
    parser.add_argument("--source", type=Path, default=Path("/usr/share/kicad/demos/vme-wren"))
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "full")
    args = parser.parse_args()
    extract(args.netlist, args.source, args.output)


if __name__ == "__main__":
    main()
