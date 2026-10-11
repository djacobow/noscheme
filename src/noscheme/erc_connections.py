"""Required, omitted, and forbidden physical connections."""

from .erc_facts import Results, net_facts, attribute


def net_results(ctx):
    results = Results()
    emit = results.emit
    facts = net_facts(ctx)
    origin = facts.origin
    name = ctx.net.label
    endpoints = ctx.endpoints
    declarations = ctx.net["declarations"]
    # Ports do not add endpoints. An external role replaces the connector's
    # passive contact for driver/load accounting, not an additional source.
    for label, pin in endpoints:
        if (
            pin.get("kind") != "port"
            and len(endpoints) < 2
            and attribute(pin, "use", "explicit") not in {"optional", "disallowed"}
        ):
            results.connection(
                ctx,
                "required_connection",
                f"{label} on {name!r} needs another physical endpoint",
                pin,
                label,
            )
    if not endpoints and not any(attribute(d, "placeholder", False) for d in declarations):
        emit("unconnected_net", f"net {name!r} has no physical endpoints", origin)
    return tuple(results.diagnostics)


def pin_results(ctx):
    results = Results()
    emit = results.emit
    pin = record = ctx.pin
    label = pin.label
    policy = attribute(pin, "use", "explicit")
    if policy == "disallowed":
        if pin["net"] is not None:
            emit("do_not_connect", f"{label} must not be assigned to any net", record, label)
        return tuple(results.diagnostics)
    if pin["binding"] == "omitted" and policy == "explicit":
        results.connection(
            ctx,
            "unused_pin",
            f"{label} must connect or explicitly bind to no_connect",
            record,
            label,
        )
    elif pin["net"] is None and policy == "required":
        results.connection(
            ctx,
            "required_connection",
            f"{label} must connect; no_connect cannot override use.required",
            record,
            label,
        )
    return tuple(results.diagnostics)
