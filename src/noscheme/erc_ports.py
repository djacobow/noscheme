"""Public port connection policies and aggregate role contracts."""

from .ast import Span
from .circuit import path_text
from .diagnostics import Related
from .erc_rules import PORT_ALLOWED, PORT_EVIDENCE
from .erc_facts import Results, attribute


def check_port(port, endpoints, emit, connection):
    """Check the block side of a public boundary without inventing endpoints."""
    scope = port["scope"]
    label = path_text(port["path"])
    roles = set(attribute(port, "role", []))
    policy = attribute(port, "use", "explicit")
    binding = port["binding"]
    internal = [(name, pin) for name, pin in endpoints if pin["scope"][: len(scope)] == scope]
    external = [(name, pin) for name, pin in endpoints if pin["scope"][: len(scope)] != scope]
    if policy == "disallowed":
        if binding == "connected":
            emit("do_not_connect", f"port {label} must not connect to a caller net", port, label)
        return
    # Board ports have no caller. Child ports must honor their caller contract,
    # regardless of how many physical endpoints happen to exist inside them.
    if scope:
        if binding == "omitted" and policy == "explicit":
            connection(
                "unused_port",
                f"port {label} must connect or explicitly bind to no_connect",
                port,
                label,
            )
        elif policy == "required" and binding != "connected":
            connection(
                "required_port_connection",
                f"port {label} must connect; no_connect cannot override use.required",
                port,
                label,
            )
        elif policy != "optional" and binding == "connected" and not external:
            connection(
                "required_port_connection",
                f"port {label} needs an endpoint outside its block",
                port,
                label,
            )
    if not roles or roles == {"unchecked"}:
        return
    known, unknown, alternatives = set(), False, []
    for _, pin in internal:
        possible = set(attribute(pin, "external_role", attribute(pin, "role", [])))
        if len(possible) != 1 or "unchecked" in possible:
            unknown = True
        else:
            known.update(possible)
        if possible and "unchecked" not in possible:
            alternatives.append(possible)
    # Each permitted role is an alternative aggregate contract. Without a
    # selection we can reject only when none could describe the implementation.
    candidates = [r for r in roles if all(possible & PORT_ALLOWED[r] for possible in alternatives)]
    if not candidates:
        emit(
            "port_role",
            f"port {label} roles {', '.join(sorted(roles))} contradict its internal endpoints ({', '.join(sorted(known))})",
            port,
            label,
            related=[
                Related(name, Span(**(pin.get("binding_span") or pin["span"])))
                for name, pin in internal
            ],
        )
        return

    def has_evidence(role):
        if role not in PORT_EVIDENCE:
            return True
        if known & PORT_EVIDENCE[role]:
            return True
        if role.endswith("_bidirectional"):
            domain = role.removesuffix("_bidirectional")
            return {domain + "_input", domain + "_output"} <= known
        return False

    if unknown or len(roles) > 1:
        emit(
            "incomplete_port_role",
            f"port {label} has unknown or unselected roles; its contract is not fully verified",
            port,
            label,
            severity="warning",
        )
    elif not any(has_evidence(r) for r in candidates):
        emit(
            "port_role",
            f"port {label} has no internal endpoint implementing {next(iter(roles))}",
            port,
            label,
        )


def net_results(ctx):
    results = Results()
    for declaration in ctx.net["declarations"]:
        if declaration["kind"] == "port":
            check_port(
                declaration,
                ctx.endpoints,
                results.emit,
                lambda *args: results.connection(ctx, *args),
            )
    return tuple(results.diagnostics)
