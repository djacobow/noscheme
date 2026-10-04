"""NoScheme language server, served over standard LSP stdin/stdout."""

import argparse
import asyncio
import logging
import sys
from contextlib import redirect_stdout
from pathlib import Path
from urllib.parse import unquote, urlparse

from lsprotocol import types
from pygls.lsp.server import LanguageServer
from pygls.exceptions import JsonRpcInvalidParams
from pygls.workspace import TextDocument

from . import ast as a
from .diagnostics import Diagnostic, DiagnosticError
from .lsp_analysis import Analysis, analyze, find_project
from .lsp_completion import completion_items
from .lsp_hover import format_hover
from .lsp_rename import RenameSession
from .project import source_text

LOG = logging.getLogger(__name__)


def uri_path(uri):
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        return None
    return Path(unquote(parsed.path)).resolve()


class NoSchemeServer(LanguageServer):
    def __init__(self, project=None):
        super().__init__("noscheme", "0.1.0")
        self.project_path = project
        self.open_uris = set()
        self.analysis = {}
        self.published = set()
        self.pending = None

    def sources(self):
        return {
            uri_path(uri): self.workspace.get_text_document(uri).source
            for uri in self.open_uris
            if uri_path(uri) is not None
        }

    def roots(self):
        roots = [uri_path(uri) for uri in self.workspace.folders]
        if self.workspace.root_uri:
            roots.append(uri_path(self.workspace.root_uri))
        return [root for root in roots if root is not None]

    def document(self, path, sources):
        # Use the negotiated encoding for unopened dependency files too.
        codec = self.workspace.get_text_document(path.as_uri()).position_codec
        try:
            source = source_text(path, sources)
        except (OSError, UnicodeError):
            source = ""
        return TextDocument(path.as_uri(), source, position_codec=codec)

    def span_range(self, span, sources):
        doc = self.document(Path(span.filename), sources)
        return types.Range(
            start=doc.client_position_at_offset(span.start_pos),
            end=doc.client_position_at_offset(span.end_pos),
        )

    def diagnostic(self, diagnostic, sources):
        message = diagnostic.message
        if diagnostic.configuration:
            message = f"[{diagnostic.configuration}] {message}"
        return types.Diagnostic(
            range=self.span_range(diagnostic.span, sources),
            message=message,
            severity=types.DiagnosticSeverity.Error
            if diagnostic.severity == "error"
            else types.DiagnosticSeverity.Warning,
            code=diagnostic.code,
            source="noscheme",
            related_information=[
                types.DiagnosticRelatedInformation(
                    location=types.Location(
                        uri=Path(r.span.filename).as_uri(), range=self.span_range(r.span, sources)
                    ),
                    message=r.message,
                )
                for r in diagnostic.related
            ]
            or None,
        )

    def refresh(self):
        sources = self.sources()
        grouped = {}
        self.analysis = {}
        # Full checks are serialized. The debounce avoids compiling each keystroke;
        # published versions identify the exact open-buffer snapshot checked.
        cache = {}
        for uri in sorted(self.open_uris):
            path = uri_path(uri)
            if path is None or path.suffix != ".nosch":
                continue
            try:
                config = find_project(path, self.roots(), self.project_path, sources)
                # Analyze each current file, including unreachable/new modules.
                # Project-local ERC extensions may print; reserve stdout for LSP.
                with redirect_stdout(sys.stderr):
                    result = analyze(path, config, sources)
            except Exception as exc:
                LOG.exception("NoScheme analysis failed")
                span = a.Span(str(path), 0, 0, 1, 1, 1, 1)
                result = Analysis([Diagnostic("lsp.analysis", "error", str(exc), span)])
            self.analysis[uri] = result
            for d in result.diagnostics:
                target = Path(d.span.filename).as_uri()
                key = (target, d.code, d.span.start_pos, d.message, d.configuration)
                if key not in cache:
                    cache[key] = True
                    grouped.setdefault(target, []).append(self.diagnostic(d, sources))
        for uri in self.published | set(grouped) | self.open_uris:
            version = (
                self.workspace.get_text_document(uri).version if uri in self.open_uris else None
            )
            self.text_document_publish_diagnostics(
                types.PublishDiagnosticsParams(
                    uri=uri,
                    diagnostics=grouped.get(uri, []),
                    version=version,
                )
            )
        self.published = set(grouped)

    def schedule(self):
        self.analysis.clear()  # Never navigate using spans from a previous buffer.
        if self.pending:
            self.pending.cancel()

        async def delayed():
            await asyncio.sleep(0.2)
            self.refresh()

        self.pending = asyncio.create_task(delayed())

    def reference(self, params, *, hover=False):
        uri = params.text_document.uri
        path = uri_path(uri)
        if path is None:
            return None
        if uri not in self.analysis:
            if self.pending:
                self.pending.cancel()
            self.refresh()
        result = self.analysis.get(uri)
        if result is None:
            return None
        doc = self.workspace.get_text_document(uri)
        lookup = result.hover_at if hover else result.reference_at
        return lookup(path, doc.offset_at_position(params.position))


def create_server(project=None):
    server = NoSchemeServer(project)

    @server.feature(types.TEXT_DOCUMENT_DID_OPEN)
    def did_open(ls: NoSchemeServer, params: types.DidOpenTextDocumentParams):
        ls.open_uris.add(params.text_document.uri)
        ls.schedule()

    @server.feature(types.TEXT_DOCUMENT_DID_CHANGE)
    def did_change(ls: NoSchemeServer, params: types.DidChangeTextDocumentParams):
        ls.schedule()

    @server.feature(types.TEXT_DOCUMENT_DID_SAVE)
    def did_save(ls: NoSchemeServer, params: types.DidSaveTextDocumentParams):
        ls.schedule()

    @server.feature(types.TEXT_DOCUMENT_DID_CLOSE)
    def did_close(ls: NoSchemeServer, params: types.DidCloseTextDocumentParams):
        ls.open_uris.discard(params.text_document.uri)
        ls.published.add(params.text_document.uri)
        ls.schedule()

    @server.feature(types.WORKSPACE_DID_CHANGE_WATCHED_FILES)
    def watched_files(ls: NoSchemeServer, params: types.DidChangeWatchedFilesParams):
        ls.schedule()

    @server.feature(types.TEXT_DOCUMENT_DEFINITION)
    def definition(ls: NoSchemeServer, params: types.DefinitionParams):
        ref = ls.reference(params)
        if ref:
            return types.Location(
                uri=Path(ref.target.filename).as_uri(),
                range=ls.span_range(ref.target, ls.sources()),
            )
        return None

    @server.feature(types.TEXT_DOCUMENT_REFERENCES)
    def references(ls: NoSchemeServer, params: types.ReferenceParams):
        # Refresh through the same snapshot path used by definition navigation.
        if ls.reference(params) is None:
            return []
        path = uri_path(params.text_document.uri)
        doc = ls.workspace.get_text_document(params.text_document.uri)
        result = ls.analysis[params.text_document.uri]
        sources = ls.sources()
        return [
            types.Location(uri=Path(span.filename).as_uri(), range=ls.span_range(span, sources))
            for span in result.references_at(
                path, doc.offset_at_position(params.position), params.context.include_declaration
            )
        ]

    def rename_context(ls, params):
        path = uri_path(params.text_document.uri)
        if path is None or params.text_document.uri not in ls.open_uris:
            raise ValueError("Open a NoScheme source file to rename a symbol.")
        sources = ls.sources()
        config = find_project(path, ls.roots(), ls.project_path, sources)
        if config is None:
            raise ValueError("Rename requires a NoScheme project.")
        session = RenameSession(config, sources)
        offset = ls.workspace.get_text_document(params.text_document.uri).offset_at_position(
            params.position
        )
        return session, path, offset, sources

    @server.feature(types.TEXT_DOCUMENT_PREPARE_RENAME)
    def prepare_rename(ls: NoSchemeServer, params: types.PrepareRenameParams):
        try:
            with redirect_stdout(sys.stderr):
                session, path, offset, sources = rename_context(ls, params)
                target = session.prepare(path, offset)
            return types.PrepareRenamePlaceholder(
                range=ls.span_range(target.span, sources), placeholder=target.name
            )
        except (ValueError, DiagnosticError, OSError, UnicodeError) as exc:
            raise JsonRpcInvalidParams(str(exc)) from exc

    @server.feature(types.TEXT_DOCUMENT_RENAME, types.RenameOptions(prepare_provider=True))
    def rename(ls: NoSchemeServer, params: types.RenameParams):
        try:
            with redirect_stdout(sys.stderr):
                session, path, offset, sources = rename_context(ls, params)
                edits = session.rename(path, offset, params.new_name)
            grouped = {}
            for span, text in edits:
                uri = Path(span.filename).as_uri()
                grouped.setdefault(uri, []).append(
                    types.TextEdit(range=ls.span_range(span, sources), new_text=text)
                )
            workspace = ls.client_capabilities.workspace
            if workspace and workspace.workspace_edit and workspace.workspace_edit.document_changes:
                return types.WorkspaceEdit(
                    document_changes=[
                        types.TextDocumentEdit(
                            text_document=types.OptionalVersionedTextDocumentIdentifier(
                                uri=uri,
                                version=ls.workspace.get_text_document(uri).version
                                if uri in ls.open_uris
                                else None,
                            ),
                            edits=items,
                        )
                        for uri, items in sorted(grouped.items())
                    ]
                )
            return types.WorkspaceEdit(changes=grouped)
        except (ValueError, DiagnosticError, OSError, UnicodeError) as exc:
            raise JsonRpcInvalidParams(str(exc)) from exc

    @server.feature(types.TEXT_DOCUMENT_HOVER)
    def hover(ls: NoSchemeServer, params: types.HoverParams):
        ref = ls.reference(params, hover=True)
        if ref:
            text_document = ls.client_capabilities.text_document
            capabilities = text_document.hover if text_document else None
            formats = capabilities.content_format if capabilities else None
            kind = formats[0] if formats else types.MarkupKind.PlainText
            return types.Hover(
                contents=types.MarkupContent(
                    kind=kind,
                    value=format_hover(ref.description, kind == types.MarkupKind.Markdown),
                ),
                range=ls.span_range(ref.span, ls.sources()),
            )
        return None

    @server.feature(
        types.TEXT_DOCUMENT_COMPLETION, types.CompletionOptions(trigger_characters=[".", ":", '"'])
    )
    def completion(ls: NoSchemeServer, params: types.CompletionParams):
        uri = params.text_document.uri
        path = uri_path(uri)
        if path is None or uri not in ls.open_uris:
            return []
        sources = ls.sources()
        doc = ls.workspace.get_text_document(uri)
        try:
            config = find_project(path, ls.roots(), ls.project_path, sources)
            with redirect_stdout(sys.stderr):
                suggestions = completion_items(
                    path, config, sources, doc.offset_at_position(params.position)
                )
        except ValueError:
            return []
        kinds = {
            "enum": types.CompletionItemKind.EnumMember,
            "type": types.CompletionItemKind.Class,
            "pin": types.CompletionItemKind.Field,
            "footprint": types.CompletionItemKind.File,
        }
        return types.CompletionList(
            is_incomplete=False,
            items=[
                types.CompletionItem(
                    label=s.label,
                    detail=s.detail,
                    kind=kinds[s.kind],
                    sort_text=f"{index:06d}",
                    text_edit=types.TextEdit(
                        range=types.Range(
                            start=doc.client_position_at_offset(s.start),
                            end=doc.client_position_at_offset(s.end),
                        ),
                        new_text=s.text,
                    ),
                )
                for index, s in enumerate(suggestions)
            ],
        )

    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, help="Explicit noscheme.toml or project directory")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)  # Logs must never enter protocol stdout.
    create_server(args.project).start_io()


if __name__ == "__main__":
    main()
