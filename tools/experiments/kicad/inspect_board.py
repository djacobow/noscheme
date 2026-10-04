"""Inspect saved boards and compare actual results with fixture manifests.

This reads files only. It never simulates the editor's import or claims that an
unperformed import succeeded. CLI output is JSON; nonempty errors exit with 1.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

import sexpdata

from .sexpr import child, children, load, scalar, tag


def properties(footprint):
    result = {str(p[1]): str(p[2]) for p in children(footprint, "property")}
    for text in children(footprint, "fp_text"):
        if str(text[1]) == "reference":
            result.setdefault("Reference", str(text[2]))
        elif str(text[1]) == "value":
            result.setdefault("Value", str(text[2]))
    return result


def net_name(form, codes):
    node = child(form, "net")
    if node is None:
        return ""
    if len(node) > 2:
        return str(node[2])
    return codes.get(str(node[1]), str(node[1]) if isinstance(node[1], str) else "")


def normalized_shape(value, codes):
    """Normalize track/via net codes to names without depending on file order."""
    if tag(value) == "net":
        return ["net", codes.get(str(value[1]), str(value[1]))]
    if isinstance(value, list):
        return [normalized_shape(v, codes) for v in value if tag(v) not in {"uuid", "tstamp"}]
    return str(value) if isinstance(value, sexpdata.Symbol) else value


def snapshot(path):
    board = load(path)
    if tag(board) != "kicad_pcb":
        raise ValueError(f"{path} is not a KiCad board")
    codes = {str(n[1]): str(n[2]) for n in children(board, "net") if len(n) > 2}
    footprints = []
    for f in children(board, "footprint") + children(board, "module"):
        props = properties(f)
        footprints.append(
            {
                "reference": props.get("Reference"),
                "value": props.get("Value"),
                "library_id": str(f[1]),
                "uuid": str(scalar(f, "uuid", scalar(f, "tstamp", ""))),
                "path": str(scalar(f, "path", "")),
                "identity": props.get("noscheme_identity"),
                "at": (child(f, "at") or [None])[1:],
                "layer": str(scalar(f, "layer", "")),
                "pads": sorted(
                    [
                        {"number": str(p[1]), "net": net_name(p, codes)}
                        for p in children(f, "pad")
                        if str(p[1])
                    ],
                    key=lambda p: (p["number"], p["net"]),
                ),
            }
        )
    copper = []
    for item in board[1:]:
        if tag(item) in {"segment", "arc", "via"}:
            copper.append({"net": net_name(item, codes), "geometry": normalized_shape(item, codes)})
    return {
        "footprints": sorted(footprints, key=lambda f: (f["reference"] or "", f["uuid"])),
        "copper": copper,
        "net_names": sorted(set(codes.values()) - {""}),
    }


def matches(footprint, part):
    # Test the exported UUID path, not just a refdes that might have changed.
    return footprint["path"].rstrip("/").split("/")[-1] == part["uuid"]


def validate(snapshot_data, manifest):
    errors, matched = [], {}
    expected = {p["identity"]: p for p in manifest["components"] if p["footprint"]}
    used = set()
    for identity, part in expected.items():
        indices = [i for i, f in enumerate(snapshot_data["footprints"]) if matches(f, part)]
        if len(indices) != 1:
            errors.append(f"{identity}: expected one UUID-path match, found {len(indices)}")
            continue
        i = indices[0]
        if i in used:
            errors.append(f"{identity}: board footprint matched multiple expected instances")
        used.add(i)
        f = snapshot_data["footprints"][i]
        matched[identity] = f
        for field, value in (
            ("reference", part["ref"]),
            ("value", part["value"]),
            ("library_id", part["footprint"]),
        ):
            if f[field] != value:
                errors.append(f"{identity}: {field} {f[field]!r} != {value!r}")
        if f["identity"] not in (None, identity):
            errors.append(f"{identity}: conflicting noscheme_identity property {f['identity']!r}")
        for pin, net in part["pins"].items():
            pads = [p for p in f["pads"] if p["number"] == pin]
            if not pads:
                errors.append(f"{identity}: missing pad {pin}")
            elif any(p["net"] != net for p in pads):
                errors.append(f"{identity}: pad {pin} not assigned to {net!r}")
        for p in f["pads"]:
            if p["number"] not in part["pins"] and p["net"]:
                errors.append(f"{identity}: unexpected connected pad {p['number']}")
    for i, f in enumerate(snapshot_data["footprints"]):
        if i not in used:
            errors.append(f"unexpected footprint {f['reference']} at path {f['path']}")
    return errors, matched


def circuit_nets(manifest):
    result = {}
    for p in manifest["components"]:
        if p["footprint"]:
            for pin, net in p["pins"].items():
                result.setdefault(net, set()).add((p["identity"], pin))
    return result


def compare(before, after, baseline, expected, allow_baseline_ref_change=False):
    base_errors, old = validate(before, baseline)
    if allow_baseline_ref_change:
        # Only the manual reannotation experiment relaxes this baseline check.
        base_errors = [e for e in base_errors if ": reference " not in e]
    errors, new = validate(after, expected)
    errors = ["baseline: " + e for e in base_errors] + errors
    preserved = []
    for identity in sorted(old.keys() & new.keys()):
        for field in ("uuid", "path", "at", "layer"):
            if old[identity][field] != new[identity][field]:
                errors.append(
                    f"{identity}: changed {field}: {old[identity][field]!r} -> {new[identity][field]!r}"
                )
        preserved.append(identity)
    old_nets, new_nets = circuit_nets(baseline), circuit_nets(expected)
    stable_nets = {n for n in old_nets if old_nets[n] == new_nets.get(n)}

    def count(data):
        return Counter(
            json.dumps(c["geometry"], sort_keys=True)
            for c in data["copper"]
            if c["net"] in stable_nets
        )

    missing = count(before) - count(after)
    if missing:
        errors.append(f"lost or changed {sum(missing.values())} copper items on unchanged nets")
    # A layout-preservation result with no exercised route is not evidence.
    eligible = sum(count(before).values())
    if not eligible:
        errors.append(
            "baseline has no routed copper on unchanged nets; routing preservation not exercised"
        )
    return {
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "matched_surviving_identities": preserved,
        "unchanged_nets": sorted(stable_nets),
        "baseline_copper_items_checked": eligible,
        "before_footprints": len(before["footprints"]),
        "after_footprints": len(after["footprints"]),
        "identity_properties_present": sum(bool(f["identity"]) for f in after["footprints"]),
        "note": "Checks supplied saved files; retain the actual editor import report as provenance.",
    }


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("board", type=Path)
    cli.add_argument("--expected", type=Path)
    cli.add_argument("--before", type=Path)
    cli.add_argument("--baseline", type=Path)
    cli.add_argument("--allow-baseline-ref-change", action="store_true")
    cli.add_argument("--output", type=Path)
    args = cli.parse_args()
    after = snapshot(args.board)
    if args.before:
        if not (args.expected and args.baseline):
            cli.error("--before requires --expected and --baseline")
        result = compare(
            snapshot(args.before),
            after,
            json.loads(args.baseline.read_text()),
            json.loads(args.expected.read_text()),
            args.allow_baseline_ref_change,
        )
    elif args.expected:
        errors, matched = validate(after, json.loads(args.expected.read_text()))
        result = {
            "status": "pass" if not errors else "fail",
            "errors": errors,
            "matched": sorted(matched),
            "snapshot": after,
        }
    else:
        result = after
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        print(text, end="")
    if result.get("errors"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
