"""Opt-in lumped capacitance and parallel pull-resistance budgets."""

from dataclasses import replace

from .diagnostics import Related
from .ast import Span
from .erc_registry import value

BIASES = frozenset({"pull_up", "pull_down", "none"})


def register(registry):
    pin_fields = {
        "bias": ("bias", BIASES),
        "bias_reference_pin": "string",
        "pull_resistance_ohm": "positive",
        "capacitance_pF": "nonnegative",
    }
    for scope in ("pin", "instance_pin"):
        registry.fields(scope, pin_fields)
    registry.fields("net", {"wiring_capacitance_pF": "nonnegative"})
    registry.pin_rule("std.bias_reference", bias_reference)


def bias_reference(ctx):
    """Check an explicitly named reference terminal without tracing passive parts."""
    parts = {tuple(part["path"]): part for part in ctx.circuit["parts"]}
    nets = {net["name"]: net for net in ctx.circuit["nets"]}
    for label, pin in ((ctx.pin.label, ctx.pin),):
        reference = value(pin, "bias_reference_pin")
        if reference is None:
            continue
        bias = value(pin, "bias")
        expected = {"pull_up": "power", "pull_down": "ground"}.get(bias)
        part = parts.get(tuple(pin.get("scope", ())))
        candidates = {} if part is None else part["pins"]
        target = candidates.get(reference)
        if target is None:
            target = next(
                (p for p in candidates.values() if reference in value(p, "aliases", [])), None
            )
        reason = None
        if expected is None:
            reason = "requires bias.pull_up or bias.pull_down"
        elif target is None:
            reason = f"unknown reference pin {reference!r} on this component"
        elif target["net"] is None:
            reason = f"reference pin {reference!r} is unconnected"
        elif ctx.net is not None and target["net"] == ctx.net["name"]:
            reason = "reference terminal is connected to the signal net itself"
        else:
            types = {d["type"] for d in nets[target["net"]]["declarations"]}
            opposite = "ground" if expected == "power" else "power"
            if expected not in types or opposite in types:
                reason = f"reference pin {reference!r} must connect to a declared {expected} net"
        if reason:
            related = (
                ()
                if target is None
                else (
                    Related(
                        "reference terminal", Span(**(target.get("binding_span") or target["span"]))
                    ),
                )
            )
            yield replace(
                ctx.diagnostic(
                    "bias_reference",
                    f"{label}: {reason}",
                    record=pin["metadata"]["bias_reference_pin"],
                    related=related,
                ),
                instance_path=label,
            )
