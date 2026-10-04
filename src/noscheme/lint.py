"""Optional source lint; no elaboration, ERC, or build artifacts."""

from dataclasses import fields

from . import ast as a
from .diagnostics import Diagnostic


def expression_names(expression):
    if isinstance(expression, a.Name):
        yield expression.text
    elif isinstance(expression, a.Member):
        yield from expression_names(expression.base)
    elif isinstance(expression, a.KeywordArgument):
        yield from expression_names(expression.value)
    elif isinstance(expression, a.Node):
        for field in fields(expression):
            value = getattr(expression, field.name)
            if isinstance(value, a.Node):
                yield from expression_names(value)
            elif isinstance(value, tuple):
                for child in value:
                    yield from expression_names(child)


def parameter_uses(node):
    if isinstance(node, a.Field):
        if (
            node.key.first.text == "custom"
            and node.fields
            or (node.key.first.text == "aliases" and isinstance(node.value, a.ListExpr))
        ):
            return
        yield from expression_names(node.value)
        yield from expression_names(node.default)
        for child in node.fields:
            yield from parameter_uses(child)
    elif isinstance(node, a.Argument):
        yield from expression_names(node.value)
    elif isinstance(node, a.Binding):
        yield from expression_names(node.target)
        for selector in (*node.endpoint.instance_indices, *node.endpoint.selectors):
            if isinstance(selector, a.Index):
                yield from expression_names(selector)
    elif isinstance(node, a.Net):
        yield from expression_names(node.type)
        for field in node.fields:
            yield from parameter_uses(field)
    elif isinstance(node, (a.Instance, a.ElementOverride)):
        if isinstance(node, a.Instance):
            for dimension in node.dimensions:
                yield from expression_names(dimension)
        for section in node.sections:
            yield from parameter_uses(section)
    elif isinstance(node, a.Section):
        for entry in node.entries:
            yield from parameter_uses(entry)


def unused_parameters(definitions):
    declared, used = {}, set()
    for definition in definitions.values():
        for name, parameter in definition.parameters.items():
            declared[parameter.span] = (name, parameter)
        names = {name for node in definition.source.body for name in parameter_uses(node)}
        if definition.kind == "component" and "value" not in definition.metadata:
            names.add("value")  # Conventional displayed part value.
        used.update(
            definition.parameters[name].span for name in names if name in definition.parameters
        )
    return tuple(
        Diagnostic(
            "lint.unused-parameter",
            "warning",
            f"parameter {name!r} is not referenced; it may be an intentional public API placeholder",
            span,
        )
        for span, (name, _) in sorted(
            declared.items(), key=lambda entry: (entry[0].filename, entry[0].start_pos)
        )
        if span not in used
    )
