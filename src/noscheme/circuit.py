"""Expand resolved declarations into a CAD-independent circuit.

Union-find joins public ports to caller nets. It does not infer connections,
assign CAD names or reference numbers, or perform electrical rules checking.
"""

from dataclasses import dataclass, field, replace

from . import ast as a
from .diagnostics import Diagnostic, Related
from .evaluate import Evaluator, Limits, encoded
from .metadata import (
    Value,
    NumericExpression,
    merge,
    read_metadata,
    possible_selections,
    valid_quantity,
)
from .resolve import Resolver
from .instances import arguments, element_overrides


def path_text(path):
    text = ""
    for segment in path:
        text += f"[{segment}]" if isinstance(segment, int) else ("." if text else "") + segment
    return text


@dataclass
class Node:
    kind: str
    leaf: int | None = None
    type: str | None = None
    items: tuple = ()
    fields: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ExpansionLimits:
    instances: int = 10000
    leaves: int = 100000
    hierarchy_depth: int = 128
    type_depth: int = 128


class Elaborator:
    def __init__(self, project, definitions, limits=None, expression_limits=None):
        self.project, self.definitions = project, definitions
        self.limits = limits or ExpansionLimits()
        self.evaluator = Evaluator(project, expression_limits or Limits())
        self.resolver = Resolver(project)
        self.resolver.definitions = definitions
        self.parents, self.leaves = [], []
        self.sizes, self.supports = [], []
        self.bound = {}
        self.parts, self.blocks = [], []
        self.choice_scopes = []
        self.globals = {}
        self.instance_count = 0
        self.identities = {}
        self.references = {}

    def error(self, code, message, span, related=()):
        self.evaluator.error(code, message, span, related)

    def root(self, leaf):
        while self.parents[leaf] != leaf:
            self.parents[leaf] = self.parents[self.parents[leaf]]
            leaf = self.parents[leaf]
        return leaf

    def union(self, child, caller):
        first, second = self.root(child), self.root(caller)
        if first == second:
            return
        if self.sizes[first] > self.sizes[second]:
            first, second = second, first
        self.parents[first] = second
        self.sizes[second] += self.sizes[first]
        self.supports[second].update(self.supports[first])
        self.supports[first].clear()

    def new_leaf(self, path, scope, kind, type_name, metadata, span):
        if len(self.leaves) >= self.limits.leaves:
            self.error("circuit.limit", "expanded net/pin count exceeds configured limit", span)
        number = len(self.leaves)
        self.parents.append(number)
        self.sizes.append(1)
        has_support = (
            kind == "pin"
            or metadata.get("placeholder", Value(False, span, "")).value
            or "external_role" in metadata
        )
        self.supports.append({scope} if has_support else set())
        self.leaves.append(
            {
                "path": path,
                "scope": scope,
                "kind": kind,
                "type": type_name,
                "metadata": metadata,
                "span": span,
            }
        )
        return Node("scalar", number, type_name)

    def node_leaves(self, node):
        if node.kind == "scalar":
            yield node.leaf
        elif node.kind == "array":
            for child in node.items:
                yield from self.node_leaves(child)
        else:
            for child in node.fields.values():
                yield from self.node_leaves(child)

    def dimensions(self, selections, module, environment):
        return tuple(
            self.evaluator.dimension(self.evaluator.evaluate(s.start, module, environment), s.span)
            for s in selections
        )

    def array(self, dimensions, build, span, prefix=()):
        if len(dimensions) + len(prefix) > self.limits.hierarchy_depth:
            self.error("circuit.limit", "array rank exceeds configured nesting limit", span)
        count = 1
        for size in dimensions:
            count *= size
            if count > self.limits.leaves:
                self.error("circuit.limit", "array exceeds configured expansion limit", span)
        if not dimensions:
            return build(prefix)
        return Node(
            "array",
            items=tuple(
                self.array(dimensions[1:], build, span, prefix + (i,)) for i in range(dimensions[0])
            ),
        )

    def typed(self, value, environment, path, scope, kind, metadata, span, depth=0):
        if depth > self.limits.type_depth or len(path) > self.limits.hierarchy_depth:
            self.error("circuit.limit", "bundle nesting exceeds configured limit", span)
        metadata = self.numeric_metadata(metadata, environment)
        expr, selections = value.value, []
        while isinstance(expr, a.Subscript):
            selections.append(expr.selection)
            expr = expr.base
        dimensions = self.dimensions(reversed(selections), value.module, environment)
        if len(path) + len(dimensions) > self.limits.hierarchy_depth:
            self.error("circuit.limit", "net path exceeds configured nesting limit", span)
        base = self.resolver.type_definition(Value(expr, expr.span, value.module))

        def build(indices):
            here = path + indices
            if base is None:
                return self.new_leaf(here, scope, kind, expr.name.text, metadata, span)
            fields = {}
            for name, port in sorted(base.interface.items()):
                fields[name] = self.typed(
                    port.type,
                    {},
                    here + (name,),
                    scope,
                    kind,
                    merge(metadata, port.metadata),
                    port.span,
                    depth + 1,
                )
            return Node("bundle", type=f"{base.module}.{base.name}", fields=fields)

        return self.array(dimensions, build, span)

    def field(self, node, name, span):
        if node.kind == "array":
            return Node("array", items=tuple(self.field(n, name, span) for n in node.items))
        if name not in node.fields:
            self.error("connection.field", f"unknown public field {name!r}", span)
        return node.fields[name]

    def select(self, node, selectors, module, environment):
        for selection in selectors:
            if isinstance(selection, a.Name):
                node = self.field(node, selection.text, selection.span)
                continue
            if node.kind != "array":
                self.error(
                    "array.scalar-index", "cannot index a scalar endpoint or net", selection.span
                )
            start = self.evaluator.index(
                self.evaluator.evaluate(selection.start, module, environment),
                selection.span,
                len(node.items),
            )
            if selection.stop is None:
                node = node.items[start]
            else:
                stop = self.evaluator.index(
                    self.evaluator.evaluate(selection.stop, module, environment),
                    selection.span,
                    len(node.items),
                    stop=True,
                )
                if stop <= start:
                    self.error(
                        "array.slice", "slices must be nonempty and ascending", selection.span
                    )
                node = Node("array", items=node.items[start:stop])
        return node

    def endpoint(self, instances, endpoint, target, module, environment):
        selected = self.select(instances, endpoint.instance_indices, module, environment)
        names = self.resolver.keys(endpoint.head)

        def ports(node):
            if node.kind == "array":
                return Node("array", items=tuple(ports(n) for n in node.items))
            values = tuple(node.fields[target.aliases[name]] for name in names)
            value = Node("array", items=values) if endpoint.head.grouped else values[0]
            return self.select(value, endpoint.selectors, module, environment)

        return ports(selected)

    def reference(self, expr, nets, module, environment):
        selectors = []
        while isinstance(expr, (a.Member, a.Subscript)):
            selectors.append(expr.name if isinstance(expr, a.Member) else expr.selection)
            expr = expr.base
        selectors.reverse()
        if expr.text == "no_connect" and not selectors:
            return None
        if expr.text == "global":
            name = selectors.pop(0)
            if name.text not in self.globals:
                self.error(
                    "connection.global",
                    f"global net {name.text!r} is not declared by the board",
                    name.span,
                )
            node = self.globals[name.text]
        else:
            if expr.text not in nets:
                self.error("connection.net", f"undeclared net {expr.text!r}", expr.span)
            node = nets[expr.text]
        return self.select(node, selectors, module, environment)

    def shape(self, node):
        result = []
        while node.kind == "array":
            result.append(len(node.items))
            node = node.items[0]
        return tuple(result)

    def connect(self, left, right, span):
        left_shape, right_shape = self.shape(left), self.shape(right) if right else ()
        if right and right_shape and left_shape != right_shape:
            self.error(
                "connection.shape",
                f"endpoint shape {left_shape} does not match net shape {right_shape}",
                span,
            )

        def bind(lhs, rhs):
            if lhs.kind == "array":
                if rhs and rhs.kind == "array":
                    for x, y in zip(lhs.items, rhs.items):
                        bind(x, y)
                else:
                    for x in lhs.items:
                        bind(x, rhs)
            elif lhs.kind == "bundle":
                if rhs and (rhs.kind != "bundle" or lhs.type != rhs.type):
                    self.error(
                        "connection.bundle-type",
                        "whole bundles must have the same named type",
                        span,
                    )
                for name in sorted(lhs.fields):
                    self.connect(lhs.fields[name], rhs.fields[name] if rhs else None, span)
            else:
                if rhs and rhs.kind != "scalar":
                    self.error(
                        "connection.shape",
                        "a physical pin or scalar port requires a scalar net",
                        span,
                    )
                if lhs.leaf in self.bound:
                    self.error(
                        "connection.overlap",
                        "endpoint is bound more than once",
                        span,
                        [Related("previous binding", self.bound[lhs.leaf]["span"])],
                    )
                self.bound[lhs.leaf] = {"state": "connected" if rhs else "no_connect", "span": span}
                if rhs:
                    self.union(lhs.leaf, rhs.leaf)

        bind(left, right)

    def numeric_metadata(self, values, environment, overrides=None, caller=None):
        result = {}
        overrides = overrides or {}
        for key, record in merge(values, overrides).items():
            value = record.value
            if (
                key in overrides
                and key in values
                and isinstance(value, dict)
                and isinstance(values[key].value, dict)
                and isinstance(overrides[key].value, dict)
            ):
                value = self.numeric_metadata(
                    values[key].value, environment, overrides[key].value, caller
                )
                result[key] = replace(record, value=value)
                continue
            scope = caller if key in overrides else environment
            if isinstance(value, NumericExpression):
                number = self.evaluator.numeric(
                    self.evaluator.evaluate(value.expression, record.module, scope),
                    record.span,
                )
                if not valid_quantity(number, value.constraint):
                    self.error(
                        "metadata.value",
                        f"{key!r} requires {value.constraint} metadata",
                        record.span,
                    )
                value = number
            elif isinstance(value, dict):
                value = self.numeric_metadata(value, scope)
            result[key] = replace(record, value=value)
        return result

    def effective_metadata(self, definition, node, owner_module, caller, environment, element=None):
        overrides, annotations = {}, {}
        for source in (node, element):
            fields = (
                next((s.entries for s in source.sections if s.name == "metadata"), ())
                if source
                else ()
            )
            values = read_metadata(
                tuple(f for f in fields if f.key.first.text != "pins"),
                "instance",
                self.project,
                owner_module,
                caller,
            )
            overrides = merge(overrides, values)
            for group in fields:
                if group.key.first.text == "pins":
                    for f in group.fields:
                        values = read_metadata(
                            f.fields, "instance_pin", self.project, owner_module, caller
                        )
                        for key in self.resolver.keys(f.key):
                            pin = definition.aliases[key]
                            annotations[pin] = merge(annotations.get(pin, {}), values)
        metadata = self.numeric_metadata(definition.metadata, environment, overrides, caller)
        return metadata, overrides, annotations

    def expand(
        self,
        key,
        path,
        node,
        caller_module,
        caller,
        ancestry=(),
        ancestor_identity=None,
        caller_scope=(),
        element=None,
    ):
        definition = self.definitions[key]
        self.evaluator.instance_path = path_text(path) or definition.name
        span = node.span if node else definition.source.span
        if key in ancestry:
            self.error("instance.cycle", "recursive block instantiation", span)
        if (
            len(ancestry) > self.limits.hierarchy_depth
            or self.instance_count >= self.limits.instances
        ):
            self.error("circuit.limit", "instance expansion exceeds configured limit", span)
        self.instance_count += 1
        args = arguments(node, element)
        environment = self.evaluator.bind(definition, args, caller_module, caller, span)
        metadata, overrides, annotations = self.effective_metadata(
            definition, node, caller_module, caller, environment, element
        )
        if definition.kind != "component":
            self.choice_scopes.append(
                {
                    "scope": list(path),
                    "parent_scope": list(caller_scope),
                    "choices": a.to_data(
                        definition.metadata.get("erc_choices", Value({}, span, caller_module)).value
                    ),
                    "bindings": a.to_data(
                        overrides.get("erc_choices", Value({}, span, caller_module)).value
                    ),
                }
            )
        ports = {}
        for name, port in sorted(definition.interface.items()):
            attrs = self.numeric_metadata(
                port.metadata, environment, annotations.get(name, {}), caller
            )
            selections = {}
            for role_key in ("role", "external_role"):
                value = attrs.get(role_key)
                if value and isinstance(value.value, dict):
                    selections[role_key] = Value(
                        {
                            **value.value,
                            "scope": Value(tuple(caller_scope), value.span, value.module),
                        },
                        value.span,
                        value.module,
                    )
                    attrs[role_key] = Value(
                        tuple(sorted(possible_selections(value))), value.span, value.module
                    )
            if selections:
                attrs["_erc_selections"] = Value(selections, span, caller_module)
            if definition.kind == "component":
                ports[name] = self.new_leaf(path + (name,), path, "pin", None, attrs, port.span)
            else:
                ports[name] = self.typed(
                    port.type, environment, path + (name,), path, "port", attrs, port.span
                )
        identity = metadata.get("identity")
        if identity:
            identity = identity.value
            if not identity:
                self.error("identity.empty", "explicit identity must not be empty", span)
            if node and node.dimensions:
                identity += path_text(path[-len(node.dimensions) :])
        elif ancestor_identity:
            ancestor_path, ancestor_name = ancestor_identity
            identity = ancestor_name + "." + path_text(path[len(ancestor_path) :])
        else:
            identity = path_text(path)
        if path:
            if identity in self.identities:
                self.error(
                    "identity.collision",
                    f"duplicate identity {identity!r}",
                    span,
                    [Related("previous instance", self.identities[identity])],
                )
            self.identities[identity] = span
        origin = {"declaration": a.to_data(span), "definition": a.to_data(definition.source.span)}
        if definition.kind == "component":
            if "ref" in metadata:
                reference = metadata["ref"].value
                if not reference or reference in self.references:
                    self.error(
                        "reference.collision",
                        f"empty or duplicate explicit reference {reference!r}",
                        span,
                    )
                self.references[reference] = span
            value = None
            if "value" in metadata:
                v = metadata["value"]
                value = self.evaluator.evaluate(
                    v.value, v.module, caller if "value" in overrides else environment
                )
            elif "value" in environment:
                value = environment["value"]
            record = {
                "path": path,
                "identity": identity,
                "definition": ".".join(key),
                "parameters": {k: encoded(v) for k, v in sorted(environment.items())},
                "value": encoded(value) if value is not None else None,
                "metadata": metadata,
                "ports": ports,
                "source": origin,
            }
            self.parts.append(record)
            if "footprint" not in metadata or not metadata["footprint"].value:
                self.evaluator.warnings.append(
                    Diagnostic(
                        "footprint.unspecified",
                        "warning",
                        "physical instance has no assigned footprint",
                        span,
                        instance_path=path_text(path),
                    )
                )
            return Node("instance", fields=ports)
        nets = dict(ports)
        for declaration in sorted(
            (n for n in definition.body if isinstance(n, a.Net)), key=lambda n: n.name.text
        ):
            attrs = read_metadata(
                declaration.fields, "net", self.project, definition.body_module, environment
            )
            net = self.typed(
                Value(declaration.type, declaration.type.span, definition.body_module),
                environment,
                path + (declaration.name.text,),
                path,
                "net",
                attrs,
                declaration.span,
            )
            nets[declaration.name.text] = net
            if declaration.global_:
                self.globals[declaration.name.text] = net
        children = []
        inherited_identity = (path, identity) if metadata.get("identity") else ancestor_identity
        for declaration in sorted(
            (n for n in definition.body if isinstance(n, a.Instance)), key=lambda n: n.name.text
        ):
            target_key = self.resolver.symbol(
                definition.body_module, declaration.component, {"component", "block"}
            )
            target = self.definitions[target_key]
            self.evaluator.instance_path = path_text(path + (declaration.name.text,))
            dimensions = self.dimensions(
                declaration.dimensions, definition.body_module, environment
            )
            if len(path) + len(dimensions) + 1 > self.limits.hierarchy_depth:
                self.error(
                    "circuit.limit",
                    "instance path exceeds configured nesting limit",
                    declaration.span,
                )
            elements = element_overrides(declaration, dimensions, self.evaluator)
            instances = self.array(
                dimensions,
                lambda indices: self.expand(
                    target_key,
                    path + (declaration.name.text,) + indices,
                    declaration,
                    definition.body_module,
                    environment,
                    ancestry + (key,),
                    inherited_identity,
                    path,
                    elements.get(indices),
                ),
                declaration.span,
            )
            children.append((declaration, target, instances))
        for declaration, target, instances in children:
            self.evaluator.instance_path = path_text(path + (declaration.name.text,))
            for section in declaration.sections:
                if section.name == "connections":
                    for binding in section.entries:
                        left = self.endpoint(
                            instances, binding.endpoint, target, definition.body_module, environment
                        )
                        right = self.reference(
                            binding.target, nets, definition.body_module, environment
                        )
                        self.connect(left, right, binding.span)
        # Check before caller bindings can make an empty port appear implemented.
        self.evaluator.instance_path = path_text(path) or definition.name
        for name, port in ports.items():
            for leaf in self.node_leaves(port):
                root = self.root(leaf)
                if not any(scope[: len(path)] == path for scope in self.supports[root]):
                    self.error(
                        "port.unimplemented",
                        f"port {name!r} has no internal physical endpoint, external endpoint, or placeholder",
                        definition.interface[name].span,
                    )
        self.blocks.append(
            {
                "path": list(path),
                "definition": ".".join(key),
                "identity": identity,
                "ports": ports,
                "source": origin,
            }
        )
        return Node("instance", fields=ports)

    def finish(self):
        board = self.definitions[(self.project.board.module, self.project.board.name)]
        groups = {}
        for i, leaf in enumerate(self.leaves):
            groups.setdefault(self.root(i), []).append(i)
        net_for_leaf, nets = {}, []
        for ids in groups.values():
            declared = [i for i in ids if self.leaves[i]["kind"] != "pin"]
            if not declared:
                continue
            # The caller's declaration has the shortest hierarchy, independent of union order.
            chosen = min(
                declared,
                key=lambda i: (
                    len(self.leaves[i]["scope"]),
                    self.leaves[i]["kind"] != "net",
                    path_text(self.leaves[i]["path"]),
                ),
            )
            source_name = path_text(self.leaves[chosen]["path"])
            for i in ids:
                net_for_leaf[i] = source_name
            nets.append(
                {
                    "name": source_name,
                    "path": list(self.leaves[chosen]["path"]),
                    "declarations": [
                        {
                            "path": list(self.leaves[i]["path"]),
                            "scope": list(self.leaves[i]["scope"]),
                            "kind": self.leaves[i]["kind"],
                            "type": self.leaves[i]["type"],
                            "metadata": a.to_data(self.leaves[i]["metadata"]),
                            "span": a.to_data(self.leaves[i]["span"]),
                            "binding": self.bound.get(i, {}).get("state", "omitted"),
                            "binding_span": a.to_data(self.bound[i]["span"])
                            if i in self.bound
                            else None,
                        }
                        for i in sorted(declared, key=lambda j: path_text(self.leaves[j]["path"]))
                    ],
                    "endpoints": [],
                }
            )
        by_name = {n["name"]: n for n in nets}
        parts = []
        for part in sorted(self.parts, key=lambda p: path_text(p["path"])):
            pins = {}
            for pin, port in sorted(part["ports"].items()):
                leaf = port.leaf
                binding = self.bound.get(leaf)
                net = net_for_leaf.get(leaf)
                pins[pin] = {
                    "net": net,
                    "binding": binding["state"] if binding else "omitted",
                    "binding_span": a.to_data(binding["span"]) if binding else None,
                    "span": a.to_data(self.leaves[leaf]["span"]),
                    "metadata": a.to_data(self.leaves[leaf]["metadata"]),
                }
                if net:
                    by_name[net]["endpoints"].append({"instance": list(part["path"]), "pin": pin})
            record = {k: v for k, v in part.items() if k not in {"ports", "metadata", "path"}}
            record.update(path=list(part["path"]), metadata=a.to_data(part["metadata"]), pins=pins)
            parts.append(record)
        blocks = []
        for block in sorted(self.blocks, key=lambda b: path_text(b["path"])):
            ports = {}
            for name, port in sorted(block["ports"].items()):
                ports[name] = [
                    {
                        "path": list(self.leaves[i]["path"]),
                        "net": net_for_leaf.get(i),
                        "binding": self.bound.get(i, {}).get("state", "omitted"),
                    }
                    for i in self.node_leaves(port)
                ]
            blocks.append({**block, "ports": ports})
        return {
            "schema_version": 1,
            "stage": "circuit",
            "erc_performed": False,
            "erc_modules": [str(p) for p in self.project.config.erc_modules],
            "erc_rule_sets": list(self.project.config.erc_rule_sets),
            "board": f"{self.project.board.module}.{self.project.board.name}",
            "source": a.to_data(board.source.span),
            "erc_choice_scopes": self.choice_scopes,
            "erc_configurations": a.to_data(
                board.metadata.get(
                    "configurations", Value({}, board.source.span, board.module)
                ).value
            ),
            "parts": parts,
            "nets": sorted(nets, key=lambda n: n["name"]),
            "blocks": blocks,
            "warnings": [a.to_data(w) for w in self.evaluator.warnings],
        }

    def elaborate(self):
        key = (self.project.board.module, self.project.board.name)
        self.expand(key, (), None, key[0], {})
        return self.finish(), tuple(self.evaluator.warnings)


def elaborate_project(project, definitions, **kwargs):
    return Elaborator(project, definitions, **kwargs).elaborate()
