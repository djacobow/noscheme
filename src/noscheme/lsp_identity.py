"""Preserve board-instance CAD identities when planning a source rename."""

import json

from . import ast as a
from .parser import Builder
from .syntax import mask_comments


def identity_edit(node, source):
    """Insert the old board-level identity, preserving existing explicit metadata."""
    metadata = next((s for s in node.sections if s.name == "metadata"), None)
    if metadata and any(f.key.first.text == "identity" for f in metadata.entries):
        return None
    builder = Builder(source, node.span.filename)
    newline = "\r\n" if "\r\n" in source else "\n"
    identity = "identity: " + json.dumps(node.name.text)
    if metadata:
        first = metadata.entries[0].key.span
        start = source.rfind("\n", 0, first.start_pos) + 1
        text = " " * (first.column - 1) + identity + newline
    else:
        base = node.span.column - 1
        section = node.sections[0].span.column - 1 if node.sections else base + 4
        fields = section + (section - base)
        masked = mask_comments(source, node.span.filename)
        closing = masked.rfind(")", node.span.start_pos, node.span.end_pos)
        if closing < 0:
            raise ValueError("Cannot locate the instance's closing parenthesis safely.")
        line_start = source.rfind("\n", 0, closing) + 1
        text = " " * section + "metadata:" + newline + " " * fields + identity + newline
        if source[line_start:closing].strip():
            start = closing
            text = newline + text + " " * base
        else:
            start = line_start
    return builder.position_span(start, start), text


def renamed_path(value, old, new):
    if value == old or value.startswith(old + ".") or value.startswith(old + "["):
        return new + value[len(old) :]
    return value


def waiver_edits(project, owner, old, new):
    """Update semantic path strings; custom text and waiver reasons stay untouched."""
    edits = []
    for module in project.modules.values():
        for definition in module.ast.items:
            if (
                not isinstance(definition, a.Definition)
                or definition.kind == "board"
                and definition is not owner
            ):
                continue
            fields = [f for node in definition.body if isinstance(node, a.Net) for f in node.fields]
            for section in definition.body:
                if isinstance(section, a.Section) and section.name == "ports":
                    fields.extend(f for port in section.entries for f in port.fields)
            for field in fields:
                if field.key.first.text != "waivers":
                    continue
                for waiver in field.fields:
                    name = waiver.key.first
                    if name.text.startswith(("pin:", "net:")) and name.text.count(":") >= 2:
                        scope, path, application = name.text.split(":", 2)
                        updated = renamed_path(path, old, new)
                        if updated != path:
                            edits.append(
                                (name.span, json.dumps(f"{scope}:{updated}:{application}"))
                            )
                    # Target pin paths are relative to the containing declaration.
                    if definition is not owner:
                        continue
                    for entry in waiver.fields:
                        if entry.key.first.text == "pins" and isinstance(entry.value, a.ListExpr):
                            for pin in entry.value.items:
                                if isinstance(pin, a.String):
                                    updated = renamed_path(pin.value, old, new)
                                    if updated != pin.value:
                                        edits.append((pin.span, json.dumps(updated)))
    return edits


def physical_signature(circuit):
    """Compare parts and physical net membership, independent of source path names."""
    nets = {}
    parts = {}
    for part in circuit["parts"]:
        metadata = part["metadata"]
        parts[part["identity"]] = (
            part["definition"],
            part["parameters"],
            part["value"],
            metadata.get("footprint", {}).get("value"),
            metadata.get("ref", {}).get("value"),
            metadata.get("ref_prefix", {}).get("value"),
        )
        for number, pin in part["pins"].items():
            if pin["net"] is not None:
                nets.setdefault(pin["net"], set()).add((part["identity"], number))
    return parts, frozenset(frozenset(endpoints) for endpoints in nets.values())
