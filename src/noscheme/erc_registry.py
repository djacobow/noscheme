"""Registration for electrical rules, predicates, reducers, and default rule sets."""

from dataclasses import dataclass, field
import importlib.util
import re
import sys

from .diagnostics import Diagnostic, DiagnosticError
from .erc_views import value as value


@dataclass(frozen=True)
class Rule:
    scope: str
    callback: object
    parameters: dict
    defaults: dict
    waivable: bool = True


@dataclass(frozen=True)
class Application:
    name: str
    checker: str
    params: dict = field(default_factory=dict)
    missing: str = "warning"


class Registry:
    def __init__(self):
        self.schemas = {}
        self.rules = {}
        self.predicates = {}
        self.reducers = {}
        self.rule_sets = {}

    def fields(self, scope, fields):
        from .metadata import SCHEMAS

        if scope not in SCHEMAS:
            raise ValueError(f"unsupported metadata scope {scope!r}")
        validate_schema(fields)
        target = self.schemas.setdefault(scope, {})
        if fields.keys() & (SCHEMAS[scope].keys() | target.keys()):
            raise ValueError("duplicate ERC metadata fields")
        target.update(fields)

    def _register(self, table, name, item):
        if not isinstance(name, str) or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+", name
        ):
            raise ValueError(f"invalid registration name {name!r}")
        if name in table:
            raise ValueError(f"duplicate registration {name!r}")
        table[name] = item

    def _rule(self, scope, name, callback, parameters=None, defaults=None, waivable=True):
        if not callable(callback):
            raise ValueError("rule callback must be callable")
        parameters, defaults = dict(parameters or {}), dict(defaults or {})
        if parameters:
            validate_schema(parameters)
        if defaults.keys() - parameters.keys():
            raise ValueError("rule defaults must name declared parameters")
        self._register(self.rules, name, Rule(scope, callback, parameters, defaults, waivable))

    def net_rule(self, name, callback, *, parameters=None, defaults=None, waivable=True):
        self._rule("net", name, callback, parameters, defaults, waivable)

    def pin_rule(self, name, callback, *, parameters=None, defaults=None, waivable=True):
        self._rule("pin", name, callback, parameters, defaults, waivable)

    def predicate(self, name, callback):
        if not callable(callback):
            raise ValueError("predicate must be callable")
        self._register(self.predicates, name, callback)

    def reducer(self, name, callback):
        if not callable(callback):
            raise ValueError("reducer must be callable")
        self._register(self.reducers, name, callback)

    def rule_set(self, name, callback):
        """A callback receives a context and yields Application objects."""
        if not callable(callback):
            raise ValueError("rule-set callback must be callable")
        self._register(self.rule_sets, name, callback)


def load_registry(paths=(), span=None):
    from .erc_standard import register
    from .erc_primitives import register as register_primitives
    from .erc_electrical import register as register_electrical
    from .erc_voltage import register as register_voltage

    registry = Registry()
    for callback in (register, register_primitives, register_electrical, register_voltage):
        callback(registry)
    for index, path in enumerate(paths):
        name = f"_noscheme_erc_{index}"
        try:
            spec = importlib.util.spec_from_file_location(name, path)
            if spec is None or spec.loader is None:
                raise ValueError("expected a Python module file")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            try:
                spec.loader.exec_module(module)
                module.register(registry)
            finally:
                sys.modules.pop(name, None)
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
                or not isinstance(kind[0], str)
                or not kind[1]
                or not all(isinstance(v, str) for v in kind[1])
            ):
                raise ValueError("unsupported metadata enum schema")
        elif kind not in {
            "number",
            "positive",
            "nonnegative",
            "string",
            "bool",
            "literal",
            "attributes",
        }:
            raise ValueError(f"unsupported metadata field type {kind!r}")
