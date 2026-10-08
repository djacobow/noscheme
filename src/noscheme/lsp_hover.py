"""Bounded hover descriptions from resolved metadata and elaborated instances."""

from contextlib import suppress
from pathlib import Path
from dataclasses import dataclass
import re

from . import ast as a
from .diagnostics import DiagnosticError
from .evaluate import Evaluator, Number
from .erc_registry import signature
from .metadata import Value, registered_name
from .project import expression_path, source_text


from .builtin_help import BUILTIN_HELP


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
        registry = self.project.config.erc_registry
        name = registered_name(expr, self.project, module.name, local)
        if name in registry.rules:
            self.builtins.append(HoverEntry(expr.span, signature(name, registry.rules[name])))
        elif name in registry.predicates:
            self.builtins.append(HoverEntry(expr.span, f"endpoint predicate {name}"))
        if name or not isinstance(expr, a.Member):
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

    def default_value(self, definition, env):
        """The displayed value with defaults, formatted like an elaborated part's value."""
        if env is not None:
            with suppress(DiagnosticError):
                return brief(self.evaluator.part_value(definition, env))
        return self.value(definition.metadata["value"], env)

    def definition(self, definition):
        lines = [f"{definition.kind} {definition.module}.{definition.name}"]
        if definition.source.base:
            lines.append("Extends: " + ".".join(n.text for n in definition.source.base))
        if definition.electrical:
            lines.append(f"Electrical type: nettype.{definition.electrical}")
        env = self.environment(definition)
        if "value" in definition.metadata or env is not None and "value" in env:
            lines.append("Default value: " + self.default_value(definition, env))
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
