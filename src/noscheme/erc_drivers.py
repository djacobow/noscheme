"""Driver presence, contention, and supply-to-ground conflicts."""

from .ast import Span
from .diagnostics import Related
from .erc_facts import Results, net_facts, compatibility, attribute


def net_results(ctx):
    results = Results()
    emit = results.emit
    facts = net_facts(ctx)
    origin = facts.origin
    name = ctx.net.label
    endpoints = ctx.endpoints
    if origin["type"] == "unchecked":
        return tuple(results.diagnostics)
    known, uncertain, types = facts.known, facts.uncertain, facts.types
    _, compatibility_uncertain = compatibility(ctx)
    drivers = [e for e in known if e[0] in {"digital_output", "analog_output", "power_output"}]
    open_drivers = [e for e in known if e[0] in {"drive.low", "drive.high"}]
    # A digital declaration still imposes its ordinary single-driver policy.
    if "tristate" in types and "digital" not in types:
        drivers = [
            e
            for e in drivers
            if e[0] != "digital_output" or not attribute(e[2], "tristate_capable", False)
        ]
        open_drivers = [e for e in open_drivers if not attribute(e[2], "tristate_capable", False)]
    if len(drivers) > 1 or (drivers and open_drivers) or len({e[0] for e in open_drivers}) > 1:
        conflicting = drivers + open_drivers
        emit(
            "multiple_drivers",
            f"net {name!r} has multiple active output drivers: "
            + ", ".join(e[1] for e in conflicting),
            origin,
            related=[
                Related(e[1], Span(**(e[2].get("binding_span") or e[2]["span"])))
                for e in conflicting
            ],
        )
    if any(e[0] == "power_output" for e in known) and (
        "ground" in types or any(e[0] == "ground" for e in known)
    ):
        emit("power_ground_conflict", f"net {name!r} connects a power source to ground", origin)
    for nettype, drive, pull, opposite in (
        ("idle_high", "drive.low", "up", "down"),
        ("idle_low", "drive.high", "down", "up"),
    ):
        if nettype not in types:
            continue
        if not any(e[0] == drive for e in known):
            emit("missing_driver", f"net {name!r} requires a {drive} endpoint", origin)
        pulls = {attribute(pin, "pull") for _, pin in endpoints}
        if pull not in pulls:
            emit("missing_pull", f"net {name!r} requires a pull.{pull} endpoint", origin)
        if opposite in pulls:
            emit("opposing_pull", f"net {name!r} forbids pull.{opposite}", origin)
    # Unknown or selectable endpoint roles cannot be treated as passive.
    # Bidirectional roles need an operating assumption on single-driver nets.
    needs_driver = bool(types & {"digital", "power"})
    ambiguous_driver = bool(uncertain) or any(
        e[0] in {"digital_bidirectional", "analog_bidirectional"} for e in known
    )
    if compatibility_uncertain and not needs_driver:
        emit(
            "incomplete_roles",
            f"net {name!r} has unknown or unselected roles; compatibility checking is incomplete",
            origin,
            severity="warning",
        )
    if needs_driver:
        expected = "power_output" if "power" in types else "digital_output"
        matching = [e for e in known if e[0] == expected]
        if ambiguous_driver or compatibility_uncertain:
            emit(
                "incomplete_roles",
                f"net {name!r} has unknown or unselected endpoint roles; driver checking is incomplete",
                origin,
                severity="warning",
            )
        elif not matching:
            pulls = {attribute(pin, "pull") for _, pin in endpoints} - {None, "none"}
            has_pull = expected == "digital_output" and pulls in ({"up"}, {"down"})
            has_pull = has_pull and all(
                ("up" if e[0] == "drive.low" else "down") in pulls for e in open_drivers
            )
            if not has_pull:
                emit("missing_driver", f"net {name!r} requires a {expected} endpoint", origin)
    return tuple(results.diagnostics)
