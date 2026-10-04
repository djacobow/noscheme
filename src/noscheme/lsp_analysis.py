"""Read-only compiler analysis and source references for the language server."""

from dataclasses import dataclass, field, fields
from pathlib import Path

from . import ast as a
from .circuit import elaborate_project
from .diagnostics import Diagnostic, DiagnosticError
from .erc import apply_erc
from .parser import parse
from .project import expression_path, load_project, read_config, source_text
from .resolve import Resolver


@dataclass(frozen=True)
class Reference:
    span: a.Span
    target: a.Span
    description: str


@dataclass
class Analysis:
    diagnostics: list = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    files: set[Path] = field(default_factory=set)

    def reference_at(self, path, offset):
        matches = [
            r
            for r in self.references
            if Path(r.span.filename) == path and r.span.start_pos <= offset < r.span.end_pos
        ]
        return min(matches, key=lambda r: r.span.end_pos - r.span.start_pos, default=None)


def reference_index(project, definitions):
    """Index named definitions, lexical names, and explicit interface bindings.

    Use resolver-owned interfaces for inherited pins and aliases. Do not guess
    private members or matches from similarly named definitions in other modules.
    """
    result = []

    def add(span, target, description):
        result.append(Reference(span, target, description))

    def symbol(module, name):
        s = project.modules[module].symbols.get(name)
        if s and s.declaration:
            d = s.declaration
            return d.name.span, f"{d.kind} {s.module}.{s.name}"

    globals_ = {
        item.name.text: (item.name.span, f"global net {item.name.text}")
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
                    local[node.name.text] = (
                        node.name.span,
                        f"{'net' if isinstance(node, a.Net) else 'inst'} {node.name.text}",
                    )
                elif isinstance(node, a.Section) and node.name in {"pins", "ports", "params"}:
                    for entry in node.entries:
                        local[entry.key.first.text] = (
                            entry.key.span,
                            f"{node.name}: {entry.key.first.text}",
                        )
            if owner:
                for name, param in owner.parameters.items():
                    local[name] = (param.span, f"parameter {name}: {param.type}")
                for alias, canonical in owner.aliases.items():
                    pin = owner.interface[canonical]
                    local[alias] = (pin.span, f"{owner.kind} {owner.name}: {canonical}")
            add(item.name.span, item.name.span, f"{item.kind} {module.name}.{item.name.text}")
            if item.base and (target := symbol(module.name, ".".join(n.text for n in item.base))):
                for name in item.base:
                    add(name.span, *target)

            def expression(expr):
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

            for node in item.body:
                if isinstance(node, a.Instance):
                    spelling = ".".join(n.text for n in node.component)
                    if target := symbol(module.name, spelling):
                        for name in node.component:
                            add(name.span, *target)
                    sym = module.symbols.get(spelling)
                    target_def = (
                        definitions.get((sym.module, sym.name)) if sym and definitions else None
                    )
                    for section in node.sections:
                        for entry in section.entries:
                            if isinstance(entry, a.Binding):
                                expression(entry.target)
                                if target_def:
                                    canonical = target_def.aliases.get(
                                        entry.endpoint.head.first.text
                                    )
                                    if canonical:
                                        pin = target_def.interface[canonical]
                                        add(
                                            entry.endpoint.head.span,
                                            pin.span,
                                            f"{target_def.name}: {canonical}",
                                        )
                            elif isinstance(entry, a.Argument):
                                expression(entry.value)
                                if target_def and entry.name.text in target_def.parameters:
                                    param = target_def.parameters[entry.name.text]
                                    add(
                                        entry.name.span,
                                        param.span,
                                        f"parameter {param.name}: {param.type}",
                                    )
                    for dim in node.dimensions:
                        expression(dim)
                elif isinstance(node, a.Net):
                    add(node.name.span, *local[node.name.text])
                    expression(node.type)
                elif isinstance(node, a.Section):
                    for entry in node.entries:
                        if isinstance(entry, a.Field):
                            expression(entry.value)
                            expression(entry.default)
    return result


def analyze(path, config, sources):
    """Analyze a snapshot without publishing artifacts or changing export state."""
    result = Analysis(files={path})
    project = None
    definitions = {}
    try:
        # Parse the current file even if it is not reachable from the board.
        parse(source_text(path, sources), str(path))
        if config is None:
            return result
        project = load_project(config, sources=sources)
        result.files.update(m.path for m in project.modules.values())
        definitions = Resolver(project).resolve_all()
        circuit, warnings = elaborate_project(project, definitions)
        result.diagnostics.extend(warnings)
        result.diagnostics.extend(apply_erc(circuit))
    except DiagnosticError as exc:
        result.diagnostics.extend(exc.diagnostics)
    except (OSError, UnicodeError) as exc:
        result.diagnostics.append(
            Diagnostic("lsp.io", "error", str(exc), a.Span(str(path), 0, 0, 1, 1, 1, 1))
        )
    if project:
        result.references = reference_index(project, definitions)
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
