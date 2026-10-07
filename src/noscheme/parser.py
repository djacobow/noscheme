"""Build a typed source AST and validate declaration structure.

No expression execution, name resolution, electrical checking, or CAD export.
"""

from bisect import bisect_right
from dataclasses import replace
from .analysis_cache import cached_parse
import json
from lark import Token, Tree
from . import ast as a
from .diagnostics import Diagnostic, DiagnosticError, Related
from .syntax import SyntaxDiagnostic, parse as syntax_parse
from .units import scaled_value

RESERVED_NAMES = frozenset({"no_connect", "global", "true", "false", "number", "string"})


class Builder:
    def __init__(self, source, filename):
        self.source, self.filename = source, str(filename)
        self.lines = [0] + [i + 1 for i, c in enumerate(source) if c == "\n"]

    def position_span(self, start, end):
        start, end = min(start, len(self.source)), min(end, len(self.source))
        line, end_line = bisect_right(self.lines, start), bisect_right(self.lines, end)
        return a.Span(
            self.filename,
            start,
            end,
            line,
            start - self.lines[line - 1] + 1,
            end_line,
            end - self.lines[end_line - 1] + 1,
        )

    def span(self, node):
        m = node.meta if isinstance(node, Tree) else node
        return self.position_span(
            getattr(m, "start_pos", 0) or 0, getattr(m, "end_pos", len(self.source)) or 0
        )

    def combine(self, left, right):
        return self.position_span(left.span.start_pos, right.span.end_pos)

    def name(self, token):
        return a.Name(self.span(token), str(token))

    def string(self, token):
        try:
            value = json.loads(str(token))
            # Reject unpaired surrogate escapes while accepting valid pairs.
            value.encode("utf-8")
        except (ValueError, UnicodeError) as exc:
            raise DiagnosticError(
                [
                    Diagnostic(
                        "syntax.string",
                        "error",
                        "invalid string escape; use JSON-style string escapes",
                        self.span(token),
                    )
                ]
            ) from exc
        return value

    def key(self, node):
        if isinstance(node, Tree):
            if node.data in {"single_pin_list", "singleton_endpoint"}:
                index = self.index(node.children[0])
                expr = index.start
                if index.stop or not (
                    isinstance(expr, (a.Name, a.String))
                    or isinstance(expr, a.Number)
                    and expr.tag == "integer"
                ):
                    raise DiagnosticError(
                        [
                            Diagnostic(
                                "syntax.pin-list",
                                "error",
                                "pin lists contain exact pin names, numbers, or inclusive ranges",
                                index.span,
                            )
                        ]
                    )
                text = (
                    expr.text
                    if isinstance(expr, a.Name)
                    else expr.value
                    if isinstance(expr, a.String)
                    else expr.spelling
                )
                spelling = self.source[expr.span.start_pos : expr.span.end_pos]
                item = a.FieldKey(expr.span, a.PinKey(expr.span, text, spelling))
                return a.FieldKey(self.span(node), item.first, items=(item,))
            if (
                len(node.children) == 1
                and isinstance(node.children[0], Tree)
                and node.children[0].data in {"range_group", "list_group"}
            ):
                group = node.children[0]
                count = 2 if group.data == "range_group" else 1
                first = self.key(group.children[0])
                if count == 2:
                    last = self.key(group.children[1])
                    first = a.FieldKey(self.combine(first, last), first.first, last.first)
                items = (first, *(self.key(c) for c in group.children[count:]))
                return a.FieldKey(self.span(node), first.first, items=items)
            keys = [self.key(c).first for c in node.children]
            return a.FieldKey(self.span(node), keys[0], keys[1] if len(keys) > 1 else None)
        text = self.string(node) if node.type == "STRING" else str(node)
        return a.FieldKey(self.span(node), a.PinKey(self.span(node), text, str(node)))

    def expr(self, node):
        if isinstance(node, Token):
            return self.name(node)
        kind, c, span = str(node.data), node.children, self.span(node)
        if kind == "scaled":
            try:
                value = scaled_value(str(c[0]))
            except ValueError:
                raise DiagnosticError(
                    [
                        Diagnostic(
                            "syntax.number",
                            "error",
                            "a scaled number has one decimal mark: 4.7k or 4k7, not 4.7k7",
                            span,
                        )
                    ]
                ) from None
            return a.Number(span, str(c[0]), "integer" if type(value) is int else "double")
        if kind in {"integer", "number"}:
            return a.Number(span, str(c[0]), "integer" if kind == "integer" else "double")
        if kind == "string":
            return a.String(span, self.string(c[0]), str(c[0]))
        if kind == "name":
            return self.name(c[0])
        if kind == "list_value":
            return a.ListExpr(span, tuple(self.expr(x) for x in c))
        if kind == "grouped":
            return a.Group(span, self.expr(c[0]))
        if kind == "unary":
            return a.Unary(span, str(c[0]), self.expr(c[1]))
        if kind in {"sum", "product", "shift"}:
            result = self.expr(c[0])
            for op, rhs in zip(c[1::2], c[2::2]):
                right = self.expr(rhs)
                result = a.Binary(self.combine(result, right), str(op), result, right)
            return result
        if kind in {"postfix", "reference"}:
            result = self.expr(c[0])
            for suffix in c[1:]:
                if isinstance(suffix, Token):
                    name = self.name(suffix)
                    result = a.Member(self.combine(result, name), result, name)
                elif suffix.data == "member":
                    name = self.name(suffix.children[0])
                    result = a.Member(self.combine(result, name), result, name)
                elif suffix.data == "index":
                    index = self.index(suffix)
                    result = a.Subscript(self.combine(result, index), result, index)
                elif suffix.data == "call":
                    call_span = self.position_span(result.span.start_pos, self.span(suffix).end_pos)
                    args, keywords, seen = [], [], set()
                    for x in suffix.children:
                        if isinstance(x, Tree) and x.data == "keyword_argument":
                            name = self.name(x.children[0])
                            if name.text in seen:
                                raise DiagnosticError(
                                    [
                                        Diagnostic(
                                            "syntax.duplicate",
                                            "error",
                                            f"duplicate named argument {name.text!r}",
                                            name.span,
                                        )
                                    ]
                                )
                            seen.add(name.text)
                            keywords.append(
                                a.KeywordArgument(self.span(x), name, self.expr(x.children[1]))
                            )
                        else:
                            if keywords:
                                raise DiagnosticError(
                                    [
                                        Diagnostic(
                                            "syntax.argument-order",
                                            "error",
                                            "positional arguments must precede named arguments",
                                            self.span(x),
                                        )
                                    ]
                                )
                            args.append(self.expr(x))
                    result = a.Call(call_span, result, tuple(args), tuple(keywords))
                else:
                    raise AssertionError(f"unhandled suffix: {suffix.data}")
            return result
        raise AssertionError(f"unhandled expression: {kind}")

    def index(self, node):
        c = node.children
        return a.Index(self.span(node), self.expr(c[0]), self.expr(c[1]) if len(c) > 1 else None)

    def endpoint(self, node):
        if isinstance(node, Token) or node.data in {
            "field_key",
            "singleton_endpoint",
            "single_pin_list",
        }:
            return a.Endpoint(self.span(node), (), self.key(node), ())
        c = list(node.children)
        leading = []
        while isinstance(c[0], Tree) and c[0].data == "index":
            leading.append(self.index(c.pop(0)))
        head = self.key(c.pop(0))
        selectors = tuple(self.index(x) if isinstance(x, Tree) else self.name(x) for x in c)
        return a.Endpoint(self.span(node), tuple(leading), head, selectors)

    def instance_arguments(self, arguments, sections):
        """Merge `R(a, b, 1: c, value=4.7k)` into the instance's own sections.

        Arguments and sections describe one instance, so inline bindings join its
        connections and inline parameters its params; a duplicate is reported as
        such rather than as a second section.
        """
        positional, entries = [], {"connections": [], "params": []}
        for argument in arguments.children if arguments is not None else ():
            c = argument.children
            if argument.data == "positional_argument":
                positional.append(self.expr(c[0]))
            elif argument.data == "inline_binding":
                entries["connections"].append(
                    a.Binding(self.span(argument), self.endpoint(c[0]), self.expr(c[1]))
                )
            else:
                entries["params"].append(
                    a.Argument(self.span(argument), self.name(c[0]), self.expr(c[1]))
                )
        merged = []
        for section in sections:
            inline = entries.pop(section.name, [])
            merged.append(replace(section, entries=(*inline, *section.entries)))
        span = self.span(arguments) if arguments is not None else None
        merged.extend(
            a.Section(span, kind, tuple(found)) for kind, found in entries.items() if found
        )
        return tuple(merged), tuple(positional)

    def field(self, node):
        c = node.children
        values = [x for x in c[1:] if not (isinstance(x, Tree) and x.data == "field")]
        nested = tuple(self.field(x) for x in c[1:] if isinstance(x, Tree) and x.data == "field")
        return a.Field(
            self.span(node),
            self.key(c[0]),
            self.expr(values[0]) if values else None,
            self.expr(values[1]) if len(values) > 1 else None,
            nested,
        )

    def build(self, node):
        c, span, kind = node.children, self.span(node), str(node.data)
        if kind == "start":
            return a.Module(
                self.position_span(0, len(self.source)), tuple(self.build(x) for x in c)
            )
        if kind == "import_stmt":
            return a.Import(
                span,
                tuple(self.name(x) for x in c[0].children),
                tuple(
                    a.ImportedName(
                        self.span(x),
                        self.name(x.children[0]),
                        self.name(x.children[1]) if len(x.children) > 1 else None,
                    )
                    for x in c[1:]
                ),
            )
        if kind == "definition":
            offset = 2
            base = None
            if len(c) > 2 and c[2].data == "extends":
                base = tuple(self.name(x) for x in c[2].children[0].children)
                offset = 3
            return a.Definition(
                span,
                str(c[0].data).removesuffix("_kind"),
                self.name(c[1]),
                base,
                tuple(self.build(x) for x in c[offset:]),
            )
        if kind == "section":
            return a.Section(
                span, str(c[0].data).removesuffix("_name"), tuple(self.field(x) for x in c[1:])
            )
        if kind == "field":
            return self.field(node)
        if kind == "net_decl":
            global_ = isinstance(c[0], Tree) and c[0].data == "global_modifier"
            rest = c[1:] if global_ else c
            return a.Net(
                span,
                self.name(rest[0]),
                self.expr(rest[1]),
                global_,
                tuple(self.field(x) for x in rest[2:]),
            )
        if kind == "instance":
            i = 1
            dimensions = []
            while c[i].data == "index":
                dimensions.append(self.index(c[i]))
                i += 1
            rest = c[i + 1 :]
            arguments = rest.pop(0) if rest and rest[0].data == "inline_arguments" else None
            sections, positional = self.instance_arguments(arguments, [self.build(x) for x in rest])
            return a.Instance(
                span,
                self.name(c[0]),
                tuple(dimensions),
                tuple(self.name(x) for x in c[i].children),
                sections,
                positional,
            )
        if kind == "element_override":
            indices = tuple(self.index(x) for x in c if x.data == "index")
            return a.ElementOverride(span, indices, tuple(self.build(x) for x in c[len(indices) :]))
        if kind in {"connections", "arguments", "instance_metadata", "elements"}:
            names = {
                "elements": "elements",
                "connections": "connections",
                "arguments": "params",
                "instance_metadata": "metadata",
            }
            return a.Section(span, names[kind], tuple(self.build(x) for x in c))
        if kind == "binding":
            return a.Binding(span, self.endpoint(c[0]), self.expr(c[1]))
        if kind == "argument":
            return a.Argument(span, self.name(c[0]), self.expr(c[1]))
        raise AssertionError(f"unhandled declaration: {kind}")


def validate_structure(module):
    errors = []

    def error(code, message, node, previous=None):
        related = (Related("previous declaration", previous.span),) if previous else ()
        errors.append(Diagnostic(code, "error", message, node.span, related))

    def unique(nodes, key):
        seen = {}
        for node in nodes:
            name = key(node)
            if name in seen:
                error("syntax.duplicate", f"duplicate entry {name!r}", node, seen[name])
            else:
                seen[name] = node

    def declaration_name(name, *, physical=False):
        if physical and name.spelling.startswith('"'):
            return
        if name.text in RESERVED_NAMES:
            error(
                "syntax.reserved-name",
                f"{name.text!r} is reserved and cannot be declared as a name",
                name,
            )

    def field_key(f):
        k = f.key
        if k.items:
            return tuple(
                (item.first.text, item.last.text if item.last else None) for item in k.items
            )
        return (k.first.text, k.last.text if k.last else None)

    def fields(entries, defaults=False, *, pin_keys=False, instance_metadata=False):
        unique(entries, field_key)
        for f in entries:
            if f.key.items and not pin_keys:
                error(
                    "syntax.pin-list-context",
                    "pin lists are only allowed for physical pins and connection bindings",
                    f.key,
                )
            if f.default is not None and not defaults:
                error(
                    "syntax.default-context",
                    "parameter default is only valid in a params declaration",
                    f,
                )
            if defaults and (f.fields or f.value is None or f.key.last is not None):
                error(
                    "syntax.parameter",
                    "parameter declarations require a type and cannot have nested fields or ranges",
                    f,
                )
            fields(f.fields, pin_keys=instance_metadata and f.key.first.text == "pins")

    definitions = [n for n in module.items if isinstance(n, a.Definition)]
    for item in module.items:
        if isinstance(item, a.Import):
            if item.names:
                for imported in item.names:
                    declaration_name(imported.alias or imported.name)
            else:
                for name in item.module:
                    declaration_name(name)
    unique(definitions, lambda n: n.name.text)
    allowed = {
        "component": {"params", "pins", "metadata"},
        "block": {"params", "ports", "metadata"},
        "board": {"metadata"},
        "bundle": {"metadata"},
        "nettype": set(),
    }
    for d in definitions:
        declaration_name(d.name)
        if d.base and d.kind not in {"component", "block", "nettype"}:
            error(
                "syntax.inheritance-context",
                "only components, blocks, and net types may specialize definitions",
                d,
            )
        if d.kind == "nettype" and not d.base:
            error(
                "syntax.nettype-base",
                "a net type extends a builtin nettype value or another net type",
                d,
            )
        sections = [n for n in d.body if isinstance(n, a.Section)]
        unique(sections, lambda n: n.name)
        if d.kind == "nettype":
            fields(tuple(n for n in d.body if isinstance(n, a.Field)))
        unique([n for n in d.body if isinstance(n, (a.Net, a.Instance))], lambda n: n.name.text)
        for n in d.body:
            if isinstance(n, a.Section):
                if n.name in {"params", "ports", "pins"}:
                    for entry in n.entries:
                        for key in entry.key.items or (entry.key,):
                            declaration_name(key.first, physical=n.name == "pins")
                            if key.last:
                                declaration_name(key.last, physical=n.name == "pins")
                permitted = (
                    (allowed[d.kind] - {"params"}) | {"defaults"} if d.base else allowed[d.kind]
                )
                if n.name not in permitted:
                    error(
                        "syntax.section-context",
                        f"{n.name} section is not allowed in this {d.kind} definition",
                        n,
                    )
                fields(n.entries, n.name == "params", pin_keys=n.name == "pins")
            elif isinstance(n, a.Field):
                if d.kind == "nettype":
                    continue  # Net metadata fields, validated together below.
                declaration_name(n.key.first)
                if d.kind != "bundle" or d.base:
                    error(
                        "syntax.declaration-context",
                        "bare fields are only allowed in bundle and net type definitions",
                        n,
                    )
            elif isinstance(n, (a.Net, a.Instance)):
                declaration_name(n.name)
                if d.kind not in {"block", "board"} or d.base:
                    error(
                        "syntax.declaration-context",
                        "internal circuit declarations require a base block or board",
                        n,
                    )
                if isinstance(n, a.Net):
                    if n.global_ and d.kind != "board":
                        error("syntax.global-context", "only a board may declare globals", n)
                    fields(n.fields)
                else:
                    unique(n.sections, lambda s: s.name)
                    nested = [
                        e.sections for s in n.sections if s.name == "elements" for e in s.entries
                    ]
                    for sections in nested:
                        unique(sections, lambda s: s.name)
                    for s in (*n.sections, *(s for sections in nested for s in sections)):
                        if s.name == "params":
                            unique(s.entries, lambda e: e.name.text)
                        elif s.name == "metadata":
                            fields(s.entries, instance_metadata=True)
        fields([n for n in d.body if isinstance(n, a.Field)])
    return tuple(errors)


def syntax_tree(source: str, filename: str = "<input>", builder=None):
    """Parse to the concrete syntax tree, reporting syntax errors as diagnostics."""
    builder = builder or Builder(source, filename)
    try:
        return syntax_parse(source, str(filename))
    except SyntaxDiagnostic as exc:
        offset = builder.lines[min(exc.line - 1, len(builder.lines) - 1)] + exc.column - 1
        code = "syntax.indentation" if "indentation" in exc.message else "syntax.invalid"
        raise DiagnosticError(
            [Diagnostic(code, "error", exc.message, builder.position_span(offset, offset + 1))]
        ) from exc


@cached_parse
def parse(source: str, filename: str = "<input>") -> a.Module:
    builder = Builder(source, filename)
    module = builder.build(syntax_tree(source, filename, builder))
    errors = validate_structure(module)
    if errors:
        raise DiagnosticError(errors)
    return module
