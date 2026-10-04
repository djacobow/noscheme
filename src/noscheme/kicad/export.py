"""KiCad version E netlists, explicit footprint records, and persistent identity.

No live CAD libraries are read. Publication stages every generated file before
replacing outputs and commits the atomic state file last. Ordinary I/O failures
roll back replaced files; abrupt process/machine failure is not a multi-file
transaction.
"""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4, uuid5, UUID

from .. import ast as a
from ..diagnostics import Diagnostic, DiagnosticError
from ..erc import apply_erc, report_files
from .sexpr import form, dump


def fail(path, code, message):
    raise DiagnosticError([Diagnostic(code, "error", message, a.Span(str(path), 0, 0, 1, 1, 1, 1))])


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (ValueError, UnicodeError) as exc:
        fail(path, "export.json", f"invalid JSON: {exc}")


def attribute(part, name, default=None):
    return part["metadata"].get(name, {}).get("value", default)


def reference_valid(reference):
    return isinstance(reference, str) and bool(re.fullmatch(r"[A-Za-z_#][A-Za-z0-9_#]*", reference))


def validate_state(state, path):
    try:
        if state["schema_version"] != 1:
            raise ValueError("unsupported state version")
        UUID(state["namespace"])
        refs, uuids = {}, set()
        for identity, entry in state["entries"].items():
            if not isinstance(identity, str) or not identity:
                raise ValueError("invalid identity")
            uuid = str(UUID(entry["uuid"]))
            if uuid in uuids:
                raise ValueError("duplicate UUID")
            uuids.add(uuid)
            if not isinstance(entry["active"], bool) or not isinstance(
                entry["reserved_references"], list
            ):
                raise ValueError("invalid entry fields")
            if entry["reference"] not in entry["reserved_references"]:
                raise ValueError("current reference is not reserved")
            for reference in entry["reserved_references"]:
                if not reference_valid(reference):
                    raise ValueError("invalid reference")
                if reference.casefold() in refs and refs[reference.casefold()] != identity:
                    raise ValueError("reference collision")
                refs[reference.casefold()] = identity
        if not isinstance(state.get("previous_parts", {}), dict):
            raise ValueError("invalid previous design")
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        fail(path, "state.invalid", str(exc))


def assign(circuit, state, path):
    state = deepcopy(state)
    entries = state["entries"]
    owners = {
        ref.casefold(): identity
        for identity, e in entries.items()
        for ref in e["reserved_references"]
    }
    for entry in entries.values():
        entry["active"] = False
    # Reserve explicit choices first so automatically assigned references cannot take them.
    explicit = {}
    for part in circuit["parts"]:
        identity, reference = part["identity"], attribute(part, "ref")
        if reference is not None:
            if not reference_valid(reference):
                fail(path, "reference.invalid", f"invalid KiCad reference {reference!r}")
            if reference.casefold() in owners and owners[reference.casefold()] != identity:
                fail(
                    path,
                    "reference.reserved",
                    f"{reference!r} is reserved by {owners[reference.casefold()]!r}",
                )
            owners[reference.casefold()] = identity
            explicit[identity] = reference
    for part in sorted(circuit["parts"], key=lambda p: p["identity"]):
        identity = part["identity"]
        entry = entries.get(identity)
        reference = explicit.get(identity) or (entry["reference"] if entry else None)
        if reference is None:
            prefix = attribute(part, "ref_prefix", "U")
            if not reference_valid(prefix):
                fail(path, "reference.prefix", f"invalid reference prefix {prefix!r}")
            number = 1
            while (prefix + str(number)).casefold() in owners:
                number += 1
            reference = prefix + str(number)
        owners[reference.casefold()] = identity
        if entry is None:
            entry = entries[identity] = {
                "uuid": str(uuid5(UUID(state["namespace"]), identity)),
                "reserved_references": [],
            }
        entry.update(reference=reference, active=True)
        if reference not in entry["reserved_references"]:
            entry["reserved_references"].append(reference)
    return state


class KiCadNames:
    """Reversible mapping for currently legal NoScheme source path segments."""

    def __init__(self):
        self.reverse = {}

    def encode(self, net):
        segments = []
        for part in net["path"]:
            if type(part) is int:
                segments[-1] += f"[{part}]"
            else:
                segments.append(part)
        name = ("/" if len(segments) > 1 else "") + "/".join(segments)
        previous = self.reverse.get(name)
        if previous is not None and previous["path"] != net["path"]:
            raise ValueError(f"exported net-name collision: {name!r}")
        self.reverse[name] = {"source_name": net["name"], "path": net["path"]}
        return name


def part_value(part):
    value = part["value"]
    if value is None:
        return part["definition"].rsplit(".", 1)[-1]
    if value["type"] == "string":
        return value["value"]
    if value["tag"] == "integer":
        return value["value"]
    text = repr(value["value"])
    return text[:-2] if text.endswith(".0") else text


def verify_footprints(circuit, snapshot, path):
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("schema_version") != 1
        or not isinstance(snapshot.get("footprints"), dict)
    ):
        fail(path, "footprint.records", "unsupported footprint-record schema")
    omitted = []
    for part in circuit["parts"]:
        name = attribute(part, "footprint")
        if not name:
            omitted.append(part["identity"])
            continue
        record = snapshot["footprints"].get(name)
        if not isinstance(record, dict) or record.get("exists") is not True:
            fail(path, "footprint.unknown", f"{part['identity']}: no converted record for {name!r}")
        pads = record.get("pads")
        if not isinstance(pads, list) or not all(isinstance(x, str) and x for x in pads):
            fail(path, "footprint.records", f"invalid pad record for {name!r}")
        pins, pads = set(part["pins"]), set(pads)
        if pins != pads:
            fail(
                path,
                "footprint.mismatch",
                f"{part['identity']}: {name}: pins without pads {sorted(pins - pads)}, pads without pins {sorted(pads - pins)}",
            )
    return omitted


KICAD_PIN_TYPES = {
    "passive": "passive",
    "digital_input": "input",
    "analog_input": "input",
    "digital_output": "output",
    "analog_output": "output",
    "digital_bidirectional": "bidirectional",
    "analog_bidirectional": "bidirectional",
    "power_input": "power_in",
    "ground": "power_in",
    "power_output": "power_out",
    "open_drain": "open_collector",
    "open_source": "open_emitter",
    "do_not_connect": "no_connect",
    "unchecked": "unspecified",
}


def libpart_pins(parts):
    """Shared library records must not pick one instance's role arbitrarily."""
    pins = []
    for number in sorted(parts[0]["pins"]):
        records = [part["pins"][number] for part in parts]
        aliases = attribute(records[0], "aliases", [])
        kinds = set()
        for record in records:
            roles = attribute(record, "role", [])
            kind = (
                KICAD_PIN_TYPES.get(roles[0], "unspecified") if len(roles) == 1 else "unspecified"
            )
            if kind == "output" and attribute(record, "tristate_capable", False):
                kind = "tri_state"
            kinds.add(kind)
        kind = next(iter(kinds)) if len(kinds) == 1 else "unspecified"
        pins.append(
            form(
                "pin",
                form("num", number),
                form("name", aliases[0] if aliases else number),
                form("type", kind),
            )
        )
    return form("pins", *pins)


def render(circuit, state):
    names = KiCadNames()
    net_names = {net["name"]: names.encode(net) for net in circuit["nets"]}
    components, libparts, nets, membership = [], {}, {}, {}
    for net in circuit["nets"]:
        classes = {
            d["metadata"]["net_class"]["value"]
            for d in net["declarations"]
            if "net_class" in d["metadata"]
        }
        if len(classes) > 1:
            raise ValueError(f"conflicting classes on net {net['name']!r}")
        if classes:
            membership[net_names[net["name"]]] = sorted(classes)
    definitions = {}
    for part in circuit["parts"]:
        if attribute(part, "footprint"):
            definitions.setdefault(part["definition"], []).append(part)
    for part in sorted(circuit["parts"], key=lambda p: p["identity"]):
        footprint = attribute(part, "footprint")
        if not footprint:
            continue
        entry = state["entries"][part["identity"]]
        ref = entry["reference"]
        kind = part["definition"]
        components.append(
            form(
                "comp",
                form("ref", ref),
                form("value", part_value(part)),
                form("footprint", footprint),
                form("libsource", form("lib", "NoScheme"), form("part", kind)),
                form(
                    "property", form("name", "noscheme_identity"), form("value", part["identity"])
                ),
                form("sheetpath", form("names", "/"), form("tstamps", "/")),
                form("tstamps", entry["uuid"]),
            )
        )
        if kind not in libparts:
            libparts[kind] = form(
                "libpart",
                form("lib", "NoScheme"),
                form("part", kind),
                libpart_pins(definitions[kind]),
            )
        for pin, data in sorted(part["pins"].items()):
            if data["net"] is not None:
                net = net_names[data["net"]]
                nets.setdefault(net, []).append(form("node", form("ref", ref), form("pin", pin)))
    netlist = dump(
        form(
            "export",
            form("version", "E"),
            form("design", form("source", circuit["board"]), form("tool", "NoScheme 0.1")),
            form("components", *components),
            form("libparts", *[libparts[k] for k in sorted(libparts)]),
            form(
                "nets",
                *[
                    form("net", form("code", i), form("name", n), *nets[n])
                    for i, n in enumerate(sorted(nets), 1)
                ],
            ),
        )
    )
    return netlist, names.reverse, membership


def publish(files):
    """Stage bytes first, restore earlier outputs if a replacement fails."""
    staged, previous, replaced = {}, {}, []
    try:
        for path, text in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            previous[path] = path.read_bytes() if path.exists() else None
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=".noscheme-export-", delete=False
            ) as f:
                staged[path] = Path(f.name)
                f.write(text.encode("utf-8"))
                f.flush()
                os.fsync(f.fileno())
        for path, temp in staged.items():
            os.replace(temp, path)
            replaced.append(path)
    except OSError:
        for path in reversed(replaced):
            old = previous[path]
            if old is None:
                path.unlink()
            else:
                with tempfile.NamedTemporaryFile(
                    dir=path.parent, prefix=".noscheme-restore-", delete=False
                ) as f:
                    f.write(old)
                    temp = f.name
                os.replace(temp, path)
        raise
    finally:
        for path in staged.values():
            if path.exists():
                path.unlink()


def probable_renames(old, current):
    """Suggest only unique exact electrical matches; never change identity state."""

    def group(parts, identities):
        groups = {}
        for identity in sorted(identities):
            part = parts[identity]
            signature = json.dumps(
                {k: part.get(k) for k in ("footprint", "value", "pins")}, sort_keys=True
            )
            groups.setdefault(signature, []).append(identity)
        return groups

    removed = group(old, old.keys() - current.keys())
    added = group(current, current.keys() - old.keys())
    return sorted(
        [
            {"before": removed[key][0], "after": added[key][0]}
            for key in removed.keys() & added.keys()
            if len(removed[key]) == len(added[key]) == 1
        ],
        key=lambda hint: (hint["before"], hint["after"]),
    )


def change_lines(changes):
    """Render state differences without changing the machine-readable report."""
    lines = [
        *(f"Added {n}" for n in changes["added"]),
        *(f"Removed {n}" for n in changes["removed"]),
    ]

    def display(value):
        if isinstance(value, dict) and "type" in value and "value" in value:
            value = value["value"]
        return "unconnected" if value is None else json.dumps(value, ensure_ascii=False)

    for change in changes["changed"]:
        lines.append(f"Changed {change['identity']}")
        before, after = change["before"], change["after"]
        for key in ("path", "reference", "value", "footprint"):
            if before.get(key) != after.get(key):
                lines.append(f"  {key}: {display(before.get(key))} -> {display(after.get(key))}")
        old_pins, new_pins = before.get("pins", {}), after.get("pins", {})
        for pin in sorted(old_pins.keys() | new_pins.keys()):
            old = display(old_pins[pin]) if pin in old_pins else "absent"
            new = display(new_pins[pin]) if pin in new_pins else "absent"
            if old != new:
                lines.append(f"  pin {pin}: {old} -> {new}")
    return lines


def export_project(project, circuit, footprint_records, *, configurations=None, warnings=None):
    apply_erc(circuit, configurations, registry=project.config.erc_registry)
    state_path = project.config.path.parent / "noscheme.state.json"
    state = (
        read_json(state_path)
        if state_path.exists()
        else {"schema_version": 1, "namespace": str(uuid4()), "entries": {}, "previous_parts": {}}
    )
    validate_state(state, state_path)
    snapshot = read_json(footprint_records)
    omitted = verify_footprints(circuit, snapshot, footprint_records)
    updated = assign(circuit, state, state_path)
    export_warnings = []
    for part in circuit["parts"]:
        reference = updated["entries"][part["identity"]]["reference"]
        if reference.startswith("#"):
            source = part["metadata"].get("ref", part["metadata"].get("ref_prefix", {}))
            export_warnings.append(
                Diagnostic(
                    "reference.leading-hash",
                    "warning",
                    f"KiCad reference {reference!r} starts with '#'; downstream BOM or placement tools may treat it as a virtual symbol and omit it",
                    a.Span(**source.get("span", part["source"]["declaration"])),
                    instance_path=part["identity"],
                )
            )
    if warnings is not None:
        warnings.extend(export_warnings)
    try:
        netlist, names, classes = render(circuit, updated)
    except ValueError as exc:
        fail(project.config.path, "export.names", str(exc))
    current = {
        p["identity"]: {
            "path": p["path"],
            "footprint": attribute(p, "footprint"),
            "value": p["value"],
            "reference": updated["entries"][p["identity"]]["reference"],
            "pins": {pin: data["net"] for pin, data in p["pins"].items()},
        }
        for p in circuit["parts"]
    }
    old = state.get("previous_parts", {})
    changes = {
        "added": sorted(current.keys() - old.keys()),
        "removed": sorted(old.keys() - current.keys()),
        "changed": [
            {"identity": k, "before": old[k], "after": current[k]}
            for k in sorted(current.keys() & old.keys())
            if current[k] != old[k]
        ],
    }
    rename_hints = probable_renames(old, current)
    updated["previous_parts"] = current
    updated["netlist_sha256"] = hashlib.sha256(netlist.encode("utf-8")).hexdigest()
    report = {
        "schema_version": 1,
        "backend": "kicad",
        "warnings": [a.to_data(w) for w in export_warnings],
        "erc_performed": circuit["erc_performed"],
        "erc_errors": circuit["erc"]["errors"],
        "erc_warnings": circuit["erc"]["warnings"],
        "erc_pending": circuit["erc"]["pending"],
        "erc_configurations": circuit["erc"]["configurations"],
        "omitted_without_footprints": omitted,
        "changes": changes,
        "probable_renames": rename_hints,
        "footprint_records_sha256": hashlib.sha256(
            Path(footprint_records).read_bytes()
        ).hexdigest(),
    }
    exported_names = {v["source_name"]: k for k, v in names.items()}
    net_table = [
        {"source": n["name"], "exported": exported_names[n["name"]], "endpoints": n["endpoints"]}
        for n in circuit["nets"]
    ]
    out = project.config.output_dir
    files = {
        out / "board.net": netlist,
        **report_files(circuit, out),
        out / "names.json": json.dumps(names, indent=2, sort_keys=True) + "\n",
        out / "netclasses.json": json.dumps(classes, indent=2, sort_keys=True) + "\n",
        out / "export.json": json.dumps(report, indent=2, sort_keys=True) + "\n",
        out / "nets.json": json.dumps(net_table, indent=2, sort_keys=True) + "\n",
        out / "nets.txt": "\n".join(
            f"{n['source']} -> {n['exported']}: "
            + ", ".join(
                ".".join(str(s) for s in e["instance"]) + ":" + e["pin"] for e in n["endpoints"]
            )
            for n in net_table
        )
        + "\n",
        out / "changes.txt": "\n".join(
            change_lines(changes)
            + [
                f"Possible rename {hint['before']} -> {hint['after']} "
                "(same footprint, value, and connections; identity was not preserved)"
                for hint in rename_hints
            ]
        )
        + "\n",
        state_path: json.dumps(updated, indent=2, sort_keys=True) + "\n",
    }
    protected = {
        project.config.path.resolve(),
        Path(footprint_records).resolve(),
        *(m.path.resolve() for m in project.modules.values()),
    }
    if any(path.resolve() in protected for path in files):
        fail(
            project.config.path,
            "export.overwrite",
            "export paths must not overwrite project inputs",
        )
    if len({p.resolve() for p in files}) != len(files):
        fail(project.config.path, "export.overwrite", "export output paths overlap")
    publish(files)
    return {"output": str(out / "board.net"), "state": str(state_path), **report}
