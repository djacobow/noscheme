"""Resolve definitions and public interfaces without executing or expanding them."""

from dataclasses import dataclass, fields, replace
import re

from . import ast as a
from .analysis_cache import cached_resolution
from .diagnostics import Related, DiagnosticError, collect_errors
from .metadata import (
    NETTYPES,
    NumericExpression,
    metadata_expressions,
    Value,
    enum,
    merge,
    pin_metadata,
    read_metadata,
    declared_choices,
    validate_selection,
    possible_selections,
)
from .project import IDENTIFIER, expression_path, fail, load_project
from .units import QUANTITIES

PARAMETER_TYPES = frozenset({"number", "string", *QUANTITIES})


def unnamed_parameter(parameters):
    """The parameter an unnamed `<value>` sets: the one without a default, or the only one."""
    candidates = [p for p in parameters.values() if p.default is None] or list(parameters.values())
    return candidates[0].name if len(candidates) == 1 else None


def unnamed_target(target, value, named):
    """The parameter of target that an unnamed value sets, or a located error."""
    if target.unnamed is None:
        candidates = [p for p in target.parameters.values() if p.default is None] or list(
            target.parameters.values()
        )
        message = (
            f"{target.name} has several parameters an unnamed value could set; "
            f"name one of: {', '.join(p.name for p in candidates)}"
            if candidates
            else f"{target.name} has no parameters"
        )
        fail("resolve.parameter", message, value.span)
    if target.unnamed in named:
        fail("resolve.parameter", f"parameter {target.unnamed!r} is set twice", value.span)
    return target.unnamed


def binding(span, pin, target):
    """A connection from a pin or port name to a target, located at span."""
    key = a.FieldKey(span, a.PinKey(span, pin, pin))
    return a.Binding(span, a.Endpoint(span, (), key, ()), target)


def flat_reference(target):
    """The net reference of a connection target, unwrapping flat(net)."""
    return target.arguments[0] if isinstance(target, a.Call) else target


@dataclass(frozen=True)
class Parameter:
    name: str
    type: str
    default: Value | None
    span: a.Span


@dataclass(frozen=True)
class Interface:
    name: str
    type: Value | None
    metadata: dict[str, Value]
    span: a.Span


@dataclass(frozen=True)
class Definition:
    module: str
    name: str
    kind: str
    source: a.Definition
    parameters: dict[str, Parameter]
    interface: dict[str, Interface]
    aliases: dict[str, str]
    metadata: dict[str, Value]
    body: tuple[a.Net | a.Instance, ...]
    body_module: str
    electrical: str | None = None  # A net type's builtin net kind: net.power is "power".
    unnamed: str | None = None  # The parameter that an unnamed `<value>` sets.


class Resolver:
    def __init__(self, project, max_pins=100_000, max_expression_depth=128):
        self.project = project
        self.max_pins = max_pins
        self.max_expression_depth = max_expression_depth
        self.definitions = {}

    def symbol(self, module, names, kinds):
        spelling = ".".join(n.text for n in names)
        symbol = self.project.modules[module].symbols.get(spelling)
        if (
            symbol is None
            and len(names) > 1
            and names[0].text in self.project.modules[module].symbols
        ):
            fail(
                "resolve.private",
                "qualified access to definition internals is not allowed",
                names[-1].span,
            )
        if symbol is None or symbol.declaration is None:
            fail("resolve.missing-definition", f"unknown definition {spelling!r}", names[0].span)
        if symbol.declaration.kind not in kinds:
            fail(
                "resolve.kind",
                f"{spelling!r} must name {' or '.join(sorted(kinds))}",
                names[0].span,
            )
        return (symbol.module, symbol.name)

    def builtin_base(self, module, source):
        """Return the builtin net kind (net.X) that a root net type extends, or None."""
        if source.kind != "nettype":
            return None
        prefix = ".".join(n.text for n in source.base[:-1])
        symbol = self.project.modules[module].symbols.get(prefix)
        if symbol is None or symbol.module != "noscheme" or symbol.name != "net":
            return None
        if source.base[-1].text not in NETTYPES:
            fail(
                "metadata.enum",
                f"expected a builtin net value from: {', '.join(sorted(NETTYPES))}",
                source.base[-1].span,
            )
        return source.base[-1].text

    def keys(self, key):
        if key.items:
            names = []
            seen = set()
            for item in key.items:
                for name in self.keys(item):
                    if name in seen:
                        fail(
                            "resolve.duplicate-pin",
                            f"pin {name!r} appears more than once in selection",
                            item.span,
                        )
                    seen.add(name)
                    names.append(name)
                    if len(names) > self.max_pins:
                        fail(
                            "resolve.limit", "pin selection exceeds configured pin limit", key.span
                        )
            return tuple(names)
        if key.last is None:
            return (key.first.text,)
        first, last = key.first.text, key.last.text
        if len(first) > 20 or len(last) > 20:
            fail("resolve.pin-range", "physical range bounds exceed the supported size", key.span)
        if not (
            first.isascii()
            and first.isdecimal()
            and last.isascii()
            and last.isdecimal()
            and str(int(first)) == first
            and str(int(last)) == last
        ):
            fail(
                "resolve.pin-range",
                "physical ranges require ordinary non-padded integer labels",
                key.span,
            )
        lo, hi = int(first), int(last)
        if abs(hi - lo) + 1 > self.max_pins:
            fail("resolve.pin-range", "physical pin range exceeds the pin limit", key.span)
        # A descending range selects its pins in descending order.
        step = 1 if hi >= lo else -1
        return tuple(str(x) for x in range(lo, hi + step, step))

    def key_spans(self, key):
        """Preserve each list member's location for navigation and hover."""
        return tuple((name, item.span) for item in key.items or (key,) for name in self.keys(item))

    def build(self, module, source, parent):
        sections = {s.name: s for s in source.body if isinstance(s, a.Section)}
        params = dict(parent.parameters) if parent else {}
        for f in sections.get("params", a.Section(source.span, "", ())).entries:
            if not isinstance(f.value, a.Name) or f.value.text not in PARAMETER_TYPES:
                fail(
                    "resolve.parameter-type",
                    "parameters have type number, string, or a quantity: "
                    + ", ".join(sorted(QUANTITIES)),
                    f.span,
                )
            name = f.key.first.text
            if not re.fullmatch(IDENTIFIER, name):
                fail("resolve.parameter-name", "parameter name must be an identifier", f.key.span)
            params[name] = Parameter(
                name,
                f.value.text,
                Value(f.default, f.default.span, module) if f.default else None,
                f.span,
            )
        defaults = sections.get("defaults", a.Section(source.span, "", ())).entries
        if source.parameter is not None:
            if parent is None:
                spelling = ".".join(n.text for n in source.base)
                fail("resolve.parameter", f"{spelling} has no parameters", source.parameter.span)
            named = {f.key.first.text for f in defaults}
            name = unnamed_target(parent, source.parameter, named)
            value = Value(source.parameter, source.parameter.span, module)
            params[name] = replace(params[name], default=value)
        for f in defaults:
            name = f.key.first.text
            if name not in params or f.key.last or f.value is None or f.fields:
                fail(
                    "resolve.default",
                    "defaults must bind existing parameters to expressions",
                    f.span,
                )
            params[name] = replace(params[name], default=Value(f.value, f.value.span, module))
        interface = dict(parent.interface) if parent else {}
        interface_section = "pins" if source.kind == "component" else "ports"
        declarations = sections.get(interface_section, a.Section(source.span, "", ())).entries
        if source.kind == "bundle":
            declarations = tuple(n for n in source.body if isinstance(n, a.Field))
        declared = {}
        for field in declarations:
            names = self.keys(field.key)
            if parent and source.kind == "component":
                names = tuple(parent.aliases.get(name, name) for name in names)
            if source.kind != "component" and field.key.grouped:
                fail("resolve.interface", "only physical pins support ranges", field.span)
            for name in names:
                if source.kind != "component" and not re.fullmatch(IDENTIFIER, name):
                    fail(
                        "resolve.interface",
                        "port and bundle field names must be identifiers",
                        field.key.span,
                    )
                if name in declared:
                    fail(
                        "resolve.duplicate-pin",
                        f"overlapping declaration for {name!r}",
                        field.span,
                        related=[Related("previous declaration", declared[name])],
                    )
                declared[name] = field.span
                previous = interface.get(name)
                if parent and previous is None:
                    fail(
                        "resolve.specialization",
                        f"specialization cannot add interface {name!r}",
                        field.span,
                    )
                if parent and source.kind != "component" and field.value is not None:
                    fail(
                        "resolve.specialization",
                        "specialization cannot change interface types",
                        field.span,
                    )
                if not parent and source.kind != "component" and field.value is None:
                    fail(
                        "resolve.interface", "port or bundle field requires a net type", field.span
                    )
                metadata = (
                    pin_metadata(field, "pin", self.project, module, params)
                    if source.kind == "component"
                    else read_metadata(field.fields, "port", self.project, module, params)
                )
                interface[name] = Interface(
                    name,
                    previous.type
                    if parent
                    else Value(field.value, field.value.span, module)
                    if field.value and source.kind != "component"
                    else None,
                    merge(previous.metadata, metadata) if previous else metadata,
                    field.span,
                )
            if len(interface) > self.max_pins:
                fail("resolve.limit", "interface exceeds configured pin limit", field.span)
        aliases = {name: name for name in interface}
        locations = {name: port.span for name, port in interface.items()}
        for name, port in interface.items():
            value = port.metadata.get("aliases")
            if value:
                for alias in value.value:
                    if alias in aliases and aliases[alias] != name:
                        fail(
                            "resolve.alias",
                            f"alias {alias!r} identifies multiple pins",
                            value.span,
                            related=[Related("other pin or alias", locations[alias])],
                        )
                    aliases[alias], locations[alias] = name, value.span
        metadata_fields = sections.get("metadata", a.Section(source.span, "", ())).entries
        if source.kind == "nettype":
            # A net type's body holds the metadata of a net declaration.
            metadata_fields = tuple(n for n in source.body if isinstance(n, a.Field))
        metadata = read_metadata(
            metadata_fields,
            "net" if source.kind == "nettype" else source.kind,
            self.project,
            module,
            params,
        )
        body = (
            parent.body
            if parent
            else tuple(n for n in source.body if isinstance(n, (a.Net, a.Instance)))
        )
        combined = merge(parent.metadata, metadata) if parent else metadata
        if source.kind == "component":
            combined = validate_current_loads(combined, interface, aliases)
            combined = validate_interchangeable(combined, interface, aliases, source.name.text)
        choices = combined.get("erc_choices", Value({}, source.span, module)).value
        configurations = combined.get("configurations", Value({}, source.span, module)).value
        for configuration in configurations.values():
            for name, selection in configuration.value.items():
                if name not in choices:
                    fail("erc.unknown_choice", f"unknown board ERC choice {name!r}", selection.span)
                if selection.value not in choices[name].value:
                    fail(
                        "erc.choice_value",
                        f"selection is outside alternatives for {name!r}",
                        selection.span,
                    )
        return Definition(
            module,
            source.name.text,
            source.kind,
            source,
            params,
            interface,
            aliases,
            combined,
            body,
            parent.body_module if parent else module,
            parent.electrical if parent else self.builtin_base(module, source),
            # Once a definition has an unnamed parameter, its extensions keep it.
            parent.unnamed if parent and parent.unnamed else unnamed_parameter(params),
        )

    @cached_resolution
    def resolve_interfaces(self):
        """Resolve inherited public interfaces without checking instance bodies."""
        sources = {
            (m.name, n.name.text): n
            for m in self.project.modules.values()
            for n in m.ast.items
            if isinstance(n, a.Definition)
        }
        for start in sorted(sources):
            chain, active = [], set()
            current = start
            while current not in self.definitions:
                if current in active:
                    fail(
                        "resolve.inheritance-cycle",
                        "inheritance cycle: " + " -> ".join(".".join(k) for k in chain + [current]),
                        sources[current].span,
                    )
                active.add(current)
                chain.append(current)
                source = sources[current]
                if not source.base or self.builtin_base(current[0], source):
                    break
                current = self.symbol(current[0], source.base, {source.kind})
            for key in reversed(chain):
                source = sources[key]
                parent = (
                    self.definitions[self.symbol(key[0], source.base, {source.kind})]
                    if source.base and not self.builtin_base(key[0], source)
                    else None
                )
                self.definitions[key] = self.build(key[0], source, parent)
        return dict(sorted(self.definitions.items()))

    def expand_arguments(self, definition):
        """Turn an unnamed parameter, positional nets, and `*` into ordinary entries.

        An unnamed parameter sets the target's one parameter without a default.
        Positional nets bind the target's interchangeable pins in order. `*` binds
        each remaining pin or port, by its name or an alias, to the caller's net or
        port of the same name; unmatched ones keep their unused-connection policy.
        """
        scope = {n.name.text for n in definition.body if isinstance(n, a.Net) and not n.global_}
        if definition.kind == "block":
            scope |= definition.interface.keys()
        body = []
        for node in definition.body:
            if isinstance(node, a.Instance) and (
                node.positional or node.wildcard or node.parameter is not None
            ):
                key = self.symbol(definition.body_module, node.component, {"component", "block"})
                target = self.definitions[key]
                existing = {s.name: s for s in node.sections}
                sections = [s for s in node.sections if s.name != "connections"]
                if node.parameter is not None:
                    params = existing.get("params", a.Section(node.parameter.span, "params", ()))
                    argument = self.parameter_argument(node, target, params.entries)
                    params = replace(params, entries=(argument, *params.entries))
                    sections = [s for s in sections if s.name != "params"] + [params]
                entries = existing["connections"].entries if "connections" in existing else ()
                entries += self.positional_bindings(node, target)
                if node.wildcard:
                    entries += self.wildcard_bindings(node, target, entries, scope)
                sections.append(a.Section(node.span, "connections", entries))
                node = replace(
                    node, sections=tuple(sections), positional=(), wildcard=None, parameter=None
                )
            body.append(node)
        return replace(definition, body=tuple(body))

    def parameter_argument(self, node, target, params):
        """`R<4.7k>` sets the one parameter without a default, or the only parameter."""
        value = node.parameter
        name = unnamed_target(target, value, {p.name.text for p in params})
        return a.Argument(value.span, a.Name(value.span, name), value)

    def positional_bindings(self, node, target):
        if not node.positional:
            return ()
        group = target.metadata.get("interchangeable")
        pins = group.value if group else ()
        if not pins:
            fail(
                "resolve.positional",
                f"{target.name} declares no interchangeable pins; connect pins by name",
                node.positional[0].span,
            )
        if len(node.positional) != len(pins):
            fail(
                "resolve.positional",
                f"{target.name} has {len(pins)} interchangeable pins; "
                "give a net or no_connect for each",
                node.span,
            )
        return tuple(binding(expr.span, pin, expr) for pin, expr in zip(pins, node.positional))

    def wildcard_bindings(self, node, target, entries, scope):
        bound = {
            target.aliases.get(name, name)
            for entry in entries
            for name in self.keys(entry.endpoint.head)
        }
        names = {}
        for alias, pin in target.aliases.items():
            names.setdefault(pin, []).append(alias)
        result = []
        for pin in target.interface:
            matches = sorted(name for name in names.get(pin, ()) if name in scope)
            if pin in bound or not matches:
                continue
            if len(matches) > 1:
                fail(
                    "resolve.wildcard",
                    f"* matches pin {pin!r} through several names: {', '.join(matches)}",
                    node.wildcard,
                )
            span = node.wildcard
            result.append(binding(span, matches[0], a.Name(span, matches[0])))
        if not result:
            fail("resolve.wildcard", "* matches no caller net or port", node.wildcard)
        return tuple(result)

    @cached_resolution
    def resolve_all(self):
        self.resolve_interfaces()
        errors = []
        for key, definition in list(self.definitions.items()):
            with collect_errors(errors):
                self.definitions[key] = self.expand_arguments(definition)
        for definition in self.definitions.values():
            with collect_errors(errors):
                self.check_body(definition)
        if errors:
            # Specializations can share the same body and diagnostic locations.
            raise DiagnosticError(tuple(dict.fromkeys(errors)))
        root = self.definitions[(self.project.board.module, self.project.board.name)]
        if root.interface:
            fail(
                "project.root-ports", "the root block must not have public ports", root.source.span
            )
        self.check_bundle_cycles()
        return dict(sorted(self.definitions.items()))

    def check_bundle_cycles(self):
        complete = set()
        for key, definition in self.definitions.items():
            if definition.kind != "bundle" or key in complete:
                continue
            active = set()
            stack = [(key, False)]
            while stack:
                current, finish = stack.pop()
                if finish:
                    active.remove(current)
                    complete.add(current)
                    continue
                if current in active:
                    fail(
                        "resolve.bundle-cycle",
                        "recursive bundle fields cannot form a finite net type",
                        self.definitions[current].source.span,
                    )
                if current in complete:
                    continue
                active.add(current)
                stack.append((current, True))
                for port in self.definitions[current].interface.values():
                    target = self.type_definition(port.type)
                    if target:
                        stack.append(((target.module, target.name), False))

    def type_definition(self, value):
        expr = value.value
        while isinstance(expr, a.Subscript):
            expr = expr.base
        spelling = expression_path(expr)
        symbol = self.project.modules[value.module].symbols.get(spelling)
        if symbol and symbol.declaration:
            names = []
            current = expr
            while isinstance(current, a.Member):
                names.insert(0, current.name)
                current = current.base
            names.insert(0, current)
            return self.definitions[self.symbol(value.module, tuple(names), {"bundle", "nettype"})]
        if isinstance(expr, a.Member):
            enum(expr, "net", NETTYPES, self.project, value.module)
            return None
        if not isinstance(expr, a.Name):
            fail("resolve.net-type", "expected a builtin net value, net type, or bundle", expr.span)
        return self.definitions[self.symbol(value.module, (expr,), {"bundle", "nettype"})]

    def select(self, value, selectors):
        for selector in selectors:
            if isinstance(selector, a.Index):
                continue  # Shape and index evaluation belongs to elaboration.
            definition = self.type_definition(value)
            if definition is None or selector.text not in definition.interface:
                fail(
                    "resolve.private",
                    f"{selector.text!r} is not a public bundle field",
                    selector.span,
                )
            value = definition.interface[selector.text].type
        return value

    def check_body(self, definition):
        locals_ = dict(definition.parameters)
        for name, port in definition.interface.items():
            if name in locals_:
                fail(
                    "resolve.duplicate-name",
                    f"interface {name!r} collides with a parameter",
                    port.span,
                )
            locals_[name] = port
            if port.type:
                self.type_definition(port.type)
        nets = {name: port.type for name, port in definition.interface.items() if port.type}
        for node in definition.body:
            if node.name.text in locals_:
                fail(
                    "resolve.duplicate-name",
                    f"duplicate local name {node.name.text!r}",
                    node.name.span,
                )
            locals_[node.name.text] = node
            if isinstance(node, a.Net):
                value = Value(node.type, node.type.span, definition.body_module)
                self.type_definition(value)
                net_metadata = read_metadata(
                    node.fields, "net", self.project, definition.body_module, definition.parameters
                )
                for expression in metadata_expressions(net_metadata):
                    self.check_expression(expression, definition.parameters)
                nets[node.name.text] = value
            elif (
                self.project.modules[definition.body_module].symbols.get(
                    ".".join(n.text for n in node.component)
                )
                is None
            ):
                # An unknown type may have been meant as a net; its instance check
                # reports it, and connections to it are not checked again.
                nets[node.name.text] = None
        errors = []
        for parameter in definition.parameters.values():
            if parameter.default:
                with collect_errors(errors):
                    self.check_expression(parameter.default, definition.parameters)
        for metadata in (
            definition.metadata,
            *(port.metadata for port in definition.interface.values()),
        ):
            for value in metadata_expressions(metadata):
                with collect_errors(errors):
                    self.check_expression(value, definition.parameters)
        for value in nets.values():
            if value is not None:
                with collect_errors(errors):
                    self.check_dimensions(value, definition.parameters)
        for node in definition.body:
            if isinstance(node, a.Instance):
                with collect_errors(errors):
                    self.check_instance(node, definition, nets)
        if errors:
            raise DiagnosticError(errors)

    def check_instance(self, node, definition, nets):
        target = self.definitions[
            self.symbol(definition.body_module, node.component, {"component", "block"})
        ]
        if "configurations" in target.metadata:
            fail("project.root-only", "only the root block may define configurations", node.span)
        if any(isinstance(n, a.Net) and n.global_ for n in target.body):
            fail("project.root-only", "only the root block may declare globals", node.span)
        errors = []
        for selection in node.dimensions:
            with collect_errors(errors):
                if selection.stop:
                    fail(
                        "resolve.dimension", "instance dimensions cannot be slices", selection.span
                    )
                self.check_expression(
                    Value(selection.start, selection.span, definition.body_module),
                    definition.parameters,
                )
        sections = {s.name: s for s in node.sections}
        for arg in sections.get("params", a.Section(node.span, "", ())).entries:
            with collect_errors(errors):
                if arg.name.text not in target.parameters:
                    fail("resolve.argument", f"unknown parameter {arg.name.text!r}", arg.name.span)
                self.check_expression(
                    Value(arg.value, arg.span, definition.body_module), definition.parameters
                )
        for binding in sections.get("connections", a.Section(node.span, "", ())).entries:
            with collect_errors(errors):
                self.check_binding(binding, target, definition, nets)
        with collect_errors(errors):
            self.instance_metadata(node, target, definition)
        for element in sections.get("elements", a.Section(node.span, "", ())).entries:
            with collect_errors(errors):
                if not node.dimensions or len(element.indices) != len(node.dimensions):
                    fail(
                        "array.element-rank",
                        "element overrides require one index per array dimension",
                        element.span,
                    )
                for index in element.indices:
                    if (
                        index.stop is not None
                        or not isinstance(index.start, a.Number)
                        or index.start.tag != "integer"
                    ):
                        fail(
                            "array.element-index",
                            "element indices must be nonnegative integer literals, without slices",
                            index.span,
                        )
                for section in element.sections:
                    if section.name == "metadata":
                        for field in section.entries:
                            if field.key.first.text in {"identity", "ref"}:
                                fail(
                                    "array.element-identity",
                                    "per-element identity and ref overrides are not supported",
                                    field.span,
                                )
                self.check_instance(replace(node, sections=element.sections), definition, nets)
        if errors:
            raise DiagnosticError(errors)

    def check_binding(self, binding, target, definition, nets):
        if binding.endpoint.head.items and target.kind != "component":
            fail(
                "resolve.pin-list",
                "pin lists select physical component pins, not block ports",
                binding.endpoint.head.span,
            )
        for selection in (*binding.endpoint.instance_indices, *binding.endpoint.selectors):
            if isinstance(selection, a.Index):
                self.check_selection(selection, definition.body_module, definition.parameters)
        for name in self.keys(binding.endpoint.head):
            pin = target.aliases.get(name)
            if pin is None:
                fail(
                    "resolve.private",
                    f"{name!r} is not a public pin or port of {target.name}",
                    binding.endpoint.head.span,
                )
            port = target.interface[pin]
            if binding.endpoint.selectors:
                if port.type is None:
                    fail(
                        "resolve.private",
                        "physical pins have no fields or elements",
                        binding.endpoint.span,
                    )
                self.select(port.type, binding.endpoint.selectors)
        expr, selectors = flat_reference(binding.target), []
        while isinstance(expr, (a.Member, a.Subscript)):
            selectors.append(expr.name if isinstance(expr, a.Member) else expr.selection)
            expr = expr.base
        selectors.reverse()
        for selector in selectors:
            if isinstance(selector, a.Index):
                self.check_selection(selector, definition.body_module, definition.parameters)
        if expr.text == "global":
            if not selectors or not isinstance(selectors[0], a.Name):
                fail(
                    "resolve.global",
                    "global references require an explicit net name",
                    expr.span,
                )
            return  # Existence depends on the board during expansion.
        if expr.text == "no_connect" and not selectors:
            if expr is not binding.target:
                fail("resolve.flat", "flat() takes an array net, not no_connect", expr.span)
            return
        if expr.text not in nets:
            fail(
                "resolve.net",
                f"{expr.text!r} is not a declared caller net or port",
                expr.span,
            )
        if nets[expr.text] is not None:
            self.select(nets[expr.text], selectors)

    def check_selection(self, selection, module, parameters):
        self.check_expression(Value(selection.start, selection.span, module), parameters)
        if selection.stop:
            self.check_expression(Value(selection.stop, selection.span, module), parameters)

    def check_dimensions(self, value, parameters):
        expr = value.value
        while isinstance(expr, a.Subscript):
            if expr.selection.stop:
                fail("resolve.dimension", "type dimensions cannot be slices", expr.selection.span)
            self.check_selection(expr.selection, value.module, parameters)
            expr = expr.base

    def check_expression(self, value, parameters, depth=0):
        if depth > self.max_expression_depth:
            fail(
                "resolve.expression-limit", "expression exceeds configured depth limit", value.span
            )
        expr = value.value
        if isinstance(expr, (a.Number, a.String)):
            return
        if isinstance(expr, a.Name):
            if expr.text not in parameters:
                fail("resolve.expression-name", f"unknown parameter {expr.text!r}", expr.span)
            return
        if isinstance(expr, (a.Unary, a.Group)):
            child = expr.operand if isinstance(expr, a.Unary) else expr.expression
            self.check_expression(Value(child, child.span, value.module), parameters, depth + 1)
            return
        if isinstance(expr, a.Binary):
            for child in (expr.left, expr.right):
                self.check_expression(Value(child, child.span, value.module), parameters, depth + 1)
            return
        if isinstance(expr, a.Call) and isinstance(expr.callee, a.Member):
            callee = expr.callee
            if callee.name.text == "format":
                self.check_expression(
                    Value(callee.base, callee.base.span, value.module), parameters, depth + 1
                )
            elif (namespace := expression_path(callee.base)) is not None:
                helpers = {"num": {"int", "ceil", "floor", "round"}}
                symbol = self.project.modules[value.module].symbols.get(namespace)
                if (
                    namespace.split(".")[0] in parameters
                    or symbol is None
                    or symbol.module != "noscheme"
                    or symbol.name not in helpers
                    or callee.name.text not in helpers[symbol.name]
                ):
                    fail(
                        "resolve.helper",
                        "unknown or unimported numeric helper",
                        callee.span,
                    )
            else:
                fail("resolve.helper", "arbitrary method calls are not supported", callee.span)
            for child in expr.arguments:
                self.check_expression(Value(child, child.span, value.module), parameters, depth + 1)
            for argument in expr.keywords:
                self.check_expression(
                    Value(argument.value, argument.span, value.module), parameters, depth + 1
                )
            return
        fail("resolve.expression", "unsupported expression in parameter/value context", expr.span)

    def instance_metadata(self, node, target, owner):
        fields = next((s.entries for s in node.sections if s.name == "metadata"), ())
        ordinary = tuple(f for f in fields if f.key.first.text != "pins")
        values = read_metadata(
            ordinary, "instance", self.project, owner.body_module, owner.parameters
        )
        if target.kind != "component" and set(values) - {"custom", "identity", "erc_choices"}:
            fail(
                "metadata.scope",
                "physical-part metadata cannot be applied to a reusable block",
                node.span,
            )
        choices = declared_choices(owner)
        bindings = values.get("erc_choices")
        if bindings:
            permitted_choices = declared_choices(target)
            for name, binding in bindings.value.items():
                if name not in permitted_choices:
                    fail("erc.unknown_choice", f"unknown public ERC choice {name!r}", binding.span)
                validate_selection(binding, choices)
                if not possible_selections(binding) <= set(permitted_choices[name].value):
                    fail(
                        "erc.choice_value",
                        f"selection is outside alternatives for {name!r}",
                        binding.span,
                    )
        for value in metadata_expressions(values):
            self.check_expression(value, owner.parameters)
        for group in fields:
            if group.key.first.text != "pins":
                continue
            if group.value is not None or group.key.last:
                fail(
                    "metadata.value",
                    "instance pins metadata requires an indented mapping",
                    group.span,
                )
            seen = {}
            for field in group.fields:
                if field.key.items and target.kind != "component":
                    fail(
                        "resolve.pin-list",
                        "pin lists select physical component pins, not block ports",
                        field.key.span,
                    )
                values = pin_metadata(
                    field, "instance_pin", self.project, owner.body_module, owner.parameters
                )
                for value in metadata_expressions(values):
                    self.check_expression(value, owner.parameters)
                for key in ("role", "external_role"):
                    if key in values:
                        validate_selection(values[key], choices)
                if "external_role" in values and target.kind == "component":
                    fail(
                        "metadata.role",
                        "write role on a component pin: on a passive or undeclared contact it "
                        "describes the attached equipment",
                        values["external_role"].span,
                    )
                for name in self.keys(field.key):
                    pin = target.aliases.get(name)
                    if pin is None:
                        fail("resolve.pin", f"unknown pin or port {name!r}", field.span)
                    if pin in seen:
                        fail(
                            "resolve.duplicate-pin",
                            f"duplicate annotation for {pin!r}",
                            field.span,
                            related=[Related("previous annotation", seen[pin])],
                        )
                    seen[pin] = field.span
                    permitted = target.interface[pin].metadata.get("role")
                    if (
                        "role" in values
                        and permitted is not None
                        and not attached_role(target, pin)
                        and not possible_selections(values["role"]) <= set(permitted.value)
                    ):
                        fail(
                            "metadata.constraint",
                            "instance role is outside the definition permitted roles",
                            values["role"].span,
                        )


def attached_role(definition, pin):
    """Whether an instance's role for a component pin describes attached equipment.

    On a contact that declares no role or only `passive`, such as a connector pin,
    the instance role describes the equipment attached there. Where a pin declares
    other roles, and on every block port (whose role is its public contract), an
    instance role selects among the declared roles.
    """
    declared = definition.interface[pin].metadata.get("role")
    return definition.kind == "component" and (
        declared is None or set(possible_selections(declared)) <= {"passive"}
    )


def instance_roles(definition, pin, values):
    """Store an attached-equipment role as the elaborated external_role attribute."""
    if "role" in values and attached_role(definition, pin):
        values = dict(values)
        values["external_role"] = values.pop("role")
    return values


def resolve_project(path=".", *, target=None):
    project = load_project(path, target=target)
    definitions = Resolver(project).resolve_all()
    return project, definitions


def resolution_summary(project, definitions):
    return {
        "schema_version": 1,
        "stage": "definition-resolution",
        "board": f"{project.board.module}.{project.board.name}",
        "definitions": [
            {
                "module": d.module,
                "name": d.name,
                "kind": d.kind,
                "parameters": {
                    k: {"type": p.type, "default": a.to_data(p.default)}
                    for k, p in sorted(d.parameters.items())
                },
                "interface": a.to_data(d.interface),
                "metadata": a.to_data(d.metadata),
                "body_module": d.body_module,
                "aliases": dict(sorted(d.aliases.items())),
            }
            for d in definitions.values()
        ],
        "remaining_stages": ["expression-evaluation", "elaboration", "ERC", "export"],
    }


def comparable(value):
    """A metadata value without source locations, for equality between pins."""
    if isinstance(value, Value):
        return comparable(value.value)
    if isinstance(value, dict):
        return {key: comparable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return tuple(comparable(item) for item in value)
    if isinstance(value, NumericExpression):
        return (value.constraint, comparable(value.expression))
    if isinstance(value, a.Node):
        return comparable(
            {f.name: getattr(value, f.name) for f in fields(value) if f.name != "span"}
            | {"node": type(value).__name__}
        )
    return value


def validate_interchangeable(metadata, interface, aliases, name):
    """Resolve the interchangeable group and require its pins to be indistinguishable."""
    group = metadata.get("interchangeable")
    if group is None or not group.value:
        return metadata
    pins = []
    for member in group.value:
        pin = aliases.get(member)
        if pin is None or pin in pins:
            fail("metadata.interchangeable", f"unknown or repeated pin {member!r}", group.span)
        pins.append(pin)
    if len(pins) < 2:
        fail("metadata.interchangeable", "an interchangeable group needs two pins", group.span)

    # Aliases name pins and custom data has no electrical meaning; all else must match.
    def electrical(pin):
        return comparable(
            {k: v for k, v in interface[pin].metadata.items() if k not in {"aliases", "custom"}}
        )

    for pin in pins[1:]:
        if electrical(pin) != electrical(pins[0]):
            fail(
                "metadata.interchangeable",
                f"{name}: pins {pins[0]!r} and {pin!r} differ, so they are not interchangeable; "
                "clear an inherited group with interchangeable: []",
                group.span,
                related=[Related("pin declaration", interface[pin].span)],
            )
    return {**metadata, "interchangeable": replace(group, value=tuple(pins))}


def validate_current_loads(metadata, interface, aliases):
    """Resolve group members to physical pads after inheritance and alias merging."""
    groups = metadata.get("current_loads")
    if groups is None:
        return metadata
    resolved, seen = {}, set()
    for name, group in groups.value.items():
        if set(group.value) != {"pins", "max_load_current"}:
            fail(
                "metadata.current_load",
                "each current load requires pins and max_load_current",
                group.span,
            )
        members = group.value["pins"]
        numbers = []
        for member in members.value:
            number = aliases.get(member)
            if number is None:
                fail("metadata.current_load", f"unknown current load pin {member!r}", members.span)
            if number in seen:
                fail(
                    "metadata.current_load",
                    f"pin {number!r} belongs to more than one current load entry",
                    members.span,
                )
            seen.add(number)
            pin = interface[number]
            roles = pin.metadata.get("role")
            if roles is None or "power_input" not in roles.value:
                fail(
                    "metadata.current_load",
                    f"current load pin {number!r} must permit role.power_input",
                    members.span,
                )
            if "max_load_current" in pin.metadata:
                fail(
                    "metadata.current_load",
                    f"pin {number!r} cannot have both an individual and grouped load",
                    members.span,
                )
            numbers.append(number)
        resolved[name] = replace(
            group, value={**group.value, "pins": replace(members, value=tuple(numbers))}
        )
    return {**metadata, "current_loads": replace(groups, value=resolved)}
