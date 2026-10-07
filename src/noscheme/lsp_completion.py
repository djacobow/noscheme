"""Contextual completion with bounded recovery of the definition being edited."""

from dataclasses import dataclass, replace
import json
import re

from .diagnostics import DiagnosticError
from .formatter import INDENT
from .parser import parse
from .lsp_bundles import BundleLookup
from .erc_registry import namespace, signature
from .metadata import MISSING, NETTYPES, PIN_SCOPES, ROLES, endpoint_fields
from .project import ERC_MODULE, IDENTIFIER, load_project
from .resolve import Resolver
from .syntax import comment_lexer

WORD = r"[A-Za-z0-9_#]*"
TYPE_PATH = rf"{IDENTIFIER}(?:\.{IDENTIFIER})*"
TYPE_PREFIX = r"[A-Za-z0-9_#.]*"
DEFINITION = re.compile(
    rf"^(component|block|board|bundle|nettype)\s+({IDENTIFIER})(?=\s|:)[^\n]*:", re.M
)
INSTANCE = re.compile(rf"^([ \t]*)inst\s+{IDENTIFIER}(?:\[[^\n]*?\])*\s*:\s*({TYPE_PATH})")
# An instance line whose argument list is open at the end of the match.
ARGUMENTS = re.compile(INSTANCE.pattern + r"\s*\(")


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
        # The placeholder instance takes the body's own indentation.
        body = re.search(r"\n([ \t]+)\S", masked[owner.end() : stop])
        recovered = (
            source[: owner.end()]
            + f"\n{body[1] if body else INDENT}inst {dummy}: {dummy}\n"
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


def registered_spellings(module, names):
    """Map the source spellings that reach registered names through this module's imports."""
    result = {}
    for key, symbol in module.symbols.items():
        if symbol.module == "noscheme" and symbol.name == "std":
            for name in names:
                if namespace(name) == "std":
                    result[key + name.removeprefix("std")] = name
        elif symbol.module == ERC_MODULE and symbol.name in names:
            result[key] = symbol.name
    return result


def current_argument(arguments):
    """Return (positional index, keyword or None, text) of the argument being typed."""
    depth, start, index = 0, 0, 0
    for position, char in enumerate(arguments):
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif char == "," and depth == 0:
            start, index = position + 1, index + 1
    text = arguments[start:]
    keyword = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", text)
    return index, keyword and keyword[1], text


def erc_completion(path, config, sources, offset):
    """Complete rule names, parameter keywords, and typed arguments in check calls."""
    source = sources[path]
    masked = masked_source(source, offset)
    owners = list(DEFINITION.finditer(masked or "", 0, offset))
    if masked is None or not owners:
        return []
    ancestors = []
    pos = 0
    for previous in masked[:offset].splitlines(keepends=True):
        if previous.strip():
            depth = len(previous) - len(previous.lstrip())
            while ancestors and ancestors[-1][0] >= depth:
                ancestors.pop()
            ancestors.append((depth, previous.strip(), pos))
        pos += len(previous)
    names = [a[1] for a in ancestors]
    if "checks:" not in names or "custom:" in names[names.index("checks:") :]:
        return []
    checks = names.index("checks:")
    if len(ancestors) < checks + 2:
        return []
    application = ancestors[checks + 1][2]
    colon = masked.find(":", application, offset)
    if colon < 0:
        return []
    text = masked[colon + 1 : offset]
    word = re.search(r"[A-Za-z0-9_#.]*$", text).group()
    start = offset - len(word)
    end = offset + len(re.match(r"[A-Za-z0-9_#]*", masked[offset:]).group())
    try:
        project, module = recovered_project(path, config, sources, masked, owners[-1])
    except (DiagnosticError, OSError, UnicodeError, ValueError):
        return []
    if module is None:
        return []
    registry = project.config.erc_registry
    # Pins (definition or instance overlays) take pin rules; nets, ports, and fields take net rules.
    scope = "pin" if names[checks - 2 : checks - 1] == ["pins:"] else "net"
    rules = registered_spellings(module, registry.rules)
    choices = []
    if re.fullmatch(r"\s*[A-Za-z0-9_#.]*", text):
        choices = [
            (spelling, spelling, signature(name, registry.rules[name]).split("\n")[0], "function")
            for spelling, name in rules.items()
            if registry.rules[name].scope == scope
        ]
    elif call := re.fullmatch(r"\s*([A-Za-z0-9_#.]+)\s*\((.*)", text, re.S):
        rule_name = rules.get(call[1])
        if rule_name is None:
            return []
        rule = registry.rules[rule_name]
        index, keyword, argument = current_argument(call[2])
        if keyword is None and re.fullmatch(r"\s*[A-Za-z0-9_#]*", argument):
            used = set(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*=", call[2]))
            choices = [
                (
                    name,
                    name + "=",
                    str(rule.parameters.get(name, "missing-data policy")),
                    "property",
                )
                for name in [*list(rule.parameters)[index:], "missing"]
                if name not in used
            ]
        kind = (
            "missing"
            if keyword == "missing"
            else rule.parameters.get(keyword)
            if keyword
            else list(rule.parameters.values())[index]
            if index < len(rule.parameters)
            else None
        )
        if kind == "selector":
            predicates = registered_spellings(module, registry.predicates)
            choices += [(s, s, "predicate", "function") for s in predicates]
            choices += [
                (n, n, "boolean field", "field")
                for n, k in endpoint_fields(project).items()
                if k == "bool"
            ]
        elif kind in {"field", "net_fields"}:
            fields = endpoint_fields(project, ("net",) if kind == "net_fields" else PIN_SCOPES)
            choices += [(n, n, str(k), "field") for n, k in fields.items() if isinstance(k, str)]
        elif kind == "missing":
            choices += [(json.dumps(v), json.dumps(v), "policy", "value") for v in MISSING]
    return [
        Suggestion(label, text, detail, kind, start, end)
        for label, text, detail, kind in sorted(set(choices))
        if label.startswith(word)
    ]


def recovered_project(path, config, sources, masked, owner):
    """Load the project with the definition under the cursor replaced by a stub.

    This lets incomplete lines, sections and parentheses coexist with the real
    import graph and component definitions, without stale ASTs.
    """
    source = sources[path]
    next_owner = DEFINITION.search(masked, owner.end())
    stop = next_owner.start() if next_owner else len(source)
    recovered = (
        source[: owner.start()]
        + f"{owner[1]} {owner[2]}:\n    metadata:\n        custom:\n            completion: true\n"
        + source[stop:]
    )
    project = load_project(config, sources={**sources, path: recovered})
    return project, next((m for m in project.modules.values() if m.path == path), None)


def inline_completion(path, config, sources, offset, masked):
    """Offer pins/parameters and bundle members inside an instance's argument list."""
    owners = list(DEFINITION.finditer(masked, 0, offset))
    if not owners or owners[-1][1] not in {"board", "block"}:
        return []
    owner = owners[-1]
    instances = list(re.finditer(ARGUMENTS.pattern, masked[owner.end() : offset], re.M))
    if not instances:
        return []
    active = instances[-1]
    opening = owner.end() + active.end()
    arguments = masked[opening:offset]
    depth = 0
    for char in arguments:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        if depth < 0:
            return []  # The instance has already closed.
    _, keyword, argument = current_argument(arguments)
    if keyword or depth:
        return []
    argument = argument.strip()
    # Reuse bundle recovery with a virtual section-form connection. All text to
    # the right of this insertion has the same offset delta, including the cursor.
    if "." in argument:
        reference = re.sub(rf"^(?:{IDENTIFIER}|[0-9]+)?\s*:\s*", "", argument)
        if re.fullmatch(rf".+\.{WORD}", reference):
            prefix = f"\n{active[1]}{INDENT}connections:\n{active[1]}{INDENT * 2}1: "
            source = sources[path]
            reference_start = offset - len(reference)
            virtual = source[:opening] + prefix + source[reference_start:]
            delta = opening + len(prefix) - reference_start
            virtual_masked = masked_source(virtual, offset + delta)
            if virtual_masked is not None:
                return [
                    replace(item, start=item.start - delta, end=item.end - delta)
                    for item in bundle_completion(
                        path, config, {**sources, path: virtual}, offset + delta, virtual_masked
                    )
                ]
    if not re.fullmatch(WORD, argument):
        return []
    try:
        project, module = recovered_project(path, config, sources, masked, owner)
        symbol = module.symbols.get(active[2]) if module else None
        if symbol is None or symbol.declaration is None:
            return []
        target = Resolver(project).resolve_interfaces()[(symbol.module, symbol.name)]
        tail = re.match(WORD, masked[offset:]).group()
        start, end = offset - len(argument), offset + len(tail)
        suffix = masked[end:].lstrip()
        choices = []
        for alias, canonical in target.aliases.items():
            spelling = alias if re.fullmatch(IDENTIFIER + r"|[0-9]+", alias) else json.dumps(alias)
            choices.append(
                (
                    alias,
                    spelling + ("" if suffix.startswith(":") else ": "),
                    f"{target.name}: pin/port {canonical}",
                    "pin",
                )
            )
        choices.extend(
            (
                name,
                name + ("" if suffix.startswith("=") else "="),
                f"{target.name}: parameter {name}",
                "parameter",
            )
            for name in target.parameters
        )
        return [
            Suggestion(label, text, detail, kind, start, end)
            for label, text, detail, kind in sorted(choices)
            if label.startswith(argument)
        ]
    except (DiagnosticError, OSError, UnicodeError):
        return []


def completion_items(path, config, sources, offset):
    source = sources[path]
    if config is not None:
        electrical = erc_completion(path, config, sources, offset)
        if electrical:
            return electrical
        footprints = footprint_completion(path, config, sources, offset)
        if footprints:
            return footprints
    masked = masked_source(source, offset)
    if masked is None or config is None:
        return []
    inline = inline_completion(path, config, sources, offset, masked)
    if inline:
        return inline
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
        rf"(component|block|nettype)[ \t]+({IDENTIFIER})[ \t]+extends[ \t]+{TYPE_PREFIX}$"
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
    try:
        project, module = recovered_project(path, config, sources, masked, owner)
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
