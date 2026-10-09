"""Persistent compiler worker. Private framed messages; stdout is never plugin output."""

from contextlib import redirect_stdout
import pickle
import struct
import sys

from .analysis_cache import AnalysisCache, ParseCache
from .diagnostics import DiagnosticError
from .lsp_analysis import analyze, find_project
from .lsp_completion import completion_items
from .lsp_code_actions import instance_actions
from .lsp_rename import RenameSession
from .project import module_path, read_config

HEADER = struct.Struct("!Q")


class Worker:
    def __init__(self):
        self.parses = ParseCache()
        self.cache = AnalysisCache(self.parses)
        self.key = None

    def execute(self, request):
        sources = request["sources"]
        key = (
            request["generation"],
            tuple(sorted(sources.items())),
            request["explicit"],
            tuple(request["roots"]),
        )
        if key != self.key or not self.cache.valid() or len(self.cache.memo) > 128:
            self.cache = AnalysisCache(self.parses)
            self.key = key
        with self.cache.activate():
            operation = request["operation"]
            if operation in {"diagnostics", "analysis"}:
                groups = {}
                for path in request["paths"]:
                    config = find_project(path, request["roots"], request["explicit"], sources)
                    groups.setdefault(config, []).append(path)
                results = {}
                for config, paths in groups.items():
                    memo_key = ("analysis", config, tuple(paths), operation)
                    if memo_key not in self.cache.memo:
                        if config is not None:
                            try:
                                settings = read_config(config, sources=sources)
                                entry = module_path(
                                    settings, settings.board_module, settings.span, sources
                                )
                            except (DiagnosticError, OSError, UnicodeError):
                                entry = paths[0]
                        else:
                            entry = paths[0]
                        result = analyze(
                            entry,
                            config,
                            sources,
                            run_erc=operation == "diagnostics",
                            extra_paths=paths,
                        )
                        # Failed configuration/discovery may depend on a file or
                        # directory that does not exist yet. Retry it next time;
                        # individual parse failures still use the bounded cache.
                        if not any(d.severity == "error" for d in result.diagnostics):
                            self.cache.memo[memo_key] = result
                    else:
                        result = self.cache.memo[memo_key]
                    results.update((path, result) for path in paths)
                value = results
            else:
                path = request["path"]
                config = find_project(path, request["roots"], request["explicit"], sources)
                if operation == "code_actions":
                    value = instance_actions(path, config, sources, request["offset"])
                elif operation == "completion":
                    value = completion_items(path, config, sources, request["offset"])
                else:
                    if config is None:
                        raise ValueError("Rename requires a NoScheme project.")
                    memo_key = ("rename", config)
                    if memo_key not in self.cache.memo:
                        self.cache.memo[memo_key] = RenameSession(config, sources)
                    session = self.cache.memo[memo_key]
                    value = (
                        session.prepare(path, request["offset"])
                        if operation == "prepare"
                        else session.rename(path, request["offset"], request["new_name"])
                    )
        # Render ranges using exactly the text analyzed, including unopened files.
        texts = {
            p: data if isinstance(data, str) else data.decode("utf-8")
            for p, data in self.cache.disk.items()
            if data is not None and p.suffix in {".nosch", ".toml"}
        }
        return {"value": value, "sources": texts | sources, "valid": self.cache.valid()}


def main():
    incoming, outgoing = sys.stdin.buffer, sys.stdout.buffer
    worker = Worker()
    while header := incoming.read(HEADER.size):
        if len(header) != HEADER.size:
            return
        size = HEADER.unpack(header)[0]
        payload = incoming.read(size)
        if len(payload) != size:
            return
        # Only our parent sends messages on this private pipe.
        request = pickle.loads(payload)
        try:
            with redirect_stdout(sys.stderr):
                response = worker.execute(request)
        except Exception as exc:
            response = {"error": str(exc)}
        payload = pickle.dumps(response, protocol=pickle.HIGHEST_PROTOCOL)
        outgoing.write(HEADER.pack(len(payload)) + payload)
        outgoing.flush()


if __name__ == "__main__":
    main()
