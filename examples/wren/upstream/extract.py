"""Regenerate reference data and component interfaces; not the handwritten wiring."""

import json
import hashlib
import re
import xml.etree.ElementTree as E
from pathlib import Path
import argparse

from noscheme.formatter import format_source

parser = argparse.ArgumentParser(
    description="Extract the WREN output subsystem reference from a KiCad XML netlist."
)
parser.add_argument("netlist", type=Path)
parser.add_argument("--source", type=Path, default=Path("/usr/share/kicad/demos/vme-wren"))
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
src = args.source
r = E.parse(args.netlist).getroot()
cs = [
    c
    for c in r.findall("./components/comp")
    if c.find("sheetpath").get("names").startswith("/io_drivers_pp/")
]
byref = {c.get("ref"): c for c in cs}
libparts = {(item.get("lib"), item.get("part")): item for item in r.findall("./libparts/libpart")}
# The UUID of a symbol is shared across the repeated instances of its sheet.
canonical = {
    "C380": "bypass",
    "C381": "bulk",
    "D77": "protection[0]",
    "D78": "protection[1]",
    "IC62": "driver",
    "R93": "series[0]",
    "R164": "series[1]",
    "R281": "series[2]",
    "R295": "series[3]",
    "R309": "pulldown[0]",
    "R323": "pulldown[1]",
    "C376": "bypass3v3",
    "C377": "bypass1v8",
    "IC61": "translator",
    "R316": "enable_pullup",
}
ids = {byref[ref].findtext("tstamps"): name for ref, name in canonical.items()}
mapping = {}
for c in cs:
    path = c.find("sheetpath").get("names")
    b = re.search(r"/bank(\d+)/", path)
    o = re.search(r"/out(\d)\d/", path)
    local = ids[c.findtext("tstamps")]
    ns = (
        ("banks[" + b[1] + "]." if b else "")
        + ("pairs[" + str(int(o[1]) // 2) + "]." if o else "")
        + local
    )
    mapping[ns] = c.get("ref")
assert len(mapping) == 189
components = {}
for ref, c in byref.items():
    lib = c.find("libsource")
    lp = libparts[(lib.get("lib"), lib.get("part"))]
    components[ref] = {
        "value": c.findtext("value"),
        "footprint": c.findtext("footprint"),
        "sheet": c.find("sheetpath").get("names"),
        "pins": [p.get("num") for p in lp.findall("./pins/pin")],
        "fields": {f.get("name"): f.text for f in c.findall("./fields/field")},
    }
nets = {}
boundary = {}
for n in r.findall("./nets/net"):
    inside = [x.get("ref") + "." + x.get("pin") for x in n.findall("node") if x.get("ref") in byref]
    if inside:
        nets[n.get("name")] = sorted(inside)
        outside = [
            x.get("ref") + "." + x.get("pin")
            for x in n.findall("node")
            if x.get("ref") not in byref
        ]
        if outside:
            boundary[n.get("name")] = sorted(outside)
files = [
    "vme-wren.kicad_sch",
    "pp_driver_32x.kicad_sch",
    "pp_driver_8x.kicad_sch",
    "pp_driver_2x.kicad_sch",
]
data = {
    "source": "KiCad 10.0.6 bundled VME-WREN demo",
    "files": {f: hashlib.sha256((src / f).read_bytes()).hexdigest() for f in files},
    "parts": components,
    "nets": nets,
    "external_endpoints": boundary,
}
(root / "upstream/connectivity.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
(root / "upstream/instance_map.json").write_text(
    json.dumps(mapping, indent=2, sort_keys=True) + "\n"
)
roles = {
    "input": "digital_input",
    "bidirectional": "digital_bidirectional",
    "tri_state": "digital_output",
    "passive": "passive",
    "power_in": "power_input",
}
text = [
    "// Pin interfaces transcribed from the KiCad netlist; see upstream provenance.",
    "from noscheme import role",
    "",
]
for ref, name in [
    ("C380", "Bypass"),
    ("C381", "Bulk"),
    ("D77", "Protection"),
    ("IC62", "Driver"),
    ("R93", "Resistor"),
    ("IC61", "Translator"),
]:
    c = byref[ref]
    lib = c.find("libsource")
    lp = libparts[(lib.get("lib"), lib.get("part"))]
    text += ["component " + name + ":"]
    if name == "Resistor":
        text += ["    params:", "        value: string"]
    text += ["    pins:"]
    for p in lp.findall("./pins/pin"):
        role = roles[p.get("type")]
        role = "ground" if p.get("name") == "GND" else role
        text += ["        " + p.get("num") + ": role." + role]
        if p.get("type") == "tri_state":
            text += ["            tristate_capable: true"]
    text += [
        "    metadata:",
        "        footprint: " + json.dumps(c.findtext("footprint")),
        "        ref_prefix: " + json.dumps(re.sub(r"\d", "", ref)),
    ]
    if name != "Resistor":
        text += ["        value: " + json.dumps(c.findtext("value"))]
    text += [""]
(root / "components.nosch").write_text(format_source("\n".join(text)))
print("Extracted", len(components), "parts", len(nets), "nets", len(boundary), "boundary nets")
