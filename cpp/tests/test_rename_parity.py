"""Run the Python rename behavior suite through the native LSP protocol."""

from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from noscheme.parser import Builder
from noscheme.project import load_project, source_text
from test_lsp import Client, SERVER, at_path, message, opening

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "tests"))
import test_lsp_rename as cases  # noqa: E402

pytestmark = pytest.mark.skipif(not SERVER, reason="native server supplied by Meson")
for name, value in vars(cases).items():
    if name.startswith("test_") and callable(value):
        globals()[name] = value


@pytest.fixture(autouse=True)
def native_sessions(monkeypatch):
    with ExitStack() as stack:

        class Session:
            def __init__(self, config, sources):
                self.project = load_project(config, sources=sources)
                self.sources = {
                    m.path: source_text(m.path, sources) for m in self.project.modules.values()
                }
                self.client = stack.enter_context(Client())
                self.next_id = 1
                self.request("initialize", {})
                for path, text in self.sources.items():
                    self.client.send(at_path(opening(text), path))

            def request(self, method, params):
                request_id = self.next_id
                self.next_id += 1
                self.client.send(message(method, params, id=request_id))
                result = self.client.until(lambda r: r.get("id") == request_id)
                if "error" in result:
                    raise ValueError(result["error"]["message"])
                return result["result"]

            def params(self, path, offset):
                before = self.sources[path][:offset]
                return {
                    "textDocument": {"uri": path.as_uri()},
                    "position": {
                        "line": before.count("\n"),
                        "character": len(before.rsplit("\n", 1)[-1].encode("utf-16-le")) // 2,
                    },
                }

            def span(self, path, range_):
                text = self.sources[path]
                lines = text.splitlines(keepends=True)

                def offset(where):
                    before = "".join(lines[: where["line"]])
                    line = lines[where["line"]] if where["line"] < len(lines) else ""
                    return len(before) + len(
                        line.encode("utf-16-le")[: where["character"] * 2].decode("utf-16-le")
                    )

                return Builder(text, str(path)).position_span(
                    offset(range_["start"]), offset(range_["end"])
                )

            def prepare(self, path, offset):
                result = self.request("textDocument/prepareRename", self.params(path, offset))
                return SimpleNamespace(
                    name=result["placeholder"], span=self.span(path, result["range"])
                )

            def rename(self, path, offset, name):
                result = self.request(
                    "textDocument/rename", {**self.params(path, offset), "newName": name}
                )
                paths = {p.as_uri(): p for p in self.sources}
                return [
                    (self.span(paths[uri], edit["range"]), edit["newText"])
                    for uri, edits in result["changes"].items()
                    for edit in edits
                ]

        monkeypatch.setattr(cases, "RenameSession", Session)
        yield
