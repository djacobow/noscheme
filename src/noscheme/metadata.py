"""Initial metadata schema. Values retain source and lexical module provenance."""

from dataclasses import dataclass
import math
import re

from . import ast as a
from .project import fail, IDENTIFIER
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
        "open_source",
        "unchecked",
        "do_not_connect",
    }
)
NETTYPES = frozenset(
    {
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
SCHEMAS = {
    "component": {
        "footprint": "string",
        "ref_prefix": "string",
        "current_loads": "current_loads",
        "value": "expression",
        "custom": "custom",
    },
    "current_load": {"pins": "aliases", "max_load_current_mA": "nonnegative"},
    "block": {"custom": "custom", "erc_choices": "choice_declarations"},
    "board": {
        "custom": "custom",
        "erc_choices": "choice_declarations",
        "configurations": "configurations",
    },
    "bundle": {"custom": "custom"},
    "pin": {
        "aliases": "aliases",
        "role": "roles",
        "unused": "unused",
        "custom": "custom",
        "max_source_current_mA": "nonnegative",
        "max_load_current_mA": "nonnegative",
    },
    "aggregate_load": {"max_load_current_mA": "nonnegative"},
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
    "instance_pin": {"role": "role", "external_role": "roles", "custom": "custom"},
}
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
            namespace, alternatives = kind
            value = enum(expr, namespace, alternatives, project, module, local_names)
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
                    "aggregate_load requires max_load_current_mA",
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
                        "each current load requires pins and max_load_current_mA",
                        group.span,
                    )
                if "pins" in entries and not entries["pins"].value:
                    fail("metadata.current_load", "current load pins cannot be empty", group.span)
                value[name] = Value(entries, group.span, module)
        elif kind == "waivers":
            if expr is not None or not field.fields:
                fail(
                    "metadata.waiver",
                    "waivers requires check names with nonempty string reasons",
                    field.span,
                )
            value = {}
            for waiver in field.fields:
                check = waiver.key.first.text
                if (
                    waiver.key.last
                    or check not in WAIVABLE | project.config.erc_registry.checks.keys()
                ):
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
                    or kind in {"positive", "nonnegative"}
                    and type(value) in {int, float}
                    and (value > 0 if kind == "positive" else value >= 0)
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


def mapping(field):
    if field.value is not None or not field.fields:
        fail("metadata.erc", "expected a nonempty indented mapping", field.span)
    result = {}
    for child in field.fields:
        name = child.key.first.text
        if child.key.last or name in result:
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
