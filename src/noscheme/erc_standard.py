"""Register standard check families and select default applications."""

from dataclasses import replace

from . import erc_connections, erc_ports, erc_compatibility, erc_drivers, erc_current
from .erc_rules import CHECKS, WAIVABLE
from .erc_registry import Application

NET_FAMILIES = {
    "do_not_connect": (erc_ports,),
    "required_connection": (erc_connections,),
    "unused_pin": (),  # Retained registry name; only physical-pin applications emit it.
    "unconnected_net": (erc_connections,),
    "missing_driver": (erc_drivers,),
    "multiple_drivers": (erc_drivers,),
    "power_ground_conflict": (erc_drivers,),
    "incomplete_roles": (erc_drivers,),
    "unchecked_net": (),  # Compatibility name; explicit unchecked nets are reported as a count.
    "aggregate_load_boundary": (erc_current,),
    "aggregate_current_overload": (erc_current,),
    "incomplete_aggregate_load": (erc_current,),
    "current_load_group": (),  # The physical-pin rule validates groups once per component.
    "current_overload": (erc_current,),
    "incomplete_budget": (erc_current,),
    "incompatible_role": (erc_compatibility,),
    "tristate_capability": (erc_compatibility,),
    "port_role": (erc_ports,),
    "incomplete_port_role": (erc_ports,),
    "required_port_connection": (erc_ports,),
    "unused_port": (erc_ports,),
}


def register(registry):
    pin_codes = {"do_not_connect", "required_connection", "unused_pin"}
    for code in CHECKS:
        if code in {"waiver_scope", "waiver_target"}:
            continue

        def net_check(ctx, code=code):
            results = []
            for family in NET_FAMILIES[code]:
                results.extend(
                    ctx.shared(
                        (family.__name__, ctx.net.label),
                        lambda family=family: family.net_results(ctx),
                    )
                )
            return (
                replace(d, severity="unknown") if code.startswith("incomplete_") else d
                for d in results
                if d.code == "erc." + code
            )

        registry.net_rule("std." + code, net_check, waivable=code in WAIVABLE)
    # Registration order is part of the report and must survive process boundaries.
    for code in sorted(pin_codes):

        def pin_check(ctx, code=code):
            return (
                d
                for d in ctx.shared(
                    ("std.connections.pin", ctx.pin.label), lambda: erc_connections.pin_results(ctx)
                )
                if d.code == "erc." + code
            )

        registry.pin_rule("std.pin_" + code, pin_check, waivable=code in WAIVABLE)

    registry.pin_rule("std.current_groups", erc_current.group_results, waivable=False)

    def defaults(ctx):
        if ctx.pin:
            for code in sorted(pin_codes):
                yield Application(code, "std.pin_" + code)
            yield Application("current_load_group", "std.current_groups")
        else:
            if ctx.owner["type"] != "unchecked":
                yield Application("voltage_limits", "std.voltage_limits")
            for code in CHECKS:
                if code not in {"waiver_scope", "waiver_target", "current_load_group"}:
                    yield Application(code, "std." + code)

    registry.rule_set("std.default", defaults)
