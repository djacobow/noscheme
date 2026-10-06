"""Explicit steady-state budget for the display's shared current sinks."""

from fractions import Fraction


def register(registry):
    registry.net_rule(
        "matrix.shared_sink_budget",
        shared_sink_budget,
        parameters={"load": "current"},
    )


def shared_sink_budget(ctx, *, load):
    """Compare an aggregate circuit-model load with one physical supply endpoint.

    The load replaces, rather than adds to, per-row demands. It includes the
    feedback divider and leakage allowance. No switching or startup is inferred.
    """
    sources = []
    for pin in ctx.pins:
        roles = pin.attributes.get("external_role", pin.attributes.get("role", ()))
        if len(roles) != 1 or "unchecked" in roles:
            yield ctx.unknown("shared-sink budget requires resolved endpoint roles")
            return
        if roles == ("power_output",):
            sources.append(pin)
        if roles == ("power_input",):
            yield ctx.error(
                "aggregate shared-sink model must include all loads; unexpected power input"
            )
            return
    if len(sources) != 1 or sources[0].attributes.get("max_source_current") is None:
        yield ctx.unknown("shared-sink budget requires one source with a current capacity")
        return
    capacity = sources[0].attributes["max_source_current"]
    if Fraction(str(load)) > Fraction(str(capacity)):
        yield ctx.error(f"display load {load:g} A exceeds buck capacity {capacity:g} A")
