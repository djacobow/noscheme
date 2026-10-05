"""Exercise the real stdio protocol and compiler-backed unsaved-buffer analysis."""

import json
from pathlib import Path
import queue
import socket
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
        # asyncio uses a socket pair to wake the loop after pygls' stdin worker
        # finishes. Some sandboxes allow creating the pair but forbid send().
        # Detect that restriction before waiting for an impossible response.
        try:
            sender, receiver = socket.socketpair()
            with sender, receiver:
                sender.send(b"x")
        except PermissionError as exc:
            pytest.fail(
                "LSP protocol tests require local socket-pair communication for asyncio; "
                f"the sandbox denied it ({exc}). Run these tests outside this sandbox.",
                pytrace=False,
            )
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


def test_footprint_completion_protocol(client, project):
    path = project / "main.nosch"
    text = """component Part:
    metadata:
        footprint: "Test:Z"
        custom:
            kicad_footprint_candidates: ["Test:A"]
board B:
    inst p: Part(
        metadata:
            footprint: "Test:"
    )
"""
    open_document(client, path, text)
    result = client.request(
        "textDocument/completion",
        {
            "textDocument": {"uri": path.as_uri()},
            "position": {"line": 8, "character": 29},
        },
    )
    assert [i["label"] for i in result["items"]] == ["Test:Z", "Test:A"]
    item = result["items"][0]
    assert item["textEdit"]["newText"] == '"Test:Z"'
    assert item["textEdit"]["range"] == {
        "start": {"line": 8, "character": 23},
        "end": {"line": 8, "character": 30},
    }
    assert item["sortText"] < result["items"][1]["sortText"]


def test_qualified_import_protocol_completion_and_definition(client, project):
    library = project / "Device.nosch"
    library.write_text('component R:\n    metadata:\n        footprint: "Test:R"\n')
    path = project / "main.nosch"
    text = "import Device\nfrom Device import R as Resistor\nboard B:\n    inst r: Device.R()\n"
    open_document(client, path, text)
    result = client.request(
        "textDocument/definition",
        {
            "textDocument": {"uri": path.as_uri()},
            "position": {"line": 3, "character": 19},
        },
    )
    assert result["uri"] == library.as_uri()
    result = client.request(
        "textDocument/completion",
        {
            "textDocument": {"uri": path.as_uri()},
            "position": {"line": 3, "character": 19},
        },
    )
    item = next(i for i in result["items"] if i["label"] == "Device.R")
    assert item["textEdit"]["newText"] == "Device.R"
    assert item["textEdit"]["range"] == {
        "start": {"line": 3, "character": 12},
        "end": {"line": 3, "character": 20},
    }


def test_extends_completion_without_colon_or_body(client, project):
    (project / "Device.nosch").write_text(
        'component R:\n    metadata:\n        ref_prefix: "R"\n'
        "block Circuit:\n    metadata:\n        custom:\n            note: true\n"
    )
    path = project / "main.nosch"
    text = "import Device\ncomponent Resistor extends Device.\nboard B:\n    inst r: Device.R()\n"
    open_document(client, path, text)
    result = client.request(
        "textDocument/completion",
        {
            "textDocument": {"uri": path.as_uri()},
            "position": {"line": 1, "character": len(text.splitlines()[1])},
        },
    )
    assert [item["label"] for item in result["items"]] == ["Device.R"]
    item = result["items"][0]
    assert item["textEdit"]["newText"] == "Device.R"
    assert item["textEdit"]["range"] == {
        "start": {"line": 1, "character": 27},
        "end": {"line": 1, "character": 34},
    }


def test_bundle_member_protocol_completion_navigation_and_hover(client, project):
    library = project / "interfaces.nosch"
    library.write_text(
        "from noscheme import nettype\nbundle Bus:\n    sck: nettype.passive\n"
        "block Port:\n    ports:\n        bus: Bus\n"
    )
    path = project / "main.nosch"
    text = (
        "import interfaces\nboard B:\n    net wire: interfaces.Bus\n"
        "    inst p: interfaces.Port(\n        connections:\n"
        "            bus.sck: wire.sck\n    )\n"
    )
    open_document(client, path, text)
    for column in [16, 26]:
        result = client.request(
            "textDocument/definition",
            {
                "textDocument": {"uri": path.as_uri()},
                "position": {"line": 5, "character": column},
            },
        )
        assert result["uri"] == library.as_uri()
        assert result["range"]["start"]["line"] == 2
        hover = client.request(
            "textDocument/hover",
            {
                "textDocument": {"uri": path.as_uri()},
                "position": {"line": 5, "character": column},
            },
        )
        assert "bundle field sck" in hover["contents"]["value"]
    incomplete = text.replace("wire.sck", "wire.")
    client.send(
        "textDocument/didChange",
        {
            "textDocument": {"uri": path.as_uri(), "version": 2},
            "contentChanges": [{"text": incomplete}],
        },
    )
    result = client.request(
        "textDocument/completion",
        {
            "textDocument": {"uri": path.as_uri()},
            "position": {"line": 5, "character": 26},
        },
    )
    assert [item["label"] for item in result["items"]] == ["sck"]
    assert result["items"][0]["textEdit"] == {
        "newText": "sck",
        "range": {"start": {"line": 5, "character": 26}, "end": {"line": 5, "character": 26}},
    }


def test_find_references_protocol_cross_file_and_unsaved_buffers(client, project):
    from test_lsp_references import LIBRARY, SOURCE as REFERENCE_SOURCE

    library = project / "library.nosch"
    library.write_text(LIBRARY)
    path = project / "main.nosch"
    open_document(client, path, REFERENCE_SOURCE)
    offset = REFERENCE_SOURCE.index("library.Unit(") + len("library.")
    prefix = REFERENCE_SOURCE[:offset]
    params = {
        "textDocument": {"uri": path.as_uri()},
        "position": {"line": prefix.count("\n"), "character": len(prefix.rsplit("\n", 1)[-1])},
        "context": {"includeDeclaration": False},
    }
    uses = client.request("textDocument/references", params)
    assert len(uses) == 5
    assert {use["uri"] for use in uses} == {path.as_uri(), library.as_uri()}
    params["context"]["includeDeclaration"] = True
    assert len(client.request("textDocument/references", params)) == 6
    changed = REFERENCE_SOURCE.replace("inst b: Alias(", "inst b: library.Derived(")
    client.send(
        "textDocument/didChange",
        {
            "textDocument": {"uri": path.as_uri(), "version": 2},
            "contentChanges": [{"text": changed}],
        },
    )
    params["context"]["includeDeclaration"] = False
    assert len(client.request("textDocument/references", params)) == 4
    assert path.read_text() == SOURCE


def test_reference_locations_use_negotiated_utf16(client, project):
    path = project / "main.nosch"
    text = """component Part:
    params:
        n: number = 1
    metadata:
        value: "😀{0}".format(n)
board B:
    inst p: Part()
"""
    open_document(client, path, text)
    result = client.request(
        "textDocument/references",
        {
            "textDocument": {"uri": path.as_uri()},
            "position": {"line": 2, "character": 8},
            "context": {"includeDeclaration": False},
        },
    )
    column = len(text.splitlines()[4].split("(n)")[0].encode("utf-16-le")) // 2 + 1
    assert result == [
        {
            "uri": path.as_uri(),
            "range": {
                "start": {"line": 4, "character": column},
                "end": {"line": 4, "character": column + 1},
            },
        }
    ]


def test_rename_protocol_prepare_edits_and_collision(client, project):
    from test_lsp_references import LIBRARY, SOURCE as REFERENCE_SOURCE

    library = project / "library.nosch"
    library.write_text(LIBRARY)
    path = project / "main.nosch"
    open_document(client, path, REFERENCE_SOURCE)
    params = {
        "textDocument": {"uri": path.as_uri()},
        "position": {"line": 12, "character": 13},  # Alias in the instance type.
    }
    # Locate the position from the fixture to keep the protocol check legible.
    prefix = REFERENCE_SOURCE[: REFERENCE_SOURCE.index("Alias(")]
    params["position"] = {"line": prefix.count("\n"), "character": len(prefix.rsplit("\n", 1)[-1])}
    prepared = client.request("textDocument/prepareRename", params)
    assert prepared["placeholder"] == "Alias"
    result = client.request("textDocument/rename", {**params, "newName": "Renamed"})
    assert set(result["changes"]) == {path.as_uri()}
    assert len(result["changes"][path.as_uri()]) == 2
    assert all(e["newText"] == "Renamed" for e in result["changes"][path.as_uri()])
    assert path.read_text() == SOURCE
    assert library.read_text() == LIBRARY
    client.identifier += 1
    client.send("textDocument/rename", {**params, "newName": "library"}, client.identifier)
    response = client.wait(lambda m: m.get("id") == client.identifier)
    assert response["error"]["code"] == -32602
    assert "already declared or imported" in response["error"]["message"]


def test_rename_versioned_edits_and_utf16(project):
    c = Client(project)
    try:
        capabilities = c.request(
            "initialize",
            {
                "processId": None,
                "rootUri": project.as_uri(),
                "capabilities": {
                    "workspace": {"workspaceEdit": {"documentChanges": True}},
                    "textDocument": {"rename": {"prepareSupport": True}},
                },
            },
        )["capabilities"]
        assert capabilities["renameProvider"]["prepareProvider"]
        c.send("initialized", {})
        path = project / "main.nosch"
        text = """component Part:
    params:
        count: number = 1
    metadata:
        value: "😀{0}".format(count)
board B:
    inst p: Part()
"""
        open_document(c, path, text, version=7)
        result = c.request(
            "textDocument/rename",
            {
                "textDocument": {"uri": path.as_uri()},
                "position": {"line": 2, "character": 8},
                "newName": "size",
            },
        )
        document = result["documentChanges"][0]
        assert document["textDocument"] == {"uri": path.as_uri(), "version": 7}
        assert len(document["edits"]) == 2
        use = next(e for e in document["edits"] if e["range"]["start"]["line"] == 4)
        column = len(text.splitlines()[4].split("(count)")[0].encode("utf-16-le")) // 2 + 1
        assert use["range"]["start"]["character"] == column
        assert path.read_text() == SOURCE
    finally:
        c.close()


def test_rich_hover_protocol_uses_instance_overrides(client, project):
    from test_lsp_hover import LIBRARY, SOURCE as HOVER_SOURCE

    (project / "parts.nosch").write_text(LIBRARY)
    path = project / "main.nosch"
    open_document(client, path, HOVER_SOURCE)
    offset = HOVER_SOURCE.index("resistors[")
    prefix = HOVER_SOURCE[:offset]
    params = {
        "textDocument": {"uri": path.as_uri()},
        "position": {"line": prefix.count("\n"), "character": len(prefix.rsplit("\n", 1)[-1])},
    }
    result = client.request("textDocument/hover", params)
    assert result["contents"]["kind"] == "plaintext"
    assert "Value: 10kOhm" in result["contents"]["value"]
    assert "Footprint: Test:0805" in result["contents"]["value"]
    assert result["range"]["start"] == params["position"]
    changed = HOVER_SOURCE.replace("10kOhm", "22kOhm")
    client.send(
        "textDocument/didChange",
        {
            "textDocument": {"uri": path.as_uri(), "version": 2},
            "contentChanges": [{"text": changed}],
        },
    )
    updated = client.request("textDocument/hover", params)["contents"]["value"]
    assert "Value: 22kOhm" in updated and "Value: 10kOhm" not in updated
    assert path.read_text() == SOURCE


@pytest.mark.parametrize("formats", [["markdown", "plaintext"], ["plaintext", "markdown"]])
def test_builtin_hover_format_negotiation(project, formats):
    c = Client(project)
    try:
        c.request(
            "initialize",
            {
                "processId": None,
                "rootUri": project.as_uri(),
                "capabilities": {"textDocument": {"hover": {"contentFormat": formats}}},
            },
        )
        c.send("initialized", {})
        path = project / "main.nosch"
        source = "from noscheme import nettype\nboard B:\n    net bus: nettype.tristate\n"
        open_document(c, path, source)
        params = {
            "textDocument": {"uri": path.as_uri()},
            "position": {"line": 2, "character": 23},
        }
        result = c.request("textDocument/hover", params)
        assert result["contents"]["kind"] == formats[0]
        assert "tristate_capable: true" in result["contents"]["value"].replace("\\", "")
        if formats[0] == "markdown":
            assert result["contents"]["value"].startswith("**nettype\\.tristate**\n\n- ")
        else:
            assert result["contents"]["value"].startswith("nettype.tristate\n\n")
        assert result["range"] == {
            "start": {"line": 2, "character": 13},
            "end": {"line": 2, "character": 29},
        }
        assert c.request("textDocument/definition", params) is None
    finally:
        c.close()


def test_board_instance_rename_returns_identity_insertion(client, project):
    path = project / "main.nosch"
    open_document(client, path, SOURCE)
    line = next(i for i, text in enumerate(SOURCE.splitlines()) if "inst receivers" in text)
    params = {"textDocument": {"uri": path.as_uri()}, "position": {"line": line, "character": 10}}
    prepared = client.request("textDocument/prepareRename", params)
    assert prepared["placeholder"] == "receivers"
    result = client.request("textDocument/rename", {**params, "newName": "inputs"})
    edits = result["changes"][path.as_uri()]
    assert any(edit["newText"] == "inputs" for edit in edits)
    insertion = next(edit for edit in edits if "identity:" in edit["newText"])
    assert 'identity: "receivers"' in insertion["newText"]
    assert insertion["range"]["start"] == insertion["range"]["end"]
    assert path.read_text() == SOURCE
    assert not (project / "noscheme.state.json").exists()


def test_analysis_reuses_open_file_parse_and_still_checks_unimported_files(project, monkeypatch):
    import noscheme.lsp_analysis as analysis_module
    import noscheme.project as project_module
    from noscheme.parser import parse

    calls = []

    def counted_parse(text, filename):
        calls.append(filename)
        return parse(text, filename)

    monkeypatch.setattr(analysis_module, "parse", counted_parse)
    monkeypatch.setattr(project_module, "parse", counted_parse)
    path = project / "main.nosch"
    result = analyze(path, project, {path: SOURCE})
    assert not any(d.severity == "error" for d in result.diagnostics)
    assert calls == [str(path)]

    calls.clear()
    draft = project / "draft.nosch"
    result = analyze(draft, project, {draft: "board Broken:\n    inst ???\n"})
    assert any(d.severity == "error" and d.span.filename == str(draft) for d in result.diagnostics)
    assert calls == [str(draft)]
