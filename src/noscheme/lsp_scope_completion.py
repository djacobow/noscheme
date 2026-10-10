"""Completion for lexical values and schema-defined metadata, even in unfinished lines."""

import re

from . import ast as a
from .diagnostics import DiagnosticError
from .lsp_completion import (
    ARGUMENTS,
    DECLARATION,
    open_parameters,
    owner_before,
    recovered_project,
)
from .metadata import SCHEMAS, ROLES, NETTYPES, UNUSED, BIAS_VALUES
from .project import net_type
from .resolve import Resolver

NAME = r"[A-Za-z_#][A-Za-z0-9_#]*"
PATH = r"[A-Za-z0-9_#.]*"
ENUMS = {"roles": "role", "role": "role", "unused": "unused", "bias": "bias"}
VALUES = {"role": ROLES, "net": NETTYPES, "unused": UNUSED, "bias": BIAS_VALUES}
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
# A typed params or ports entry, `number gain = 2`, or an untyped specialization port.
TYPED = re.compile(rf"(?:[A-Za-z0-9_#.]+[ \t]+)?({NAME})\b[^:\n]*(?::|$|=)")


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


def lexical_names(masked, owner, stop, symbols):
    """Names declared directly in this definition, never instance internals."""
    nets, parameters = set(), set()
    section = None
    section_indent = 0
    body_indent = None
    for line in masked[owner.end : stop].splitlines():
        text = line.strip()
        if not text:
            continue
        indent = len(line) - len(line.lstrip())
        if body_indent is None:
            body_indent = indent
        if indent == body_indent:
            section = text if text in {"params:", "ports:"} else None
            section_indent = None
            match = DECLARATION.match(line)
            if match and net_type(symbols, match[2]):
                global_ = "global." if text.startswith("global") else ""
                names = re.findall(rf"(?:^|,)\s*({NAME})", line[match.start(3) :])
                nets.update(global_ + n for n in names)
        elif section:
            if section_indent is None:
                section_indent = indent
            match = TYPED.match(text)
            if indent == section_indent and match:
                (parameters if section == "params:" else nets).add(match[1])
    return nets, parameters


def scope_completion(path, config, sources, offset, masked, *, expression=False):
    from .lsp_completion import Suggestion, current_argument

    owner, stop = owner_before(masked, offset)
    if owner is None:
        return []
    parents, line = ancestors(masked, offset)
    word = re.search(PATH + r"$", masked[:offset]).group()
    start = offset - len(word)
    end = offset + len(re.match(PATH, masked[offset:]).group())
    mode, scope, field = "expression" if expression else None, None, None
    # An open connection list can span several lines. Only its current argument
    # decides whether this is a connection target or an index expression.
    instances = list(ARGUMENTS.finditer(masked, owner.end, offset))
    if instances:
        args = masked[instances[-1].end() : offset]
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
            _, _, argument = current_argument(args)
            if "[" in argument:
                mode = "expression"
            elif ":" in argument:
                mode = "target"
    # `<...>` values are expressions in the caller's scope.
    parameters = open_parameters(masked, offset)
    if mode is None and parameters and ":" in masked[parameters[0] : offset].rsplit(",", 1)[-1]:
        mode = "expression"
    # The body-level line enclosing the cursor: a declaration of a net or an instance.
    declaration = DECLARATION.match("  " + parents[1]) if len(parents) >= 2 else None
    possible = mode is not None or (
        "custom:" not in parents
        and (
            declaration is not None
            or "metadata:" in parents
            or owner.kind == "extend"
            or parents[-1:] == ["params:"]
            or len(parents) >= 2
            and parents[-2] in {"pins:", "ports:"}
            or line.count("[") > line.count("]")
        )
    )
    if not possible:
        return []
    try:
        project, module = recovered_project(path, config, sources, masked, owner)
    except (DiagnosticError, OSError, UnicodeError):
        return []
    if module is None:
        return []
    symbols = module.symbols
    is_net = declaration is not None and net_type(symbols, declaration[2])
    in_instance = declaration is not None and not is_net
    if (
        mode is None
        and line.count("[") > line.count("]")
        and (DECLARATION.match("  " + line) or ":" in line)
    ):
        mode = "expression"
    if (
        mode is None
        and (parents and parents[-1] == "params:")
        and ("=" in line or (in_instance and ":" in line))
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
        kind = owner.kind
        if kind == "extend" and owner.name in symbols and symbols[owner.name].declaration:
            kind = symbols[owner.name].declaration.kind
        if "metadata:" in parents and parents[-1] == "metadata:":
            scope = "instance" if in_instance else kind
        elif is_net and parents[-1] == parents[1] or kind == "nettype" and len(parents) == 1:
            scope = "net"
        elif len(parents) >= 2 and parents[-2] in {"pins:", "ports:"}:
            scope = "instance_pin" if in_instance else ("pin" if parents[-2] == "pins:" else "port")
    if mode is None and scope is None:
        return []
    try:
        choices = []
        nets, parameters = lexical_names(masked, owner, stop, symbols)
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
                        for alias, symbol in symbols.items()
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
            if owner.base and owner.base in symbols:
                symbol = symbols[owner.base]
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
                    for alias, symbol in symbols.items()
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
