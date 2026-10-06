"""Initial metadata schema. Values retain source and lexical module provenance."""

from dataclasses import dataclass
import math
import re

from . import ast as a
from .project import ERC_MODULE, fail, IDENTIFIER, expression_path
from .erc_rules import WAIVABLE, CHECKS
from .units import constraint, number_value

BIAS_VALUES = {"pull_up", "pull_down", "none"}

ROLES = frozenset(
    {
        "passive",
        "digital_input",
        "digital_output",
        "digital_bidirectional",
        "analog_input",
        "analog_output",
        "analog_bidirectional",
        "power_input",
        "power_output",
        "ground",
        "open_drain",
        "open_source",
        "unchecked",
        "do_not_connect",
    }
)
NETTYPES = frozenset(
    {
        "tristate",
        "passive",
        "digital",
        "analog",
        "power",
        "ground",
        "open_drain",
        "open_source",
        "bidirectional",
        "unchecked",
    }
)
UNUSED = frozenset({"always", "explicit", "never"})
MISSING = ("warning", "error", "ignore")
NUMERIC = frozenset({"number", "positive", "nonnegative"})
PIN_SCOPES = ("pin", "instance_pin", "port")
SCHEMAS = {
    "component": {
        "footprint": "string",
        "ref_prefix": "string",
        "current_loads": "current_loads",
        "value": "expression",
        "custom": "custom",
    },
    "current_load": {"pins": "aliases", "max_load_current": "current"},
    "block": {"custom": "custom", "erc_choices": "choice_declarations"},
    "board": {
        "custom": "custom",
        "erc_choices": "choice_declarations",
        "configurations": "configurations",
    },
    "bundle": {"custom": "custom"},
    "pin": {
        "tristate_capable": "bool",
        "aliases": "aliases",
        "role": "roles",
        "unused": "unused",
        "custom": "custom",
        "max_source_current": "current",
        "max_load_current": "current",
    },
    "aggregate_load": {"max_load_current": "current"},
    "port": {
        "aggregate_load": "aggregate_load",
        "placeholder": "bool",
        "role": "roles",
        "unused": "unused",
        "waivers": "waivers",
        "custom": "custom",
    },
    "net": {"placeholder": "bool", "net_class": "string", "waivers": "waivers", "custom": "custom"},
    "instance": {
        "footprint": "string",
        "ref": "string",
        "identity": "string",
        "erc_choices": "choice_bindings",
        "value": "expression",
        "custom": "custom",
    },
    "instance_pin": {
        "role": "role",
        # Only on block ports: an explicitly modeled endpoint outside the block.
        "external_role": "roles",
        "tristate_capable": "bool",
        "custom": "custom",
    },
}
for _scope in ("pin", "port", "net", "instance_pin"):
    SCHEMAS[_scope]["checks"] = "checks"

UNIMPLEMENTED = {
    "erc",
    "differential_pair",
    "permitted_roles",
}


@dataclass(frozen=True)
class Value:
    value: object
    span: a.Span
    module: str


@dataclass(frozen=True)
class NumericExpression:
    expression: a.Expr
    constraint: str


def valid_quantity(value, constraint):
    return (
        type(value) in {int, float}
        and (not isinstance(value, float) or math.isfinite(value))
        and (constraint == "number" or (value > 0 if constraint == "positive" else value >= 0))
    )


def metadata_expressions(values):
    """Yield expressions with lexical provenance, including nested quantities."""
    for value in values.values():
        if isinstance(value.value, NumericExpression):
            yield Value(value.value.expression, value.span, value.module)
        elif isinstance(value.value, a.Node):
            yield value
        elif isinstance(value.value, dict):
            yield from metadata_expressions(value.value)


def enum(expr, namespace, choices, project, module, local_names=()):
    spelling = expression_path(expr.base) if isinstance(expr, a.Member) else None
    symbol = project.modules[module].symbols.get(spelling)
    if (
        (spelling or "").split(".")[0] in local_names
        or symbol is None
        or symbol.module != "noscheme"
        or symbol.name != namespace
        or not isinstance(expr, a.Member)
        or expr.name.text not in choices
    ):
        fail(
            "metadata.enum",
            f"expected an explicitly imported {namespace} value from: {', '.join(sorted(choices))}",
            expr.span,
        )
    return expr.name.text


def literal(expr):
    if isinstance(expr, a.String):
        return expr.value
    if isinstance(expr, a.Number):
        try:
            value = number_value(expr.spelling)
        except ValueError:
            fail("metadata.value", "metadata number exceeds the supported literal size", expr.span)
        if isinstance(value, float) and not math.isfinite(value):
            fail("metadata.value", "metadata numbers must be finite", expr.span)
        return value
    if isinstance(expr, a.Unary) and isinstance(expr.operand, a.Number):
        value = literal(expr.operand)
        return -value if expr.operator == "-" else value
    if isinstance(expr, a.Name) and expr.text in {"true", "false"}:
        return expr.text == "true"
    if isinstance(expr, a.ListExpr):
        return tuple(literal(x) for x in expr.items)
    fail("metadata.value", "expected a literal metadata value", expr.span)


def read_metadata(fields, scope, project, module, local_names=()):
    result = {}
    for field in fields:
        key = field.key.first.text
        if field.key.grouped:
            fail("metadata.key", "metadata field names cannot be ranges", field.key.span)
        if isinstance(scope, str) and scope in {"custom", "attributes"}:
            kind = scope if field.fields else "literal"
        else:
            schema = scope if isinstance(scope, dict) else SCHEMAS[scope]
            extension = (
                {}
                if isinstance(scope, dict)
                else project.config.erc_registry.schemas.get(scope, {})
            )
            if schema.keys() & extension.keys():
                fail("metadata.schema", "ERC module duplicates core metadata fields", field.span)
            kind = schema.get(key, extension.get(key))
            if kind is None:
                code = "metadata.unsupported" if key in UNIMPLEMENTED else "metadata.unknown"
                fail(
                    code,
                    f"{key!r} is not supported in {scope} metadata; user data belongs under custom",
                    field.span,
                )
        expr = field.value
        if isinstance(kind, dict):
            if expr is not None or not field.fields:
                fail("metadata.value", f"{key!r} requires an indented mapping", field.span)
            value = read_metadata(field.fields, kind, project, module, local_names)
        elif isinstance(kind, tuple):
            if expr is None or field.fields:
                fail("metadata.value", f"{key!r} requires an enum value", field.span)
            value = read_value(key, kind, expr, scope, project, module, local_names)
        elif kind in {"choice_declarations", "choice_bindings", "configurations"}:
            value = read_choices(field, kind, module)
        elif scope == "instance_pin" and kind in {"roles", "role"} and field.fields:
            value = read_selection(
                field, module, lambda expr: enum(expr, "role", ROLES, project, module, local_names)
            )
        elif kind == "aggregate_load":
            if expr is not None or not field.fields:
                fail(
                    "metadata.current_load",
                    "aggregate_load requires max_load_current",
                    field.span,
                )
            value = read_metadata(field.fields, "aggregate_load", project, module, local_names)
        elif kind == "current_loads":
            value = {}
            for name, group in mapping(field).items():
                entries = read_metadata(group.fields, "current_load", project, module, local_names)
                if group.value is not None or not entries:
                    fail(
                        "metadata.current_load",
                        "each current load requires pins and max_load_current",
                        group.span,
                    )
                if "pins" in entries and not entries["pins"].value:
                    fail("metadata.current_load", "current load pins cannot be empty", group.span)
                value[name] = Value(entries, group.span, module)
        elif kind == "checks":
            value = read_checks(field, scope, project, module, local_names)
        elif kind == "attributes":
            if expr is not None or not field.fields:
                fail("metadata.value", f"{key!r} requires an indented mapping", field.span)
            value = read_metadata(field.fields, "attributes", project, module, local_names)
        elif kind == "waivers":
            if expr is not None or not field.fields:
                fail(
                    "metadata.waiver",
                    "waivers requires check names with nonempty string reasons",
                    field.span,
                )
            value = {}
            for waiver in mapping(field).values():
                check = waiver.key.first.text
                if ":" in check:
                    fail(
                        "metadata.waiver",
                        "waive an application by its name, and restrict it to physical pins "
                        "with reason and pins fields",
                        waiver.span,
                    )
                if waiver.key.last or check in set(CHECKS) - WAIVABLE:
                    fail(
                        "metadata.waiver",
                        f"{check!r} is not a waivable implemented net check",
                        waiver.span,
                    )
                reason = waiver.value
                targets = None
                if waiver.fields:
                    entries = mapping(waiver)
                    if reason is not None or set(entries) != {"reason", "pins"}:
                        fail(
                            "metadata.waiver",
                            "targeted waivers require reason and pins fields",
                            waiver.span,
                        )
                    reason = entries["reason"].value
                    pins = entries["pins"]
                    if (
                        entries["reason"].fields
                        or pins.fields
                        or not isinstance(pins.value, a.ListExpr)
                    ):
                        fail(
                            "metadata.waiver",
                            "pins requires a nonempty list of literal pin paths",
                            waiver.span,
                        )
                    targets = [
                        p.value
                        for p in pins.value.items
                        if isinstance(p, a.String) and p.value.strip()
                    ]
                    if (
                        not targets
                        or len(targets) != len(pins.value.items)
                        or len(set(targets)) != len(targets)
                    ):
                        fail(
                            "metadata.waiver",
                            "pins requires distinct nonempty literal pin paths",
                            pins.span,
                        )
                if not isinstance(reason, a.String) or not reason.value.strip():
                    fail(
                        "metadata.waiver",
                        "each waiver requires a nonempty literal string reason",
                        waiver.span,
                    )
                data = (
                    reason.value
                    if targets is None
                    else {
                        "reason": Value(reason.value, reason.span, module),
                        "pins": Value(targets, pins.span, module),
                    }
                )
                value[check] = Value(data, waiver.span, module)
        elif kind == "custom":
            if expr is not None:
                fail("metadata.value", "custom mappings use indented fields", field.span)
            value = read_metadata(field.fields, "custom", project, module, local_names)
        else:
            if expr is None or field.fields:
                fail("metadata.value", f"{key!r} requires a value, not nested fields", field.span)
            value = read_value(key, kind, expr, scope, project, module, local_names)
        result[key] = Value(value, field.span, module)
    return result


def read_value(key, kind, expr, scope, project, module, local_names=()):
    """Read one scalar metadata or rule-argument value."""
    if isinstance(kind, tuple):
        namespace, alternatives = kind
        if isinstance(expr, a.String) and expr.value in alternatives:
            return expr.value
        return enum(expr, namespace, alternatives, project, module, local_names)
    if kind == "expression":
        return expr  # Evaluated later, in the retained lexical module.
    if kind in {"roles", "role"}:
        items = expr.items if kind == "roles" and isinstance(expr, a.ListExpr) else (expr,)
        if not items:
            fail("metadata.value", "role list cannot be empty", expr.span)
        value = tuple(enum(x, "role", ROLES, project, module, local_names) for x in items)
    elif kind == "unused":
        value = enum(expr, "unused", UNUSED, project, module, local_names)
    elif kind == "aliases":
        if not isinstance(expr, a.ListExpr):
            fail("metadata.value", "aliases must be a list of names or strings", expr.span)
        value = tuple(
            x.text if isinstance(x, a.Name) else x.value if isinstance(x, a.String) else None
            for x in expr.items
        )
        if any(x is None or not x for x in value):
            fail("metadata.value", "aliases must be nonempty names or strings", expr.span)
    elif constraint(kind) in NUMERIC and not (
        isinstance(expr, (a.Number, a.String, a.ListExpr))
        or isinstance(expr, a.Unary)
        and isinstance(expr.operand, a.Number)
        or isinstance(expr, a.Name)
        and expr.text in {"true", "false"}
    ):
        value = NumericExpression(expr, constraint(kind))
    else:
        if scope == "attributes" and isinstance(expr, a.Member):
            symbol = project.modules[module].symbols.get(expression_path(expr.base))
            alternatives = {
                "bias": BIAS_VALUES,
                "role": ROLES,
                "nettype": NETTYPES,
                "unused": UNUSED,
            }
            if symbol is None or symbol.name not in alternatives:
                fail("metadata.value", "expected a literal or imported enum value", expr.span)
            value = enum(expr, symbol.name, alternatives[symbol.name], project, module, local_names)
        else:
            value = literal(expr)
        valid = (
            kind == "literal"
            or kind == "string"
            and isinstance(value, str)
            or kind == "bool"
            and isinstance(value, bool)
            or constraint(kind) in NUMERIC
            and valid_quantity(value, constraint(kind))
        )
        if not valid:
            fail("metadata.value", f"{key!r} requires {kind} metadata", expr.span)
    return value


# Builtin namespaces whose values may stand alone in a pin entry, and the field each sets.
SHORTHAND = {"role": "role", "bias": "bias", "unused": "unused"}


def pin_metadata(field, scope, project, module, local_names=()):
    """Read a pin entry: builtin values in its value position, then indented fields.

    `3: [role.open_drain, bias.none]` sets role and bias; several values from one
    namespace form a list, such as a definition's permitted roles.
    """
    result = read_metadata(field.fields, scope, project, module, local_names)
    if field.value is None:
        return result
    symbols = project.modules[module].symbols
    groups = {}
    expr = field.value
    for item in expr.items if isinstance(expr, a.ListExpr) else (expr,):
        base = expression_path(item.base) if isinstance(item, a.Member) else None
        symbol = symbols.get(base) if base and base.split(".")[0] not in local_names else None
        if symbol is None or symbol.module != "noscheme" or symbol.name not in SHORTHAND:
            fail(
                "metadata.shorthand",
                "a pin entry's value lists builtin role, bias, or unused values",
                item.span,
            )
        groups.setdefault(SHORTHAND[symbol.name], []).append(item)
    schema = SCHEMAS[scope] | project.config.erc_registry.schemas.get(scope, {})
    for key, items in groups.items():
        if key in result or key not in schema:
            fail(
                "metadata.shorthand",
                f"{key!r} is already set or not allowed in {scope} metadata",
                items[0].span,
            )
        if len(items) > 1 and schema[key] != "roles":
            fail("metadata.shorthand", f"{key!r} takes one value", items[1].span)
        value = items[0] if len(items) == 1 else a.ListExpr(expr.span, tuple(items))
        result[key] = Value(
            read_value(key, schema[key], value, scope, project, module, local_names),
            value.span,
            module,
        )
    return result


def merge(parent, child, *, _path=()):
    result = dict(parent)
    for key, value in child.items():
        previous = result.get(key)
        if (
            previous
            and key == "checker"
            and _path[-2:-1] == ("checks",)
            and "custom" not in _path
            and previous.value != value.value
        ):
            fail(
                "erc.application_checker",
                "an inherited application cannot change checker; use a new application name",
                value.span,
            )
        if previous and isinstance(previous.value, dict) and isinstance(value.value, dict):
            value = Value(
                merge(previous.value, value.value, _path=(*_path, key)), value.span, value.module
            )
        result[key] = value
    return result


def mapping(field):
    if field.value is not None or not field.fields:
        fail("metadata.erc", "expected a nonempty indented mapping", field.span)
    result = {}
    for child in field.fields:
        name = child.key.first.text
        if child.key.grouped or name in result:
            fail("metadata.erc", "mapping keys must be distinct names", child.span)
        result[name] = child
    return result


def choice_string(expr):
    if not isinstance(expr, a.String) or not expr.value.strip():
        fail("metadata.erc", "expected a nonempty literal string", expr.span)
    return expr.value


def read_selection(field, module, read_case=choice_string):
    fields = mapping(field)
    if set(fields) != {"choice", "cases"}:
        fail("metadata.erc", "a selection requires exactly choice and cases", field.span)
    choice = fields["choice"]
    if choice.value is None or choice.fields:
        fail("metadata.erc", "choice requires a local choice name string", choice.span)
    name = choice_string(choice.value)
    if not re.fullmatch(IDENTIFIER, name):
        fail(
            "metadata.erc", "choice references must be local names, not instance paths", choice.span
        )
    cases = {}
    for name, case in mapping(fields["cases"]).items():
        if case.value is None or case.fields:
            fail("metadata.erc", "each case requires one value", case.span)
        cases[name] = Value(read_case(case.value), case.span, module)
    return {
        "choice": Value(choice_string(choice.value), choice.span, module),
        "cases": Value(cases, fields["cases"].span, module),
    }


def read_choices(field, kind, module):
    result = {}
    for name, entry in mapping(field).items():
        if not re.fullmatch(IDENTIFIER, name):
            fail(
                "metadata.erc", "ERC choice and configuration names must be identifiers", entry.span
            )
        if kind == "configurations":
            value = read_choices(entry, "configuration_values", module)
        elif kind == "choice_declarations":
            if not isinstance(entry.value, a.ListExpr) or entry.fields or not entry.value.items:
                fail(
                    "metadata.erc",
                    "a public choice requires a nonempty list of string alternatives",
                    entry.span,
                )
            value = tuple(choice_string(x) for x in entry.value.items)
            if len(set(value)) != len(value):
                fail("metadata.erc", "choice alternatives must be distinct", entry.span)
        elif entry.fields and kind == "choice_bindings":
            value = read_selection(entry, module)
        else:
            if entry.value is None or entry.fields:
                fail("metadata.erc", "choice selection requires a string", entry.span)
            value = choice_string(entry.value)
        result[name] = Value(value, entry.span, module)
    return result


def possible_selections(value):
    if isinstance(value.value, dict):
        return {case.value for case in value.value["cases"].value.values()}
    return {value.value} if isinstance(value.value, str) else set(value.value)


def validate_selection(value, choices):
    if not isinstance(value.value, dict):
        return
    selection = value.value
    choice = selection["choice"]
    if choice.value not in choices:
        fail("erc.unknown_choice", f"unknown local ERC choice {choice.value!r}", choice.span)
    if set(selection["cases"].value) != set(choices[choice.value].value):
        fail(
            "erc.choice_cases",
            "cases must cover every declared choice alternative exactly",
            selection["cases"].span,
        )


def declared_choices(definition):
    value = definition.metadata.get("erc_choices")
    return value.value if value else {}


def registered_name(expr, project, module, local_names=()):
    """Resolve an imported rule or predicate reference to its registered name, or None.

    `from noscheme import std` exposes std.NAME; an import of a project ERC
    namespace binds its registered names like module members.
    """
    path = expression_path(expr)
    if not path or path.split(".")[0] in local_names:
        return None
    symbols = project.modules[module].symbols
    symbol = symbols.get(path)
    if symbol is not None and symbol.module == ERC_MODULE:
        return symbol.name
    base, _, name = path.rpartition(".")
    symbol = symbols.get(base)
    if symbol is not None and symbol.module == "noscheme" and symbol.name == "std":
        return "std." + name
    return None


def endpoint_fields(project, scopes=PIN_SCOPES):
    """Core and registered field kinds for the given metadata scopes."""
    registry = project.config.erc_registry
    result = {}
    for scope in scopes:
        result.update(SCHEMAS[scope], **registry.schemas.get(scope, {}))
    return result


def read_selector(expr, project, module, local_names=()):
    """Read a predicate, role or bias value, boolean field name, or a list of them."""
    symbols = project.modules[module].symbols
    fields = endpoint_fields(project)
    terms = []
    for item in expr.items if isinstance(expr, a.ListExpr) else (expr,):
        name = registered_name(item, project, module, local_names)
        base = symbols.get(expression_path(item.base)) if isinstance(item, a.Member) else None
        if name in project.config.erc_registry.predicates:
            terms.append(("predicate", name))
        elif base is not None and base.module == "noscheme" and base.name == "role":
            terms.append(("role", enum(item, "role", ROLES, project, module, local_names)))
        elif base is not None and base.module == "noscheme" and base.name == "bias":
            value = enum(item, "bias", BIAS_VALUES, project, module, local_names)
            terms.append(("field", "bias", value))
        elif isinstance(item, a.Name) and fields.get(item.text) == "bool":
            terms.append(("field", item.text, True))
        else:
            fail(
                "erc.application",
                "a selector is a predicate, a role or bias value, a boolean field name, "
                "or a list of them",
                item.span,
            )
    return tuple(terms)


def read_argument(key, kind, expr, project, module, local_names=()):
    if kind == "field":
        if not isinstance(expr, a.Name) or expr.text not in endpoint_fields(project):
            fail("erc.application", f"{key!r} requires an endpoint field name", expr.span)
        return expr.text
    if kind == "net_fields":
        items = expr.items if isinstance(expr, a.ListExpr) else (expr,)
        names = endpoint_fields(project, ("net",))
        if any(not isinstance(x, a.Name) or x.text not in names for x in items):
            fail("erc.application", f"{key!r} requires net field names", expr.span)
        return tuple(x.text for x in items)
    if kind == "selector":
        return read_selector(expr, project, module, local_names)
    return read_value(key, kind, expr, "rule", project, module, local_names)


def rule_call(call, project, module, local_names=()):
    """Bind a check call's arguments to its rule's parameters.

    Returns (registered name, rule, {parameter: expression}, missing expression).
    Positional arguments fill parameters in registration order.
    """
    registry = project.config.erc_registry
    name = (
        registered_name(call.callee, project, module, local_names)
        if isinstance(call, a.Call)
        else None
    )
    if name not in registry.rules:
        fail(
            "erc.application",
            "a check calls an imported rule, such as std.sum(capacitance, max=400p)",
            (call.callee if isinstance(call, a.Call) else call).span,
        )
    rule = registry.rules[name]
    if len(call.arguments) > len(rule.parameters):
        fail("erc.application", f"too many positional arguments for {name}", call.span)
    arguments = dict(zip(rule.parameters, call.arguments))
    missing = None
    for keyword in call.keywords:
        key = keyword.name.text
        if key != "missing" and key not in rule.parameters:
            fail("erc.application", f"{name} has no parameter {key!r}", keyword.span)
        if key in arguments or key == "missing" and missing is not None:
            fail("erc.application", f"duplicate argument {key!r}", keyword.span)
        if key == "missing":
            missing = keyword.value
        else:
            arguments[key] = keyword.value
    return name, rule, arguments, missing


def read_checks(field, scope, project, module, local_names):
    result = {}
    expected = "pin" if scope in {"pin", "instance_pin"} else "net"
    for name, entry in mapping(field).items():
        if entry.fields or entry.value is None or entry.default is not None:
            fail(
                "erc.application",
                "each application is one rule call, such as load: std.sum(capacitance, max=400p)",
                entry.span,
            )
        call = entry.value
        checker, rule, arguments, missing = rule_call(call, project, module, local_names)
        if rule.scope != expected:
            fail(
                "erc.application",
                f"{checker} is a {rule.scope} rule, not a {expected} rule",
                call.callee.span,
            )
        values = {"checker": Value(checker, call.callee.span, module)}
        if arguments:
            values["params"] = Value(
                {
                    key: Value(
                        read_argument(
                            key, rule.parameters[key], expr, project, module, local_names
                        ),
                        expr.span,
                        module,
                    )
                    for key, expr in arguments.items()
                },
                call.span,
                module,
            )
        if missing is not None:
            if not isinstance(missing, a.String) or missing.value not in MISSING:
                fail(
                    "erc.application",
                    'missing must be "warning", "error", or "ignore"',
                    missing.span,
                )
            values["missing"] = Value(missing.value, missing.span, module)
        result[name] = Value(values, entry.span, module)
    return result
