"""Registration for electrical rules, predicates, reducers, and default rule sets."""

from dataclasses import dataclass, field
import importlib.util
from pathlib import Path
import re
import sys

from .diagnostics import Diagnostic, DiagnosticError
from .erc_views import value as value
from .units import QUANTITIES


@dataclass(frozen=True)
class Rule:
    scope: str
    callback: object
    parameters: dict
    defaults: dict
    waivable: bool = True
    # A promise that results depend only on the flattened net and the arguments,
    # never on ctx.owner, so identical applications may run once per net.
    net_only: bool = False


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
        for name in fields:
            if UNIT_SUFFIX.search(name):
                raise ValueError(
                    f"field {name!r} names a unit; declare a quantity kind and use base SI values"
                )
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

    def _rule(
        self, scope, name, callback, parameters=None, defaults=None, waivable=True, net_only=False
    ):
        if not callable(callback):
            raise ValueError("rule callback must be callable")
        parameters, defaults = dict(parameters or {}), dict(defaults or {})
        if parameters:
            validate_schema(parameters, arguments=True)
        if "missing" in parameters:
            raise ValueError("'missing' is reserved for the missing-data policy")
        if defaults.keys() - parameters.keys():
            raise ValueError("rule defaults must name declared parameters")
        self._register(
            self.rules, name, Rule(scope, callback, parameters, defaults, waivable, net_only)
        )

    def net_rule(
        self, name, callback, *, parameters=None, defaults=None, waivable=True, net_only=False
    ):
        self._rule("net", name, callback, parameters, defaults, waivable, net_only)

    def pin_rule(self, name, callback, *, parameters=None, defaults=None, waivable=True):
        self._rule("pin", name, callback, parameters, defaults, waivable)

    def predicate(self, name, callback):
        if not callable(callback):
            raise ValueError("predicate must be callable")
        self._register(self.predicates, name, callback)

    def reducer(self, name, callback):
        """Register an interval reducer and a net rule of the same name that applies it."""
        from .erc_primitives import REDUCTION_DEFAULTS, REDUCTION_PARAMETERS, reduce_rule

        if not callable(callback):
            raise ValueError("reducer must be callable")
        self._register(self.reducers, name, callback)

        def rule(ctx, **params):
            return reduce_rule(ctx, reducer=name, **params)

        rule.__doc__ = f"Reduce a field over selected endpoints with {name}; bound by min/max."
        self.net_rule(
            name,
            rule,
            parameters=REDUCTION_PARAMETERS,
            defaults=REDUCTION_DEFAULTS,
            net_only=True,
        )

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
        path = Path(path)
        name = f"_noscheme_erc_{index}"
        try:
            spec = importlib.util.spec_from_file_location(name, path)
            if spec is None or spec.loader is None:
                raise ValueError("expected a Python module file")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            try:
                # Read source directly: editor changes can preserve both byte count
                # and timestamp, making Python's timestamp-based pyc stale.
                exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
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


# Values are in base SI units; a field name never carries a (scaled) unit.
UNIT_SUFFIX = re.compile(r"_[afpnumkMGTP]?(?:A|V|F|H|W|Hz|[Oo]hm)\Z")

# Argument-only kinds name source entities rather than values: a pin-side field,
# outermost-net fields, or an endpoint selector (see erc_primitives.selected).
ARGUMENT_KINDS = frozenset({"field", "net_fields", "selector"})


def signature(name, rule):
    """One-line editor description of a registered rule."""
    summary = (rule.callback.__doc__ or "").strip().split("\n")[0]
    text = f"{rule.scope} rule {name}(" + ", ".join(rule.parameters) + ")"
    return text + ("\n" + summary if summary else "")


def namespace(name):
    """Return the importable namespace of a qualified registration name."""
    return name.rpartition(".")[0]


def validate_schema(fields, *, arguments=False):
    if not isinstance(fields, dict) or not fields:
        raise ValueError("metadata schemas must be nonempty mappings")
    for name, kind in fields.items():
        if not isinstance(name, str) or not name.isidentifier():
            raise ValueError("metadata field names must be identifiers")
        if isinstance(kind, dict) or kind == "attributes":
            if arguments:
                raise ValueError("rule parameters cannot be mappings; use a selector")
            if isinstance(kind, dict):
                validate_schema(kind)
        elif arguments and kind in ARGUMENT_KINDS:
            pass
        elif isinstance(kind, tuple):
            if (
                len(kind) != 2
                or not isinstance(kind[0], str)
                or not kind[1]
                or not all(isinstance(v, str) for v in kind[1])
            ):
                raise ValueError("unsupported metadata enum schema")
        elif kind not in QUANTITIES and kind not in {
            "number",
            "positive",
            "nonnegative",
            "string",
            "bool",
            "literal",
            "attributes",
        }:
            raise ValueError(f"unsupported metadata field type {kind!r}")
