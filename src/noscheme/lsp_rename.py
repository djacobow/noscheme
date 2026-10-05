"""Plan source-only renames using resolved references and import bindings."""

from dataclasses import dataclass, replace
from pathlib import Path
import re

from . import ast as a
from .diagnostics import DiagnosticError
from .lsp_analysis import Analysis, reference_index
from .project import IDENTIFIER, load_project, source_text
from .parser import RESERVED_NAMES
from .lsp_identity import identity_edit, waiver_edits, physical_signature
from .circuit import elaborate_project
from .resolve import Resolver


def span_key(span):
    return (span.filename, span.start_pos, span.end_pos)


@dataclass(frozen=True)
class RenameTarget:
    span: a.Span
    name: str
    occurrences: tuple[a.Span, ...]
    instance: a.Instance | None = None
    owner: a.Definition | None = None


class RenameSession:
    def __init__(self, config, sources):
        self.project = load_project(config, sources=sources)
        self.definitions = Resolver(self.project).resolve_all()
        self.sources = dict(sources)
        for module in self.project.modules.values():
            self.sources[module.path] = source_text(module.path, sources)
        self.index = Analysis(references=reference_index(self.project, self.definitions))
        self.modules_by_path = {str(m.path): m for m in self.project.modules.values()}
        self.binding_cache = {}

    def binding_origin(self, module_name, spelling):
        """An explicit alias owns its spelling; an unaliased import forwards it."""
        key = (module_name, spelling)
        if key in self.binding_cache:
            return self.binding_cache[key]
        module = self.project.modules.get(module_name)
        if module is None:
            return key
        for item in module.ast.items:
            if isinstance(item, a.Definition) and item.name.text == spelling:
                return key
            if not isinstance(item, a.Import):
                continue
            imported_module = ".".join(n.text for n in item.module)
            if not item.names and spelling.startswith(imported_module + "."):
                origin = self.binding_origin(imported_module, spelling[len(imported_module) + 1 :])
                self.binding_cache[key] = origin
                return origin
            for imported in item.names:
                if (imported.alias or imported.name).text == spelling:
                    origin = (
                        key
                        if imported.alias
                        else self.binding_origin(imported_module, imported.name.text)
                    )
                    self.binding_cache[key] = origin
                    return origin
        return key

    def occurrence_origin(self, ref):
        module = self.modules_by_path[ref.span.filename]
        for item in module.ast.items:
            if not isinstance(item, a.Import):
                continue
            for imported in item.names:
                if imported.name.span == ref.span:
                    return self.binding_origin(
                        ".".join(n.text for n in item.module), imported.name.text
                    )
                if imported.alias and imported.alias.span == ref.span:
                    return (module.name, imported.alias.text)
        spelling = self.sources[module.path][ref.span.start_pos : ref.span.end_pos]
        return self.binding_origin(module.name, spelling)

    def prepare(self, path, offset):
        ref = self.index.reference_at(path, offset)
        if ref is None:
            raise ValueError("No renameable symbol at this position.")
        allowed, named = set(), set()
        instances = {}
        for module in self.project.modules.values():
            for item in module.ast.items:
                if not isinstance(item, a.Definition):
                    continue
                if item.kind in {"component", "block", "bundle"}:
                    named.add(span_key(item.name.span))
                for node in item.body:
                    if isinstance(node, a.Instance) and item.kind == "board":
                        key = span_key(node.name.span)
                        allowed.add(key)
                        instances[key] = (item, node)
                    elif isinstance(node, a.Net):
                        allowed.add(span_key(node.name.span))
                    elif isinstance(node, a.Field) and item.kind == "bundle":
                        allowed.add(span_key(node.span))
                    elif isinstance(node, a.Section) and node.name == "params":
                        allowed.update(span_key(entry.span) for entry in node.entries)
        if len(ref.identity) == 4 and ref.identity[2] == "interface":
            owner = self.definitions[ref.identity[:2]]
            if owner.kind != "block":
                raise ValueError(
                    "Physical pin IDs and pin aliases cannot be renamed; they map to footprint pads."
                )
            allowed.add(ref.identity)
        if ref.identity not in allowed | named:
            raise ValueError(
                "Rename supports nets, parameters, components, blocks, bundles, ports, bundle fields, and board-level instances; board names, internal instances, and module names are not supported."
            )
        occurrences = [r for r in self.index.references if r.identity == ref.identity]
        if ref.identity in named:
            origin = self.occurrence_origin(ref)
            occurrences = [r for r in occurrences if self.occurrence_origin(r) == origin]
        spans = set()
        selected = None
        for occurrence in occurrences:
            span = occurrence.span
            spelling = self.sources[Path(span.filename)][span.start_pos : span.end_pos]
            if not re.fullmatch(rf"{IDENTIFIER}(?:\.{IDENTIFIER})*", spelling):
                raise ValueError("This source occurrence cannot be renamed safely.")
            # Preserve module qualifiers. Renaming the module itself is separate.
            prefix = spelling.rfind(".") + 1
            span = replace(span, start_pos=span.start_pos + prefix, column=span.column + prefix)
            spans.add(span)
            if occurrence.span == ref.span:
                selected = span
        if selected is None or not selected.start_pos <= offset < selected.end_pos:
            raise ValueError("Place the cursor on the symbol name, not its module qualifier.")
        name = self.sources[path][selected.start_pos : selected.end_pos]
        owner, instance = instances.get(ref.identity, (None, None))
        return RenameTarget(selected, name, tuple(sorted(spans, key=span_key)), instance, owner)

    def rename(self, path, offset, new_name):
        target = self.prepare(path, offset)
        if not re.fullmatch(IDENTIFIER, new_name) or new_name in RESERVED_NAMES:
            raise ValueError("The new name must be a NoScheme identifier, not a reserved value.")
        if new_name == target.name:
            return []
        edits = [(span, new_name) for span in target.occurrences]
        if target.instance:
            insertion = identity_edit(
                target.instance, self.sources[Path(target.instance.span.filename)]
            )
            if insertion:
                edits.append(insertion)
            edits.extend(waiver_edits(self.project, target.owner, target.name, new_name))
        edited = dict(self.sources)
        for span, text in sorted(edits, key=lambda edit: span_key(edit[0]), reverse=True):
            file = Path(span.filename)
            source = edited[file]
            edited[file] = source[: span.start_pos] + text + source[span.end_pos :]
        # Validate the proposed workspace without publishing or changing export state.
        try:
            project = load_project(self.project.config.path, sources=edited)
            definitions = Resolver(project).resolve_all()
            if target.instance:
                before, _ = elaborate_project(self.project, self.definitions)
                after, _ = elaborate_project(project, definitions)
                if physical_signature(before) != physical_signature(after):
                    raise ValueError(
                        "Rename would change physical identities, parts, or connectivity."
                    )
        except DiagnosticError as exc:
            raise ValueError(f"Rename rejected: {exc.diagnostics[0].message}") from exc
        return edits
