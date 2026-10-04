"""Immutable circuit views shared by every electrical checker."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .ast import Span
from .diagnostics import Diagnostic


def freeze(value, _memo=None):
    """Freeze shared container subgraphs once; never expose mutable descendants."""
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    memo = {} if _memo is None else _memo
    key = id(value)
    if key in memo:
        return memo[key][1]
    if isinstance(value, Mapping):
        result = MappingProxyType({key: freeze(item, memo) for key, item in value.items()})
    elif isinstance(value, (set, frozenset)):
        result = frozenset(freeze(item, memo) for item in value)
    elif isinstance(value, (list, tuple)):
        result = tuple(freeze(item, memo) for item in value)
    else:
        result = value
    memo[key] = (value, result)
    return result


def value(record, key, default=None):
    return record.get("metadata", {}).get(key, {}).get("value", default)


def attributes(record):
    return MappingProxyType(
        {key: unwrap(item["value"]) for key, item in record.get("metadata", {}).items()}
    )


def unwrap(item):
    if isinstance(item, Mapping):
        return MappingProxyType(
            {
                key: unwrap(
                    v["value"]
                    if isinstance(v, Mapping) and "value" in v and "span" in v and "module" in v
                    else v
                )
                for key, v in item.items()
            }
        )
    return item


def path_text(path):
    result = ""
    for item in path:
        result += f"[{item}]" if isinstance(item, int) else ("." if result else "") + item
    return result


@dataclass(frozen=True)
class Record(Mapping):
    raw: Mapping
    label: str
    attributes: Mapping = field(init=False)

    def __post_init__(self):
        object.__setattr__(self, "attributes", attributes(self.raw))

    def __getitem__(self, key):
        return self.raw[key]

    def __iter__(self):
        return iter(self.raw)

    def __len__(self):
        return len(self.raw)


@dataclass(frozen=True)
class Pin(Record):
    component: Record


@dataclass(frozen=True)
class Net(Record):
    pins: tuple
    declarations: tuple


@dataclass(frozen=True)
class Circuit(Mapping):
    raw: Mapping
    nets: Mapping = field(init=False)
    pins: Mapping = field(init=False)
    components: Mapping = field(init=False)

    def __post_init__(self):
        components, pins, by_net = {}, {}, {}
        for part in self.raw["parts"]:
            label = path_text(part["path"])
            component = Record(part, label)
            components[label] = component
            for number, data in part["pins"].items():
                pin = Pin(
                    MappingProxyType(dict(data, scope=part["path"])), f"{label}.{number}", component
                )
                pins[pin.label] = pin
                if data["net"] is not None:
                    by_net.setdefault(data["net"], []).append(pin)
        nets = {}
        for data in self.raw["nets"]:
            origin = next(d for d in data["declarations"] if d["path"] == data["path"])
            declarations = tuple(Record(d, path_text(d["path"])) for d in data["declarations"])
            endpoints = by_net.get(data["name"], []) + [
                d for d in declarations if "external_role" in d.attributes
            ]
            # The outer declaration supplies convenient attributes; all boundaries remain available.
            nets[data["name"]] = Net(
                MappingProxyType(dict(data, metadata=origin["metadata"])),
                data["name"],
                tuple(endpoints),
                declarations,
            )
        object.__setattr__(self, "nets", MappingProxyType(nets))
        object.__setattr__(self, "pins", MappingProxyType(pins))
        object.__setattr__(self, "components", MappingProxyType(components))

    def __getitem__(self, key):
        return self.raw[key]

    def __iter__(self):
        return iter(self.raw)

    def __len__(self):
        return len(self.raw)


@dataclass(frozen=True)
class Context:
    circuit: Circuit
    net: Net | None
    pin: Pin | None
    owner: Record
    application: str
    checker: str
    span: Span
    missing: str = "warning"
    predicates: Mapping = field(default_factory=dict)
    reducers: Mapping = field(default_factory=dict)
    domains: Mapping = field(default_factory=dict)
    _memo: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def pins(self):
        return self.net.pins if self.net else ()

    @property
    def other_pins(self):
        return tuple(p for p in self.pins if p is not self.pin)

    @property
    def endpoints(self):
        return tuple((pin.label, pin) for pin in self.pins)

    @property
    def origin(self):
        return self.owner

    def diagnostic(self, code, message, *, record=None, severity="error", related=()):
        span = Span(**record["span"]) if record is not None else self.span
        return Diagnostic(
            "erc." + code,
            severity,
            message,
            span,
            tuple(related),
            self.pin.label if self.pin else None,
            application=self.application,
        )

    def error(self, message, *, related=()):
        return self.diagnostic(self.checker, message, related=related)

    def unknown(self, message, *, related=()):
        return self.diagnostic(self.checker, message, severity="unknown", related=related)

    def shared(self, key, factory):
        """Cache immutable derived facts once per configuration and key."""
        if key not in self._memo:
            self._memo[key] = freeze(factory())
        return self._memo[key]
