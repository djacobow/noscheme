"""Opt-in lumped capacitance and parallel pull-resistance budgets."""

from dataclasses import replace

from .diagnostics import Related
from .ast import Span
from .erc_registry import value

from .metadata import DRIVE_VALUES, PULL_VALUES


def register(registry):
    pin_fields = {
        "pull": ("pull", PULL_VALUES),
        "pull_resistance": "resistance",
        "capacitance": "capacitance",
    }
    for scope in ("pin", "instance_pin"):
        registry.fields(scope, pin_fields)
    for scope in ("pin", "instance_pin", "port"):
        registry.fields(scope, {"drive": ("drive", DRIVE_VALUES)})
    registry.fields("instance_pin", {"external_drive": ("drive", DRIVE_VALUES)})
    registry.fields("net", {"wiring_capacitance": "capacitance"})
    registry.pin_rule("std.pull_reference", pull_reference, parameters={"pin": "string"})


def pull_reference(ctx, *, pin):
    """Check that a pull's reference terminal reaches a power (pull-up) or ground (pull-down) net."""
    reference, label = pin, ctx.pin.label
    expected = {"up": "power", "down": "ground"}.get(value(ctx.pin, "pull"))
    part = next(
        (p for p in ctx.circuit["parts"] if tuple(p["path"]) == tuple(ctx.pin.get("scope", ()))),
        None,
    )
    candidates = {} if part is None else part["pins"]
    target = candidates.get(reference) or next(
        (p for p in candidates.values() if reference in value(p, "aliases", [])), None
    )
    reason = None
    if expected is None:
        reason = "requires pull.up or pull.down"
    elif target is None:
        reason = f"unknown reference pin {reference!r} on this component"
    elif target["net"] is None:
        reason = f"reference pin {reference!r} is unconnected"
    elif ctx.net is not None and target["net"] == ctx.net["name"]:
        reason = "reference terminal is connected to the signal net itself"
    else:
        net = next(n for n in ctx.circuit["nets"] if n["name"] == target["net"])
        types = {d["type"] for d in net["declarations"]}
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
            ctx.diagnostic("pull_reference", f"{label}: {reason}", related=related),
            instance_path=label,
        )
