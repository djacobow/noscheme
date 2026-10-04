"""Build a typed source AST and validate declaration structure.

No expression execution, name resolution, electrical checking, or CAD export.
"""

from bisect import bisect_right
import json
from lark import Token, Tree
from . import ast as a
from .diagnostics import Diagnostic, DiagnosticError, Related
from .syntax import SyntaxDiagnostic, parse as syntax_parse


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
            keys = [self.key(c).first for c in node.children]
            return a.FieldKey(self.span(node), keys[0], keys[1] if len(keys) > 1 else None)
        text = self.string(node) if node.type == "STRING" else str(node)
        return a.FieldKey(self.span(node), a.PinKey(self.span(node), text, str(node)))

    def expr(self, node):
        if isinstance(node, Token):
            return self.name(node)
        kind, c, span = str(node.data), node.children, self.span(node)
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
        if isinstance(node, Token) or node.data == "field_key":
            return a.Endpoint(self.span(node), (), self.key(node), ())
        c = list(node.children)
        leading = []
        while isinstance(c[0], Tree) and c[0].data == "index":
            leading.append(self.index(c.pop(0)))
        head = self.key(c.pop(0))
        selectors = tuple(self.index(x) if isinstance(x, Tree) else self.name(x) for x in c)
        return a.Endpoint(self.span(node), tuple(leading), head, selectors)

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
                span, tuple(self.name(x) for x in c[0].children), tuple(self.name(x) for x in c[1:])
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
            return a.Instance(
                span,
                self.name(c[0]),
                tuple(dimensions),
                tuple(self.name(x) for x in c[i].children),
                tuple(self.build(x) for x in c[i + 1 :]),
            )
        if kind in {"connections", "arguments", "instance_metadata"}:
            names = {
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

    def field_key(f):
        k = f.key
        return (k.first.text, k.last.text if k.last else None)

    def fields(entries, defaults=False):
        unique(entries, field_key)
        for f in entries:
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
            fields(f.fields)

    definitions = [n for n in module.items if isinstance(n, a.Definition)]
    unique(definitions, lambda n: n.name.text)
    allowed = {
        "component": {"params", "pins", "metadata"},
        "block": {"params", "ports", "metadata"},
        "board": {"metadata"},
        "bundle": {"metadata"},
    }
    for d in definitions:
        if d.base and d.kind not in {"component", "block"}:
            error(
                "syntax.inheritance-context",
                "only components and blocks may specialize definitions",
                d,
            )
        sections = [n for n in d.body if isinstance(n, a.Section)]
        unique(sections, lambda n: n.name)
        unique([n for n in d.body if isinstance(n, (a.Net, a.Instance))], lambda n: n.name.text)
        for n in d.body:
            if isinstance(n, a.Section):
                permitted = (
                    (allowed[d.kind] - {"params"}) | {"defaults"} if d.base else allowed[d.kind]
                )
                if n.name not in permitted:
                    error(
                        "syntax.section-context",
                        f"{n.name} section is not allowed in this {d.kind} definition",
                        n,
                    )
                fields(n.entries, n.name == "params")
            elif isinstance(n, a.Field):
                if d.kind != "bundle" or d.base:
                    error(
                        "syntax.declaration-context",
                        "bare fields are only allowed in bundle definitions",
                        n,
                    )
            elif isinstance(n, (a.Net, a.Instance)):
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
                    for s in n.sections:
                        if s.name == "params":
                            unique(s.entries, lambda e: e.name.text)
                        elif s.name == "metadata":
                            fields(s.entries)
        fields([n for n in d.body if isinstance(n, a.Field)])
    return tuple(errors)


def parse(source: str, filename: str = "<input>") -> a.Module:
    builder = Builder(source, filename)
    try:
        tree = syntax_parse(source, str(filename))
    except SyntaxDiagnostic as exc:
        offset = builder.lines[min(exc.line - 1, len(builder.lines) - 1)] + exc.column - 1
        code = "syntax.indentation" if "indentation" in exc.message else "syntax.invalid"
        raise DiagnosticError(
            [Diagnostic(code, "error", exc.message, builder.position_span(offset, offset + 1))]
        ) from exc
    module = builder.build(tree)
    errors = validate_structure(module)
    if errors:
        raise DiagnosticError(errors)
    return module
