"""Bounded hover descriptions from resolved metadata and elaborated instances."""

from pathlib import Path
from dataclasses import dataclass
import re

from . import ast as a
from .diagnostics import DiagnosticError
from .evaluate import Evaluator, Number
from .metadata import Value
from .project import expression_path, source_text


BUILTIN_HELP = {
    "role": {
        "passive": "Passive contact; imposes no electrical domain and supplies no active driver.",
        "digital_input": "Receives a digital signal. An explicitly declared input port may be tied to a power or ground rail.",
        "digital_output": "Actively drives a digital signal; participates in multiple-driver checks.",
        "digital_bidirectional": "Can receive or drive digital signals. Single-driver nets need an operating assumption to resolve driver ambiguity.",
        "analog_input": "Receives an analog signal.",
        "analog_output": "Actively drives an analog signal; participates in multiple-driver checks.",
        "analog_bidirectional": "Can receive or drive analog signals.",
        "power_input": "Consumes power. Connecting to a ground net requires an explicit incompatible_role waiver. Optional max_load_current_mA metadata contributes to current-budget checks.",
        "power_output": "Supplies power. Optional max_source_current_mA metadata declares its current capacity.",
        "ground": "Ground connection; connecting a power output to ground is an ERC error.",
        "open_drain": "Can pull a signal low or release it. Multiple open-drain drivers may share a net; a pull-up provides the high level.",
        "open_source": "Can pull a signal high or release it. Multiple open-source drivers may share a net; a pull-down provides the low level.",
        "unchecked": "Electrical behavior is unspecified; ERC cannot treat this endpoint as passive or prove its driver behavior.",
        "do_not_connect": "Must not be assigned to any net. Conflicts with unused.never; this connection rule cannot be waived.",
    },
    "nettype": {
        "passive": "Allows passive, digital, analog, power, and ground roles. Driver conflicts and power-to-ground conflicts are still checked.",
        "digital": "Digital net with driver checks. Requires a digital output or a compatible declared bias; conflicting active drivers are errors.",
        "analog": "Allows passive and analog endpoints; checks conflicting active drivers without requiring a driver.",
        "power": "Supply rail. Allows power inputs/outputs, signal inputs, and passive contacts; requires a power source and checks annotated current budgets.",
        "ground": "Ground rail. Allows ground, digital/analog inputs, and passive contacts; rejects power inputs and outputs.",
        "open_drain": "Allows passive contacts, digital inputs, and open-drain drivers. Multiple open-drain drivers are allowed.",
        "open_source": "Allows passive contacts, digital inputs, and open-source drivers. Multiple open-source drivers are allowed.",
        "bidirectional": "Allows digital and passive endpoints without requiring a single known driver. Conflicting active output drivers are still errors.",
        "tristate": "Shared digital bus. Endpoints other than passive contacts and digital inputs require tristate_capable: true. Multiple capable drivers are allowed; enable timing and arbitration are not modeled.",
        "unchecked": "Skips this declaration's electrical compatibility constraints and reports an unchecked-net warning. Other declarations and mandatory connection policies still apply.",
    },
    "unused": {
        "always": "May be left unconnected without an explicit no_connect binding.",
        "explicit": "An unused pin or port must be explicitly bound to no_connect. This is the default unused policy.",
        "never": "Must be connected; no_connect is not allowed. An instance cannot weaken this policy.",
    },
}


@dataclass(frozen=True)
class HoverEntry:
    span: a.Span
    description: str


def format_hover(description, markdown=False):
    """Keep line boundaries in all clients; escape user text before Markdown."""
    lines = description.splitlines()
    if not lines:
        return ""
    if markdown:

        def escape(text):
            return re.sub(r"([\\`*_{}\[\]()<>#+.!|~-])", r"\\\1", text)

        return (
            "**"
            + escape(lines[0])
            + "**\n\n"
            + "\n".join("- " + escape(line) if line else "" for line in lines[1:])
        )
    return lines[0] + "\n\n" + "\n".join(lines[1:]) if len(lines) > 1 else lines[0]


def brief(value, limit=180):
    if isinstance(value, Value):
        value = value.value
    if isinstance(value, Number):
        value = value.value
    if isinstance(value, dict):
        text = "{" + ", ".join(list(value)[:6]) + (", …" if len(value) > 6 else "") + "}"
    elif isinstance(value, (tuple, list)):
        text = ", ".join(brief(v, 40) for v in value[:6]) + (
            f", … ({len(value)} items)" if len(value) > 6 else ""
        )
    else:
        text = str(value)
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


def key(span):
    if isinstance(span, dict):
        return span["filename"], span["start_pos"]
    return span.filename, span.start_pos


class HoverDetails:
    def __init__(self, project, definitions, circuit=None, sources=None):
        self.project, self.definitions = project, definitions
        self.sources = dict(sources or {})
        self.evaluator = Evaluator(project)
        self.environments = {}
        self.instances = {}
        self.nets = {}
        self.builtins = []
        if circuit:
            for record in [*circuit["parts"], *circuit["blocks"]]:
                self.instances.setdefault(key(record["source"]["declaration"]), []).append(record)
            for net in circuit["nets"]:
                for declaration in net["declarations"]:
                    self.nets.setdefault(key(declaration["span"]), {})[net["name"]] = len(
                        net["endpoints"]
                    )

    def builtin(self, expr, module, local):
        if not isinstance(expr, a.Member):
            return
        base = expression_path(expr.base)
        if not base or base.split(".")[0] in local:
            return
        symbol = module.symbols.get(base)
        if not symbol or symbol.module != "noscheme" or symbol.declaration is not None:
            return
        help_text = BUILTIN_HELP.get(symbol.name, {}).get(expr.name.text)
        if help_text:
            self.builtins.append(
                HoverEntry(expr.span, f"{symbol.name}.{expr.name.text}\n{help_text}")
            )

    def expression(self, expr):
        if not isinstance(expr, a.Node):
            return brief(expr)
        path = Path(expr.span.filename)
        if path not in self.sources:
            self.sources[path] = source_text(path)
        return brief(self.sources[path][expr.span.start_pos : expr.span.end_pos])

    def environment(self, definition):
        name = (definition.module, definition.name)
        if name not in self.environments:
            try:
                env = self.evaluator.bind(
                    definition, (), definition.module, {}, definition.source.span
                )
            except DiagnosticError:
                env = None
            self.environments[name] = env
        return self.environments[name]

    def value(self, value, environment=None):
        if isinstance(value.value, a.Node):
            if environment is not None:
                try:
                    return brief(self.evaluator.evaluate(value.value, value.module, environment))
                except DiagnosticError:
                    pass
            return self.expression(value.value) + " (expression)"
        return brief(value.value)

    def metadata(self, metadata, environment=None):
        lines = []
        for name, value in sorted(metadata.items()):
            if len(lines) >= 10:
                lines.append("… more metadata")
                break
            origin = f"{value.module}, {Path(value.span.filename).name}:{value.span.line}"
            if name == "custom":
                custom = value.value
                for custom_name in sorted(custom)[:4]:
                    item = custom[custom_name]
                    if custom_name == "kicad_footprint_candidates" and isinstance(
                        item.value, (tuple, list)
                    ):
                        text = f"{len(item.value)} saved candidates"
                    else:
                        text = brief(item.value)
                    lines.append(f"custom.{custom_name}: {text}")
                if len(custom) > 4:
                    lines.append("… more custom metadata")
            else:
                lines.append(f"{name}: {self.value(value, environment)} (from {origin})")
        return lines

    def parameter(self, definition, name):
        parameter = definition.parameters[name]
        text = f"parameter {name}: {parameter.type}"
        if parameter.default is None:
            return text + "\nRequired; no default."
        env = self.environment(definition)
        default = brief(env[name]) if env is not None else self.value(parameter.default)
        return text + (
            f"\nDefault: {default}\nDefault from {parameter.default.module}, "
            f"{Path(parameter.default.span.filename).name}:{parameter.default.span.line}"
        )

    def definition(self, definition):
        lines = [f"{definition.kind} {definition.module}.{definition.name}"]
        if definition.source.base:
            lines.append("Extends: " + ".".join(n.text for n in definition.source.base))
        env = self.environment(definition)
        if "value" in definition.metadata:
            lines.append("Default value: " + self.value(definition.metadata["value"], env))
        elif env is not None and "value" in env:
            lines.append("Default value: " + brief(env["value"]))
        for name in list(definition.parameters)[:6]:
            lines.append(self.parameter(definition, name))
        if len(definition.parameters) > 6:
            lines.append(f"… {len(definition.parameters)} parameters total")
        label = (
            "pins"
            if definition.kind == "component"
            else "fields"
            if definition.kind == "bundle"
            else "ports"
        )
        names = list(definition.interface)
        if names:
            lines.append(f"{label}: {brief(names)}")
        lines.extend(self.metadata(definition.metadata, env))
        return "\n".join(lines)

    def interface(self, definition, name):
        return self.field(
            definition.interface[name], f"{definition.kind} {definition.name}: {name}"
        )

    def field(self, field, label=None):
        lines = [label or f"bundle field {field.name}"]
        if field.type:
            lines.append("Type: " + self.expression(field.type.value))
        lines.extend(self.metadata(field.metadata))
        return "\n".join(lines)

    def instance(self, node, target):
        lines = [f"inst {node.name.text}: {target.module}.{target.name}"]
        records = self.instances.get(key(node.span), [])
        if records:
            lines.append(f"Elaborated instances: {len(records)}")
            # A reusable declaration can elaborate with different parameter values.
            for record in records[:4]:
                lines.append("\nInstance: " + record["identity"])
                if record.get("value") is not None:
                    lines.append("Value: " + brief(record["value"]["value"]))
                footprint = record.get("metadata", {}).get("footprint")
                if footprint:
                    lines.append("Footprint: " + brief(footprint["value"]))
                for name, value in list(record.get("parameters", {}).items())[:6]:
                    lines.append(f"Parameter {name}: {brief(value['value'])}")
            if len(records) > 4:
                lines.append(f"… {len(records) - 4} more instances")
        else:
            lines.append("Instance values unavailable; definition defaults follow.")
            lines.append(self.definition(target))
        return "\n".join(lines)

    def bound_interface(self, node, target, name):
        lines = [self.interface(target, name)]
        seen = set()
        for record in self.instances.get(key(node.span), []):
            pin = record.get("pins", {}).get(name)
            if not pin:
                continue
            metadata = pin.get("metadata", {})
            role = metadata.get("role", {}).get("value")
            net = pin.get("net")
            detail = f"Effective role: {brief(role)}; net: {net or '(unconnected)'}"
            if detail not in seen:
                seen.add(detail)
                lines.append(detail)
            if len(seen) == 4:
                break
        return "\n".join(lines)

    def net(self, node):
        lines = [
            f"{'global ' if node.global_ else ''}net {node.name.text}",
            "Type: " + self.expression(node.type),
        ]
        nets = self.nets.get(key(node.span), {})
        for name, count in list(nets.items())[:6]:
            lines.append(f"{name}: {count} endpoints")
        if len(nets) > 6:
            lines.append(f"… {len(nets)} elaborated nets total")
        return "\n".join(lines)
