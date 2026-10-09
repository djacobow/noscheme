"""Completion for lexical values and schema-defined metadata, even in unfinished lines."""

import re

from . import ast as a
from .diagnostics import DiagnosticError
from .metadata import SCHEMAS, ROLES, NETTYPES, UNUSED, BIAS_VALUES
from .resolve import Resolver

NAME = r"[A-Za-z_#][A-Za-z0-9_#]*"
PATH = r"[A-Za-z0-9_#.]*"
DEFINITION = re.compile(rf"^(component|block|board|bundle|nettype)\s+({NAME})[^\n]*:", re.M)
ENUMS = {"roles": "role", "role": "role", "unused": "unused", "bias": "bias"}
VALUES = {"role": ROLES, "nettype": NETTYPES, "unused": UNUSED, "bias": BIAS_VALUES}
NUMERIC = {
    "expression",
    "number",
    "positive",
    "nonnegative",
    "current",
    "voltage",
    "capacitance",
    "resistance",
}


def ancestors(masked, offset):
    """Indentation ancestors and the current line, without strings or comments."""
    start = masked.rfind("\n", 0, offset) + 1
    before = masked[start:offset]
    indent = len(before) - len(before.lstrip())
    stack = []
    for line in masked[:start].splitlines():
        if not line.strip():
            continue
        depth = len(line) - len(line.lstrip())
        while stack and stack[-1][0] >= depth:
            stack.pop()
        stack.append((depth, line.strip()))
    while stack and stack[-1][0] >= indent:
        stack.pop()
    return [text for _, text in stack], before.strip()


def lexical_names(masked, owner, stop):
    """Names declared directly in this definition, never instance internals."""
    nets, parameters = set(), set()
    section = None
    section_indent = 0
    body_indent = None
    for line in masked[owner.end() : stop].splitlines():
        text = line.strip()
        if not text:
            continue
        indent = len(line) - len(line.lstrip())
        if body_indent is None:
            body_indent = indent
        if indent == body_indent:
            section = text if text in {"params:", "ports:"} else None
            section_indent = None
            match = re.match(rf"(global\s+)?net\s+({NAME}(?:\s*,\s*{NAME})*)\s*:", text)
            if match:
                nets.update(
                    ("global." if match[1] else "") + n.strip() for n in match[2].split(",")
                )
        elif section:
            if section_indent is None:
                section_indent = indent
            match = re.match(rf"({NAME})\s*:", text)
            if indent == section_indent and match:
                (parameters if section == "params:" else nets).add(match[1])
    return nets, parameters


def scope_completion(path, config, sources, offset, masked, *, expression=False):
    from .lsp_completion import Suggestion, recovered_project, current_argument

    owners = list(DEFINITION.finditer(masked, 0, offset))
    if not owners:
        return []
    owner = owners[-1]
    next_owner = DEFINITION.search(masked, owner.end())
    stop = next_owner.start() if next_owner else len(masked)
    parents, line = ancestors(masked, offset)
    word = re.search(PATH + r"$", masked[:offset]).group()
    start = offset - len(word)
    end = offset + len(re.match(PATH, masked[offset:]).group())
    mode, scope, field = "expression" if expression else None, None, None
    # An open argument list can span several lines. Only its current argument
    # decides whether this is a connection target or a parameter expression.
    instances = list(
        re.finditer(
            rf"\binst\s+{NAME}(?:\[[^\n]*?\])*\s*:\s*[A-Za-z0-9_#.]+\s*\(",
            masked[owner.end() : offset],
        )
    )
    if instances:
        args = masked[owner.end() + instances[-1].end() : offset]
        depth = 0
        closed = False
        for char in args:
            if char in "([":
                depth += 1
            elif char in ")]":
                depth -= 1
            if depth < 0:
                closed = True
                break
        if not closed:
            _, keyword, argument = current_argument(args)
            if keyword or "[" in argument:
                mode = "expression"
            elif ":" in argument:
                mode = "target"
    in_instance = any(p.startswith("inst ") for p in parents)
    if (
        mode is None
        and line.count("[") > line.count("]")
        and (line.startswith("inst ") or ":" in line)
    ):
        mode = "expression"
    if (
        mode is None
        and (parents and parents[-1] in {"params:", "defaults:"})
        and (
            "=" in line
            or (in_instance and ":" in line)
            or parents[-1] == "defaults:"
            and ":" in line
        )
    ):
        mode = "expression"
    if (
        mode is None
        and in_instance
        and ":" in line
        and not any(p in {"metadata:", "params:", "elements:"} for p in parents)
    ):
        mode = "target"
    if "custom:" not in parents:
        if "metadata:" in parents and parents[-1] == "metadata:":
            scope = "instance" if in_instance else owner[1]
        elif parents and re.match(r"(?:global\s+)?net\s", parents[-1]):
            scope = "net"
        elif len(parents) >= 2 and parents[-2] in {"pins:", "ports:"}:
            scope = "instance_pin" if in_instance else ("pin" if parents[-2] == "pins:" else "port")
    if mode is None and scope is None:
        return []
    try:
        project, module = recovered_project(path, config, sources, masked, owner)
        if module is None:
            return []
        choices = []
        nets, parameters = lexical_names(masked, owner, stop)
        locals_ = nets | parameters
        if scope:
            schema = SCHEMAS.get(scope, {}) | project.config.erc_registry.schemas.get(scope, {})
            if ":" not in line:
                suffix = "" if masked[end:].lstrip().startswith(":") else ": "
                choices = [
                    (name, name + suffix, str(kind), "property") for name, kind in schema.items()
                ]
                mode = "keys"
            else:
                field = line.split(":", 1)[0].strip()
                kind = schema.get(field)
                if isinstance(kind, tuple) or (isinstance(kind, str) and kind in ENUMS):
                    namespace = kind[0] if isinstance(kind, tuple) else ENUMS[kind]
                    values = kind[1] if isinstance(kind, tuple) else VALUES[namespace]
                    choices = [
                        (f"{alias}.{value}", f"{alias}.{value}", namespace, "enum")
                        for alias, symbol in module.symbols.items()
                        if symbol.module == "noscheme"
                        and symbol.name == namespace
                        and alias.split(".")[0] not in locals_
                        for value in values
                    ]
                    mode = "enum"
                elif kind == "bool":
                    choices = [(v, v, "boolean", "value") for v in ("false", "true")]
                    mode = "enum"
                elif isinstance(kind, str) and kind in NUMERIC:
                    mode = "expression"
        if mode in {"target", "expression"}:
            base = re.search(r"\bextends\s+([A-Za-z0-9_#.]+)", owner.group())
            if base and base[1] in module.symbols:
                symbol = module.symbols[base[1]]
                definitions = Resolver(project).resolve_interfaces()
                parent = definitions.get((symbol.module, symbol.name))
                if parent:
                    parameters.update(parent.parameters)
                    if parent.kind == "block":
                        nets.update(parent.interface)
            if mode == "target":
                for node in project.board.declaration.body:
                    if isinstance(node, a.Net) and node.global_:
                        nets.add("global." + node.name.text)
                choices = [(n, n, "connection target", "variable") for n in nets]
            else:
                choices = [(n, n, "local parameter", "variable") for n in parameters]
                choices += [
                    (f"{alias}.{v}", f"{alias}.{v}", "numeric helper", "function")
                    for alias, symbol in module.symbols.items()
                    if symbol.module == "noscheme"
                    and symbol.name == "num"
                    and alias.split(".")[0] not in nets | parameters
                    for v in ("int", "ceil", "floor", "round")
                ]
        qualifier = word.rsplit(".", 1)[0] + "." if "." in word else ""
        return [
            Suggestion(
                label[len(qualifier) :],
                text[len(qualifier) :],
                detail,
                kind,
                start + len(qualifier),
                end,
            )
            for label, text, detail, kind in sorted(choices)
            if label.startswith(word)
        ]
    except (DiagnosticError, OSError, UnicodeError):
        return []
