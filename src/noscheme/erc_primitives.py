"""Reusable three-valued predicates, interval reductions, and bounds assertions."""

from dataclasses import dataclass
from math import inf, isnan

from .erc_views import effective_roles
from .ast import Span
from .diagnostics import Related
from .units import format_quantity


@dataclass(frozen=True)
class Interval:
    lower: object
    upper: object
    optional: bool = False
    unknown: bool = False

    @property
    def complete(self):
        return self.lower == self.upper and not self.unknown and not self.optional


def numeric(value):
    if type(value) not in (int, float):
        raise ValueError("reduction values must be numbers")
    return float(value)


def floating_sum(values):
    """Accumulate in circuit order, identically to the native ERC."""
    result = 0.0
    for value in values:
        result += value
    return result


def total(values):
    return Interval(
        floating_sum(min(0, v.lower) if v.optional else v.lower for v in values),
        floating_sum(max(0, v.upper) if v.optional else v.upper for v in values),
        unknown=any(not v.complete for v in values),
    )


def parallel(values):
    if any(v.lower < 0 or v.upper <= 0 for v in values):
        raise ValueError("parallel resistance requires positive values")
    low_g = floating_sum(0 if v.optional or v.upper == inf else 1 / v.upper for v in values)
    high_g = floating_sum(inf if v.lower == 0 else 1 / v.lower for v in values)
    return Interval(
        0 if high_g == inf else 1 / high_g if high_g else inf,
        1 / low_g if low_g else inf,
        unknown=any(not v.complete for v in values),
    )


def roles(pin, accepted):
    possible = effective_roles(pin)
    if not possible or "unchecked" in possible:
        return None
    matches = [r in accepted for r in possible]
    return True if all(matches) else False if not any(matches) else None


def register(registry):
    registry.reducer("std.sum", total)
    registry.reducer("std.parallel", parallel)
    for name, accepted in {
        "active_digital_driver": {"digital_output"},
        "power_source": {"power_output"},
        "digital_compatible": {
            "passive",
            "digital_input",
            "digital_output",
            "digital_bidirectional",
            "drive.low",
            "drive.high",
        },
        "idle_high_compatible": {"passive", "digital_input", "drive.low"},
        "idle_low_compatible": {"passive", "digital_input", "drive.high"},
    }.items():
        registry.predicate("std." + name, lambda pin, ctx, accepted=accepted: roles(pin, accepted))
    registry.predicate(
        "std.tristate_capable",
        lambda pin, ctx: True
        if roles(pin, {"passive", "digital_input"}) is True
        else bool(pin.attributes.get("tristate_capable"))
        if roles(pin, {"digital_output", "digital_bidirectional", "drive.low", "drive.high"})
        is True
        else None,
    )
    registry.net_rule(
        "std.count",
        count_rule,
        parameters={"what": "selector", "min": "number", "max": "number"},
        defaults={"min": None, "max": None},
        net_only=True,
    )
    registry.net_rule("std.any", any_rule, parameters={"what": "selector"}, net_only=True)
    registry.net_rule(
        "std.all",
        all_rule,
        parameters={"what": "selector", "where": "selector"},
        defaults={"where": ()},
        net_only=True,
    )


# A reducer is also a net rule: std.sum(capacitance, max=400).
REDUCTION_PARAMETERS = {
    "field": "field",
    "where": "selector",
    "net": "net_fields",
    "min": "number",
    "max": "number",
}
REDUCTION_DEFAULTS = {"where": (), "net": (), "min": None, "max": None}


def selected(pin, ctx, where):
    """Three-valued selection: every term must hold; unknown terms make it None.

    A selector is a tuple of terms resolved from source: ("field", name, value)
    compares an endpoint attribute, ("role", name) tests the effective role, and
    ("predicate", name) calls a registered predicate.
    """
    state = True
    for term in where:
        if term[0] == "role":
            result = roles(pin, {term[1]})
        elif term[0] == "predicate":
            result = ctx.predicates[term[1]](pin, ctx)
            if result is not True and result is not False and result is not None:
                raise ValueError("predicate must return True, False, or None")
        else:
            actual = pin.attributes.get(term[1])
            if isinstance(actual, tuple):
                result = None if term[2] in actual and len(actual) > 1 else term[2] in actual
            else:
                result = None if actual is None else actual == term[2]
        if result is False:
            return False
        if result is None:
            state = None
    return state


def related(pins):
    return tuple(Related(pin.label, Span(**pin["span"])) for pin in pins)


def shown(value, quantity):
    return format_quantity(value, quantity) if quantity else str(value)


def compare(ctx, result, minimum, maximum, pins, quantity=None):
    if (
        not isinstance(result, Interval)
        or any(isinstance(v, float) and isnan(v) for v in (result.lower, result.upper))
        or result.lower > result.upper
    ):
        raise ValueError("reducer must return an ordered Interval")
    if minimum is None and maximum is None:
        raise ValueError("a bounds assertion requires min and/or max")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError("min exceeds max")
    if minimum is not None and result.upper < numeric(minimum):
        yield ctx.error(
            f"reduction is below minimum {shown(minimum, quantity)}", related=related(pins)
        )
    if maximum is not None and result.lower > numeric(maximum):
        yield ctx.error(
            f"reduction exceeds maximum {shown(maximum, quantity)}", related=related(pins)
        )
    uncertain = (
        minimum is not None
        and result.lower < numeric(minimum) <= result.upper
        or maximum is not None
        and result.lower <= numeric(maximum) < result.upper
    )
    if uncertain or not result.complete:
        yield ctx.unknown(
            "some contributions are unknown; the reduction is incomplete", related=related(pins)
        )


def reduce_rule(ctx, *, reducer, field, where, net, min, max):
    samples, pins = [], []
    nonnegative = ctx.domains["pin"].get(field) in {"nonnegative", "positive"}
    for pin in ctx.pins:
        state = selected(pin, ctx, where)
        if state is False:
            continue
        pins.append(pin)
        amount = pin.attributes.get(field)
        sample = (
            Interval(0 if nonnegative else -inf, inf, unknown=True)
            if amount is None
            else Interval(numeric(amount), numeric(amount))
        )
        if state is None:
            sample = Interval(sample.lower, sample.upper, optional=True, unknown=True)
        samples.append(sample)
    for name in net:
        amount = ctx.net.attributes.get(name)
        samples.append(
            Interval(
                0 if ctx.domains["net"].get(name) in {"positive", "nonnegative"} else -inf,
                inf,
                unknown=True,
            )
            if amount is None
            else Interval(numeric(amount), numeric(amount))
        )
    result = ctx.reducers[reducer](tuple(samples))
    yield from compare(ctx, result, min, max, pins, ctx.domains["quantity"].get(field))


def count_rule(ctx, *, what, min, max):
    """Bound the number of endpoints matching a selector."""
    states = [(p, selected(p, ctx, what)) for p in ctx.pins]
    pins = [p for p, s in states if s is not False]
    result = Interval(sum(s is True for _, s in states), len(pins))
    yield from compare(ctx, result, min, max, pins)


def any_rule(ctx, *, what):
    """Require at least one endpoint matching a selector."""
    yield from count_rule(ctx, what=what, min=1, max=None)


def all_rule(ctx, *, what, where):
    """Require every endpoint selected by where to match what."""
    pins = [p for p in ctx.pins if selected(p, ctx, where) is not False]
    states = []
    for pin in pins:
        result = selected(pin, ctx, what)
        states.append(
            True if result is True else None if selected(pin, ctx, where) is None else result
        )
    if False in states:
        yield ctx.error(
            "not every selected endpoint satisfies the condition", related=related(pins)
        )
    if None in states:
        yield ctx.unknown("some endpoint conditions are unknown", related=related(pins))
