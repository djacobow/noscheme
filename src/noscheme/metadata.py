"""Initial metadata schema. Values retain source and lexical module provenance."""

from dataclasses import dataclass
import math

from . import ast as a
from .project import fail
from .erc_rules import WAIVABLE

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
        "unchecked",
        "do_not_connect",
    }
)
NETTYPES = frozenset(
    {"passive", "digital", "analog", "power", "ground", "open_drain", "bidirectional", "unchecked"}
)
UNUSED = frozenset({"always", "explicit", "never"})
SCHEMAS = {
    "component": {
        "footprint": "string",
        "ref_prefix": "string",
        "value": "expression",
        "custom": "custom",
    },
    "block": {"custom": "custom"},
    "board": {"custom": "custom"},
    "bundle": {"custom": "custom"},
    "pin": {
        "aliases": "aliases",
        "role": "roles",
        "unused": "unused",
        "custom": "custom",
        "max_source_current_mA": "nonnegative",
        "max_load_current_mA": "nonnegative",
    },
    "port": {
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
        "value": "expression",
        "custom": "custom",
    },
    "instance_pin": {"role": "role", "external_role": "roles", "custom": "custom"},
}
UNIMPLEMENTED = {
    "erc",
    "erc_choices",
    "configurations",
    "differential_pair",
    "aggregate_load",
    "permitted_roles",
    "capacitance_pF",
}


@dataclass(frozen=True)
class Value:
    value: object
    span: a.Span
    module: str


def enum(expr, namespace, choices, project, module, local_names=()):
    symbol = project.modules[module].symbols.get(namespace)
    if (
        namespace in local_names
        or symbol is None
        or symbol.module != "noscheme"
        or symbol.name != namespace
        or not isinstance(expr, a.Member)
        or not isinstance(expr.base, a.Name)
        or expr.base.text != namespace
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
            value = int(expr.spelling) if expr.tag == "integer" else float(expr.spelling)
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
        if field.key.last:
            fail("metadata.key", "metadata field names cannot be ranges", field.key.span)
        if scope == "custom":
            kind = "custom" if field.fields else "literal"
        else:
            kind = SCHEMAS[scope].get(key)
            if kind is None:
                code = "metadata.unsupported" if key in UNIMPLEMENTED else "metadata.unknown"
                fail(
                    code,
                    f"{key!r} is not supported in {scope} metadata; user data belongs under custom",
                    field.span,
                )
        expr = field.value
        if kind == "waivers":
            if expr is not None or not field.fields:
                fail(
                    "metadata.waiver",
                    "waivers requires check names with nonempty string reasons",
                    field.span,
                )
            value = {}
            for waiver in field.fields:
                check = waiver.key.first.text
                if waiver.key.last or check not in WAIVABLE:
                    fail(
                        "metadata.waiver",
                        f"{check!r} is not a waivable implemented net check",
                        waiver.span,
                    )
                if (
                    waiver.fields
                    or not isinstance(waiver.value, a.String)
                    or not waiver.value.value.strip()
                ):
                    fail(
                        "metadata.waiver",
                        "each waiver requires a nonempty literal string reason",
                        waiver.span,
                    )
                value[check] = Value(waiver.value.value, waiver.span, module)
        elif kind == "custom":
            if expr is not None:
                fail("metadata.value", "custom mappings use indented fields", field.span)
            value = read_metadata(field.fields, "custom", project, module, local_names)
        else:
            if expr is None or field.fields:
                fail("metadata.value", f"{key!r} requires a value, not nested fields", field.span)
            if kind == "expression":
                value = expr  # Evaluated later, in the retained lexical module.
            elif kind in {"roles", "role"}:
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
                    x.text
                    if isinstance(x, a.Name)
                    else x.value
                    if isinstance(x, a.String)
                    else None
                    for x in expr.items
                )
                if any(x is None or not x for x in value):
                    fail("metadata.value", "aliases must be nonempty names or strings", expr.span)
            else:
                value = literal(expr)
                valid = (
                    kind == "literal"
                    or kind == "string"
                    and isinstance(value, str)
                    or kind == "bool"
                    and isinstance(value, bool)
                    or kind == "nonnegative"
                    and type(value) in {int, float}
                    and value >= 0
                )
                if not valid:
                    fail("metadata.value", f"{key!r} requires {kind} metadata", expr.span)
        result[key] = Value(value, field.span, module)
    return result


def merge(parent, child):
    result = dict(parent)
    for key, value in child.items():
        previous = result.get(key)
        if previous and isinstance(previous.value, dict) and isinstance(value.value, dict):
            value = Value(merge(previous.value, value.value), value.span, value.module)
        result[key] = value
    return result
