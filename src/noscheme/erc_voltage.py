"""Opt-in voltage intervals and absolute pin limits, without domain inference."""

from dataclasses import replace

from .ast import Span
from .diagnostics import Related
from .erc_registry import value


def register(registry):
    for scope in ("pin", "instance_pin"):
        registry.fields(scope, {"min_voltage_V": "number", "max_voltage_V": "number"})
    registry.fields("net", {"voltage_min_V": "number", "voltage_max_V": "number"})
    registry.net_rule("std.voltage_limits", voltage_limits)


def voltage_limits(ctx):
    """Compare explicit intervals only; no logic or supply-domain inference."""
    ranges = []
    for declaration in ctx.net["declarations"]:
        low, high = value(declaration, "voltage_min_V"), value(declaration, "voltage_max_V")
        if low is None and high is None:
            continue
        if low is None or high is None or low > high:
            yield ctx.diagnostic(
                "voltage_limits",
                "net voltage requires both bounds with voltage_min_V <= voltage_max_V",
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
        low, high = value(pin, "min_voltage_V"), value(pin, "max_voltage_V")
        if low is None and high is None:
            continue
        record = pin["metadata"].get("min_voltage_V", pin["metadata"].get("max_voltage_V"))
        if low is not None and high is not None and low > high:
            yield replace(
                ctx.diagnostic(
                    "voltage_limits", f"{label}: min_voltage_V exceeds max_voltage_V", record=record
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
                        f"{label}: net voltage range [{rail_low}, {rail_high}] V exceeds pin limits",
                        record=record,
                        related=(Related("net voltage range", Span(**declaration["span"])),),
                    ),
                    instance_path=label,
                )
