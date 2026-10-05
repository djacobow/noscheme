"""Opt-in voltage intervals and absolute pin limits, without domain inference."""

from dataclasses import replace

from .ast import Span
from .diagnostics import Related
from .erc_registry import value
from .units import format_quantity


def register(registry):
    # Nets declare an operating range; pins declare absolute limits. Both use one pair of names.
    for scope in ("pin", "instance_pin", "net"):
        registry.fields(scope, {"min_voltage": "voltage", "max_voltage": "voltage"})
    registry.net_rule("std.voltage_limits", voltage_limits, net_only=True)


def voltage_limits(ctx):
    """Compare explicit intervals only; no logic or supply-domain inference."""
    ranges = []
    for declaration in ctx.net["declarations"]:
        low, high = value(declaration, "min_voltage"), value(declaration, "max_voltage")
        if low is None and high is None:
            continue
        if low is None or high is None or low > high:
            yield ctx.diagnostic(
                "voltage_limits",
                "net voltage requires both bounds with min_voltage <= max_voltage",
                record=declaration,
            )
        else:
            ranges.append((low, high, declaration))
    distinct = {(low, high) for low, high, _ in ranges}
    if len(distinct) > 1:
        yield ctx.diagnostic(
            "voltage_limits",
            "connected net declarations specify different voltage ranges",
            related=tuple(Related("voltage range", Span(**d["span"])) for _, _, d in ranges),
        )
    for label, pin in ctx.endpoints:
        low, high = value(pin, "min_voltage"), value(pin, "max_voltage")
        if low is None and high is None:
            continue
        record = pin["metadata"].get("min_voltage", pin["metadata"].get("max_voltage"))
        if low is not None and high is not None and low > high:
            yield replace(
                ctx.diagnostic(
                    "voltage_limits", f"{label}: min_voltage exceeds max_voltage", record=record
                ),
                instance_path=label,
            )
            continue
        if not ranges:
            yield replace(
                ctx.diagnostic(
                    "voltage_limits",
                    f"{label}: pin voltage limits cannot be checked without a complete net voltage range",
                    record=record,
                    severity="unknown",
                ),
                instance_path=label,
            )
        for rail_low, rail_high, declaration in ranges:
            if low is not None and rail_low < low or high is not None and rail_high > high:
                yield replace(
                    ctx.diagnostic(
                        "voltage_limits",
                        f"{label}: net voltage range [{format_quantity(rail_low, 'voltage')}, "
                        f"{format_quantity(rail_high, 'voltage')}] exceeds pin limits",
                        record=record,
                        related=(Related("net voltage range", Span(**declaration["span"])),),
                    ),
                    instance_path=label,
                )
