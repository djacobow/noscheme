"""Preserve board-instance CAD identities when planning a source rename."""

import json
from textwrap import indent

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
    if node.inline:
        return inline_identity_edit(node, source, builder, newline, identity)
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


def inline_identity_edit(node, source, builder, newline, identity):
    """Expand compact arguments into sections so rename can add stable identity.

    The resolver has already mapped positional nets to physical pins. Keep source
    expressions and comments, including those between comma-separated arguments.
    """
    masked = mask_comments(source, node.span.filename)
    opening = masked.index("(", node.component[-1].span.end_pos, node.span.end_pos)
    closing = masked.rfind(")", opening, node.span.end_pos)
    entries = sorted(
        ((entry, section.name) for section in node.sections for entry in section.entries),
        key=lambda item: item[0].span.start_pos,
    )
    groups = {}
    previous = opening + 1

    def gap(start, stop):
        # Only the separator is discarded; commas within comments are preserved.
        comma = masked.find(",", start, stop)
        return (
            source[start:comma] + source[comma + 1 : stop] if comma >= 0 else source[start:stop]
        ).strip()

    for entry, section in entries:
        comments = gap(previous, entry.span.start_pos)
        if isinstance(entry, a.Binding):
            endpoint = entry.endpoint
            key = (
                json.dumps(endpoint.head.first.text)
                if endpoint.span == entry.target.span
                else source[endpoint.span.start_pos : endpoint.span.end_pos]
            )
            expr = entry.target
        else:
            key, expr = entry.name.text, entry.value
        text = key + ": " + source[expr.span.start_pos : expr.span.end_pos]
        groups.setdefault(section, []).append((comments + newline if comments else "") + text)
        previous = entry.span.end_pos
    comments = gap(previous, closing)
    base = " " * (node.span.column - 1)
    lines = []
    for section, values in groups.items():
        lines.append(base + "    " + section + ":")
        lines.extend(indent(value, base + "        ") for value in values)
    if comments:
        lines.append(indent(comments, base + "    "))
    lines.extend([base + "    metadata:", base + "        " + identity])
    text = newline + newline.join(lines) + newline + base
    return builder.position_span(opening + 1, closing), text


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
