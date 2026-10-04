"""Contextual completion with bounded recovery of the definition being edited."""

from dataclasses import dataclass
import json
import re

from .diagnostics import DiagnosticError
from .parser import parse
from .lsp_bundles import BundleLookup
from .metadata import NETTYPES, ROLES
from .project import IDENTIFIER, load_project
from .resolve import Resolver
from .syntax import comment_lexer

WORD = r"[A-Za-z0-9_#]*"
TYPE_PATH = rf"{IDENTIFIER}(?:\.{IDENTIFIER})*"
TYPE_PREFIX = r"[A-Za-z0-9_#.]*"
DEFINITION = re.compile(rf"^(component|block|board|bundle)\s+({IDENTIFIER})(?=\s|:)[^\n]*:", re.M)
INSTANCE = re.compile(rf"^(\s*)inst\s+{IDENTIFIER}(?:\[[^\n]*?\])*\s*:\s*({TYPE_PATH})\s*\(")


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


def footprint_completion(path, config, sources, offset):
    """Complete only a direct metadata.footprint value, including an open quote."""
    source = sources[path]
    line_start = source.rfind("\n", 0, offset) + 1
    line_end = source.find("\n", offset)
    if line_end < 0:
        line_end = len(source)
    line = source[line_start:line_end]
    match = re.match(r'(\s*)footprint:\s*("([^"\\]|\\.)*"?)?\s*(?://.*)?$', line)
    if not match:
        return []
    value_start = line_start + line.index(":") + 1
    while value_start < line_end and source[value_start].isspace():
        value_start += 1
    if offset < value_start:
        return []
    raw = match[2] or ""
    value_end = value_start + len(raw)
    if offset > value_end or (raw.endswith('"') and len(raw) > 1 and offset == value_end):
        return []
    # Remove the value before masking so incomplete quotes cannot swallow context.
    safe = source[:value_start] + " " * len(raw) + source[value_end:]
    masked = masked_source(safe, value_start)
    if masked is None:
        return []
    owners = list(DEFINITION.finditer(masked, 0, line_start))
    if not owners:
        return []
    owner = owners[-1]
    # Track indentation ancestors to exclude custom fields and pin metadata.
    ancestors = []
    for previous in masked[owner.end() : line_start].splitlines():
        if not previous.strip():
            continue
        indent = len(previous) - len(previous.lstrip())
        while ancestors and ancestors[-1][0] >= indent:
            ancestors.pop()
        ancestors.append((indent, previous.strip()))
    indent = len(match[1])
    while ancestors and ancestors[-1][0] >= indent:
        ancestors.pop()
    if not ancestors or ancestors[-1][1] != "metadata:":
        return []
    active = (
        INSTANCE.match(" " * ancestors[-2][0] + ancestors[-2][1]) if len(ancestors) >= 2 else None
    )
    if active is None and (owner[1] != "component" or len(ancestors) != 1):
        return []
    if active:
        next_owner = DEFINITION.search(masked, owner.end())
        stop = next_owner.start() if next_owner else len(source)
        recovered = (
            source[: owner.start()]
            + f"{owner[1]} {owner[2]}:\n    metadata:\n        custom:\n            completion: true\n"
            + source[stop:]
        )
        target_name = active[2]
    else:
        recovered = source[:value_start] + '""' + source[value_end:]
        target_name = owner[2]
    try:
        project = load_project(config, sources={**sources, path: recovered})
        module = next(m for m in project.modules.values() if m.path == path)
        symbol = module.symbols.get(target_name)
        if symbol is None:
            return []
        definitions = Resolver(project).resolve_interfaces()
        target = definitions[(symbol.module, symbol.name)]
        if target.kind != "component":
            return []
        default = target.metadata.get("footprint")
        if not active:
            # The edited field masks inheritance; fetch the base's default separately.
            base = re.search(
                r"\bextends\s+(" + TYPE_PATH + ")", source[owner.start() : owner.end()]
            )
            if base and (base_symbol := module.symbols.get(base[1])):
                default = definitions[(base_symbol.module, base_symbol.name)].metadata.get(
                    "footprint"
                )
        names = [default.value] if default and default.value else []
        custom = target.metadata.get("custom")
        candidates = custom.value.get("kicad_footprint_candidates") if custom else None
        if candidates and isinstance(candidates.value, tuple):
            names.extend(sorted(v for v in candidates.value if isinstance(v, str)))
        prefix = source[value_start:offset]
        if prefix.startswith('"'):
            prefix = prefix[1:]
        # Decode escaped characters without requiring a closing quote in the buffer.
        try:
            prefix = json.loads('"' + prefix + '"')
        except ValueError:
            return []
        return [
            Suggestion(
                name,
                json.dumps(name),
                f"{target.name}: "
                + (
                    "default footprint"
                    if default and name == default.value
                    else "footprint hint (verify package dimensions)"
                ),
                "footprint",
                value_start,
                value_end,
            )
            for name in dict.fromkeys(names)
            if name.startswith(prefix)
        ]
    except (DiagnosticError, OSError, UnicodeError):
        return []


def connection_instance(masked, owner, line_start):
    """Find the enclosing instance when editing its connections section."""
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
    return active if section == "connections" else None


def bundle_completion(path, config, sources, offset, masked):
    source = sources[path]
    line_start = source.rfind("\n", 0, offset) + 1
    owners = list(DEFINITION.finditer(masked, 0, line_start))
    if not owners:
        return []
    owner = owners[-1]
    if owner[1] not in {"board", "block"}:
        return []
    active = connection_instance(masked, owner, line_start)
    if active is None:
        return []
    before = masked[line_start:offset].strip()
    # A slice's colon belongs to its index, not the connection separator.
    depth, separator = 0, None
    for i, char in enumerate(before):
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        elif char == ":" and depth == 0:
            separator = i
            break
    ports_only = separator is None
    reference = before if ports_only else before[separator + 1 :].strip()
    if ports_only:
        reference = re.sub(r"^(?:\[[^\]]+\])+\.", "", reference)
    match = re.fullmatch(rf"(.+)\.({WORD})", reference)
    if not match:
        return []
    try:
        # Reuse the real expression parser for nested members and array selectors.
        fragment = parse("board Completion:\n    net value: " + match[1] + "\n")
        expr = fragment.items[0].body[0].type
        next_owner = DEFINITION.search(masked, owner.end())
        stop = next_owner.start() if next_owner else len(source)
        # Preserve this owner's interfaces/nets, but remove instance bodies, which
        # may be unfinished. Resolve public types without elaborating connections.
        kept, instance_indent = [], None
        raw_lines = source[owner.end() : stop].splitlines(keepends=True)
        for raw, line in zip(raw_lines, masked[owner.end() : stop].splitlines(keepends=True)):
            indent = len(line) - len(line.lstrip())
            if instance_indent is not None and line.strip() and indent <= instance_indent:
                if line.strip().startswith(")"):
                    continue
                instance_indent = None
            if INSTANCE.match(line):
                instance_indent = indent
            if instance_indent is None:
                kept.append(raw)
        dummy = "__lsp_completion"
        while dummy in source:
            dummy += "_"
        recovered = (
            source[: owner.end()]
            + f"\n    inst {dummy}: {dummy}()\n"
            + "".join(kept)
            + source[stop:]
        )
        project = load_project(config, sources={**sources, path: recovered})
        module = next(m for m in project.modules.values() if m.path == path)
        definitions = Resolver(project).resolve_interfaces()
        context = definitions[(module.name, owner[2])]
        if ports_only:
            symbol = module.symbols.get(active[2])
            if symbol is None or symbol.declaration is None:
                return []
            context = definitions[(symbol.module, symbol.name)]
        bundles = BundleLookup(project, definitions)
        value = bundles.reference_type(expr, context, ports_only=ports_only)
        word = match[2]
        end = offset + len(re.match(WORD, masked[offset:]).group())
        return [
            Suggestion(name, name, f"bundle field {name}", "pin", offset - len(word), end)
            for name in sorted(bundles.members(value))
            if name.startswith(word)
        ]
    except (DiagnosticError, OSError, UnicodeError):
        return []


def completion_items(path, config, sources, offset):
    source = sources[path]
    if config is not None:
        footprints = footprint_completion(path, config, sources, offset)
        if footprints:
            return footprints
    masked = masked_source(source, offset)
    if masked is None or config is None:
        return []
    bundles = bundle_completion(path, config, sources, offset, masked)
    if bundles:
        return bundles
    line_start = source.rfind("\n", 0, offset) + 1
    before = masked[line_start:offset]
    tail = re.match(WORD, masked[offset:]).group()
    word = re.search(WORD + r"$", before).group()
    start, end = offset - len(word), offset + len(tail)
    namespace = re.search(rf"(?<![A-Za-z0-9_#.])({TYPE_PATH})\.({WORD})$", before)
    instance_type = re.fullmatch(
        rf"\s*inst\s+{IDENTIFIER}(?:\[[^\n]*?\])*\s*:\s*{TYPE_PREFIX}", before
    )
    base_type = re.compile(
        rf"(component|block)[ \t]+({IDENTIFIER})[ \t]+extends[ \t]+{TYPE_PREFIX}$"
    ).match(masked, line_start, offset)
    if instance_type or base_type:
        word = re.search(TYPE_PREFIX + r"$", before).group()
        tail = re.match(TYPE_PREFIX, masked[offset:]).group()
        start, end = offset - len(word), offset + len(tail)
        namespace = None
    pin = re.fullmatch(rf"\s*(?:\[[^\n]*?\]\.)?{WORD}", before)
    pin_list = re.fullmatch(rf"\s*(?:\[[^\n]*?\]\.)?\[[^\]:]*{WORD}", before)
    if not (namespace or instance_type or base_type or pin or pin_list):
        return []

    owners = list(DEFINITION.finditer(masked, 0, offset))
    if not owners and not base_type:
        return []
    owner = base_type or owners[-1]
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
            if (
                imported is None
                or imported.module != "noscheme"
                or imported.name not in {"role", "nettype"}
            ):
                return []
            choices = [
                (v, v, f"{name}.{v}", "enum")
                for v in (ROLES if imported.name == "role" else NETTYPES)
            ]
        elif instance_type or base_type:
            if instance_type and owner[1] not in {"board", "block"}:
                return []
            kinds = {owner[1]} if base_type else {"component", "block"}
            choices = [
                (name, name, f"{symbol.declaration.kind} {symbol.module}.{symbol.name}", "type")
                for name, symbol in module.symbols.items()
                if symbol.declaration
                and symbol.declaration.kind in kinds
                and not (base_type and symbol.module == module.name and symbol.name == owner[2])
            ]
        else:
            active = connection_instance(masked, owner, line_start)
            if active is None:
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
