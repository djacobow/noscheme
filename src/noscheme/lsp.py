"""NoScheme language server, served over standard LSP stdin/stdout."""

import asyncio
import logging
from pathlib import Path
from urllib.parse import unquote, urlparse

from lsprotocol import types
from pygls.lsp.server import LanguageServer
from pygls.exceptions import JsonRpcInvalidParams, JsonRpcContentModified
from pygls.workspace import TextDocument

from . import ast as a
from .diagnostics import Diagnostic, DiagnosticError
from .formatter import format_source
from .lsp_analysis import Analysis
from .lsp_hover import format_hover
from .lsp_transport import WorkerLane
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
        self.generation = 0
        self.interactive = WorkerLane()
        self.checker = WorkerLane()
        self.analysis_sources = {}

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
        if diagnostic.application:
            message = f"{diagnostic.application}: {message}"
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

    async def request_analysis(self, operation, *, path=None, offset=None, new_name=None):
        generation = self.generation
        request = {
            "operation": operation,
            "generation": generation,
            "sources": self.sources(),
            "roots": self.roots(),
            "explicit": self.project_path,
            "paths": sorted(
                p
                for uri in self.open_uris
                if (p := uri_path(uri)) is not None and p.suffix == ".nosch"
            ),
            "path": path,
            "offset": offset,
            "new_name": new_name,
        }
        lane = self.checker if operation == "diagnostics" else self.interactive
        response = await lane.call(request)
        if generation != self.generation or not response["valid"]:
            if generation == self.generation:
                self.schedule()
            raise JsonRpcContentModified("Source changed during analysis")
        return response["value"], response["sources"]

    async def refresh(self):
        generation = self.generation
        try:
            results, sources = await self.request_analysis("diagnostics")
        except JsonRpcContentModified:
            return
        except Exception as exc:
            LOG.exception("NoScheme analysis failed")
            if generation != self.generation:
                return
            sources = self.sources()
            results = {
                path: Analysis(
                    [
                        Diagnostic(
                            "lsp.analysis", "error", str(exc), a.Span(str(path), 0, 0, 1, 1, 1, 1)
                        )
                    ]
                )
                for path in sources
                if path.suffix == ".nosch"
            }
        grouped = {}
        seen = set()
        self.analysis = {path.as_uri(): result for path, result in results.items()}
        self.analysis_sources = sources
        for result in results.values():
            for d in result.diagnostics:
                target = Path(d.span.filename).as_uri()
                key = (target, d.code, d.span.start_pos, d.message, d.configuration, d.application)
                if key not in seen:
                    seen.add(key)
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
        self.generation += 1
        self.analysis.clear()
        self.analysis_sources = {}
        if self.pending is not None and not self.pending.done():
            return

        async def delayed():
            try:
                while True:
                    generation = self.generation
                    await asyncio.sleep(0.2)
                    if generation != self.generation:
                        continue
                    await self.refresh()
                    if generation == self.generation:
                        return
            finally:
                self.pending = None

        self.pending = asyncio.create_task(delayed())

    async def reference(self, params, *, hover=False):
        uri = params.text_document.uri
        path = uri_path(uri)
        if path is None:
            return None
        # The worker validates disk content even when the editor sends no watcher
        # notification. An unchanged snapshot reuses its semantic index.
        results, sources = await self.request_analysis("analysis")
        self.analysis = {p.as_uri(): result for p, result in results.items()}
        self.analysis_sources = sources
        result = self.analysis.get(uri)
        if result is None:
            return None
        doc = self.workspace.get_text_document(uri)
        lookup = result.hover_at if hover else result.reference_at
        return lookup(path, doc.offset_at_position(params.position))

    async def close_workers(self):
        if self.pending is not None:
            self.pending.cancel()
            await asyncio.gather(self.pending, return_exceptions=True)
        await asyncio.gather(self.interactive.close(), self.checker.close())


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
    async def definition(ls: NoSchemeServer, params: types.DefinitionParams):
        ref = await ls.reference(params)
        if ref:
            return types.Location(
                uri=Path(ref.target.filename).as_uri(),
                range=ls.span_range(ref.target, ls.analysis_sources),
            )
        return None

    @server.feature(types.TEXT_DOCUMENT_REFERENCES)
    async def references(ls: NoSchemeServer, params: types.ReferenceParams):
        # Refresh through the same snapshot path used by definition navigation.
        if await ls.reference(params) is None:
            return []
        path = uri_path(params.text_document.uri)
        doc = ls.workspace.get_text_document(params.text_document.uri)
        result = ls.analysis[params.text_document.uri]
        sources = ls.analysis_sources
        return [
            types.Location(uri=Path(span.filename).as_uri(), range=ls.span_range(span, sources))
            for span in result.references_at(
                path, doc.offset_at_position(params.position), params.context.include_declaration
            )
        ]

    def request_position(ls, params):
        path = uri_path(params.text_document.uri)
        if path is None or params.text_document.uri not in ls.open_uris:
            raise ValueError("Open a NoScheme source file to rename a symbol.")
        offset = ls.workspace.get_text_document(params.text_document.uri).offset_at_position(
            params.position
        )
        return path, offset

    @server.feature(types.TEXT_DOCUMENT_PREPARE_RENAME)
    async def prepare_rename(ls: NoSchemeServer, params: types.PrepareRenameParams):
        try:
            path, offset = request_position(ls, params)
            target, sources = await ls.request_analysis("prepare", path=path, offset=offset)
            return types.PrepareRenamePlaceholder(
                range=ls.span_range(target.span, sources), placeholder=target.name
            )
        except (ValueError, DiagnosticError, OSError, UnicodeError) as exc:
            raise JsonRpcInvalidParams(str(exc)) from exc

    @server.feature(types.TEXT_DOCUMENT_RENAME, types.RenameOptions(prepare_provider=True))
    async def rename(ls: NoSchemeServer, params: types.RenameParams):
        try:
            path, offset = request_position(ls, params)
            edits, sources = await ls.request_analysis(
                "rename", path=path, offset=offset, new_name=params.new_name
            )
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

    @server.feature(
        types.TEXT_DOCUMENT_CODE_ACTION,
        types.CodeActionOptions(code_action_kinds=[types.CodeActionKind.RefactorRewrite]),
    )
    async def code_actions(ls: NoSchemeServer, params: types.CodeActionParams):
        uri = params.text_document.uri
        path = uri_path(uri)
        if path is None or uri not in ls.open_uris:
            return []
        only = params.context.only
        if only and not any(
            k == "refactor.rewrite" or "refactor.rewrite".startswith(k + ".") for k in only
        ):
            return []
        doc = ls.workspace.get_text_document(uri)
        actions, _ = await ls.request_analysis(
            "code_actions", path=path, offset=doc.offset_at_position(params.range.start)
        )

        def edit_position(at):
            # pygls' offset conversion advances past a final line without a
            # newline. Keep EOF insertions on that line, then apply the codec.
            prefix = doc.source[:at]
            return doc.position_to_client_units(
                types.Position(line=prefix.count("\n"), character=len(prefix.rsplit("\n", 1)[-1]))
            )

        result = []
        for action in actions:
            edits = [
                types.TextEdit(
                    range=types.Range(
                        start=edit_position(at),
                        end=edit_position(at),
                    ),
                    new_text=text,
                )
                for at, text in action["edits"]
            ]
            workspace = ls.client_capabilities.workspace
            if workspace and workspace.workspace_edit and workspace.workspace_edit.document_changes:
                edit = types.WorkspaceEdit(
                    document_changes=[
                        types.TextDocumentEdit(
                            text_document=types.OptionalVersionedTextDocumentIdentifier(
                                uri=uri, version=doc.version
                            ),
                            edits=edits,
                        )
                    ]
                )
            else:
                edit = types.WorkspaceEdit(changes={uri: edits})
            result.append(
                types.CodeAction(
                    title=action["title"], kind=types.CodeActionKind.RefactorRewrite, edit=edit
                )
            )
        return result

    @server.feature(types.TEXT_DOCUMENT_FORMATTING)
    async def formatting(ls: NoSchemeServer, params: types.DocumentFormattingParams):
        # The layout is canonical, so the client's tab and spacing options do not apply.
        doc = ls.workspace.get_text_document(params.text_document.uri)
        try:
            result = await asyncio.to_thread(format_source, doc.source, doc.path)
        except DiagnosticError as exc:
            problem = exc.diagnostics[0]
            if problem.code.startswith("syntax."):
                return None  # Already published as a diagnostic.
            raise JsonRpcInvalidParams(f"line {problem.span.line}: {problem.message}") from exc
        if result == doc.source:
            return []
        start = types.Position(line=0, character=0)
        end = doc.client_position_at_offset(len(doc.source))
        return [types.TextEdit(range=types.Range(start=start, end=end), new_text=result)]

    @server.feature(types.TEXT_DOCUMENT_HOVER)
    async def hover(ls: NoSchemeServer, params: types.HoverParams):
        ref = await ls.reference(params, hover=True)
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
                range=ls.span_range(ref.span, ls.analysis_sources),
            )
        return None

    @server.feature(
        types.TEXT_DOCUMENT_COMPLETION,
        types.CompletionOptions(trigger_characters=[".", ":", '"', "<", "(", ","]),
    )
    async def completion(ls: NoSchemeServer, params: types.CompletionParams):
        uri = params.text_document.uri
        path = uri_path(uri)
        if path is None or uri not in ls.open_uris:
            return []
        doc = ls.workspace.get_text_document(uri)
        try:
            suggestions, _ = await ls.request_analysis(
                "completion", path=path, offset=doc.offset_at_position(params.position)
            )
        except ValueError:
            return []
        kinds = {
            "enum": types.CompletionItemKind.EnumMember,
            "value": types.CompletionItemKind.Value,
            "type": types.CompletionItemKind.Class,
            "pin": types.CompletionItemKind.Field,
            "footprint": types.CompletionItemKind.File,
            "variable": types.CompletionItemKind.Variable,
            "function": types.CompletionItemKind.Function,
            "parameter": types.CompletionItemKind.Variable,
            "property": types.CompletionItemKind.Property,
            "field": types.CompletionItemKind.Field,
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

    @server.feature(types.SHUTDOWN)
    async def shutdown(ls: NoSchemeServer, *args):
        await ls.close_workers()

    return server


def main(argv=None):
    from .lsp_launcher import main as launch

    return launch(argv)


if __name__ == "__main__":
    main()
