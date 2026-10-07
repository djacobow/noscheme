"""Preserve board-instance CAD identities when planning a source rename."""

import json

from . import ast as a
from .formatter import INDENT
from .parser import Builder
from .syntax import mask_comments


def identity_edit(node, source):
    """Insert the old board-level identity, preserving existing explicit metadata.

    Add a line to an existing metadata section, or a new section after the
    instance's last line; arguments, sections, and comments stay untouched.
    """
    metadata = next((s for s in node.sections if s.name == "metadata"), None)
    if metadata and any(f.key.first.text == "identity" for f in metadata.entries):
        return None
    builder = Builder(source, node.span.filename)
    newline = "\r\n" if "\r\n" in source else "\n"
    identity = "identity: " + json.dumps(node.name.text)
    if metadata:
        first = metadata.entries[0].key.span
        start = source.rfind("\n", 0, first.start_pos) + 1
        return builder.position_span(start, start), " " * (first.column - 1) + identity + newline
    base = node.span.column - 1
    section = (
        node.sections[0].span.column - 1
        if node.sections and node.sections[0].span.line != node.span.line
        else base + len(INDENT)
    )
    # Insert after the end of the instance's last line, past any trailing comment.
    masked = mask_comments(source, node.span.filename)
    last = len(masked[: node.span.end_pos].rstrip())
    end = source.find("\n", last)
    end = len(source) if end < 0 else end - (source[end - 1] == "\r")
    text = newline + " " * section + "metadata:" + newline + " " * (section + section - base)
    return builder.position_span(end, end), text + identity


def renamed_path(value, old, new):
    if value == old or value.startswith(old + ".") or value.startswith(old + "["):
        return new + value[len(old) :]
    return value


def waiver_edits(owner, old, new):
    """Update waiver pin paths in the renamed instance's definition; reasons stay untouched."""
    # Target pin paths are relative to the containing declaration.
    fields = [f for node in owner.body if isinstance(node, a.Net) for f in node.fields]
    for section in owner.body:
        if isinstance(section, a.Section) and section.name == "ports":
            fields.extend(f for port in section.entries for f in port.fields)
    edits = []
    for field in fields:
        if field.key.first.text != "waivers":
            continue
        for waiver in field.fields:
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
