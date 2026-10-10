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
SECTION_KINDS = ("arguments", "instance_metadata", "elements")


def fail(code, message, where):
    """Raise one error, located at a span or a node's span."""
    span = where.span if isinstance(where, a.Node) else where
    raise DiagnosticError([Diagnostic(code, "error", message, span)])


def is_tree(node, *kinds):
    return isinstance(node, Tree) and node.data in kinds


def has_value(field):
    """Whether a `key: value` field tree has a value, not only nested fields."""
    return any(not is_tree(x, "field") for x in field.children[1:])


def declared_key(name):
    return a.FieldKey(name.span, a.PinKey(name.span, name.text, name.text))


def typed(type_, declarator):
    """A declared type with the declarator's array dimensions: net.passive[4][2]."""
    for index in declarator.dimensions:
        type_ = a.Subscript(
            a.Span(
                type_.span.filename,
                type_.span.start_pos,
                index.span.end_pos,
                type_.span.line,
                type_.span.column,
                index.span.end_line,
                index.span.end_column,
            ),
            type_,
            index,
        )
    return type_


def is_reference(value):
    if isinstance(value, (a.Member, a.Subscript)):
        return is_reference(value.base)
    return isinstance(value, a.Name)


def is_target(value):
    """A net reference, or flat(net)."""
    if isinstance(value, a.Call):
        return (
            isinstance(value.callee, a.Name)
            and value.callee.text == "flat"
            and len(value.arguments) == 1
            and not value.keywords
            and is_reference(value.arguments[0])
        )
    return is_reference(value)


def not_a_target(node):
    if isinstance(node, a.Call) and isinstance(node.callee, a.Name):
        node = node.callee
    fail("syntax.target", "a connection target is a net reference or flat(net)", node)


def declaration_form(span, typed_entry):
    if typed_entry:
        message = "declare names with a type first only in params, ports, and bundles"
    else:
        message = "write the type first: `net.power supply`, `number gain = 2`"
    fail("syntax.declaration-form", message, span)


def net_fields(declaration, noun):
    """The fields of a net or bundle member: its body lines, with no instance parts."""
    for span in (declaration.parameters, declaration.arguments):
        if span is not None:
            fail("syntax.net-declaration", f"a {noun} takes no parameters or connections", span)
    fields = list(declaration.fields)
    for section in declaration.sections:
        if section.name != "connections":
            fail("syntax.net-declaration", f"a {noun} has no {section.name} section", section)
        for b in section.entries:
            if b.endpoint.instance_indices or b.endpoint.selectors or isinstance(b.target, a.Call):
                fail("syntax.net-declaration", f"a {noun} has fields, not connections", b)
            fields.append(a.Field(b.span, b.endpoint.head, b.target, None, ()))
    fields.sort(key=lambda f: f.span.start_pos)
    errors = field_errors(fields)
    if errors:
        raise DiagnosticError(errors)
    return tuple(fields)


def member_fields(declaration):
    """A bundle's `net.passive scl, sda` declares one member Field per name."""
    if declaration.global_:
        fail("syntax.net-declaration", "a bundle member is not global", declaration)
    fields = net_fields(declaration, "bundle member")
    type_ = declaration_type(declaration)
    return tuple(
        a.Field(declaration.span, declared_key(d.name), typed(type_, d), None, fields)
        for d in declaration.declarators
    )


def declaration_type(declaration):
    result = declaration.type[0]
    for name in declaration.type[1:]:
        result = a.Member(
            a.Span(
                result.span.filename,
                result.span.start_pos,
                name.span.end_pos,
                result.span.line,
                result.span.column,
                name.span.end_line,
                name.span.end_column,
            ),
            result,
            name,
        )
    return result


def classify(declaration, net):
    """The Nets or the Instance a declaration makes, given whether its type is a net type."""
    if net:
        fields = net_fields(declaration, "net")
        type_ = declaration_type(declaration)
        return tuple(
            a.Net(declaration.span, d.name, typed(type_, d), declaration.global_, fields)
            for d in declaration.declarators
        )
    if declaration.global_ or len(declaration.declarators) > 1:
        message = "an instance is declared alone, and only nets are global"
        fail("syntax.instance-declaration", message, declaration)
    if declaration.fields:
        field = declaration.fields[0]
        not_a_target(field.value or field.key)
    (declarator,) = declaration.declarators
    return (
        a.Instance(
            declaration.span,
            declarator.name,
            declarator.dimensions,
            declaration.type,
            declaration.sections,
            declaration.positional,
            declaration.wildcard,
            declaration.parameter,
        ),
    )


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

    def pin_key(self, token):
        text = self.string(token) if token.type == "STRING" else str(token)
        return a.PinKey(self.span(token), text, str(token))

    def key(self, node):
        if isinstance(node, Tree):
            if node.data == "aliased_key":
                first, *aliases = (self.pin_key(c) for c in node.children)
                return a.FieldKey(self.span(node), first, aliases=tuple(aliases))
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
        return a.FieldKey(self.span(node), self.pin_key(node))

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
        if kind == "call_target":
            callee = self.name(c[0])
            if callee.text != "flat":
                raise DiagnosticError(
                    [
                        Diagnostic(
                            "syntax.target",
                            "error",
                            "a connection target is a net reference or flat(net)",
                            callee.span,
                        )
                    ]
                )
            return a.Call(span, callee, (self.expr(c[1]),))
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

    def instance_arguments(self, parameters, arguments, sections):
        """Merge `R<4.7k, tol: 1>(a, b, 1: c)` into the instance's own sections.

        Parameters and arguments describe one instance, so named parameters join its
        params and inline bindings its connections; a duplicate is reported as such
        rather than as a second section.
        """
        positional, wildcard = [], None
        parameter, named = self.type_arguments(parameters)
        entries = {"connections": [], "params": named}
        spans = {"connections": arguments, "params": parameters}
        for argument in arguments.children if arguments is not None else ():
            c = argument.children
            if argument.data == "wildcard_argument":
                if str(c[0]) != "*" or wildcard is not None:
                    message = "write one * to connect same-name nets"
                    raise DiagnosticError(
                        [Diagnostic("syntax.wildcard", "error", message, self.span(argument))]
                    )
                wildcard = self.span(argument)
            elif argument.data == "positional_argument":
                positional.append(self.expr(c[0]))
            else:
                entries["connections"].append(
                    a.Binding(self.span(argument), self.endpoint(c[0]), self.expr(c[1]))
                )
        merged = []
        for section in sections:
            inline = entries.pop(section.name, [])
            merged.append(replace(section, entries=(*inline, *section.entries)))
        merged.extend(
            a.Section(self.span(spans[kind]), kind, tuple(found))
            for kind, found in entries.items()
            if found
        )
        return tuple(merged), parameter, tuple(positional), wildcard

    def type_arguments(self, node):
        """`<4.7k, tol: 1>`: the unnamed parameter, if any, and the named ones."""
        parameter, named = None, []
        for argument in node.children if node is not None else ():
            c = argument.children
            if argument.data == "named_parameter":
                named.append(a.Argument(self.span(argument), self.name(c[0]), self.expr(c[1])))
            elif parameter is None and not named:
                parameter = self.expr(c[0])
            else:
                message = "write one unnamed parameter, before the named ones"
                fail("syntax.parameter-order", message, self.span(argument))
        return parameter, named

    def type_path(self, node):
        """The Names of a declaration's type, and the type as an expression."""
        names = tuple(self.name(x) for x in node.children)
        result = names[0]
        for name in names[1:]:
            result = a.Member(self.combine(result, name), result, name)
        return names, result

    def declarators(self, nodes):
        return tuple(
            a.Declarator(
                self.span(x), self.name(x.children[0]), tuple(self.index(i) for i in x.children[1:])
            )
            for x in nodes
        )

    def typed_field(self, node):
        """`net.power supply:` or `number gain = 2`: one Field per declared name."""
        c = node.children
        declarators = self.declarators([x for x in c if is_tree(x, "declarator")])
        rest = c[1 + len(declarators) :]
        nested = tuple(self.field(x) for x in rest if is_tree(x, "field"))
        default = next((self.expr(x) for x in rest if not is_tree(x, "field")), None)
        type_ = self.type_path(c[0])[1]
        return tuple(
            a.Field(self.span(node), declared_key(d.name), typed(type_, d), default, nested)
            for d in declarators
        )

    def body_entry(self, node):
        """A body line: a connection when it reads as one, otherwise a net field."""
        c = node.children
        endpoint = self.endpoint(c[0])
        nested = tuple(self.field(x) for x in c[1:] if is_tree(x, "field"))
        values = [x for x in c[1:] if not is_tree(x, "field")]
        value = self.field_value(values[0]) if values else None
        plain = not endpoint.instance_indices and not endpoint.selectors
        if is_target(value) and not nested or not plain:
            if not is_target(value) or nested:
                not_a_target(value or endpoint)
            return a.Binding(self.span(node), endpoint, value)
        return a.Field(self.span(node), endpoint.head, value, None, nested)

    def field_value(self, node):
        if isinstance(node, Tree) and node.data == "range_value":
            return a.Range(
                self.span(node), self.expr(node.children[0]), self.expr(node.children[1])
            )
        if isinstance(node, Tree) and node.data == "tolerance_value":
            c = node.children
            return a.Tolerance(self.span(node), self.expr(c[0]), self.expr(c[2]), len(c) > 3)
        return self.expr(node)

    def field(self, node):
        c = node.children
        values = [x for x in c[1:] if not (isinstance(x, Tree) and x.data == "field")]
        nested = tuple(self.field(x) for x in c[1:] if isinstance(x, Tree) and x.data == "field")
        return a.Field(
            self.span(node),
            self.key(c[0]),
            self.field_value(values[0]) if values else None,
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
        if kind in {"definition", "extension"}:
            base = parameter = None
            body = []
            if kind == "extension":
                base, _ = self.type_path(c[0])
                parameters = next((x for x in c if is_tree(x, "type_arguments")), None)
                parameter, named = self.type_arguments(parameters)
                if named:
                    # Named parameters are the extension's defaults.
                    defaults = tuple(
                        a.Field(x.span, declared_key(x.name), x.value, None, ()) for x in named
                    )
                    body.append(a.Section(self.span(parameters), "defaults", defaults))
                c = [x for x in c[1:] if not is_tree(x, "type_arguments")]
                kind = "extend"
            else:
                kind, c = str(c[0].data).removesuffix("_kind"), c[1:]
            for x in c[1:]:
                if kind == "bundle" and x.data == "field" and has_value(x):
                    declaration_form(self.span(x.children[0]), False)
                item = self.build(x)
                if kind == "bundle" and isinstance(item, a.Declaration):
                    item = member_fields(item)
                body.extend(item if isinstance(item, tuple) else (item,))
            return a.Definition(span, kind, self.name(c[0]), base, tuple(body), parameter)
        if kind == "section":
            name = str(c[0].data).removesuffix("_name")
            entries = []
            for x in c[1:]:
                typed_entry = x.data == "typed_field"
                if typed_entry != (name in {"params", "ports"}) and (typed_entry or has_value(x)):
                    first = x.children[1] if typed_entry else x.children[0]
                    declaration_form(self.span(first), typed_entry)
                entries.extend(self.typed_field(x) if typed_entry else (self.field(x),))
            return a.Section(span, name, tuple(entries))
        if kind == "field":
            return self.field(node)
        if kind == "declaration":
            global_ = isinstance(c[0], Token) and c[0].type == "GLOBAL"
            c = c[1:] if global_ else c
            names, _ = self.type_path(c[0])
            parameters = next((x for x in c if is_tree(x, "type_arguments")), None)
            argument_list = next((x for x in c if is_tree(x, "argument_list")), None)
            arguments = argument_list.children[0] if argument_list else None
            # Bindings precede the sections and form the connections section.
            lines = [self.body_entry(x) for x in c if is_tree(x, "body_entry")]
            bindings = tuple(x for x in lines if isinstance(x, a.Binding))
            body = [self.build(x) for x in c if is_tree(x, *SECTION_KINDS)]
            if bindings:
                connections_span = self.combine(bindings[0], bindings[-1])
                body.insert(0, a.Section(connections_span, "connections", bindings))
            sections, parameter, positional, wildcard = self.instance_arguments(
                parameters, arguments, body
            )
            return a.Declaration(
                span,
                global_,
                names,
                self.declarators([x for x in c if is_tree(x, "declarator")]),
                self.span(parameters) if parameters else None,
                self.span(argument_list) if argument_list else None,
                sections,
                tuple(x for x in lines if isinstance(x, a.Field)),
                parameter,
                positional,
                wildcard,
            )
        if kind == "element_override":
            indices = tuple(self.index(x) for x in c if x.data == "index")
            return a.ElementOverride(span, indices, tuple(self.build(x) for x in c[len(indices) :]))
        if kind in {"arguments", "instance_metadata", "elements"}:
            names = {
                "elements": "elements",
                "arguments": "params",
                "instance_metadata": "metadata",
            }
            return a.Section(span, names[kind], tuple(self.build(x) for x in c))
        if kind == "argument":
            return a.Argument(span, self.name(c[0]), self.expr(c[1]))
        raise AssertionError(f"unhandled declaration: {kind}")


class Validator:
    """Collects structural errors; a check that fails records an error and goes on."""

    def __init__(self):
        self.errors = []

    def error(self, code, message, node, previous=None):
        related = (Related("previous declaration", previous.span),) if previous else ()
        self.errors.append(Diagnostic(code, "error", message, node.span, related))

    def unique(self, nodes, key):
        seen = {}
        for node in nodes:
            name = key(node)
            if name in seen:
                self.error("syntax.duplicate", f"duplicate entry {name!r}", node, seen[name])
            else:
                seen[name] = node

    def declaration_name(self, name, *, physical=False):
        if physical and name.spelling.startswith('"'):
            return
        if name.text in RESERVED_NAMES:
            self.error(
                "syntax.reserved-name",
                f"{name.text!r} is reserved and cannot be declared as a name",
                name,
            )

    @staticmethod
    def field_key(f):
        k = f.key
        if k.items:
            return tuple(
                (item.first.text, item.last.text if item.last else None) for item in k.items
            )
        return (k.first.text, k.last.text if k.last else None)

    def fields(
        self, entries, defaults=False, *, pin_keys=False, instance_metadata=False, pins=False
    ):
        self.unique(entries, self.field_key)
        for f in entries:
            if f.key.aliases and not pins:
                self.error(
                    "syntax.alias-context",
                    "pin aliases are declared in a component's pins section",
                    f.key,
                )
            if isinstance(f.value, (a.Range, a.Tolerance)) and (
                f.key.first.text != "voltage" or f.default is not None
            ):
                self.error(
                    "syntax.range-context",
                    "ranges and tolerances are only valid as a voltage value",
                    f.value,
                )
            if f.key.items and not pin_keys:
                self.error(
                    "syntax.pin-list-context",
                    "pin lists are only allowed for physical pins and connection bindings",
                    f.key,
                )
            if f.default is not None and not defaults:
                self.error(
                    "syntax.default-context",
                    "parameter default is only valid in a params declaration",
                    f,
                )
            if defaults and (f.fields or f.value is None or f.key.last is not None):
                self.error(
                    "syntax.parameter",
                    "parameter declarations require a type and cannot have nested fields or ranges",
                    f,
                )
            self.fields(f.fields, pin_keys=instance_metadata and f.key.first.text == "pins")

    def instance_sections(self, sections):
        self.unique(sections, lambda s: s.name)
        nested = [e.sections for s in sections if s.name == "elements" for e in s.entries]
        for element in nested:
            self.unique(element, lambda s: s.name)
        for s in (*sections, *(s for element in nested for s in element)):
            if s.name == "params":
                self.unique(s.entries, lambda e: e.name.text)
            elif s.name == "metadata":
                self.fields(s.entries, instance_metadata=True)


def field_errors(fields):
    validator = Validator()
    validator.fields(fields)
    return tuple(validator.errors)


ALLOWED_SECTIONS = {
    "component": {"params", "pins", "metadata"},
    "block": {"params", "ports", "metadata"},
    "board": {"metadata"},
    "bundle": {"metadata"},
    "nettype": set(),
}


def validate_structure(module):
    """Check structure that does not depend on other modules.

    The checks that depend on a definition's kind wait, for an extension, until the
    project loader knows its base's kind; see kind_errors.
    """
    v = Validator()
    definitions = [n for n in module.items if isinstance(n, a.Definition)]
    for item in module.items:
        if isinstance(item, a.Import):
            if item.names:
                for imported in item.names:
                    v.declaration_name(imported.alias or imported.name)
            else:
                for name in item.module:
                    v.declaration_name(name)
    v.unique(definitions, lambda n: n.name.text)
    for d in definitions:
        v.declaration_name(d.name)
        v.unique([n for n in d.body if isinstance(n, a.Section)], lambda n: n.name)
        declarators = [x for n in d.body if isinstance(n, a.Declaration) for x in n.declarators]
        v.unique(declarators, lambda n: n.name.text)
        for n in d.body:
            if isinstance(n, a.Section):
                if n.name in {"params", "ports", "pins"}:
                    for entry in n.entries:
                        for key in entry.key.items or (entry.key,):
                            v.declaration_name(key.first, physical=n.name == "pins")
                            if key.last:
                                v.declaration_name(key.last, physical=n.name == "pins")
                v.fields(
                    n.entries,
                    n.name == "params",
                    pin_keys=n.name == "pins",
                    pins=n.name == "pins",
                )
            elif isinstance(n, a.Declaration):
                for declarator in n.declarators:
                    v.declaration_name(declarator.name)
                v.fields(n.fields)
                v.instance_sections(n.sections)
        v.fields([n for n in d.body if isinstance(n, a.Field)])
        if d.kind != "extend":
            v.errors.extend(kind_errors(d))
    return tuple(v.errors)


def kind_errors(d):
    """Structural errors that depend on a definition's kind."""
    v = Validator()
    if d.base and d.kind not in {"component", "block", "nettype"}:
        v.error(
            "syntax.inheritance-context",
            "only components, blocks, and net types may be extended",
            d,
        )
    permitted = ALLOWED_SECTIONS[d.kind]
    if d.base:
        permitted = permitted - {"params"} | ({"defaults"} if permitted else set())
    for n in d.body:
        if isinstance(n, a.Section) and n.name not in permitted:
            v.error(
                "syntax.section-context",
                f"{n.name} section is not allowed in this {d.kind} definition",
                n,
            )
        elif isinstance(n, a.Field) and d.kind != "nettype":
            v.declaration_name(n.key.first)
            if d.kind != "bundle" or d.base:
                v.error(
                    "syntax.declaration-context",
                    "bare fields are only allowed in bundle and net type definitions",
                    n,
                )
        elif isinstance(n, a.Declaration):
            if d.kind not in {"block", "board"} or d.base:
                v.error(
                    "syntax.declaration-context",
                    "internal circuit declarations require a base block or board",
                    n,
                )
            if n.global_ and d.kind not in {"board", "block"}:
                v.error("syntax.global-context", "only a board may declare globals", n)
    return tuple(v.errors)


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
