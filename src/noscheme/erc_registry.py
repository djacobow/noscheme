"""Project-local ERC schemas and net checks; extensions never change the grammar."""

from dataclasses import dataclass
from copy import deepcopy
import importlib.util
import sys

from .ast import Span
from .diagnostics import Diagnostic, DiagnosticError


def value(record, key, default=None):
    return record.get("metadata", {}).get(key, {}).get("value", default)


@dataclass
class NetContext:
    circuit: dict
    net: dict
    endpoints: list
    origin: dict

    def diagnostic(self, code, message, *, record=None, severity="error", related=()):
        record = record or self.origin
        return Diagnostic("erc." + code, severity, message, Span(**record["span"]), tuple(related))


class Registry:
    def __init__(self):
        self.schemas = {}
        self.checks = {}
        self.activation = {}

    def fields(self, scope, fields):
        """Register metadata fields: literal types, enum tuples, or nested schemas."""
        if scope not in {
            "pin",
            "port",
            "net",
            "instance_pin",
            "component",
            "block",
            "board",
            "instance",
        }:
            raise ValueError(f"unsupported metadata scope {scope!r}")
        from .metadata import SCHEMAS

        validate_schema(fields)
        if SCHEMAS[scope].keys() & fields.keys():
            raise ValueError("ERC metadata duplicates core fields")
        target = self.schemas.setdefault(scope, {})
        if target.keys() & fields.keys():
            raise ValueError("duplicate ERC metadata fields")
        target.update(fields)

    def check(self, name, callback, *, net_fields=()):
        """Register a net callback returning diagnostics under its check name."""
        from .erc_rules import CHECKS

        if (
            not name.isidentifier()
            or name in self.checks
            or name in CHECKS
            or not callable(callback)
        ):
            raise ValueError(f"invalid or duplicate ERC check {name!r}")
        allowed = set(self.schemas.get("net", {})) | set(self.schemas.get("port", {}))
        from .metadata import SCHEMAS

        allowed |= SCHEMAS["net"].keys() | SCHEMAS["port"].keys()
        if not isinstance(net_fields, (tuple, list)) or any(
            not isinstance(k, str) or k not in allowed for k in net_fields
        ):
            raise ValueError("activation fields must name registered net or port metadata")
        self.checks[name] = callback
        self.activation[name] = tuple(net_fields)

    def run(self, context):
        for name, callback in self.checks.items():
            fields = self.activation[name]
            if fields and not any(
                set(fields) & d["metadata"].keys() for d in context.net["declarations"]
            ):
                continue
            try:
                # A check cannot alter another check's inputs or the exported circuit.
                result = list(callback(deepcopy(context)))
                for diagnostic in result:
                    if (
                        not isinstance(diagnostic, Diagnostic)
                        or not isinstance(diagnostic.span, Span)
                        or diagnostic.code != "erc." + name
                        or diagnostic.severity not in {"error", "warning"}
                    ):
                        raise ValueError(f"check {name!r} returned an invalid diagnostic")
                yield from result
            except Exception as exc:
                raise DiagnosticError(
                    [context.diagnostic("plugin_failure", f"ERC check {name!r} failed: {exc}")]
                ) from exc


def load_registry(paths=(), span=None):
    from .erc_electrical import register

    registry = Registry()
    register(registry)
    for index, path in enumerate(paths):
        try:
            spec = importlib.util.spec_from_file_location(f"_noscheme_erc_{index}", path)
            if spec is None or spec.loader is None:
                raise ValueError("expected a Python module file")
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            try:
                spec.loader.exec_module(module)
                module.register(registry)
            finally:
                sys.modules.pop(spec.name, None)
        except Exception as exc:
            raise DiagnosticError(
                [
                    Diagnostic(
                        "erc.plugin_load", "error", f"cannot load ERC module {path}: {exc}", span
                    )
                ]
            ) from exc
    return registry


def validate_schema(fields):
    if not isinstance(fields, dict) or not fields:
        raise ValueError("metadata schemas must be nonempty mappings")
    for name, kind in fields.items():
        if not isinstance(name, str) or not name.isidentifier():
            raise ValueError("metadata field names must be identifiers")
        if isinstance(kind, dict):
            validate_schema(kind)
        elif isinstance(kind, tuple):
            if (
                len(kind) != 2
                or kind[0] != "bias"
                or not kind[1]
                or not all(isinstance(v, str) for v in kind[1])
            ):
                raise ValueError("unsupported metadata enum schema")
        elif kind not in {"positive", "nonnegative", "string", "bool", "literal"}:
            raise ValueError(f"unsupported metadata field type {kind!r}")
