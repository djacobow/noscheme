"""Registry discovery and plugin ERC work for the native compiler (internal protocol v1).

The native compiler runs standard rules itself. Plugin rules, rule sets, and
predicates, with their defaults, remain in Python and run here. Each invocation
loads a fresh registry, so a subsequent request observes plugin source edits,
including edits that preserve the file's size and timestamp. Stdout belongs to
the protocol; ordinary plugin print() output is redirected to stderr.
"""

from collections.abc import Mapping
from contextlib import redirect_stdout
from copy import deepcopy
import math
import re

import json
import sys

from .integers import checked_integer
from .ast import Span, to_data
from .builtin_help import BUILTIN_HELP
from .diagnostics import DiagnosticError
from .erc import Scheduler
from .erc_config import configuration_views
from .erc_registry import exported_names, load_registry, signature
from .erc_rules import CHECKS, WAIVABLE
from .metadata import DRIVE_VALUES, PULL_VALUES, MISSING, NETTYPES, ROLES, SCHEMAS, USE_POLICIES
from .units import QUANTITIES

VERSION = 1


def schema(kind):
    if isinstance(kind, dict):
        return {"kind": "mapping", "fields": fields(kind)}
    if isinstance(kind, tuple):
        return {"kind": "enum", "namespace": kind[0], "values": sorted(kind[1])}
    return {"kind": kind}


def fields(mapping):
    # Lists preserve positional rule-argument order across JSON implementations.
    return [{"name": name, "schema": schema(kind)} for name, kind in mapping.items()]


def describe_registry(registry):
    return {
        "help": deepcopy(BUILTIN_HELP),
        "schemas": {
            scope: fields(core | registry.schemas.get(scope, {})) for scope, core in SCHEMAS.items()
        },
        "enums": {
            "role": sorted(ROLES),
            "net": sorted(NETTYPES),
            "use": sorted(USE_POLICIES),
            "pull": sorted(PULL_VALUES),
            "drive": sorted(DRIVE_VALUES),
            "missing": list(MISSING),
        },
        "quantities": {
            name: {"unit": unit, "constraint": constraint}
            for name, (unit, constraint) in QUANTITIES.items()
        },
        "rules": {
            name: {
                "scope": rule.scope,
                "parameters": [
                    item | {"has_default": item["name"] in rule.defaults}
                    for item in fields(rule.parameters)
                ],
                "waivable": rule.waivable,
                "net_only": rule.net_only,
                "signature": signature(name, rule),
            }
            for name, rule in registry.rules.items()
        },
        "predicates": sorted(registry.predicates),
        "reducers": sorted(registry.reducers),
        "rule_sets": sorted(registry.rule_sets),
        # Registration order; reports list implemented checks in this order.
        "rule_order": list(registry.rules),
        "checks": list(CHECKS),
        "waivable_checks": sorted(WAIVABLE),
        "exports": exported_names(registry),
    }


def decode_metadata(value):
    """Decode only native metadata; parameter encodings and source ASTs stay intact."""
    if isinstance(value, list):
        return [decode_metadata(item) for item in value]
    if not isinstance(value, dict):
        return value
    if value.get("type") == "number":
        if set(value) != {"type", "tag", "value"}:
            raise ValueError("invalid native metadata number")
        number = value["value"]
        if value["tag"] == "integer":
            if (
                not isinstance(number, str)
                or len(number) > 20
                or not re.fullmatch(r"-?(0|[1-9][0-9]*)", number)
            ):
                raise ValueError("invalid native metadata integer")
            result = checked_integer(int(number))
            return result
        if value["tag"] == "double" and type(number) in (int, float) and math.isfinite(number):
            return float(number)
        raise ValueError("invalid native metadata numeric tag or value")
    return {key: decode_metadata(item) for key, item in value.items()}


def decode_circuit(source, paths):
    if (
        not isinstance(source, dict)
        or type(source.get("schema_version")) is not int
        or source["schema_version"] != 1
        or source.get("stage") != "circuit"
    ):
        raise ValueError("expected a native circuit with schema_version 1")
    if source.get("erc_modules") != paths:
        raise ValueError("circuit ERC modules differ from request")
    # The request owns the circuit, so it is decoded in place.
    circuit = source
    for part in circuit["parts"]:
        part["metadata"] = decode_metadata(part["metadata"])
        for pin in part["pins"].values():
            pin["metadata"] = decode_metadata(pin["metadata"])
    for net in circuit["nets"]:
        for declaration in net["declarations"]:
            declaration["metadata"] = decode_metadata(declaration["metadata"])
    circuit.pop("erc", None)
    circuit["erc_performed"] = False
    return circuit


def encode_arguments(value):
    """JSON arguments with exact integers, including nested selector values."""
    if type(value) is int:
        return {"type": "number", "tag": "integer", "value": str(checked_integer(value))}
    if isinstance(value, Mapping):
        return {key: encode_arguments(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode_arguments(item) for item in value]
    return value


def plugin_predicates(params, native):
    for item in params.values() if isinstance(params, Mapping) else ():
        for term in item if isinstance(item, (list, tuple)) else ():
            if (
                isinstance(term, (list, tuple))
                and len(term) == 2
                and term[0] == "predicate"
                and term[1] not in native
            ):
                yield term[1]


def plugin_checks(request, registry, paths):
    """Run what the native engine cannot: plugin rule sets, rules, and predicates.

    Results follow the shared schedule, keyed by owner or application identity.
    The native engine applies severities, waivers, and diagnostic order.
    """
    native_rules = set(request["native_rules"])
    native_predicates = set(request["native_predicates"])
    names = request["configurations"]
    if not isinstance(names, list) or any(n is not None and not isinstance(n, str) for n in names):
        raise ValueError("configurations must be a list of names")
    circuit = decode_circuit(request.get("circuit"), paths)
    requested = [n for n in names if n is not None] or None
    rule_sets = [n for n in circuit.get("erc_rule_sets", ["std.default"]) if n != "std.default"]
    freeze_memo, configurations = {}, []
    for name, view, selections in configuration_views(circuit, requested):
        view["erc_configuration"] = name
        view["erc_effective_choices"] = selections
        scheduler = Scheduler(view, registry, freeze_memo, rule_sets=rule_sets)
        schedules = scheduler.schedules()
        applications = {}
        for (kind, label, rule_set), items in scheduler.rule_set_cache.items():
            entries = []
            for item in items:
                app, span = item if isinstance(item, tuple) else (item, None)
                entries.append(
                    {
                        "name": app.name,
                        "checker": app.checker,
                        "params": encode_arguments(app.params),
                        "missing": app.missing,
                        "span": to_data(span) if span is not None else None,
                    }
                )
            applications.setdefault(f"{kind}:{label}", {})[rule_set] = entries
        results, predicates = {}, {}
        for entries in schedules.values():
            for app, rule, active in entries:
                if app.checker not in native_rules:
                    try:
                        diagnostics = scheduler.invoke(app, rule, active)
                        results[active.application] = {
                            "diagnostics": [to_data(d) for d in diagnostics]
                        }
                    except DiagnosticError as exc:
                        results[active.application] = {"failure": exc.diagnostics[0].message}
                    continue
                for predicate in plugin_predicates(app.params, native_predicates):
                    values = {}
                    for pin in active.pins:
                        try:
                            value = active.predicates[predicate](pin, active)
                            if value is not True and value is not False and value is not None:
                                raise ValueError("predicate must return True, False, or None")
                        except Exception as exc:
                            value = {"failure": str(exc)}
                        values[pin.label] = value
                    predicates.setdefault(active.application, {})[predicate] = values
        configurations.append(
            {"applications": applications, "results": results, "predicates": predicates}
        )
    return configurations


def respond(request):
    if not isinstance(request, dict) or type(request.get("version")) is not int:
        raise ValueError("request requires an integer protocol version")
    if request["version"] != VERSION:
        raise ValueError(f"unsupported bridge version {request['version']!r}")
    if request.get("method") not in ("describe_registry", "plugin_checks"):
        raise ValueError("unsupported bridge method")
    paths = request.get("erc_modules", [])
    if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
        raise ValueError("erc_modules must be a list of filenames")
    span = (
        Span(**request["span"])
        if request.get("span") is not None
        else Span(
            filename="<registry>",
            start_pos=0,
            end_pos=0,
            line=1,
            column=1,
            end_line=1,
            end_column=1,
        )
    )
    registry = load_registry(paths, span)
    description = describe_registry(registry)
    if request["method"] == "describe_registry":
        return {"version": VERSION, "registry": description}
    if request.get("registry") != description:
        raise ValueError("ERC registry changed since native resolution; rebuild the circuit")
    return {"version": VERSION, "configurations": plugin_checks(request, registry, paths)}


def main():
    try:
        request = json.load(sys.stdin)
        with redirect_stdout(sys.stderr):
            response = respond(request)
    except DiagnosticError as exc:
        response = {"version": VERSION, "diagnostics": [to_data(d) for d in exc.diagnostics]}
    except Exception as exc:
        response = {"version": VERSION, "error": f"{type(exc).__name__}: {exc}"}
    json.dump(response, sys.stdout, allow_nan=False, ensure_ascii=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
