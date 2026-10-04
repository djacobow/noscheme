"""Electrical checks on flattened physical endpoints, without component traversal.

This first pass deliberately reports its coverage. Configuration selection,
port contracts, bias models, and aggregate budgets remain separate work.
"""

from fractions import Fraction
import json

from .ast import Span, to_data
from .circuit import path_text
from .diagnostics import Diagnostic, Related, render_text
from .erc_rules import CHECKS

PENDING = (
    "role_nettype_compatibility",
    "public_port_contracts",
    "erc_configurations",
    "bias_and_tristate",
    "aggregate_current_budgets",
)


def attribute(record, name, default=None):
    return record.get("metadata", {}).get(name, {}).get("value", default)


def check_circuit(circuit, *, waiver_records=None):
    diagnostics = []
    waiver_records = waiver_records if waiver_records is not None else []
    active_waivers = {}
    by_net = {n["name"]: [] for n in circuit["nets"]}

    def emit(code, message, record, path=None, severity="error", related=()):
        span = record.get("binding_span") or record.get("span")
        diagnostic = Diagnostic(
            "erc." + code, severity, message, Span(**span), tuple(related), path
        )
        if code in active_waivers:
            active_waivers[code]["diagnostics"].append(to_data(diagnostic))
        else:
            diagnostics.append(diagnostic)

    for part in circuit["parts"]:
        for number, pin in part["pins"].items():
            label = f"{path_text(part['path'])}.{number}"
            record = dict(pin, span=pin.get("span", part["source"]["declaration"]))
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

    for net in circuit["nets"]:
        endpoints = by_net[net["name"]]
        declarations = net["declarations"]
        origin = next(d for d in declarations if d["path"] == net["path"])
        name = net["name"]
        active_waivers = {}
        for declaration in declarations:
            for check, waiver in attribute(declaration, "waivers", {}).items():
                valid = declaration is origin
                record = {
                    "net": name,
                    "check": check,
                    "reason": waiver["value"],
                    "span": waiver["span"],
                    "valid_scope": valid,
                    "diagnostics": [],
                }
                waiver_records.append(record)
                if valid:
                    active_waivers[check] = record
                else:
                    emit(
                        "waiver_scope",
                        f"waiver for {check} on {name!r} must be on its outermost net declaration",
                        waiver,
                        related=[Related("outermost declaration", Span(**origin["span"]))],
                    )
        # Ports do not add endpoints. An external role replaces the connector's
        # passive contact for driver/load accounting, not an additional source.
        for label, pin in endpoints:
            if (
                len(endpoints) < 2
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
        types = {d["type"] for d in declarations}
        if "unchecked" in types:
            emit(
                "unchecked_net",
                f"net {name!r} skips driver and current checks",
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
        if len(drivers) > 1:
            emit(
                "multiple_drivers",
                f"net {name!r} has multiple active output drivers: "
                + ", ".join(e[1] for e in drivers),
                origin,
                related=[
                    Related(e[1], Span(**(e[2].get("binding_span") or e[2]["span"])))
                    for e in drivers
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
        if needs_driver:
            expected = "power_output" if "power" in types else "digital_output"
            matching = [e for e in known if e[0] == expected]
            if ambiguous_driver:
                emit(
                    "incomplete_roles",
                    f"net {name!r} has unknown or unselected endpoint roles; driver checking is incomplete",
                    origin,
                    severity="warning",
                )
            elif not matching:
                emit("missing_driver", f"net {name!r} requires a {expected} endpoint", origin)
        sources = [e for e in known if e[0] == "power_output"]
        loads = [e for e in known if e[0] == "power_input"]
        annotated = any(
            attribute(pin, key) is not None
            for _, pin in endpoints
            for key in ("max_source_current_mA", "max_load_current_mA")
        )
        if not annotated:
            continue
        capacities = [attribute(e[2], "max_source_current_mA") for e in sources]
        demands = [attribute(e[2], "max_load_current_mA") for e in loads]
        if uncertain or len(sources) != 1 or any(x is None for x in capacities + demands):
            emit(
                "incomplete_budget",
                f"net {name!r} lacks a single annotated source or complete load information",
                origin,
                severity="warning",
            )
        if len(sources) == 1 and capacities[0] is not None:
            # Decimal spelling preserves finite metadata without floating sum overflow.
            total = sum((Fraction(str(x)) for x in demands if x is not None), Fraction())
            if total > Fraction(str(capacities[0])):
                emit(
                    "current_overload",
                    f"known maximum loads on {name!r} exceed source capacity ({capacities[0]} mA)",
                    origin,
                )

    return tuple(diagnostics)


def apply_erc(circuit):
    waivers = []
    diagnostics = check_circuit(circuit, waiver_records=waivers)
    circuit["erc_performed"] = True
    circuit["erc"] = {
        "schema_version": 1,
        "checks": list(CHECKS),
        "pending": list(PENDING),
        "errors": sum(d.severity == "error" for d in diagnostics),
        "warnings": sum(d.severity == "warning" for d in diagnostics),
        "waivers": waivers,
        "diagnostics": [to_data(d) for d in diagnostics],
    }
    return diagnostics


def report_files(circuit, output_dir):
    report = circuit["erc"]
    diagnostics = [
        Diagnostic(
            d["code"],
            d["severity"],
            d["message"],
            Span(**d["span"]),
            tuple(Related(r["message"], Span(**r["span"])) for r in d["related"]),
            d["instance_path"],
        )
        for d in report["diagnostics"]
    ]
    header = f"ERC: {report['errors']} errors, {report['warnings']} warnings\n"
    header += "Implemented checks: " + ", ".join(report["checks"]) + "\n"
    header += "Pending checks: " + ", ".join(report["pending"]) + "\n"
    for waiver in report["waivers"]:
        span = waiver["span"]
        status = (
            "invalid scope"
            if not waiver["valid_scope"]
            else f"{len(waiver['diagnostics'])} diagnostics waived"
        )
        header += (
            f"{span['filename']}:{span['line']}:{span['column']}: waiver {waiver['check']} "
            f"on {waiver['net']}: {waiver['reason']} ({status})\n"
        )
        for diagnostic in waiver["diagnostics"]:
            header += (
                f"  waived {diagnostic['severity']} {diagnostic['code']}: {diagnostic['message']}\n"
            )
    return {
        output_dir / "erc.json": json.dumps(report, indent=2) + "\n",
        output_dir / "erc.txt": header + render_text(diagnostics) + "\n",
    }
