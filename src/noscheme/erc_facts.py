"""Shared immutable facts and diagnostic construction for standard ERC rules."""

from dataclasses import dataclass
from collections.abc import Mapping

from .ast import Span
from .diagnostics import Diagnostic
from .erc_rules import ALLOWED_ROLES
from .erc_views import value as attribute


class Results:
    def __init__(self):
        self.diagnostics = []

    def emit(self, code, message, record, path=None, severity="error", related=()):
        self.diagnostics.append(
            Diagnostic(
                "erc." + code,
                severity,
                message,
                Span(**(record.get("binding_span") or record["span"])),
                tuple(related),
                path,
            )
        )


@dataclass(frozen=True)
class NetFacts:
    origin: Mapping
    constraints: tuple[Mapping, ...]
    types: frozenset[str]
    known: tuple[tuple[str, str, Mapping], ...]
    uncertain: tuple[tuple[str, Mapping], ...]


def net_facts(ctx):
    def calculate():
        declarations = ctx.net["declarations"]
        origin = next(d for d in declarations if d["path"] == ctx.net["path"])

        def fixed_input_port(declaration):
            return (
                origin["type"] in {"power", "ground"}
                and declaration["kind"] == "port"
                and declaration["type"] in {"digital", "bidirectional", "open_drain"}
                and attribute(declaration, "role") == ("digital_input",)
            )

        constraints = tuple(
            d for d in declarations if d["type"] != "unchecked" and not fixed_input_port(d)
        )
        known, uncertain = [], []
        for label, pin in ctx.endpoints:
            roles = attribute(pin, "external_role", attribute(pin, "role", ()))
            if len(roles) != 1 or roles[0] in {"unchecked", "do_not_connect"}:
                uncertain.append((label, pin))
            else:
                known.append((roles[0], label, pin))
        return NetFacts(
            origin,
            constraints,
            frozenset(d["type"] for d in constraints),
            tuple(known),
            tuple(uncertain),
        )

    return ctx.shared(("std.facts", ctx.net.label), calculate)


def allowed(declaration, pin):
    roles = ALLOWED_ROLES[declaration["type"]]
    if declaration["type"] == "tristate" and not attribute(pin, "tristate_capable", False):
        return {"passive", "digital_input"}
    return roles


def compatibility(ctx):
    """Return incompatible endpoints and uncertainty, without running any checks."""

    def calculate():
        constraints = net_facts(ctx).constraints
        uncertain, conflicts = False, []
        for label, pin in ctx.endpoints:
            roles = frozenset(attribute(pin, "external_role", attribute(pin, "role", ())))
            if not roles or "unchecked" in roles:
                uncertain = True
                continue
            if roles == {"do_not_connect"}:
                continue
            permitted = set(roles)
            for declaration in constraints:
                permitted &= allowed(declaration, pin)
            incompatible = tuple(d for d in constraints if not roles & allowed(d, pin))
            if not permitted and not incompatible:
                incompatible = constraints
            if incompatible:
                capability = any(
                    d["type"] == "tristate" and roles <= ALLOWED_ROLES["tristate"]
                    for d in incompatible
                )
                conflicts.append((label, pin, roles, incompatible, capability))
            elif any(not roles <= allowed(d, pin) for d in constraints):
                uncertain = True
        return tuple(conflicts), uncertain

    return ctx.shared(("std.compatibility", ctx.net.label), calculate)
