"""Read-only compiler analysis and source references for the language server."""

from dataclasses import dataclass, field, fields, replace
from pathlib import Path

from . import ast as a
from .circuit import elaborate_project
from .diagnostics import Diagnostic, DiagnosticError
from .erc import apply_erc
from .parser import parse
from .project import expression_path, load_project, read_config, source_text
from .resolve import Resolver
from .lsp_bundles import BundleLookup
from .lsp_hover import HoverDetails
from .metadata import rule_call


@dataclass(frozen=True)
class Reference:
    span: a.Span
    target: a.Span
    description: str
    identity: tuple = ()
    declaration: bool = False


@dataclass
class Analysis:
    diagnostics: list = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    files: set[Path] = field(default_factory=set)
    hovers: list = field(default_factory=list)

    def hover_at(self, path, offset):
        for entry in self.hovers:
            if (
                Path(entry.span.filename) == path
                and entry.span.start_pos <= offset < entry.span.end_pos
            ):
                return entry
        return self.reference_at(path, offset)

    def reference_at(self, path, offset):
        matches = [
            r
            for r in self.references
            if Path(r.span.filename) == path and r.span.start_pos <= offset < r.span.end_pos
        ]
        return min(matches, key=lambda r: r.span.end_pos - r.span.start_pos, default=None)

    def references_at(self, path, offset, include_declaration=True):
        selected = self.reference_at(path, offset)
        if selected is None:
            return []
        # A physical pin range is a single source declaration for several pins.
        identities = {r.identity for r in self.references if r.span == selected.span}
        spans = {
            r.span
            for r in self.references
            if r.identity in identities and (include_declaration or not r.declaration)
        }
        return sorted(spans, key=lambda s: (s.filename, s.start_pos, s.end_pos))


def reference_index(project, definitions, hover=None):
    """Index named definitions, lexical names, and explicit interface bindings.

    Use resolver-owned interfaces for inherited pins and aliases. Do not guess
    private members or matches from similarly named definitions in other modules.
    """
    result = []
    bundles = BundleLookup(project, definitions)

    # Grouped declarations share a span; include the name to select one member.
    names = {}
    declaration_spans = {}

    def field_names(node):
        if isinstance(node, a.Field) and not node.key.items and node.key.last is None:
            names[node.span, node.key.first.text] = node.key.first.span
            declaration_spans[node.key.first.span] = node.span
        if isinstance(node, a.Node):
            for field_ in fields(node):
                value = getattr(node, field_.name)
                if isinstance(value, a.Node):
                    field_names(value)
                elif isinstance(value, tuple):
                    for child in value:
                        field_names(child)

    for module in project.modules.values():
        field_names(module.ast)

    def declared_name(span, name):
        return names.get((span, name), span)

    def add(span, target, description, identity=(), *, declaration=False):
        original = declaration_spans.get(target, target)
        identity = identity or (original.filename, original.start_pos, original.end_pos)
        result.append(Reference(span, target, description, identity, declaration))

    def path_span(names):
        return replace(
            names[0].span,
            end_pos=names[-1].span.end_pos,
            end_line=names[-1].span.end_line,
            end_column=names[-1].span.end_column,
        )

    def interface_target(definition, canonical):
        pin = definition.interface[canonical]
        original = definition
        while original.source.base:
            key = bundles.resolver.symbol(original.module, original.source.base, {original.kind})
            original = definitions[key]
        return (
            declared_name(pin.span, pin.name),
            hover.interface(definition, canonical)
            if hover
            else f"{definition.kind} {definition.name}: {canonical}",
            (original.module, original.name, "interface", canonical),
        )

    def symbol(module, name):
        s = project.modules[module].symbols.get(name)
        if s and s.declaration:
            d = s.declaration
            resolved = definitions.get((s.module, s.name))
            return d.name.span, (
                hover.definition(resolved)
                if hover and resolved
                else f"{d.kind} {s.module}.{s.name}"
            )

    globals_ = {
        item.name.text: (
            item.name.span,
            hover.net(item) if hover else f"global net {item.name.text}",
        )
        for item in project.board.declaration.body
        if isinstance(item, a.Net) and item.global_
    }
    for module in project.modules.values():
        for item in module.ast.items:
            if isinstance(item, a.Import):
                for imported in item.names:
                    if target := symbol(module.name, (imported.alias or imported.name).text):
                        add(imported.name.span, *target)
                        if imported.alias:
                            add(imported.alias.span, *target)
                if not item.names:
                    name = ".".join(n.text for n in item.module)
                    if name in project.modules:
                        for part in item.module:
                            add(part.span, project.modules[name].ast.span, f"module {name}")
                continue
            owner = definitions.get((module.name, item.name.text)) if definitions else None
            local = {}
            for node in item.body:
                if isinstance(node, (a.Net, a.Instance)):
                    description = (
                        f"{'net' if isinstance(node, a.Net) else 'instance'} {node.name.text}"
                    )
                    if hover:
                        if isinstance(node, a.Net):
                            description = hover.net(node)
                        else:
                            binding = module.symbols.get(".".join(n.text for n in node.component))
                            target = (
                                definitions.get((binding.module, binding.name)) if binding else None
                            )
                            if target:
                                description = hover.instance(node, target)
                    local[node.name.text] = (node.name.span, description)
                elif isinstance(node, a.Section) and node.name in {"pins", "ports", "params"}:
                    for entry in node.entries:
                        local[entry.key.first.text] = (
                            entry.key.span,
                            f"{node.name}: {entry.key.first.text}",
                        )
            if owner:
                for name, param in owner.parameters.items():
                    local[name] = (
                        declared_name(param.span, param.name),
                        hover.parameter(owner, name)
                        if hover
                        else f"parameter {name}: {param.type}",
                    )
                for alias, canonical in owner.aliases.items():
                    pin = owner.interface[canonical]
                    local[alias] = interface_target(owner, canonical)
            add(
                item.name.span,
                item.name.span,
                hover.definition(owner)
                if hover and owner
                else f"{item.kind} {module.name}.{item.name.text}",
                declaration=True,
            )
            if item.base and (target := symbol(module.name, ".".join(n.text for n in item.base))):
                add(path_span(item.base), *target)

            def expression(expr):
                if hover:
                    hover.builtin(expr, module, local)
                if isinstance(expr, a.Member):
                    if isinstance(expr.base, a.Name) and expr.base.text == "global":
                        if target := globals_.get(expr.name.text):
                            add(expr.name.span, *target)
                    elif (expression_path(expr) or "").split(".")[0] not in local and (
                        target := symbol(module.name, expression_path(expr))
                    ):
                        add(expr.span, *target)
                    else:
                        expression(expr.base)
                        member = bundles.member(
                            bundles.reference_type(expr.base, owner), expr.name.text
                        )
                        if member:
                            add(
                                expr.name.span,
                                declared_name(member.span, member.name),
                                hover.field(member) if hover else f"bundle field {member.name}",
                            )
                elif isinstance(expr, a.Name):
                    if target := local.get(expr.text) or symbol(module.name, expr.text):
                        add(expr.span, *target)
                elif isinstance(expr, a.Node):
                    for f in fields(expr):
                        value = getattr(expr, f.name)
                        if isinstance(value, a.Node):
                            expression(value)
                        elif isinstance(value, tuple):
                            for child in value:
                                expression(child)

            def check_call(call):
                # Field and selector names refer to metadata fields, not names in scope.
                try:
                    _, rule, arguments, missing = rule_call(call, project, module.name, local)
                except DiagnosticError:
                    expression(call)
                    return
                expression(call.callee)
                for name, value in arguments.items():
                    kind = rule.parameters[name]
                    items = value.items if isinstance(value, a.ListExpr) else (value,)
                    if kind == "selector":
                        for item in items:
                            if not isinstance(item, a.Name):
                                expression(item)
                    elif kind not in {"field", "net_fields"}:
                        expression(value)

            def metadata_expressions(entry):
                if entry.key.first.text == "custom":
                    return
                if entry.key.first.text == "checks":
                    for application in entry.fields:
                        if application.value is not None:
                            check_call(application.value)
                    return
                expression(entry.value)
                expression(entry.default)
                for child in entry.fields:
                    metadata_expressions(child)

            def interface_declaration(entry):
                if owner is None:
                    return
                for canonical, key_span in bundles.resolver.key_spans(entry.key):
                    canonical = owner.aliases.get(canonical, canonical)
                    if canonical not in owner.interface:
                        continue
                    target = interface_target(owner, canonical)
                    add(key_span, *target, declaration=not item.base)
                    for alias in entry.key.aliases:
                        add(alias.span, *target, declaration=True)

            for node in item.body:
                if isinstance(node, a.Instance):
                    add(node.name.span, *local[node.name.text], declaration=True)
                    spelling = ".".join(n.text for n in node.component)
                    if target := symbol(module.name, spelling):
                        add(path_span(node.component), *target)
                    sym = module.symbols.get(spelling)
                    target_def = (
                        definitions.get((sym.module, sym.name)) if sym and definitions else None
                    )
                    for net in node.positional:
                        expression(net)
                    sections = (
                        *node.sections,
                        *(
                            s
                            for group in node.sections
                            if group.name == "elements"
                            for element in group.entries
                            for s in element.sections
                        ),
                    )
                    for section in sections:
                        for entry in section.entries:
                            if isinstance(entry, a.Binding):
                                expression(entry.target)
                                for index in (
                                    *entry.endpoint.instance_indices,
                                    *entry.endpoint.selectors,
                                ):
                                    if isinstance(index, a.Index):
                                        expression(index)
                                if target_def:
                                    for name, key_span in bundles.resolver.key_spans(
                                        entry.endpoint.head
                                    ):
                                        canonical = target_def.aliases.get(name)
                                        if canonical:
                                            pin = target_def.interface[canonical]
                                            target_info = interface_target(target_def, canonical)
                                            if hover:
                                                target_info = (
                                                    target_info[0],
                                                    hover.bound_interface(
                                                        node, target_def, canonical
                                                    ),
                                                    target_info[2],
                                                )
                                            add(key_span, *target_info)
                                            value = pin.type
                                            for selector in entry.endpoint.selectors:
                                                if isinstance(selector, a.Name):
                                                    member = bundles.member(value, selector.text)
                                                    if member is None:
                                                        break
                                                    add(
                                                        selector.span,
                                                        declared_name(member.span, member.name),
                                                        hover.field(member)
                                                        if hover
                                                        else f"bundle field {member.name}",
                                                    )
                                                    value = member.type
                            elif isinstance(entry, a.Argument):
                                expression(entry.value)
                                if target_def and entry.name.text in target_def.parameters:
                                    param = target_def.parameters[entry.name.text]
                                    add(
                                        entry.name.span,
                                        declared_name(param.span, param.name),
                                        hover.parameter(target_def, param.name)
                                        if hover
                                        else f"parameter {param.name}: {param.type}",
                                    )
                            elif isinstance(entry, a.Field):
                                if target_def and entry.key.first.text == "pins":
                                    for overlay in entry.fields:
                                        for name, key_span in bundles.resolver.key_spans(
                                            overlay.key
                                        ):
                                            canonical = target_def.aliases.get(name)
                                            if canonical:
                                                add(
                                                    key_span,
                                                    *interface_target(target_def, canonical),
                                                )
                                metadata_expressions(entry)
                    for dim in node.dimensions:
                        expression(dim)
                elif isinstance(node, a.Net):
                    add(node.name.span, *local[node.name.text], declaration=True)
                    expression(node.type)
                    for metadata in node.fields:
                        metadata_expressions(metadata)
                elif isinstance(node, a.Field) and item.kind == "nettype":
                    metadata_expressions(node)
                elif isinstance(node, a.Field) and item.kind == "bundle":
                    if owner and node.key.first.text in owner.interface:
                        member = owner.interface[node.key.first.text]
                        add(
                            node.key.span,
                            declared_name(member.span, member.name),
                            hover.field(member) if hover else f"bundle field {member.name}",
                            declaration=True,
                        )
                    expression(node.value)
                elif isinstance(node, a.Section):
                    for entry in node.entries:
                        if isinstance(entry, a.Field):
                            if node.name in {"pins", "ports"}:
                                interface_declaration(entry)
                            elif (
                                node.name in {"params", "defaults"}
                                and entry.key.first.text in local
                            ):
                                add(
                                    entry.key.span,
                                    *local[entry.key.first.text],
                                    declaration=node.name == "params",
                                )
                            if node.name == "params":
                                expression(entry.default)
                            elif node.name == "defaults":
                                expression(entry.value)
                            elif node.name in {"pins", "ports"}:
                                expression(entry.value)
                                for metadata in entry.fields:
                                    metadata_expressions(metadata)
                            else:
                                metadata_expressions(entry)
    return result


def analyze(path, config, sources, *, run_erc=True, extra_paths=()):
    """Analyze a snapshot without publishing artifacts or changing export state."""
    result = Analysis(files={path})
    project = None
    definitions = {}
    circuit = None
    try:
        # Parse the current file even if it is not reachable from the board.
        text = source_text(path, sources)
        tree = parse(text, str(path.resolve()))
        if config is not None:
            project = load_project(
                config, sources=sources, parsed_sources={path.resolve(): (text, tree)}
            )
            result.files.update(m.path for m in project.modules.values())
            definitions = Resolver(project).resolve_all()
            circuit, warnings = elaborate_project(project, definitions)
            result.diagnostics.extend(warnings)
            if run_erc:
                result.diagnostics.extend(apply_erc(circuit, registry=project.config.erc_registry))
    except DiagnosticError as exc:
        result.diagnostics.extend(exc.diagnostics)
    except (OSError, UnicodeError) as exc:
        result.diagnostics.append(
            Diagnostic("lsp.io", "error", str(exc), a.Span(str(path), 0, 0, 1, 1, 1, 1))
        )
    # Open, unreachable files still receive syntax/structural diagnostics.
    for extra in extra_paths:
        if extra == path or (project and extra in result.files):
            continue
        result.files.add(extra)
        try:
            parse(source_text(extra, sources), str(extra))
        except DiagnosticError as exc:
            result.diagnostics.extend(exc.diagnostics)
        except (OSError, UnicodeError) as exc:
            result.diagnostics.append(
                Diagnostic("lsp.io", "error", str(exc), a.Span(str(extra), 0, 0, 1, 1, 1, 1))
            )
    if project:
        details = HoverDetails(project, definitions, circuit, sources)
        result.references = reference_index(project, definitions, details)
        result.hovers = details.builtins
    return result


def find_project(path, roots, explicit=None, sources=None):
    if explicit:
        return Path(explicit).resolve()
    for parent in path.parents:
        config = parent / "noscheme.toml"
        if config.is_file():
            return config
    # A project's source roots may lie above/beside its TOML (the STM32 example).
    # Workspace discovery is deliberately limited to config files, not imports.
    candidates = []
    import os

    for root in roots:
        for directory, dirs, files_ in os.walk(root):
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".")
                and d not in {"build", "dist", "__pycache__", "node_modules"}
            ]
            if "noscheme.toml" not in files_:
                continue
            config = Path(directory) / "noscheme.toml"
            try:
                settings = read_config(config, sources=sources)
            except (DiagnosticError, OSError, UnicodeError):
                continue
            depths = [len(r.parts) for r in settings.search_paths if path.is_relative_to(r)]
            if depths:
                candidates.append((max(depths), config))
    if not candidates:
        return None
    deepest = max(depth for depth, _ in candidates)
    matches = sorted({p for depth, p in candidates if depth == deepest})
    if len(matches) != 1:
        raise ValueError(
            "Multiple NoScheme projects contain this file; start noscheme-lsp with --project PATH."
        )
    return matches[0]
