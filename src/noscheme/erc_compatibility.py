"""Endpoint compatibility with every declared net constraint."""

from .ast import Span
from .diagnostics import Related
from .erc_facts import Results, compatibility


def net_results(ctx):
    results = Results()
    emit = results.emit
    name = ctx.net.label
    conflicts, _ = compatibility(ctx)
    for label, pin, roles, incompatible, capability in conflicts:
        emit(
            "tristate_capability" if capability else "incompatible_role",
            (
                f"{label} on tri-state net {name!r} requires tristate_capable: true"
                if capability
                else f"{label} roles {', '.join(sorted(roles))} are incompatible with net {name!r}"
            ),
            pin,
            label,
            related=[
                Related(f"nettype.{d['type']} constraint", Span(**d["span"])) for d in incompatible
            ],
        )
    return tuple(results.diagnostics)
