"""Grouped loads, public supply budgets, and source capacity checks."""

from .erc_primitives import floating_sum
from .erc_views import effective_roles
from .ast import Span
from .circuit import path_text
from .diagnostics import Related
from .erc_facts import Results, net_facts, attribute
from .units import format_quantity


def grouped_current_loads(circuit, emit):
    """Count each valid group once; invalid groups never hide missing pin budgets."""
    demands, covered, annotated = {}, set(), set()
    for part in circuit["parts"]:
        path = path_text(part["path"])
        for name, group in attribute(part, "current_loads", {}).items():
            fields = group["value"]
            numbers = fields["pins"]["value"]
            pins = [part["pins"][number] for number in numbers]
            nets = {pin["net"] for pin in pins}
            annotated.update(nets - {None})
            related = [
                Related(f"{path}.{number}", Span(**(pin.get("binding_span") or pin["span"])))
                for number, pin in zip(numbers, pins)
            ]
            if None in nets or len(nets) != 1:
                emit(
                    "current_load_group",
                    f"current load {path}.{name} requires every member pin on the same net",
                    group,
                    path,
                    related=related,
                )
                continue
            roles = [effective_roles(pin) for pin in pins]
            if any(tuple(role) != ("power_input",) for role in roles):
                unknown = any(len(role) != 1 or "unchecked" in role for role in roles)
                emit(
                    "current_load_group",
                    f"current load {path}.{name} requires power_input roles on every member pin",
                    group,
                    path,
                    severity="warning" if unknown else "error",
                    related=related,
                )
                continue
            net = next(iter(nets))
            demands.setdefault(net, []).append((part["path"], fields["max_load_current"]["value"]))
            covered.update(f"{path}.{number}" for number in numbers)
    return demands, covered, annotated


def aggregate_current_loads(declarations, endpoints, demands, emit):
    """Replace descendants' demands from deepest supply boundary outward."""
    boundaries = [d for d in declarations if attribute(d, "aggregate_load") is not None]
    for port in sorted(boundaries, key=lambda d: (-len(d["scope"]), path_text(d["path"]))):
        scope = port["scope"]
        label = path_text(port["path"])
        annotation = port["metadata"]["aggregate_load"]
        amount = annotation["value"]["max_load_current"]["value"]

        def inside(path):
            return path[: len(scope)] == scope

        internal = [(name, pin) for name, pin in endpoints if inside(pin["scope"])]
        roles = effective_roles(port)
        overlap = sum(d["scope"] == scope for d in boundaries) > 1
        sources = [pin for _, pin in internal if "power_output" in effective_roles(pin)]
        if (
            not scope
            or port["kind"] != "port"
            or overlap
            or sources
            or "external_role" in port["metadata"]
            or "external_drive" in port["metadata"]
            or tuple(roles) != ("power_input",)
        ):
            emit(
                "aggregate_load_boundary",
                f"aggregate load {label} requires a unique power_input boundary with no internal source on this net",
                annotation,
                label,
            )
            continue
        local = [(path, value) for path, value in demands if inside(path)]
        unknown_roles = any(
            len(effective_roles(pin)) != 1 or "unchecked" in effective_roles(pin)
            for _, pin in internal
        )
        related = [
            Related(name, Span(**(pin.get("binding_span") or pin["span"])))
            for name, pin in internal
        ]
        if not internal or unknown_roles or any(value is None for _, value in local):
            emit(
                "incomplete_aggregate_load",
                f"aggregate load {label} has incomplete internal load information",
                annotation,
                label,
                severity="warning",
                related=related,
            )
        total = floating_sum(float(value) for _, value in local if value is not None)
        if total > float(amount):
            emit(
                "aggregate_current_overload",
                f"known internal loads exceed aggregate {label} ({format_quantity(amount, 'current')})",
                annotation,
                label,
                related=related,
            )
        demands = [(path, value) for path, value in demands if not inside(path)]
        demands.append((scope, amount))
    return demands


def group_facts(ctx):
    def calculate():
        results = Results()
        demands, pins, nets = grouped_current_loads(ctx.circuit, results.emit)
        return demands, pins, nets, tuple(results.diagnostics)

    return ctx.shared("std.current_loads", calculate)


def group_results(ctx):
    first = next(iter(ctx.pin.component["pins"]))
    if ctx.pin.label != ctx.pin.component.label + "." + first:
        return ()
    return tuple(d for d in group_facts(ctx)[3] if d.instance_path == ctx.pin.component.label)


def net_results(ctx):
    results = Results()
    emit = results.emit
    facts = net_facts(ctx)
    origin = facts.origin
    name = ctx.net.label
    endpoints = ctx.endpoints
    declarations = ctx.net["declarations"]
    grouped_demands, grouped_pins, grouped_nets, _ = group_facts(ctx)
    known, uncertain = facts.known, facts.uncertain
    demands = [
        (pin["scope"], attribute(pin, "max_load_current"))
        for label, pin in endpoints
        if label not in grouped_pins and effective_roles(pin) == ("power_input",)
    ]
    demands.extend(grouped_demands.get(name, ()))
    demands = aggregate_current_loads(declarations, endpoints, demands, emit)
    amounts = [amount for _, amount in demands]
    if origin["type"] == "unchecked":
        return tuple(results.diagnostics)
    sources = [e for e in known if e[0] == "power_output"]
    annotated = (
        any(attribute(d, "aggregate_load") is not None for d in declarations)
        or name in grouped_nets
        or any(
            attribute(pin, key) is not None
            for _, pin in endpoints
            for key in ("max_source_current", "max_load_current")
        )
    )
    if not annotated:
        return tuple(results.diagnostics)
    capacities = [attribute(e[2], "max_source_current") for e in sources]
    if uncertain or len(sources) != 1 or any(x is None for x in capacities + amounts):
        emit(
            "incomplete_budget",
            f"net {name!r} lacks a single annotated source or complete load information",
            origin,
            severity="warning",
        )
    if len(sources) == 1 and capacities[0] is not None:
        # Electrical budgets use ordinary floating-point accumulation.
        total = floating_sum(float(x) for x in amounts if x is not None)
        if total > float(capacities[0]):
            emit(
                "current_overload",
                f"known maximum loads on {name!r} exceed source capacity "
                f"({format_quantity(capacities[0], 'current')})",
                origin,
            )
    return tuple(results.diagnostics)
