"""Schedule registered ERC applications and collect diagnostics and waivers."""

from dataclasses import replace
import json
from collections.abc import Mapping

from .ast import Span, to_data
from .circuit import path_text
from .diagnostics import Diagnostic, DiagnosticError, Related, render_text
from .erc_config import configuration_views
from .erc_registry import Application, load_registry
from .erc_views import Circuit, Context, freeze, value
from .metadata import NUMERIC, SCHEMAS, valid_quantity
from .units import QUANTITIES, constraint

PENDING = ()


def failure(message, span):
    raise DiagnosticError([Diagnostic("erc.plugin", "error", message, span)])


def validate_params(rule, supplied, span, registry):
    if not isinstance(supplied, Mapping):
        failure("rule parameters must be a mapping", span)
    unknown = supplied.keys() - rule.parameters.keys()
    missing = rule.parameters.keys() - supplied.keys() - rule.defaults.keys()
    if unknown or missing:
        failure(
            f"invalid rule arguments: unknown {sorted(unknown)}, missing {sorted(missing)}", span
        )
    params = dict(rule.defaults, **supplied)

    def validate(item, kind):
        if isinstance(kind, dict):
            return (
                isinstance(item, Mapping)
                and item.keys() <= kind.keys()
                and all(validate(v, kind[k]) for k, v in item.items())
            )
        if isinstance(kind, tuple):
            return item in kind[1]
        if constraint(kind) in NUMERIC:
            return valid_quantity(item, constraint(kind))
        if kind == "string":
            return isinstance(item, str)
        if kind == "bool":
            return isinstance(item, bool)
        if kind == "field":
            return isinstance(item, str) and bool(item)
        if kind == "net_fields":
            return isinstance(item, tuple) and all(isinstance(v, str) and v for v in item)
        if kind == "selector":
            return isinstance(item, tuple) and all(valid_term(term) for term in item)
        return True

    def valid_term(term):
        if not isinstance(term, tuple) or not term or not all(isinstance(v, str) for v in term[:2]):
            return False
        if term[0] == "predicate":
            return len(term) == 2 and term[1] in registry.predicates
        return term[0] == "role" and len(term) == 2 or term[0] == "field" and len(term) == 3

    for name, item in params.items():
        if item is None and name in rule.defaults and rule.defaults[name] is None:
            continue
        kind = rule.parameters[name]
        if not validate(item, kind):
            failure(f"rule parameter {name!r} requires {kind}", span)
    return freeze(params)


class Scheduler:
    """Applications, contexts, and rule invocation for one configuration view.

    check_circuit runs every application; the native bridge uses the same
    schedule to run only plugin rules, rule sets, and predicates.
    """

    def __init__(self, circuit, registry, freeze_memo=None, rule_sets=None):
        self.registry = registry
        self.view = Circuit(freeze(circuit, freeze_memo))
        self.memo = {}
        schemas = {
            scope: dict(SCHEMAS[scope], **registry.schemas.get(scope, {}))
            for scope in ("pin", "instance_pin", "port", "net")
        }
        # A shared field can have different domain constraints in different scopes.
        # Use a nonnegative lower bound only if every registered endpoint domain agrees.
        pin_domains = {}
        for scope in ("pin", "instance_pin", "port"):
            for key, kind in schemas[scope].items():
                if isinstance(kind, str):
                    kind = constraint(kind)
                    previous = pin_domains.get(key, kind)
                    pin_domains[key] = (
                        kind
                        if previous == kind
                        else "nonnegative"
                        if {previous, kind} <= {"positive", "nonnegative"}
                        else "number"
                    )
        domains = {
            "pin": pin_domains,
            "net": {
                key: constraint(kind)
                for key, kind in schemas["net"].items()
                if isinstance(kind, str)
            },
            # The physical quantity of each field, for messages; independent of scope.
            "quantity": {
                key: kind
                for scope in schemas.values()
                for key, kind in scope.items()
                if isinstance(kind, str) and kind in QUANTITIES
            },
        }
        self.rulesets = circuit.get("erc_rule_sets", ["std.default"])
        for name in self.rulesets:
            if name not in registry.rule_sets:
                failure(f"unknown ERC rule set {name!r}", Span(**circuit["source"]))
        # Rule sets that contribute applications; all by default.
        self.active_rule_sets = self.rulesets if rule_sets is None else rule_sets
        self.predicates, self.reducers, self.domains = (
            freeze(registry.predicates),
            freeze(registry.reducers),
            freeze(domains),
        )
        self.contexts = {}
        self.default_params = {}
        # Rule-set items by owner and set, computed once per context.
        self.rule_set_cache = {}

    def context(self, owner, net=None, pin=None):
        key = ("pin" if pin else "net", owner.label)
        if key not in self.contexts:
            self.contexts[key] = Context(
                self.view,
                net,
                pin,
                owner,
                "",
                "",
                Span(**owner["span"]),
                predicates=self.predicates,
                reducers=self.reducers,
                domains=self.domains,
                _memo=self.memo,
            )
        return self.contexts[key]

    def rule_set_items(self, ctx):
        items = []
        for name in self.active_rule_sets:
            key = ("pin" if ctx.pin else "net", ctx.owner.label, name)
            if key not in self.rule_set_cache:
                try:
                    self.rule_set_cache[key] = tuple(self.registry.rule_sets[name](ctx))
                except Exception as exc:
                    failure(f"rule set {name!r} failed: {exc}", ctx.span)
            items.extend(self.rule_set_cache[key])
        return items

    def applications(self, ctx, defaults=True):
        registry = self.registry
        items = list(self.rule_set_items(ctx)) if defaults else []
        for name, entry in ctx.owner.attributes.get("checks", {}).items():
            raw = ctx.owner["metadata"]["checks"]["value"][name]
            items.append(
                (
                    Application(
                        name,
                        entry["checker"],
                        entry.get("params", {}),
                        entry.get("missing", "warning"),
                    ),
                    Span(**raw["span"]),
                )
            )
        seen = set()
        for item in items:
            if isinstance(item, tuple) and (len(item) != 2 or not isinstance(item[1], Span)):
                failure("rule sets must yield Application objects", ctx.span)
            app, span = item if isinstance(item, tuple) else (item, ctx.span)
            if not isinstance(app, Application) or not isinstance(app.name, str) or not app.name:
                failure("rule sets must return named Application objects", span)
            if app.name in seen:
                failure(f"duplicate application {app.name!r} on {ctx.owner.label}", span)
            seen.add(app.name)
            if app.checker not in registry.rules:
                failure(f"unknown rule {app.checker!r}", span)
            rule = registry.rules[app.checker]
            if rule.scope != ("pin" if ctx.pin else "net"):
                failure(f"rule {app.checker!r} has the wrong scope", span)
            if app.missing not in {"warning", "error", "ignore"}:
                failure("invalid missing-data policy", span)
            identity = ("pin:" if ctx.pin else "net:") + ctx.owner.label + ":" + app.name
            yield (
                app,
                rule,
                replace(
                    ctx, application=identity, checker=app.checker, span=span, missing=app.missing
                ),
            )

    def schedules(self):
        view, schedules = self.view, {}
        for net in view.nets.values():
            # A net type's application reaches a net through every declaration of that type.
            # Identical applications of a net-only rule run once, on the outermost declaration;
            # other rules may inspect ctx.owner, so each declaration keeps its application.
            seen = set()
            for declaration in sorted(net.declarations, key=lambda d: d["path"] != net["path"]):
                entries = []
                for entry in self.applications(
                    self.context(declaration, net), declaration["path"] == net["path"]
                ):
                    app, rule, active = entry
                    key = (app.name, app.checker, app.missing, active.span, repr(app.params))
                    if not rule.net_only or key not in seen:
                        seen.add(key)
                        entries.append(entry)
                schedules[("net", declaration.label)] = tuple(entries)
        for pin in view.pins.values():
            schedules[("pin", pin.label)] = tuple(
                self.applications(self.context(pin, view.nets.get(pin["net"]), pin))
            )
        return schedules

    def invoke(self, app, rule, active):
        """Validate arguments and run one application's callback."""
        if isinstance(app.params, Mapping) and not app.params:
            # Cache only successful default validation, preserving the first
            # application span for errors. Explicit arguments still validate
            # independently, and callbacks receive immutable parameters.
            if app.checker not in self.default_params:
                self.default_params[app.checker] = validate_params(
                    rule, app.params, active.span, self.registry
                )
            params = self.default_params[app.checker]
        else:
            params = validate_params(rule, app.params, active.span, self.registry)
        try:
            results = tuple(rule.callback(active, **params))
            for diagnostic in results:
                if (
                    not isinstance(diagnostic, Diagnostic)
                    or diagnostic.severity not in {"error", "warning", "unknown"}
                    or not isinstance(diagnostic.span, Span)
                    or not isinstance(diagnostic.code, str)
                    or not diagnostic.code.startswith("erc.")
                    or not isinstance(diagnostic.message, str)
                    or not isinstance(diagnostic.related, tuple)
                    or any(
                        not isinstance(r, Related) or not isinstance(r.span, Span)
                        for r in diagnostic.related
                    )
                ):
                    raise ValueError("rules must yield source-located Diagnostic objects")
        except Exception as exc:
            failure(
                f"application {active.application!r} ({app.checker}) failed: {exc}", active.span
            )
        return results


def check_circuit(circuit, *, waiver_records=None, registry=None, _freeze_memo=None):
    registry = registry or load_registry(circuit.get("erc_modules", ()), Span(**circuit["source"]))
    scheduler = Scheduler(circuit, registry, _freeze_memo)
    view = scheduler.view
    diagnostics = []
    waiver_records = waiver_records if waiver_records is not None else []
    schedules = scheduler.schedules()

    def run(ctx, waivers=None):
        for app, rule, active in schedules[("pin" if ctx.pin else "net", ctx.owner.label)]:
            for diagnostic in scheduler.invoke(app, rule, active):
                if diagnostic.severity == "unknown" and app.missing == "ignore":
                    continue
                diagnostic = replace(
                    diagnostic,
                    application=active.application,
                    severity=app.missing
                    if diagnostic.severity == "unknown"
                    else diagnostic.severity,
                )
                # A waiver names applications anywhere on its flattened net.
                waiver = (waivers or {}).get(app.name)
                if (
                    waiver
                    and rule.waivable
                    and ("pins" not in waiver or diagnostic.instance_path in waiver["pins"])
                ):
                    waiver["diagnostics"].append(to_data(diagnostic))
                else:
                    diagnostics.append(diagnostic)

    by_net_waivers = {}
    for net in view.nets.values():
        origin = next(d for d in net.declarations if d["path"] == net["path"])
        active_waivers = {}
        for declaration in net.declarations:
            for check, waiver in value(declaration, "waivers", {}).items():
                valid = declaration is origin
                payload = waiver["value"]
                record = {
                    "net": net.label,
                    "check": check,
                    "reason": payload["reason"]["value"]
                    if isinstance(payload, Mapping)
                    else payload,
                    "span": dict(waiver["span"]),
                    "valid_scope": valid,
                    "diagnostics": [],
                }
                if isinstance(payload, Mapping):
                    prefix = path_text(declaration["scope"])
                    record["pins"] = [
                        f"{prefix}.{p}" if prefix else p for p in payload["pins"]["value"]
                    ]
                    missing = set(record["pins"]) - {
                        p.label for p in net.pins if p.get("kind") != "port"
                    }
                    record["valid_targets"] = not missing
                    if missing:
                        diagnostics.append(
                            Diagnostic(
                                "erc.waiver_target",
                                "error",
                                f"waiver pins are not physical endpoints on {net.label!r}: {', '.join(sorted(missing))}",
                                Span(**waiver["span"]),
                            )
                        )
                waiver_records.append(record)
                if valid and record.get("valid_targets", True):
                    candidates = [
                        entry
                        for entries in schedules.values()
                        for entry in entries
                        if entry[2].net is net and entry[0].name == check
                    ]
                    if not candidates or any(not rule.waivable for _, rule, _ in candidates):
                        diagnostics.append(
                            Diagnostic(
                                "erc.waiver_target",
                                "error",
                                f"waiver {check!r} names no waivable application on {net.label!r}",
                                Span(**waiver["span"]),
                            )
                        )
                    else:
                        active_waivers[check] = record
                elif not valid:
                    diagnostics.append(
                        Diagnostic(
                            "erc.waiver_scope",
                            "error",
                            f"waiver for {check} on {net.label!r} must be on its outermost net declaration",
                            Span(**waiver["span"]),
                            (Related("outermost declaration", Span(**origin["span"])),),
                        )
                    )
        by_net_waivers[net.label] = active_waivers
        run(scheduler.context(origin, net), waivers=active_waivers)
        for declaration in net.declarations:
            if declaration is not origin:
                run(scheduler.context(declaration, net), waivers=active_waivers)
    for pin in view.pins.values():
        net = view.nets.get(pin["net"])
        run(scheduler.context(pin, net, pin), waivers=by_net_waivers.get(pin["net"]))
    return tuple(diagnostics)


def apply_erc(circuit, configurations=None, *, registry=None):
    if registry is None:
        registry = load_registry(circuit.get("erc_modules", ()), Span(**circuit["source"]))
    waivers, diagnostics, reports = [], [], []
    # Configuration views share unchanged source records. Keep this memo local
    # to one run: later ERC calls must see edits to the mutable input circuit.
    freeze_memo = {}
    for name, view, selections in configuration_views(circuit, configurations):
        view["erc_configuration"] = name
        view["erc_effective_choices"] = selections
        local_waivers = []
        local = tuple(
            replace(d, configuration=name)
            for d in check_circuit(
                view, waiver_records=local_waivers, registry=registry, _freeze_memo=freeze_memo
            )
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
        "checks": list(registry.rules),
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
            d.get("application"),
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
