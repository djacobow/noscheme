"""Registry discovery and ERC execution for the native compiler (internal protocol v1).

Callbacks and their defaults remain in Python. Each invocation loads a fresh
registry, so a subsequent request observes plugin source edits, including edits
that preserve the file's size and timestamp. Stdout belongs to the protocol;
ordinary plugin print() output is redirected to stderr.
"""

from contextlib import redirect_stdout
from copy import deepcopy
from decimal import Decimal
import math
from pathlib import Path
import re
import json
import sys

from .ast import Span, to_data
from .builtin_help import BUILTIN_HELP
from .diagnostics import DiagnosticError
from .erc import apply_erc, report_files
from .erc_registry import exported_names, load_registry, signature
from .erc_rules import CHECKS, WAIVABLE
from .metadata import BIAS_VALUES, MISSING, NETTYPES, ROLES, SCHEMAS, UNUSED
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
        "literal_integer_digits": sys.get_int_max_str_digits(),
        "schemas": {
            scope: fields(core | registry.schemas.get(scope, {})) for scope, core in SCHEMAS.items()
        },
        "enums": {
            "role": sorted(ROLES),
            "nettype": sorted(NETTYPES),
            "unused": sorted(UNUSED),
            "bias": sorted(BIAS_VALUES),
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
                or len(number) > 20000
                or not re.fullmatch(r"-?(0|[1-9][0-9]*)", number)
            ):
                raise ValueError("invalid native metadata integer")
            # Decimal avoids Python's process-global decimal digit limit without
            # changing it. The native evaluator's default integer limit applies.
            result = int(Decimal(number))
            if result.bit_length() > 65536:
                raise ValueError("native metadata integer exceeds 65536 bits")
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
    circuit = deepcopy(source)
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


def respond(request):
    if not isinstance(request, dict) or type(request.get("version")) is not int:
        raise ValueError("request requires an integer protocol version")
    if request["version"] != VERSION:
        raise ValueError(f"unsupported bridge version {request['version']!r}")
    if request.get("method") not in ("describe_registry", "check_circuit"):
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
    configurations = request.get("configurations")
    if configurations is not None and (
        not isinstance(configurations, list)
        or any(not isinstance(name, str) for name in configurations)
    ):
        raise ValueError("configurations must be null or a list of names")
    circuit = decode_circuit(request.get("circuit"), paths)
    apply_erc(circuit, configurations, registry=registry)
    reports = report_files(circuit, Path("."))
    return {
        "version": VERSION,
        "erc": circuit["erc"],
        "reports": {path.name: text for path, text in reports.items()},
    }


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
