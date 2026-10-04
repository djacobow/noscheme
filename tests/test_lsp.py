"""Exercise the real stdio protocol and compiler-backed unsaved-buffer analysis."""

import json
from pathlib import Path
import queue
import subprocess
import sys
import threading

import pytest

from noscheme.lsp_analysis import analyze, find_project
from noscheme.project import load_project
from test_circuit import make

SOURCE = """from noscheme import role, nettype
component Receiver:
    pins:
        signal:
            role: role.digital_input
board B:
    net bus: nettype.tristate
    inst receivers[2]: Receiver(
        connections:
            signal: bus
    )
"""


@pytest.fixture
def project(tmp_path):
    make(tmp_path, "")
    (tmp_path / "main.nosch").write_text(SOURCE)
    return tmp_path


class Client:
    def __init__(self, project):
        self.process = subprocess.Popen(
            [sys.executable, "-m", "noscheme.lsp", "--project", str(project)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.messages = queue.Queue()
        self.identifier = 0
        self.saved = []
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        try:
            while True:
                header = self.process.stdout.readline()
                if not header:
                    return
                size = int(header.decode().split(":")[1])
                while self.process.stdout.readline() != b"\r\n":
                    pass
                self.messages.put(json.loads(self.process.stdout.read(size)))
        except Exception as exc:
            self.messages.put(exc)

    def send(self, method, params=None, identifier=None):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if identifier is not None:
            message["id"] = identifier
        data = json.dumps(message).encode()
        self.process.stdin.write(f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
        self.process.stdin.flush()

    def wait(self, predicate):
        while True:
            message = self.messages.get(timeout=10)
            assert isinstance(message, dict), message
            self.saved.append(message)
            if predicate(message):
                return message

    def request(self, method, params=None):
        self.identifier += 1
        self.send(method, params, self.identifier)
        response = self.wait(lambda m: m.get("id") == self.identifier)
        assert "error" not in response, response
        return response["result"]

    def diagnostics(self, uri, version):
        return self.wait(
            lambda m: m.get("method") == "textDocument/publishDiagnostics"
            and m["params"].get("uri") == uri
            and m["params"].get("version") == version
        )["params"]["diagnostics"]

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.communicate(timeout=5)


@pytest.fixture
def client(project):
    c = Client(project)
    try:
        capabilities = c.request(
            "initialize", {"processId": None, "rootUri": project.as_uri(), "capabilities": {}}
        )["capabilities"]
        assert capabilities["definitionProvider"]
        assert capabilities["hoverProvider"]
        c.send("initialized", {})
        yield c
    finally:
        c.close()


def open_document(client, path, text, version=1):
    client.send(
        "textDocument/didOpen",
        {
            "textDocument": {
                "uri": path.as_uri(),
                "languageId": "noscheme",
                "version": version,
                "text": text,
            }
        },
    )


def test_protocol_diagnostics_navigation_and_unsaved_edits(client, project):
    path = project / "main.nosch"
    open_document(client, path, SOURCE)
    assert not any(d["severity"] == 1 for d in client.diagnostics(path.as_uri(), 1))
    params = {"textDocument": {"uri": path.as_uri()}, "position": {"line": 7, "character": 25}}
    location = client.request("textDocument/definition", params)
    assert location["uri"] == path.as_uri()
    assert location["range"]["start"]["line"] == 1
    assert "Receiver" in client.request("textDocument/hover", params)["contents"]["value"]
    broken = SOURCE.replace("nettype.tristate", "nettype.digital")
    client.send(
        "textDocument/didChange",
        {
            "textDocument": {"uri": path.as_uri(), "version": 2},
            "contentChanges": [{"text": broken}],
        },
    )
    assert any(d["code"] == "erc.missing_driver" for d in client.diagnostics(path.as_uri(), 2))
    assert path.read_text() == SOURCE
    client.send(
        "textDocument/didChange",
        {
            "textDocument": {"uri": path.as_uri(), "version": 3},
            "contentChanges": [{"text": SOURCE}],
        },
    )
    assert not any(d["severity"] == 1 for d in client.diagnostics(path.as_uri(), 3))
    assert not (project / "build").exists()
    assert not (project / "noscheme.state.json").exists()
    assert client.request("shutdown") is None
    client.send("exit")
    assert client.process.wait(timeout=5) == 0


def test_syntax_error_clears_old_navigation_and_recovers(client, project):
    path = project / "main.nosch"
    open_document(client, path, "board B:\n    inst ???\n")
    assert any(d["severity"] == 1 for d in client.diagnostics(path.as_uri(), 1))
    assert (
        client.request(
            "textDocument/definition",
            {"textDocument": {"uri": path.as_uri()}, "position": {"line": 1, "character": 9}},
        )
        is None
    )
    client.send(
        "textDocument/didChange",
        {
            "textDocument": {"uri": path.as_uri(), "version": 2},
            "contentChanges": [{"text": SOURCE}],
        },
    )
    diagnostics = client.diagnostics(path.as_uri(), 2)
    assert not any(d["severity"] == 1 for d in diagnostics), diagnostics
    client.send("textDocument/didClose", {"textDocument": {"uri": path.as_uri()}})
    assert client.diagnostics(path.as_uri(), None) == []


def test_unsaved_import_and_new_file_use_source_overlay(project):
    path = project / "main.nosch"
    library = project / "parts.nosch"
    source = (
        SOURCE[: SOURCE.index("component Receiver:")]
        + "from parts import Receiver\n"
        + SOURCE[SOURCE.index("board B:") :]
    )
    lib = (
        "from noscheme import role\n"
        + SOURCE[SOURCE.index("component Receiver:") : SOURCE.index("board B:")]
    )
    buffers = {path: source, library: lib}
    loaded = load_project(project, sources=buffers)
    assert "parts" in loaded.modules
    result = analyze(path, project, buffers)
    assert not any(d.severity == "error" for d in result.diagnostics)
    ref = result.reference_at(path, source.index("Receiver("))
    assert Path(ref.target.filename) == library
    assert not library.exists()


def test_project_discovery_handles_sibling_configuration(project):
    path = project / "main.nosch"
    assert find_project(path, []) == project / "noscheme.toml"
    config = project / "noscheme.toml"
    text = config.read_text().replace('source_paths = ["."]', 'source_paths = [".."]')
    config.unlink()
    child = project / "config"
    child.mkdir()
    (child / "noscheme.toml").write_text(text)
    assert find_project(path, [project]) == child / "noscheme.toml"
    other = project / "other"
    other.mkdir()
    (other / "noscheme.toml").write_text(text)
    with pytest.raises(ValueError, match="Multiple"):
        find_project(path, [project])


def test_utf16_diagnostics_and_incremental_edits(client, project):
    path = project / "main.nosch"
    text = SOURCE.replace(
        "component Receiver:\n", 'component Receiver:\n    params:\n        value: string = ""\n'
    ).replace("    )\n", '        params:\n            value: "😀{0}".format(missing)\n    )\n')
    line_number, line = next(
        (i, line) for i, line in enumerate(text.splitlines()) if "missing" in line
    )
    column = len(line[: line.index("missing")].encode("utf-16-le")) // 2
    open_document(client, path, text)
    diagnostic = next(d for d in client.diagnostics(path.as_uri(), 1) if d["severity"] == 1)
    assert diagnostic["range"]["start"] == {"line": line_number, "character": column}
    client.send(
        "textDocument/didChange",
        {
            "textDocument": {"uri": path.as_uri(), "version": 2},
            "contentChanges": [
                {
                    "range": {
                        "start": {"line": line_number, "character": column},
                        "end": {"line": line_number, "character": column + len("missing")},
                    },
                    "text": '"ok"',
                }
            ],
        },
    )
    diagnostics = client.diagnostics(path.as_uri(), 2)
    assert not any(d["severity"] == 1 for d in diagnostics), diagnostics


def test_lexical_net_and_pin_alias_navigation(project):
    path = project / "main.nosch"
    text = SOURCE.replace(
        "            role:", "            aliases: [input]\n            role:"
    ).replace("signal: bus", "input: bus")
    result = analyze(path, project, {path: text})
    pin = result.reference_at(path, text.index("input: bus"))
    net = result.reference_at(path, text.index("input: bus") + len("input: "))
    assert pin.target.line == 4
    assert net.target.start_pos == text.index("bus: nettype")


def test_standalone_file_gets_syntax_diagnostics(tmp_path):
    path = tmp_path / "draft.nosch"
    result = analyze(path, None, {path: "board Broken:\n    inst ???\n"})
    assert any(d.severity == "error" for d in result.diagnostics)


def test_open_dependency_edits_recheck_board_and_close_restores_disk(client, project):
    path = project / "main.nosch"
    library = project / "parts.nosch"
    source = (
        "from noscheme import nettype\nfrom parts import Receiver\n"
        + SOURCE[SOURCE.index("board B:") :]
    )
    lib = (
        "from noscheme import role\n"
        + SOURCE[SOURCE.index("component Receiver:") : SOURCE.index("board B:")]
    )
    path.write_text(source)
    library.write_text(lib)
    open_document(client, path, source)
    assert not any(d["severity"] == 1 for d in client.diagnostics(path.as_uri(), 1))
    open_document(client, library, lib.replace("digital_input", "digital_output"))
    diagnostics = client.diagnostics(path.as_uri(), 1)
    assert any(d["code"] == "erc.tristate_capability" for d in diagnostics), diagnostics
    client.send("textDocument/didClose", {"textDocument": {"uri": library.as_uri()}})
    diagnostics = client.diagnostics(path.as_uri(), 1)
    assert not any(d["severity"] == 1 for d in diagnostics), diagnostics
    assert library.read_text() == lib


@pytest.mark.parametrize(
    "old,new,label",
    [
        ("Receiver(", "Rec", "Receiver"),
        ("signal: bus", "si", "signal"),
        ("role.digital_input", "role.digital_", "digital_input"),
    ],
)
def test_completion_on_incomplete_buffers(client, project, old, new, label):
    path = project / "main.nosch"
    text = SOURCE.replace(old, new)
    offset = text.rindex(new) + len(new)
    line = text[:offset].count("\n")
    character = len(text[:offset].rsplit("\n", 1)[-1])
    open_document(client, path, text)
    result = client.request(
        "textDocument/completion",
        {
            "textDocument": {"uri": path.as_uri()},
            "position": {"line": line, "character": character},
        },
    )
    item = next(i for i in result["items"] if i["label"] == label)
    assert item["textEdit"]["newText"] == label
    assert item["textEdit"]["range"]["end"] == {"line": line, "character": character}
    assert path.read_text() == SOURCE
