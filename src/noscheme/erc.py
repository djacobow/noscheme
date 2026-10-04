"""Electrical checks on flattened physical endpoints, without component traversal.

Tri-state buses check declared capabilities, without modeling output enables.
"""

from fractions import Fraction
from dataclasses import replace
import json

from .ast import Span, to_data
from .circuit import path_text
from .diagnostics import Diagnostic, Related, render_text
from .erc_rules import CHECKS, ALLOWED_ROLES, PORT_ALLOWED, PORT_EVIDENCE
from .erc_config import configuration_views
from .erc_registry import NetContext, load_registry

PENDING = ()


def attribute(record, name, default=None):
    return record.get("metadata", {}).get(name, {}).get("value", default)


def check_port(port, endpoints, emit):
    """Check the block side of a public boundary without inventing endpoints."""
    scope = port["scope"]
    label = path_text(port["path"])
    roles = set(attribute(port, "role", []))
    policy = attribute(port, "unused", "explicit")
    binding = port["binding"]
    internal = [(name, pin) for name, pin in endpoints if pin["scope"][: len(scope)] == scope]
    external = [(name, pin) for name, pin in endpoints if pin["scope"][: len(scope)] != scope]
    if roles == {"do_not_connect"}:
        if binding == "connected":
            emit("do_not_connect", f"port {label} must not connect to a caller net", port, label)
        if policy == "never":
            emit(
                "required_port_connection",
                f"port {label}: unused.never conflicts with role.do_not_connect",
                port,
                label,
            )
        return
    # Board ports have no caller. Child ports must honor their caller contract,
    # regardless of how many physical endpoints happen to exist inside them.
    if scope:
        if binding == "omitted" and policy == "explicit":
            emit(
                "unused_port",
                f"port {label} must connect or explicitly bind to no_connect",
                port,
                label,
            )
        elif policy == "never" and binding != "connected":
            emit(
                "required_port_connection",
                f"port {label} must connect; no_connect cannot override unused.never",
                port,
                label,
            )
        elif policy != "always" and binding == "connected" and not external:
            emit(
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
            roles = [attribute(pin, "external_role", attribute(pin, "role", [])) for pin in pins]
            if any(role != ["power_input"] for role in roles):
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
            demands.setdefault(net, []).append(
                (part["path"], fields["max_load_current_mA"]["value"])
            )
            covered.update(f"{path}.{number}" for number in numbers)
    return demands, covered, annotated


def aggregate_current_loads(declarations, endpoints, demands, emit):
    """Replace descendants' demands from deepest supply boundary outward."""
    boundaries = [d for d in declarations if attribute(d, "aggregate_load") is not None]
    for port in sorted(boundaries, key=lambda d: (-len(d["scope"]), path_text(d["path"]))):
        scope = port["scope"]
        label = path_text(port["path"])
        annotation = port["metadata"]["aggregate_load"]
        amount = annotation["value"]["max_load_current_mA"]["value"]

        def inside(path):
            return path[: len(scope)] == scope

        internal = [(name, pin) for name, pin in endpoints if inside(pin["scope"])]
        roles = attribute(port, "external_role", attribute(port, "role", []))
        overlap = sum(d["scope"] == scope for d in boundaries) > 1
        sources = [
            pin
            for _, pin in internal
            if "power_output" in attribute(pin, "external_role", attribute(pin, "role", []))
        ]
        if (
            not scope
            or port["kind"] != "port"
            or overlap
            or sources
            or "external_role" in port["metadata"]
            or roles != ["power_input"]
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
            len(attribute(pin, "external_role", attribute(pin, "role", []))) != 1
            or "unchecked" in attribute(pin, "external_role", attribute(pin, "role", []))
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
        total = sum((Fraction(str(value)) for _, value in local if value is not None), Fraction())
        if total > Fraction(str(amount)):
            emit(
                "aggregate_current_overload",
                f"known internal loads exceed aggregate {label} ({amount} mA)",
                annotation,
                label,
                related=related,
            )
        demands = [(path, value) for path, value in demands if not inside(path)]
        demands.append((scope, amount))
    return demands


def check_circuit(circuit, *, waiver_records=None, registry=None):
    registry = registry or load_registry(circuit.get("erc_modules", ()), Span(**circuit["source"]))
    diagnostics = []
    waiver_records = waiver_records if waiver_records is not None else []
    active_waivers = {}
    by_net = {n["name"]: [] for n in circuit["nets"]}

    def record_diagnostic(diagnostic):
        check = diagnostic.code.removeprefix("erc.")
        waiver = active_waivers.get(check)
        if waiver and ("pins" not in waiver or diagnostic.instance_path in waiver["pins"]):
            waiver["diagnostics"].append(to_data(diagnostic))
        else:
            diagnostics.append(diagnostic)

    def emit(code, message, record, path=None, severity="error", related=()):
        span = record.get("binding_span") or record.get("span")
        diagnostic = Diagnostic(
            "erc." + code, severity, message, Span(**span), tuple(related), path
        )
        record_diagnostic(diagnostic)

    grouped_demands, grouped_pins, grouped_nets = grouped_current_loads(circuit, emit)

    for part in circuit["parts"]:
        for number, pin in part["pins"].items():
            label = f"{path_text(part['path'])}.{number}"
            record = dict(
                pin, scope=part["path"], span=pin.get("span", part["source"]["declaration"])
            )
            if pin["net"] is not None:
                by_net[pin["net"]].append((label, record))
            roles = attribute(pin, "role", [])
            policy = attribute(pin, "unused", "explicit")
            if roles == ["do_not_connect"]:
                if policy == "never":
                    emit(
                        "required_connection",
                        f"{label}: unused.never conflicts with role.do_not_connect",
                        record,
                        label,
                    )
                if pin["net"] is not None:
                    emit(
                        "do_not_connect", f"{label} must not be assigned to any net", record, label
                    )
                continue
            if pin["binding"] == "omitted" and policy == "explicit":
                emit(
                    "unused_pin",
                    f"{label} must connect or explicitly bind to no_connect",
                    record,
                    label,
                )
            elif pin["net"] is None and policy == "never":
                emit(
                    "required_connection",
                    f"{label} must connect; no_connect cannot override unused.never",
                    record,
                    label,
                )

    # A declared external port endpoint is real modeled equipment. Ordinary
    # ports remain contracts only and never add drivers or loads.
    for net in circuit["nets"]:
        for declaration in net["declarations"]:
            if "external_role" in declaration["metadata"]:
                by_net[net["name"]].append((path_text(declaration["path"]), declaration))

    for net in circuit["nets"]:
        endpoints = by_net[net["name"]]
        declarations = net["declarations"]
        origin = next(d for d in declarations if d["path"] == net["path"])
        name = net["name"]
        active_waivers = {}
        for declaration in declarations:
            for check, waiver in attribute(declaration, "waivers", {}).items():
                valid = declaration is origin
                value = waiver["value"]
                record = {
                    "net": name,
                    "check": check,
                    "reason": value["reason"]["value"] if isinstance(value, dict) else value,
                    "span": waiver["span"],
                    "valid_scope": valid,
                    "diagnostics": [],
                }
                if isinstance(value, dict):
                    prefix = path_text(declaration["scope"])
                    record["pins"] = [
                        f"{prefix}.{p}" if prefix else p for p in value["pins"]["value"]
                    ]
                    physical = {label for label, pin in endpoints if pin.get("kind") != "port"}
                    missing = sorted(set(record["pins"]) - physical)
                    record["valid_targets"] = not missing
                    if missing:
                        emit(
                            "waiver_target",
                            f"waiver pins are not physical endpoints on {name!r}: {', '.join(missing)}",
                            waiver,
                        )
                waiver_records.append(record)
                if valid and record.get("valid_targets", True):
                    active_waivers[check] = record
                elif not valid:
                    emit(
                        "waiver_scope",
                        f"waiver for {check} on {name!r} must be on its outermost net declaration",
                        waiver,
                        related=[Related("outermost declaration", Span(**origin["span"]))],
                    )
        if origin["type"] != "unchecked":
            for diagnostic in registry.run(NetContext(circuit, net, endpoints, origin)):
                record_diagnostic(diagnostic)
        # Ports do not add endpoints. An external role replaces the connector's
        # passive contact for driver/load accounting, not an additional source.
        for label, pin in endpoints:
            if (
                pin.get("kind") != "port"
                and len(endpoints) < 2
                and attribute(pin, "unused", "explicit") != "always"
                and attribute(pin, "role") != ["do_not_connect"]
            ):
                emit(
                    "required_connection",
                    f"{label} on {name!r} needs another physical endpoint",
                    pin,
                    label,
                )
        if not endpoints and not any(attribute(d, "placeholder", False) for d in declarations):
            emit("unconnected_net", f"net {name!r} has no physical endpoints", origin)
        for declaration in declarations:
            if declaration["kind"] == "port":
                check_port(declaration, endpoints, emit)

        # Each declaration keeps its own constraints. An inner unchecked port
        # cannot turn off checking for the rest of the flattened net.
        def fixed_input_port(declaration):
            return (
                origin["type"] in {"power", "ground"}
                and declaration["kind"] == "port"
                and declaration["type"] in {"digital", "bidirectional", "open_drain"}
                and attribute(declaration, "role") == ["digital_input"]
            )

        constraints = [
            d for d in declarations if d["type"] != "unchecked" and not fixed_input_port(d)
        ]

        def allowed(declaration, pin):
            roles = ALLOWED_ROLES[declaration["type"]]
            if declaration["type"] == "tristate" and not attribute(pin, "tristate_capable", False):
                return {"passive", "digital_input"}
            return roles

        compatibility_uncertain = False
        for label, pin in endpoints:
            roles = set(attribute(pin, "external_role", attribute(pin, "role", [])))
            if not roles or "unchecked" in roles:
                compatibility_uncertain = True
                continue
            if roles == {"do_not_connect"}:
                continue  # The mandatory connection policy reports this separately.
            permitted = roles.copy()
            for declaration in constraints:
                permitted &= allowed(declaration, pin)
            incompatible = [d for d in constraints if not roles & allowed(d, pin)]
            if not permitted and not incompatible:
                incompatible = constraints
            if incompatible:
                capability = any(
                    d["type"] == "tristate" and roles <= ALLOWED_ROLES["tristate"]
                    for d in incompatible
                )
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
                        Related(f"nettype.{d['type']} constraint", Span(**d["span"]))
                        for d in incompatible
                    ],
                )
            elif any(not roles <= allowed(d, pin) for d in constraints):
                compatibility_uncertain = True
        types = {d["type"] for d in constraints}
        demands = [
            (pin["scope"], attribute(pin, "max_load_current_mA"))
            for label, pin in endpoints
            if label not in grouped_pins
            and attribute(pin, "external_role", attribute(pin, "role", [])) == ["power_input"]
        ]
        demands.extend(grouped_demands.get(name, ()))
        demands = aggregate_current_loads(declarations, endpoints, demands, emit)
        amounts = [amount for _, amount in demands]
        if origin["type"] == "unchecked":
            emit(
                "unchecked_net",
                f"net {name!r} skips driver and current checks; inner type and port constraints still apply",
                origin,
                severity="warning",
            )
            continue
        known, uncertain = [], []
        for label, pin in endpoints:
            roles = attribute(pin, "external_role", attribute(pin, "role", []))
            if len(roles) != 1 or roles[0] in {"unchecked", "do_not_connect"}:
                uncertain.append((label, pin))
            else:
                known.append((roles[0], label, pin))
        drivers = [e for e in known if e[0] in {"digital_output", "analog_output", "power_output"}]
        open_drivers = [e for e in known if e[0] in {"open_drain", "open_source"}]
        # A digital declaration still imposes its ordinary single-driver policy.
        if "tristate" in types and "digital" not in types:
            drivers = [
                e
                for e in drivers
                if e[0] != "digital_output" or not attribute(e[2], "tristate_capable", False)
            ]
            open_drivers = [
                e for e in open_drivers if not attribute(e[2], "tristate_capable", False)
            ]
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
                biases = {attribute(pin, "bias") for _, pin in endpoints} - {None, "none"}
                biased = expected == "digital_output" and biases in ({"pull_up"}, {"pull_down"})
                biased = biased and all(
                    ("pull_up" if e[0] == "open_drain" else "pull_down") in biases
                    for e in open_drivers
                )
                if not biased:
                    emit("missing_driver", f"net {name!r} requires a {expected} endpoint", origin)
        sources = [e for e in known if e[0] == "power_output"]
        annotated = (
            any(attribute(d, "aggregate_load") is not None for d in declarations)
            or name in grouped_nets
            or any(
                attribute(pin, key) is not None
                for _, pin in endpoints
                for key in ("max_source_current_mA", "max_load_current_mA")
            )
        )
        if not annotated:
            continue
        capacities = [attribute(e[2], "max_source_current_mA") for e in sources]
        if uncertain or len(sources) != 1 or any(x is None for x in capacities + amounts):
            emit(
                "incomplete_budget",
                f"net {name!r} lacks a single annotated source or complete load information",
                origin,
                severity="warning",
            )
        if len(sources) == 1 and capacities[0] is not None:
            # Decimal spelling preserves finite metadata without floating sum overflow.
            total = sum((Fraction(str(x)) for x in amounts if x is not None), Fraction())
            if total > Fraction(str(capacities[0])):
                emit(
                    "current_overload",
                    f"known maximum loads on {name!r} exceed source capacity ({capacities[0]} mA)",
                    origin,
                )

    return tuple(diagnostics)


def apply_erc(circuit, configurations=None, *, registry=None):
    if registry is None:
        registry = load_registry(circuit.get("erc_modules", ()), Span(**circuit["source"]))
    waivers, diagnostics, reports = [], [], []
    for name, view, selections in configuration_views(circuit, configurations):
        view["erc_configuration"] = name
        view["erc_effective_choices"] = selections
        local_waivers = []
        local = tuple(
            replace(d, configuration=name)
            for d in check_circuit(view, waiver_records=local_waivers, registry=registry)
        )
        for waiver in local_waivers:
            waiver["configuration"] = name
            for diagnostic in waiver["diagnostics"]:
                diagnostic["configuration"] = name
        waivers.extend(local_waivers)
        diagnostics.extend(local)
        reports.append(
            {
                "name": name,
                "choices": selections,
                "errors": sum(d.severity == "error" for d in local),
                "warnings": sum(d.severity == "warning" for d in local),
            }
        )
    circuit["erc_performed"] = True
    circuit["erc"] = {
        "schema_version": 1,
        "checks": list(CHECKS) + list(registry.checks),
        "pending": list(PENDING),
        "errors": sum(d.severity == "error" for d in diagnostics),
        "warnings": sum(d.severity == "warning" for d in diagnostics),
        "waivers": waivers,
        "configurations": reports,
        "diagnostics": [to_data(d) for d in diagnostics],
    }
    return tuple(diagnostics)


def report_diagnostics(circuit):
    """Restore diagnostics from an existing report without rerunning checks."""
    return tuple(
        Diagnostic(
            d["code"],
            d["severity"],
            d["message"],
            Span(**d["span"]),
            tuple(Related(r["message"], Span(**r["span"])) for r in d["related"]),
            d["instance_path"],
            d.get("configuration"),
        )
        for d in circuit["erc"]["diagnostics"]
    )


def report_files(circuit, output_dir):
    report = circuit["erc"]
    diagnostics = report_diagnostics(circuit)
    header = f"ERC: {report['errors']} errors, {report['warnings']} warnings\n"
    header += "Implemented checks: " + ", ".join(report["checks"]) + "\n"
    header += "Pending checks: " + ", ".join(report["pending"]) + "\n"
    for configuration in report["configurations"]:
        label = configuration["name"] if configuration["name"] is not None else "unconfigured"
        header += f"Configuration {label}: {configuration['errors']} errors, {configuration['warnings']} warnings\n"
        for scope in configuration["choices"]:
            header += f"  {path_text(scope['scope']) or '(board)'}: {json.dumps(scope['selections'], sort_keys=True)}\n"
    for waiver in report["waivers"]:
        span = waiver["span"]
        status = (
            "invalid scope"
            if not waiver["valid_scope"]
            else "invalid pin targets"
            if not waiver.get("valid_targets", True)
            else f"{len(waiver['diagnostics'])} diagnostics waived"
        )
        targets = f" pins {', '.join(waiver['pins'])}" if "pins" in waiver else ""
        header += (
            f"{span['filename']}:{span['line']}:{span['column']}: waiver {waiver['check']} "
            f"on {waiver['net']}{targets} [{waiver.get('configuration') or 'unconfigured'}]: {waiver['reason']} ({status})\n"
        )
        for diagnostic in waiver["diagnostics"]:
            header += (
                f"  waived {diagnostic['severity']} {diagnostic['code']}: {diagnostic['message']}\n"
            )
    return {
        output_dir / "erc.json": json.dumps(report, indent=2) + "\n",
        output_dir / "erc.txt": header + render_text(diagnostics) + "\n",
    }
