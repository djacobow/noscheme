"""Reusable three-valued predicates, interval reductions, and bounds assertions."""

from dataclasses import dataclass
from fractions import Fraction
from math import inf, isnan

from .ast import Span
from .diagnostics import Related


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
    return Fraction(str(value))


def total(values):
    return Interval(
        sum(min(0, v.lower) if v.optional else v.lower for v in values),
        sum(max(0, v.upper) if v.optional else v.upper for v in values),
        unknown=any(not v.complete for v in values),
    )


def parallel(values):
    if any(v.lower < 0 or v.upper <= 0 for v in values):
        raise ValueError("parallel resistance requires positive values")
    low_g = sum(0 if v.optional or v.upper == inf else 1 / v.upper for v in values)
    high_g = sum(inf if v.lower == 0 else 1 / v.lower for v in values)
    return Interval(
        0 if high_g == inf else 1 / high_g if high_g else inf,
        1 / low_g if low_g else inf,
        unknown=any(not v.complete for v in values),
    )


def roles(pin, accepted):
    possible = pin.attributes.get("external_role", pin.attributes.get("role", ()))
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
            "open_drain",
            "open_source",
        },
        "open_drain_compatible": {"passive", "digital_input", "open_drain"},
        "open_source_compatible": {"passive", "digital_input", "open_source"},
    }.items():
        registry.predicate("std." + name, lambda pin, ctx, accepted=accepted: roles(pin, accepted))
    registry.predicate(
        "std.tristate_capable",
        lambda pin, ctx: True
        if roles(pin, {"passive", "digital_input"}) is True
        else bool(pin.attributes.get("tristate_capable"))
        if roles(pin, {"digital_output", "digital_bidirectional", "open_drain", "open_source"})
        is True
        else None,
    )
    bounds = {"min": "number", "max": "number"}
    registry.net_rule(
        "std.reduce",
        reduce_rule,
        parameters={
            "attribute": "string",
            "reducer": "string",
            "where": "attributes",
            "predicate": "string",
            "net_attributes": "literal",
            **bounds,
        },
        defaults={"where": {}, "predicate": "", "net_attributes": (), "min": None, "max": None},
    )
    for name, callback in [("count", count_rule), ("any", any_rule), ("all", all_rule)]:
        registry.net_rule(
            "std." + name,
            callback,
            parameters={
                "predicate": "string",
                "where": "attributes",
                **(bounds if name == "count" else {}),
            },
            defaults={"where": {}, **({"min": None, "max": None} if name == "count" else {})},
        )


def selected(pin, ctx, where, predicate=""):
    state = True
    for key, expected in where.items():
        actual = pin.attributes.get(key)
        if actual is None:
            state = None
        elif isinstance(actual, tuple):
            if expected not in actual:
                return False
            if len(actual) > 1:
                state = None
        elif actual != expected:
            return False
    if predicate:
        result = ctx.predicates[predicate](pin, ctx)
        if result is not True and result is not False and result is not None:
            raise ValueError("predicate must return True, False, or None")
        if result is False:
            return False
        if result is None:
            state = None
    return state


def related(pins):
    return tuple(Related(pin.label, Span(**pin["span"])) for pin in pins)


def compare(ctx, result, minimum, maximum, pins):
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
        yield ctx.error(f"reduction is below minimum {minimum}", related=related(pins))
    if maximum is not None and result.lower > numeric(maximum):
        yield ctx.error(f"reduction exceeds maximum {maximum}", related=related(pins))
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


def reduce_rule(ctx, *, attribute, reducer, where, predicate, net_attributes, min, max):
    if not isinstance(net_attributes, (list, tuple)) or any(
        not isinstance(name, str) for name in net_attributes
    ):
        raise ValueError("net_attributes must be a list of attribute names")
    if (
        not attribute
        or reducer not in ctx.reducers
        or predicate
        and predicate not in ctx.predicates
    ):
        raise ValueError("reduce requires an attribute and registered reducer/predicate names")
    samples, pins = [], []
    nonnegative = ctx.domains["pin"].get(attribute) in {"nonnegative", "positive"}
    for pin in ctx.pins:
        state = selected(pin, ctx, where, predicate)
        if state is False:
            continue
        pins.append(pin)
        amount = pin.attributes.get(attribute)
        sample = (
            Interval(0 if nonnegative else -inf, inf, unknown=True)
            if amount is None
            else Interval(numeric(amount), numeric(amount))
        )
        if state is None:
            sample = Interval(sample.lower, sample.upper, optional=True, unknown=True)
        samples.append(sample)
    for field in net_attributes:
        amount = ctx.net.attributes.get(field)
        samples.append(
            Interval(
                0 if ctx.domains["net"].get(field) in {"positive", "nonnegative"} else -inf,
                inf,
                unknown=True,
            )
            if amount is None
            else Interval(numeric(amount), numeric(amount))
        )
    result = ctx.reducers[reducer](tuple(samples))
    yield from compare(ctx, result, min, max, pins)


def count_rule(ctx, *, predicate, where, min, max):
    if predicate not in ctx.predicates:
        raise ValueError("predicate must name a registered predicate")
    pins = [p for p in ctx.pins if selected(p, ctx, where) is not False]
    states = [selected(p, ctx, where, predicate) for p in pins]
    result = Interval(sum(s is True for s in states), sum(s is not False for s in states))
    yield from compare(ctx, result, min, max, pins)


def any_rule(ctx, *, predicate, where):
    yield from count_rule(ctx, predicate=predicate, where=where, min=1, max=None)


def all_rule(ctx, *, predicate, where):
    if predicate not in ctx.predicates:
        raise ValueError("predicate must name a registered predicate")
    pins = [p for p in ctx.pins if selected(p, ctx, where) is not False]
    states = []
    for pin in pins:
        selection = selected(pin, ctx, where)
        predicate_result = selected(pin, ctx, {}, predicate)
        states.append(
            True if predicate_result is True else None if selection is None else predicate_result
        )
    if False in states:
        yield ctx.error(
            "not every selected endpoint satisfies the predicate", related=related(pins)
        )
    if None in states:
        yield ctx.unknown("some endpoint predicates are unknown", related=related(pins))
