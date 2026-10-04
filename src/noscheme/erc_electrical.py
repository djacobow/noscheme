"""Opt-in lumped capacitance and parallel pull-resistance budgets."""

from fractions import Fraction

from .diagnostics import Related
from .ast import Span
from .erc_registry import value

BIASES = frozenset({"pull_up", "pull_down", "none"})


def register(registry):
    pin_fields = {
        "bias": ("bias", BIASES),
        "pull_resistance_ohm": "positive",
        "capacitance_pF": "nonnegative",
    }
    for scope in ("pin", "instance_pin"):
        registry.fields(scope, pin_fields)
    limits = {"max_capacitance_pF": "nonnegative"}
    for direction in ("pull_up", "pull_down"):
        for bound in ("min", "max"):
            limits[f"{bound}_{direction}_resistance_ohm"] = "positive"
    for scope in ("net", "port"):
        registry.fields(scope, limits)
    registry.fields("net", {"wiring_capacitance_pF": "nonnegative"})
    registry.check("capacitance_budget", capacitance, net_fields=("max_capacitance_pF",))
    registry.check(
        "pull_resistance_budget",
        resistance,
        net_fields=tuple(k for k in limits if "resistance" in k),
    )


def quantity(number):
    return Fraction(str(number))


def related(endpoints):
    return tuple(Related(name, Span(**pin["span"])) for name, pin in endpoints)


def capacitance(ctx):
    limits = [
        (d, value(d, "max_capacitance_pF"))
        for d in ctx.net["declarations"]
        if value(d, "max_capacitance_pF") is not None
    ]
    if not limits:
        return
    amounts = [value(pin, "capacitance_pF") for _, pin in ctx.endpoints]
    # Wiring is a single explicit estimate on the outermost net, not every port.
    amounts.append(value(ctx.origin, "wiring_capacitance_pF"))
    total = sum((quantity(v) for v in amounts if v is not None), Fraction())
    for declaration, maximum in limits:
        if total > quantity(maximum):
            yield ctx.diagnostic(
                "capacitance_budget",
                f"known capacitance on {ctx.net['name']!r} exceeds {maximum} pF",
                record=declaration["metadata"]["max_capacitance_pF"],
                related=related(ctx.endpoints),
            )
    if any(v is None for v in amounts):
        yield ctx.diagnostic(
            "capacitance_budget",
            f"capacitance budget on {ctx.net['name']!r} lacks endpoint or wiring capacitance; use explicit zero for a known zero contribution",
            severity="warning",
            related=related(ctx.endpoints),
        )


def resistance(ctx):
    for direction in ("pull_up", "pull_down"):
        limits = [
            (d, bound, value(d, f"{bound}_{direction}_resistance_ohm"))
            for d in ctx.net["declarations"]
            for bound in ("min", "max")
            if value(d, f"{bound}_{direction}_resistance_ohm") is not None
        ]
        if not limits:
            continue
        pulls = [(name, pin) for name, pin in ctx.endpoints if value(pin, "bias") == direction]
        amounts = [value(pin, "pull_resistance_ohm") for _, pin in pulls]
        incomplete = not amounts or any(v is None for v in amounts)
        conductance = sum((1 / quantity(v) for v in amounts if v is not None), Fraction())
        effective = 1 / conductance if conductance else None
        for declaration, bound, limit in limits:
            violates = effective is not None and (
                effective < quantity(limit)
                if bound == "min"
                else not incomplete and effective > quantity(limit)
            )
            if violates:
                yield ctx.diagnostic(
                    "pull_resistance_budget",
                    f"parallel {direction} resistance on {ctx.net['name']!r} violates {bound}imum {limit} ohm",
                    record=declaration["metadata"][f"{bound}_{direction}_resistance_ohm"],
                    related=related(pulls),
                )
        if incomplete:
            yield ctx.diagnostic(
                "pull_resistance_budget",
                f"{direction} budget on {ctx.net['name']!r} lacks declared pulls or their resistance",
                severity="warning",
                related=related(pulls),
            )
