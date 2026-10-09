"""Additive instance scaffolding. Never infer electrical connections or values."""

import json
import re

from . import ast as a
from .diagnostics import DiagnosticError
from .lsp_completion import masked_source
from .project import load_project
from .resolve import Resolver


def action_edits(text, start, pins, parameters, metadata):
    """Insert at section boundaries; retain all existing source and comments."""
    masked = masked_source(text, start)
    if masked is None:
        return []
    line_start = text.rfind("\n", 0, start) + 1
    indent = text[line_start:start]
    newline = "\r\n" if "\r\n" in text else "\n"
    depth = 0
    header_end = len(text)
    for i in range(start, len(masked)):
        char = masked[i]
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        if char == "\n" and depth == 0:
            header_end = i + 1
            break
    stop = len(text)
    sections = []
    default_body_indent = indent + (indent or "    ")
    body_indent = default_body_indent
    for match in re.finditer(r"[^\n]*\n|[^\n]+$", text[header_end:]):
        pos = header_end + match.start()
        row = masked[pos : header_end + match.end()]
        if not row.strip():
            continue
        leading = len(row) - len(row.lstrip())
        if leading <= len(indent):
            stop = pos
            break
        if not sections and body_indent == default_body_indent:
            body_indent = row[:leading]
        if leading == len(body_indent) and row.strip() in {"params:", "metadata:", "elements:"}:
            sections.append((row.strip(), pos, header_end + match.end()))
    step = body_indent[len(indent) :]
    region = text[start:stop]
    parameters = [
        (name, required)
        for name, required in parameters
        if required or f"// {name}: TODO_parameter_{name}" not in region
    ]
    metadata = metadata and "// metadata:" not in region
    edits = {}
    used = text

    def placeholder(base):
        nonlocal used
        name = base
        while name in used:
            name += "_"
        used += " " + name
        return name

    def insert(at, lines):
        if lines:
            edits[at] = edits.get(at, "") + newline.join(lines) + newline

    connection_end = sections[0][1] if sections else stop
    insert(
        connection_end,
        [
            body_indent + pin + ": " + placeholder("TODO_connection_" + str(i + 1))
            for i, pin in enumerate(pins)
        ],
    )
    if parameters:
        section = next((s for s in sections if s[0] == "params:"), None)
        at = (
            next((s[1] for s in sections if section and s[1] > section[1]), stop)
            if section
            else connection_end
        )
        lines = [] if section else [body_indent + "params:"]
        # Optional-only scaffolding is entirely commented: no empty section.
        commented = not section and not any(required for _, required in parameters)
        if commented:
            lines = [body_indent + "// params:"]
        for name, required in parameters:
            prefix = "" if required else "// "
            lines.append(
                body_indent + step + prefix + name + ": " + placeholder("TODO_parameter_" + name)
            )
        insert(at, lines)
    if metadata:
        insert(
            stop, [body_indent + "// metadata:", body_indent + "//     Add metadata fields here."]
        )
    return [
        (at, (newline if at and text[at - 1] != "\n" else "") + value)
        for at, value in sorted(edits.items())
    ]


def instance_actions(path, config, sources, offset):
    if config is None:
        return []
    try:
        project = load_project(config, sources=sources)
        resolver = Resolver(project)
        definitions = resolver.resolve_interfaces()
        module = next((m for m in project.modules.values() if m.path == path), None)
        if module is None:
            return []
        text = sources[path]
        for owner in module.ast.items:
            if not isinstance(owner, a.Definition):
                continue
            for node in owner.body:
                if (
                    not isinstance(node, a.Instance)
                    or not node.span.start_pos <= offset < node.span.end_pos
                ):
                    continue
                symbol = module.symbols.get(".".join(n.text for n in node.component))
                if symbol is None:
                    return []
                target = definitions.get((symbol.module, symbol.name))
                if target is None:
                    return []
                if any(s.name == "elements" for s in node.sections):
                    return []  # Per-element holes need a separate expansion policy.
                bound = set()
                group = target.metadata.get("interchangeable")
                if group:
                    bound.update(group.value[: len(node.positional)])
                if node.wildcard:
                    bound.update(target.interface)
                for section in node.sections:
                    if section.name == "connections":
                        for binding in section.entries:
                            for name in resolver.keys(binding.endpoint.head):
                                bound.add(target.aliases.get(name, name))
                supplied = {
                    arg.name.text for s in node.sections if s.name == "params" for arg in s.entries
                }
                pins = []
                for name, pin in sorted(target.interface.items()):
                    if name in bound or (
                        pin.metadata.get("role")
                        and pin.metadata["role"].value == ("do_not_connect",)
                    ):
                        continue
                    spelling = (
                        name
                        if re.fullmatch(r"[A-Za-z_#][A-Za-z0-9_#]*|[0-9]+", name)
                        else json.dumps(name)
                    )
                    unused = pin.metadata.get("unused")
                    pins.append((spelling, unused is None or unused.value != "always"))
                parameters = [
                    (name, param.default is None)
                    for name, param in sorted(target.parameters.items())
                    if name not in supplied
                ]
                actions = []
                for title, expanded in [
                    ("Add required connections and parameters", False),
                    ("Add all connections and parameters", True),
                ]:
                    selected_pins = [name for name, required in pins if required or expanded]
                    selected_params = [
                        (name, required) for name, required in parameters if required or expanded
                    ]
                    edits = action_edits(
                        text, node.span.start_pos, selected_pins, selected_params, False
                    )
                    if edits:
                        actions.append({"title": title, "edits": edits})
                if not any(s.name == "metadata" for s in node.sections):
                    edits = action_edits(text, node.span.start_pos, [], [], True)
                    if edits:
                        actions.append({"title": "Add metadata template", "edits": edits})
                return actions
    except (DiagnosticError, OSError, UnicodeError):
        return []
    return []
