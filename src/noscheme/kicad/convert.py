"""Convert explicit KiCad symbol inputs and snapshot explicit footprint libraries."""

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import tempfile
import os
import shutil
import sexpdata

from .. import ast as a
from ..diagnostics import Diagnostic, DiagnosticError
from ..formatter import format_source
from ..parser import parse, RESERVED_NAMES
from .sexpr import load, tag, children, scalar

ROLE_MAP = {
    "input": "digital_input",
    "output": "digital_output",
    "tri_state": "digital_output",
    "bidirectional": "digital_bidirectional",
    "passive": "passive",
    "power_in": "power_input",
    "power_out": "power_output",
    "open_collector": "drive.low",
    "open_emitter": "drive.high",
}

PASSIVE_QUANTITIES = {"R": "resistance", "C": "capacitance", "L": "inductance"}


def interchangeable_pins(original, props, pins):
    """Pins whose assignment cannot matter, judged conservatively from library data.

    Two passive pins on a resistor, capacitor, inductor, or ferrite bead qualify
    unless the symbol is described as polarized (KiCad's polarized capacitors have
    the same pin data as plain ones); so do a bidirectional diode's A1/A2 pins.
    """
    entries = [pin for found in pins.values() for pin in found]
    if len(pins) != 2 or any(str(pin[1]) != "passive" for pin in entries):
        return ()
    text = " ".join([original, props.get("Description", ""), props.get("ki_keywords", "")])
    names = {str(scalar(pin, "name", "")) for pin in entries}
    reference = props.get("Reference")
    polarized = re.search(r"(?<!un)(?<!non)(?<!non-)(?<!non )polari[sz]ed", text, re.IGNORECASE)
    if (
        reference in {"R", "C", "L", "FB"}
        and not polarized
        or (reference == "D" and names == {"A1", "A2"})
    ):
        return tuple(sorted(pins))
    return ()


def failure(path, code, message):
    raise DiagnosticError([Diagnostic(code, "error", message, a.Span(str(path), 0, 0, 1, 1, 1, 1))])


def identifier(name):
    result = re.sub(r"[^A-Za-z0-9_#]", "_", name)
    if not result or result[0].isdigit() or result in RESERVED_NAMES:
        result = "C_" + result
    return result


def pin_spelling(name):
    """A pin alias as written in a pin key: bare when it is a plain name."""
    plain = re.fullmatch(r"[A-Za-z_#][A-Za-z0-9_#]*", name) and name not in RESERVED_NAMES
    return name if plain else json.dumps(name)


def footprint_record(name, roots):
    if ":" not in name:
        raise ValueError(f"footprint must have Library:Name syntax: {name!r}")
    library, item = name.split(":", 1)
    if (
        not library
        or not item
        or any(c in library + item for c in "/\\\0")
        or library in {".", ".."}
        or item in {".", ".."}
    ):
        raise ValueError(f"invalid footprint library identifier {name!r}")
    matches = []
    for root in roots:
        root = Path(root).resolve()
        path = (root / (library + ".pretty") / (item + ".kicad_mod")).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"footprint escapes library root: {name}")
        if path.is_file():
            matches.append(path)
    if len(matches) > 1:
        raise ValueError(f"ambiguous footprint {name}: {matches}")
    if not matches:
        return {"name": name, "exists": False}
    path = matches[0]
    try:
        tree = load(path)
    except (
        sexpdata.ExpectClosingBracket,
        sexpdata.ExpectNothing,
        sexpdata.ExpectSExp,
        ValueError,
        AssertionError,
    ) as exc:
        raise ValueError(f"invalid footprint file {path}: {exc}") from exc
    if tag(tree) not in {"footprint", "module"}:
        raise ValueError(f"not a footprint file: {path}")
    pads = children(tree, "pad")
    return {
        "name": name,
        "exists": True,
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "pads": sorted({str(p[1]) for p in pads if str(p[1])}),
        "pad_records": [
            {"number": str(p[1]), "technology": str(p[2]), "shape": str(p[3])} for p in pads
        ],
        "unnumbered_pads": sum(not str(p[1]) for p in pads),
    }


def footprint_matches(name, pattern):
    """KiCad library-convention globs; unqualified filters match the item only."""
    expression = "".join(".*" if c == "*" else ".?" if c == "?" else re.escape(c) for c in pattern)
    subject = name if ":" in pattern else name.split(":", 1)[1]
    return re.fullmatch(expression, subject, re.IGNORECASE) is not None


def convert_library(source, output, symbols=(), footprint_roots=(), extra_footprints=()):
    source, output = Path(source).resolve(), Path(output).absolute()
    if output.exists():
        raise FileExistsError(
            f"conversion output already exists: {output}; choose a fresh directory"
        )
    try:
        tree = load(source)
    except (
        sexpdata.ExpectClosingBracket,
        sexpdata.ExpectNothing,
        sexpdata.ExpectSExp,
        ValueError,
        AssertionError,
    ) as exc:
        failure(source, "convert.format", f"invalid KiCad symbol library: {exc}")
    if tag(tree) != "kicad_symbol_lib":
        failure(source, "convert.format", "expected a KiCad symbol library")
    raw = {}
    for symbol in children(tree, "symbol"):
        if len(symbol) < 2 or not isinstance(symbol[1], str):
            failure(source, "convert.format", "symbol is missing its name")
        name = str(symbol[1])
        if name in raw:
            failure(source, "convert.duplicate", f"duplicate symbol {name!r}")
        raw[name] = symbol
    selected = sorted(set(symbols) if symbols else raw)
    if set(selected) - raw.keys():
        failure(source, "convert.missing", f"unknown symbols: {sorted(set(selected) - raw.keys())}")
    resolved = {}
    for name in selected:
        chain, seen = [], set()
        current = name
        while current not in resolved:
            if current in seen or current not in raw:
                failure(source, "convert.inheritance", f"invalid inheritance chain at {current!r}")
            seen.add(current)
            chain.append(current)
            base = scalar(raw[current], "extends")
            if base is None:
                break
            current = str(base)
        for current in reversed(chain):
            symbol = raw[current]
            base = scalar(symbol, "extends")
            parent = resolved.get(str(base)) if base is not None else None
            properties = dict(parent["properties"]) if parent else {}
            if any(len(p) < 3 for p in children(symbol, "property")):
                failure(source, "convert.format", f"{current!r} has a malformed property")
            properties.update({str(p[1]): str(p[2]) for p in children(symbol, "property")})
            pins = []
            pending = [symbol]
            while pending:
                item = pending.pop()
                pins.extend(children(item, "pin"))
                pending.extend(children(item, "symbol"))
            if parent and pins:
                failure(
                    source,
                    "convert.inheritance",
                    f"{current!r} both inherits and defines pins; explicit handling is required",
                )
            resolved[current] = {
                "properties": properties,
                "pins": deepcopy(parent["pins"]) if parent else pins,
                "on_board": str(
                    scalar(symbol, "on_board", parent["on_board"] if parent else "yes")
                ),
                "jumpers": str(scalar(symbol, "duplicate_pin_numbers_are_jumpers", "no")),
            }
    generated, report, used_names, footprints = [], [], {}, set(extra_footprints)
    catalog, cache = set(), {}
    for root in footprint_roots:
        if not Path(root).is_dir():
            failure(source, "convert.footprint-root", f"footprint root is not a directory: {root}")
        for path in Path(root).glob("*.pretty/*.kicad_mod"):
            catalog.add(f"{path.parent.stem}:{path.stem}")

    def get_record(name):
        if name not in cache:
            cache[name] = footprint_record(name, footprint_roots)
        return cache[name]

    mapping = {}
    for original in selected:
        data = resolved[original]
        if data["on_board"] == "no":
            report.append(
                {
                    "symbol": original,
                    "check": "not_on_board",
                    "message": "schematic-only symbol omitted",
                }
            )
            continue
        name = identifier(original)
        if name in used_names:
            failure(
                source,
                "convert.name-collision",
                f"{original!r} and {used_names[name]!r} map to {name!r}; convert separately or rename explicitly",
            )
        used_names[name], mapping[original] = original, name
        pins = defaultdict(list)
        for pin in data["pins"]:
            number = str(scalar(pin, "number", ""))
            if not number:
                failure(
                    source, "convert.pin-number", f"{original!r} has an empty physical pin number"
                )
            pins[number].append(pin)
        aliases = defaultdict(set)
        for number, entries in pins.items():
            for pin in entries:
                alias = str(scalar(pin, "name", ""))
                if alias and alias != "~":
                    aliases[alias].add(number)
        ambiguous = {
            alias
            for alias, numbers in aliases.items()
            if len(numbers) > 1 or alias in pins and numbers != {alias}
        }
        for alias in sorted(ambiguous):
            report.append(
                {
                    "symbol": original,
                    "check": "ambiguous_alias",
                    "alias": alias,
                    "pins": sorted(aliases[alias]),
                    "message": "alias omitted; physical pin IDs retained",
                }
            )
        props = data["properties"]
        # A two-terminal R, C, or L symbol's value is a quantity; its KiCad Value is a
        # placeholder such as "R", so each instance supplies the number, e.g. 4.7k.
        quantity = PASSIVE_QUANTITIES.get(props.get("Reference")) if len(pins) == 2 else None
        lines = [
            f"component {name}:",
            "    params:",
            f"        {quantity} value"
            if quantity
            else f"        string value = {json.dumps(props.get('Value', original))}",
        ]
        if pins:
            lines.append("    pins:")
        for number, entries in sorted(pins.items()):
            names = sorted(
                alias
                for alias, numbers in aliases.items()
                if number in numbers and alias not in ambiguous and alias != number
            )
            spelled = ", ".join(map(pin_spelling, names))
            lines.append(f"        {json.dumps(number)}" + (f" ({spelled})" if names else "") + ":")
            types = sorted({str(pin[1]) for pin in entries})
            roles = (
                sorted(
                    {
                        r
                        for t in types
                        for r in (("power_input", "ground") if t == "power_in" else (ROLE_MAP[t],))
                    }
                )
                if all(t in ROLE_MAP for t in types)
                else []
            )
            if "no_connect" in types:
                lines.append("            use: use.disallowed")
            directional = set(roles) & {"drive.low", "drive.high"}
            if directional:
                if len(directional) == 1 and set(roles) <= directional | {
                    "passive",
                    "digital_input",
                }:
                    direction = next(iter(directional))
                    lines.append("            drive: " + direction)
                    report.append(
                        {
                            "symbol": original,
                            "pin": number,
                            "check": "assumed_drive_direction",
                            "message": f"KiCad convention mapped to {direction}; verify polarity against the device datasheet",
                        }
                    )
                else:
                    report.append(
                        {
                            "symbol": original,
                            "pin": number,
                            "check": "ambiguous_drive",
                            "message": "stacked pin types have incompatible drive capabilities; annotate explicitly",
                        }
                    )
            elif len(roles) == 1:
                lines.append(f"            role: role.{roles[0]}")
            elif roles:
                lines.append("            role: [" + ", ".join("role." + r for r in roles) + "]")
            if "tri_state" in types and set(types) <= {"tri_state", "input", "passive"}:
                lines.append("            tristate_capable: true")
            lines.extend(
                ["            custom:", f"                kicad_pin_types: {json.dumps(types)}"]
            )
            for unknown in set(types) - ROLE_MAP.keys() - {"no_connect"}:
                report.append(
                    {
                        "symbol": original,
                        "pin": number,
                        "check": "unmapped_pin_type",
                        "type": unknown,
                        "message": "raw type retained; no role inferred for this type",
                    }
                )
        candidates = []
        filters = props.get("ki_fp_filters", "").split()
        for candidate in sorted(catalog):
            if not any(footprint_matches(candidate, pattern) for pattern in filters):
                continue
            try:
                candidate_record = get_record(candidate)
            except ValueError as exc:
                report.append(
                    {
                        "symbol": original,
                        "footprint": candidate,
                        "check": "invalid_footprint_candidate",
                        "message": str(exc),
                    }
                )
                continue
            if candidate_record["exists"] and set(candidate_record["pads"]) == set(pins):
                candidates.append(candidate)
                footprints.add(candidate)
        footprint = props.get("Footprint", "")
        lines.extend(
            ["    metadata:", f"        ref_prefix: {json.dumps(props.get('Reference', 'U'))}"]
        )
        if group := interchangeable_pins(original, props, pins):
            lines.append(f"        interchangeable: {json.dumps(list(group))}")
        if footprint:
            footprints.add(footprint)
            lines.append(f"        footprint: {json.dumps(footprint)}")
        else:
            report.append({"symbol": original, "check": "unspecified_footprint"})
        lines.extend(
            [
                "        custom:",
                f"            kicad_symbol: {json.dumps(source.stem + ':' + original)}",
                f"            kicad_duplicate_pin_numbers_are_jumpers: {json.dumps(data['jumpers'])}",
                f"            kicad_footprint_candidates: {json.dumps(candidates)}",
                "            kicad_properties:",
            ]
        )
        for key, value in sorted(props.items()):
            lines.append(f"                {json.dumps(key)}: {json.dumps(value)}")
        if not props:
            lines.pop()  # No empty indentation-only section.
        generated.append("\n".join(lines))
    records = {}
    for name in sorted(footprints):
        try:
            records[name] = get_record(name)
        except ValueError as exc:
            failure(source, "convert.footprint", str(exc))
        if not records[name]["exists"]:
            report.append({"footprint": name, "check": "unknown_footprint"})
    for original in mapping:
        data = resolved[original]
        footprint = data["properties"].get("Footprint", "")
        record = records.get(footprint)
        if record and record["exists"]:
            pins = {str(scalar(pin, "number")) for pin in data["pins"]}
            pads = set(record["pads"])
            for check, identifiers in [
                ("pin_without_pad", pins - pads),
                ("pad_without_pin", pads - pins),
            ]:
                if identifiers:
                    report.append(
                        {
                            "symbol": original,
                            "footprint": footprint,
                            "check": check,
                            "identifiers": sorted(identifiers),
                        }
                    )
    text = "from noscheme import role, use, drive\n\n" + "\n\n".join(generated) + "\n"
    module = identifier(source.stem)
    filename = str(output / (module + ".nosch"))
    parse(text, filename)
    text = format_source(text, filename)
    manifest = {
        "schema_version": 1,
        "source": str(source),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "module": module,
        "symbols": mapping,
        "issues": sorted(report, key=lambda x: json.dumps(x, sort_keys=True)),
    }
    snapshots = {"schema_version": 1, "footprints": records}
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".noscheme-convert-", dir=output.parent))
    try:
        (stage / (module + ".nosch")).write_text(text, encoding="utf-8")
        (stage / "footprints.json").write_text(
            json.dumps(snapshots, indent=2, sort_keys=True) + "\n"
        )
        (stage / "conversion.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
        if output.exists():
            raise FileExistsError(f"conversion output appeared while converting: {output}")
        os.rename(stage, output)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return manifest
