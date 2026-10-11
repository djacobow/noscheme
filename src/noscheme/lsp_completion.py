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
from .project import ERC_MODULE, IDENTIFIER, load_project, net_type
from .resolve import Resolver
from .syntax import comment_lexer

WORD = r"[A-Za-z0-9_#]*"
TYPE_PATH = rf"{IDENTIFIER}(?:\.{IDENTIFIER})*"
TYPE_PREFIX = r"[A-Za-z0-9_#.]*"
# A definition header: `component Name:`, or `extend Base<params> Name`, on one line.
HEADER = re.compile(
    rf"^(?:(component|block|bundle)[ \t]+({IDENTIFIER})"
    rf"|extend[ \t]+({TYPE_PATH})(?:[ \t]*<[^\n]*>)?[ \t]+({IDENTIFIER}))"
    r"(?=[ \t]*(?::|$))[^\n]*",
    re.M,
)
# An indented `Type<params> name` line: a net or an instance, told apart by the type.
DECLARATION = re.compile(
    rf"^([ \t]+)(?:global[ \t]+)?({TYPE_PATH})(?:[ \t]*<[^\n]*?>)?[ \t]+({IDENTIFIER})", re.M
)
# A declaration whose connection list is open at the end of the match.
ARGUMENTS = re.compile(DECLARATION.pattern + r"(?:\[[^\n]*?\])*[ \t]*\(", re.M)
# The type, `<...>`, and names of a declaration line, without its connections.
SKELETON = re.compile(
    rf"([ \t]+)(global[ \t]+)?({TYPE_PATH})(?:[ \t]*<[^\n]*?>)?[ \t]+"
    rf"({IDENTIFIER}(?:\[[^\n]*?\])*(?:[ \t]*,[ \t]*{IDENTIFIER}(?:\[[^\n]*?\])*)*)"
)


@dataclass(frozen=True)
class Suggestion:
    label: str
    text: str
    detail: str
    kind: str
    start: int
    end: int


@dataclass(frozen=True)
class Owner:
    """A definition header in a masked buffer; an extension's kind is its base's."""

    kind: str  # component, block, bundle, or extend
    name: str
    base: str | None
    start: int
    end: int  # The end of the header line, before its newline.


def owners(masked, stop=None):
    return [
        Owner(m[1] or "extend", m[2] or m[4], m[3], m.start(), m.end())
        for m in HEADER.finditer(masked, 0, len(masked) if stop is None else stop)
    ]


def owner_before(masked, offset):
    """The definition whose header precedes offset, and where its region stops."""
    found = owners(masked, offset)
    if not found:
        return None, len(masked)
    owner = found[-1]
    following = HEADER.search(masked, owner.end)
    return owner, following.start() if following else len(masked)


def stub(owner):
    """A parseable stand-in for a definition whose body is being edited."""
    if owner.kind == "extend":
        return f"extend {owner.base} {owner.name}\n"
    return f"{owner.kind} {owner.name}:\n    metadata:\n        custom:\n            completion: true\n"


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
    owner, stop = owner_before(masked, line_start)
    if owner is None:
        return []
    # Track indentation ancestors to exclude custom fields and pin metadata.
    ancestors = []
    for previous in masked[owner.end : line_start].splitlines():
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
        DECLARATION.match(" " * ancestors[-2][0] + ancestors[-2][1])
        if len(ancestors) >= 2
        else None
    )
    if active is None and (owner.kind not in {"component", "extend"} or len(ancestors) != 1):
        return []
    if active:
        recovered = source[: owner.start] + stub(owner) + source[stop:]
        target_name = active[2]
    else:
        recovered = source[:value_start] + '""' + source[value_end:]
        target_name = owner.name
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
        # The edited field masks inheritance; fetch the base's default separately.
        if not active and owner.base and (base_symbol := module.symbols.get(owner.base)):
            default = definitions[(base_symbol.module, base_symbol.name)].metadata.get("footprint")
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


def body_indent(masked, owner):
    """The indentation of a definition's first body line, or None."""
    found = re.search(r"\n([ \t]+)\S", masked[owner.end :])
    return len(found[1]) if found else None


def connection_instance(masked, owner, line_start):
    """Find the enclosing declaration when editing its connections, which lead its body."""
    active, section = None, None
    body = body_indent(masked, owner)
    for line in masked[owner.end : line_start].splitlines():
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent == body:
            active = DECLARATION.match(line)
            section = "connections" if active else None
        elif active and indent > body and re.fullmatch(r"\s*(params|metadata|elements):\s*", line):
            section = line.strip().rstrip(":")
    current = masked[line_start:]
    if active and len(current) - len(current.lstrip(" ")) <= len(active[1]):
        return None  # The cursor's line is outside the declaration body.
    return active if section == "connections" else None


def skeleton(masked, source, owner, stop):
    """The owner's body with each declaration reduced to its type and names.

    Unfinished connections and bodies cannot break the parse; nets keep the
    types that bundle member lookups need, and sections stay as written.
    """
    kept, skipping = [], False
    body = body_indent(masked, owner)
    raw_lines = source[owner.end : stop].splitlines(keepends=True)
    for raw, line in zip(raw_lines, masked[owner.end : stop].splitlines(keepends=True)):
        indent = len(line) - len(line.lstrip())
        if line.strip() and (indent == body and not line.strip().startswith((")", ">"))):
            match = SKELETON.match(line)
            skipping = match is not None
            if match:
                kept.append(f"{match[1]}{match[2] or ''}{match[3]} {match[4]}\n")
                continue
        if not skipping:
            kept.append(raw)
    return "".join(kept)


def bundle_completion(path, config, sources, offset, masked):
    source = sources[path]
    line_start = source.rfind("\n", 0, offset) + 1
    owner, stop = owner_before(masked, line_start)
    if owner is None or owner.kind not in {"block"}:
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
        fragment = parse("component Completion:\n    metadata:\n        value: " + match[1] + "\n")
        expr = fragment.items[0].body[0].entries[0].value
        dummy = "__lsp_completion"
        while dummy in source:
            dummy += "_"
        # A placeholder declaration takes the body's own indentation.
        body = re.search(r"\n([ \t]+)\S", masked[owner.end : stop])
        recovered = (
            source[: owner.end]
            + f"\n{body[1] if body else INDENT}{dummy} {dummy}"
            + skeleton(masked, source, owner, stop)
            + source[stop:]
        )
        project = load_project(config, sources={**sources, path: recovered})
        module = next(m for m in project.modules.values() if m.path == path)
        definitions = Resolver(project).resolve_interfaces()
        context = definitions[(module.name, owner.name)]
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
    owner, _ = owner_before(masked or "", offset)
    if masked is None or owner is None:
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
        project, module = recovered_project(path, config, sources, masked, owner)
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
        elif isinstance(kind, str):
            from .lsp_scope_completion import NUMERIC, scope_completion

            if kind in NUMERIC:
                scoped = scope_completion(path, config, sources, offset, masked, expression=True)
                if keyword or "." in word:
                    return scoped
                choices += [(s.label, s.text, s.detail, s.kind) for s in scoped]
    qualifier = (
        word.rsplit(".", 1)[0] + "."
        if "." in word and re.fullmatch(r"\s*[A-Za-z0-9_#.]*", text)
        else ""
    )
    return [
        Suggestion(
            label[len(qualifier) :],
            text[len(qualifier) :],
            detail,
            kind,
            start + len(qualifier),
            end,
        )
        for label, text, detail, kind in sorted(set(choices))
        if label.startswith(word)
    ]


def recovered_project(path, config, sources, masked, owner):
    """Load the project with the definition under the cursor replaced by a stub.

    This lets incomplete lines, sections and parentheses coexist with the real
    import graph and component definitions, without stale ASTs.
    """
    source = sources[path]
    following = HEADER.search(masked, owner.end)
    stop = following.start() if following else len(source)
    return loaded(path, config, sources, source[: owner.start] + stub(owner) + source[stop:])


def loaded(path, config, sources, recovered):
    project = load_project(config, sources={**sources, path: recovered})
    return project, next((m for m in project.modules.values() if m.path == path), None)


def without_line_region(path, config, sources, masked, line_start):
    """Load the project without the header being typed and the body that follows it."""
    source = sources[path]
    following = HEADER.search(masked, source.find("\n", line_start) + 1 or len(source))
    stop = following.start() if following else len(source)
    return loaded(path, config, sources, source[:line_start] + source[stop:])


def target_definition(project, module, spelling):
    symbol = module.symbols.get(spelling) if module else None
    if symbol is None or symbol.declaration is None:
        return None
    return Resolver(project).resolve_interfaces()[(symbol.module, symbol.name)]


def inline_completion(path, config, sources, offset, masked):
    """Offer pins and bundle members inside a declaration's connection list."""
    owner, _ = owner_before(masked, offset)
    if owner is None or owner.kind not in {"block"}:
        return []
    instances = list(ARGUMENTS.finditer(masked, owner.end, offset))
    if not instances:
        return []
    active = instances[-1]
    opening = active.end()
    arguments = masked[opening:offset]
    depth = 0
    for char in arguments:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        if depth < 0:
            return []  # The declaration's list has already closed.
    _, keyword, argument = current_argument(arguments)
    if keyword or depth:
        return []
    argument = argument.strip()
    # Reuse bundle recovery with a virtual section-form connection. All text to
    # the right of this insertion has the same offset delta, including the cursor.
    if "." in argument:
        reference = re.sub(rf"^(?:{IDENTIFIER}|[0-9]+)?\s*:\s*", "", argument)
        if re.fullmatch(rf".+\.{WORD}", reference):
            prefix = f":\n{active[1]}{INDENT}1: "
            source = sources[path]
            # The virtual body follows the declaration's name, replacing its list.
            reference_start = offset - len(reference)
            header = active.start(3) + len(active[3])
            virtual = source[:header] + prefix + source[reference_start:]
            delta = header + len(prefix) - reference_start
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
        target = target_definition(project, module, active[2])
        if target is None:
            return []
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
        return [
            Suggestion(label, text, detail, kind, start, end)
            for label, text, detail, kind in sorted(choices)
            if label.startswith(argument)
        ]
    except (DiagnosticError, OSError, UnicodeError):
        return []


def open_parameters(masked, offset):
    """The `<` of a parameter list open at offset, with the header before it, or None.

    A declaration's header is indented; an extension's header starts its line.
    """
    opening = masked.rfind("<", 0, offset)
    if opening < 0 or ">" in masked[opening:offset]:
        return None
    line_start = masked.rfind("\n", 0, opening) + 1
    header = re.fullmatch(
        rf"([ \t]*)(extend[ \t]+|global[ \t]+)?({TYPE_PATH})[ \t]*", masked[line_start:opening]
    )
    extension = header is not None and (header[2] or "").startswith("extend")
    if header is None or extension == bool(header[1]):
        return None
    return opening, line_start, header


def parameter_completion(path, config, sources, offset, masked):
    """Offer parameter names inside `<...>` of a declaration or an extension header."""
    found = open_parameters(masked, offset)
    if found is None:
        return []
    opening, line_start, header = found
    arguments = masked[opening + 1 : offset]
    if "(" in arguments or "[" in arguments:
        return []
    *previous, argument = arguments.split(",")
    if not re.fullmatch(r"\s*" + WORD, argument):
        return []
    word = argument.strip()
    named = {m[1] for m in re.finditer(rf"({IDENTIFIER})\s*:", ",".join(previous))}
    try:
        if header[1]:
            owner, _ = owner_before(masked, offset)
            if owner is None:
                return []
            project, module = recovered_project(path, config, sources, masked, owner)
        else:
            project, module = without_line_region(path, config, sources, masked, line_start)
        target = target_definition(project, module, header[3])
        if target is None:
            return []
        tail = re.match(WORD, masked[offset:]).group()
        start, end = offset - len(word), offset + len(tail)
        suffix = "" if masked[end:].lstrip().startswith(":") else ": "
        return [
            Suggestion(
                name, name + suffix, f"{target.name}: parameter {name}", "parameter", start, end
            )
            for name in sorted(target.parameters)
            if name.startswith(word) and name not in named
        ]
    except (DiagnosticError, OSError, UnicodeError):
        return []


def type_choices(module, kinds, owner=None, *, builtin_nets=False):
    """Visible definitions of the given kinds, except the owner itself, and builtin nets."""
    choices = [
        (name, name, f"{symbol.declaration.kind} {symbol.module}.{symbol.name}", "type")
        for name, symbol in module.symbols.items()
        if symbol.declaration
        and symbol.declaration.kind in kinds
        and not (owner and symbol.module == module.name and symbol.name == owner.name)
    ]
    if builtin_nets:
        choices.extend(
            (f"{name}.{v}", f"{name}.{v}", f"{name}.{v}", "enum")
            for name, symbol in module.symbols.items()
            if symbol.module == "noscheme" and symbol.name == "net"
            for v in NETTYPES
        )
    return choices


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
    from .lsp_scope_completion import ancestors, scope_completion

    for complete in (scope_completion, parameter_completion, inline_completion, bundle_completion):
        if found := complete(path, config, sources, offset, masked):
            return found
    line_start = source.rfind("\n", 0, offset) + 1
    before = masked[line_start:offset]
    tail = re.match(WORD, masked[offset:]).group()
    word = re.search(WORD + r"$", before).group()
    start, end = offset - len(word), offset + len(tail)
    namespace = re.search(rf"(?<![A-Za-z0-9_#.])({TYPE_PATH})\.({WORD})$", before)
    owner, _ = owner_before(masked, offset)
    parents, _ = ancestors(masked, offset)
    # A type starts an extension header, a body line, a bundle member, or a port.
    base_type = re.fullmatch(rf"extend[ \t]+{TYPE_PREFIX}", before)
    declared_type = re.fullmatch(rf"[ \t]+(global[ \t]+)?{TYPE_PREFIX}", before)
    body_line = owner is not None and len(parents) == 1 and owner.kind in {"block"}
    net_line = owner is not None and (
        len(parents) == 1 and owner.kind == "bundle" or parents[1:] == ["ports:"]
    )
    if declared_type and not (body_line or net_line):
        declared_type = None
    if base_type or declared_type:
        word = re.search(TYPE_PREFIX + r"$", before).group()
        tail = re.match(TYPE_PREFIX, masked[offset:]).group()
        start, end = offset - len(word), offset + len(tail)
        namespace = None
    pin = re.fullmatch(rf"\s*(?:\[[^\n]*?\]\.)?{WORD}", before)
    pin_list = re.fullmatch(rf"\s*(?:\[[^\n]*?\]\.)?\[[^\]:]*{WORD}", before)
    if not (namespace or base_type or declared_type or pin or pin_list):
        return []
    if owner is None and not base_type:
        return []
    try:
        if base_type:
            project, module = without_line_region(path, config, sources, masked, line_start)
        else:
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
                or imported.name not in {"role", "net"}
            ):
                return []
            choices = [
                (v, v, f"{name}.{v}", "enum")
                for v in (ROLES if imported.name == "role" else NETTYPES)
            ]
        elif base_type:
            choices = type_choices(module, {"component", "block", "nettype"}, builtin_nets=True)
        elif declared_type:
            nets_only = net_line or declared_type[1]
            kinds = {"bundle", "nettype"} | (set() if nets_only else {"component", "block"})
            choices = type_choices(module, kinds, owner, builtin_nets=True)
        else:
            active = connection_instance(masked, owner, line_start)
            if active is None:
                return []
            symbol = module.symbols.get(active[2])
            if symbol is None or symbol.declaration is None or net_type(module.symbols, active[2]):
                return []
            definitions = Resolver(project).resolve_interfaces()
            target = definitions[(symbol.module, symbol.name)]
            for alias, canonical in target.aliases.items():
                # Numeric pin identifiers preserve spelling (including leading zeros).
                spelling = (
                    alias if re.fullmatch(IDENTIFIER + r"|[0-9]+", alias) else json.dumps(alias)
                )
                choices.append((alias, spelling, f"{target.name}: pin/port {canonical}", "pin"))
        qualifier = (
            word.rsplit(".", 1)[0] + "." if (base_type or declared_type) and "." in word else ""
        )
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
