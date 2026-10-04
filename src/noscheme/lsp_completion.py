"""Contextual completion with bounded recovery of the definition being edited."""

from dataclasses import dataclass
import json
import re

from .diagnostics import DiagnosticError
from .metadata import NETTYPES, ROLES
from .project import IDENTIFIER, load_project
from .resolve import Resolver
from .syntax import comment_lexer

WORD = r"[A-Za-z0-9_#]*"
DEFINITION = re.compile(rf"^(component|block|board|bundle)\s+({IDENTIFIER})(?=\s|:)[^\n]*:", re.M)
INSTANCE = re.compile(rf"^(\s*)inst\s+{IDENTIFIER}(?:\[[^\n]*?\])*\s*:\s*({IDENTIFIER})\s*\(")


@dataclass(frozen=True)
class Suggestion:
    label: str
    text: str
    detail: str
    kind: str
    start: int
    end: int


def masked_source(source, offset):
    """Hide comments and strings, retaining offsets and rejecting their contents."""
    masked = list(source)
    for token in comment_lexer().lex(source):
        if token.type in {"STRING", "LINE_COMMENT", "BLOCK_COMMENT", "UNCLOSED_BLOCK"}:
            # At an unfinished comment's end the cursor is still inside it.
            end_inside = token.type in {"LINE_COMMENT", "UNCLOSED_BLOCK"}
            if token.start_pos <= offset < token.end_pos or end_inside and offset == token.end_pos:
                return None
            masked[token.start_pos : token.end_pos] = ["\n" if c == "\n" else " " for c in token]
    # The comment lexer deliberately doesn't treat unfinished strings as strings.
    if '"' in "".join(masked[:offset]).rsplit("\n", 1)[-1]:
        return None
    return "".join(masked)


def completion_items(path, config, sources, offset):
    source = sources[path]
    masked = masked_source(source, offset)
    if masked is None or config is None:
        return []
    line_start = source.rfind("\n", 0, offset) + 1
    before = masked[line_start:offset]
    tail = re.match(WORD, masked[offset:]).group()
    word = re.search(WORD + r"$", before).group()
    start, end = offset - len(word), offset + len(tail)
    namespace = re.search(rf"(?<![A-Za-z0-9_#.])(role|nettype)\.({WORD})$", before)
    instance_type = re.fullmatch(rf"\s*inst\s+{IDENTIFIER}(?:\[[^\n]*?\])*\s*:\s*{WORD}", before)
    pin = re.fullmatch(rf"\s*(?:\[[^\n]*?\]\.)?{WORD}", before)
    if not (namespace or instance_type or pin):
        return []

    owners = list(DEFINITION.finditer(masked, 0, offset))
    if not owners:
        return []
    owner = owners[-1]
    # Only the definition under the cursor is replaced in a private analysis
    # snapshot. This lets incomplete lines, sections and parentheses coexist with
    # the real import graph and component definitions, without stale ASTs.
    next_owner = DEFINITION.search(masked, owner.end())
    stop = next_owner.start() if next_owner else len(source)
    recovered = (
        source[: owner.start()]
        + f"{owner[1]} {owner[2]}:\n    metadata:\n        custom:\n            completion: true\n"
        + source[stop:]
    )
    try:
        project = load_project(config, sources={**sources, path: recovered})
        module = next((m for m in project.modules.values() if m.path == path), None)
        if module is None:
            return []
        choices = []
        if namespace:
            name = namespace[1]
            imported = module.symbols.get(name)
            if imported is None or imported.module != "noscheme":
                return []
            choices = [
                (v, v, f"{name}.{v}", "enum") for v in (ROLES if name == "role" else NETTYPES)
            ]
        elif instance_type:
            if owner[1] not in {"board", "block"}:
                return []
            choices = [
                (name, name, f"{symbol.declaration.kind} {symbol.module}.{symbol.name}", "type")
                for name, symbol in module.symbols.items()
                if symbol.declaration and symbol.declaration.kind in {"component", "block"}
            ]
        else:
            # Locate the still-open instance and its current section by indentation.
            active, section = None, None
            for line in masked[owner.end() : line_start].splitlines():
                if match := INSTANCE.match(line):
                    active, section = match, None
                elif active and line.strip():
                    indent = len(line) - len(line.lstrip())
                    if indent <= len(active[1]):
                        active, section = None, None
                    elif re.fullmatch(r"\s*(connections|params|metadata):\s*", line):
                        section = line.strip().rstrip(":")
            if active is None or section != "connections":
                return []
            symbol = module.symbols.get(active[2])
            if symbol is None or symbol.declaration is None:
                return []
            definitions = Resolver(project).resolve_interfaces()
            target = definitions[(symbol.module, symbol.name)]
            for alias, canonical in target.aliases.items():
                # Numeric pin identifiers preserve spelling (including leading zeros).
                spelling = (
                    alias if re.fullmatch(IDENTIFIER + r"|[0-9]+", alias) else json.dumps(alias)
                )
                choices.append((alias, spelling, f"{target.name}: pin/port {canonical}", "pin"))
        return [
            Suggestion(label, text, detail, kind, start, end)
            for label, text, detail, kind in sorted(choices)
            if label.startswith(word)
        ]
    except (DiagnosticError, OSError, UnicodeError):
        return []
