"""Compare editor-visible Python/native LSP latency through the stdio protocol.

Run without competing builds/tests. Files, CAD output, and identity state are
never written. Cold hover includes the typing debounce; warm requests wait for
full diagnostics first. Edits append comments in an unsaved editor buffer.
"""

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import platform
from statistics import median
import sys
import threading
from time import perf_counter

from noscheme import ast
from noscheme.project import load_project
from tools.lsp_client import LspClient


def message(method, params=None, **extra):
    return {"jsonrpc": "2.0", "method": method, "params": params or {}, **extra}


class MemorySampler:
    """Sample Linux process-tree RSS; shared pages may be counted more than once."""

    def __init__(self, pid):
        self.pid = pid
        self.peak_kib = 0
        self.done = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while not self.done.is_set():
            pending, visited, total = [self.pid], set(), 0
            while pending:
                pid = pending.pop()
                if pid in visited:
                    continue
                visited.add(pid)
                root = Path(f"/proc/{pid}")
                try:
                    for line in (root / "status").read_text().splitlines():
                        if line.startswith("VmRSS:"):
                            total += int(line.split()[1])
                    for task in (root / "task").iterdir():
                        pending.extend(int(p) for p in (task / "children").read_text().split())
                except (OSError, ValueError):
                    pass  # Processes can exit during a sample.
            self.peak_kib = max(self.peak_kib, total)
            self.done.wait(0.05)

    def stop(self):
        self.done.set()
        self.thread.join()


def measure(command, config, path, text, position, repeat):
    start = perf_counter()
    with LspClient([*command, "--project", str(config)]) as client, ExitStack() as cleanup:
        memory = MemorySampler(client.process.pid)
        cleanup.callback(memory.stop)
        request_id = 0

        def request(method, params):
            nonlocal request_id
            request_id += 1
            client.send(message(method, params, id=request_id))
            reply = client.until(lambda r: r.get("id") == request_id, 120)
            if "error" in reply:
                raise RuntimeError(reply["error"])
            return reply["result"]

        request(
            "initialize", {"rootUri": config.parent.as_uri(), "capabilities": {}, "processId": None}
        )
        initialize = (perf_counter() - start) * 1000
        client.send(message("initialized"))
        uri = path.as_uri()
        params = {"textDocument": {"uri": uri}, "position": position}

        def diagnostics(version):
            def matches(reply):
                return (
                    reply.get("method") == "textDocument/publishDiagnostics"
                    and reply["params"]["uri"] == uri
                    and reply["params"].get("version") == version
                )

            reply = next((r for r in reversed(client.replies) if matches(r)), None)
            if reply is None:
                reply = client.until(matches, 120)
            return reply["params"]["diagnostics"]

        start = perf_counter()
        client.send(
            message(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": "noscheme",
                        "version": 1,
                        "text": text,
                    }
                },
            )
        )
        if request("textDocument/hover", params) is None:
            raise RuntimeError("Benchmark target did not produce a hover")
        cold_hover = (perf_counter() - start) * 1000
        problems = diagnostics(1)
        cold_check = (perf_counter() - start) * 1000
        warm, edited, checked = [], [], []
        for i in range(repeat):
            start = perf_counter()
            request("textDocument/hover", params)
            warm.append((perf_counter() - start) * 1000)
            version = i + 2
            start = perf_counter()
            client.send(
                message(
                    "textDocument/didChange",
                    {
                        "textDocument": {"uri": uri, "version": version},
                        "contentChanges": [{"text": text + f"\n// benchmark edit {version}\n"}],
                    },
                )
            )
            request("textDocument/hover", params)
            edited.append((perf_counter() - start) * 1000)
            diagnostics(version)
            checked.append((perf_counter() - start) * 1000)
        request("shutdown", {})
        client.send(message("exit"))
        assert client.process.wait(timeout=5) == 0

        def summary(samples):
            return {
                "median_ms": round(median(samples), 3),
                "samples_ms": [round(v, 3) for v in samples],
            }

        return {
            "sampled_peak_tree_rss_mib": round(memory.peak_kib / 1024, 1),
            "initialize_ms": round(initialize, 3),
            "cold_hover_ms": round(cold_hover, 3),
            "cold_diagnostics_ms": round(cold_check, 3),
            "diagnostic_count": len(problems),
            "warm_hover": summary(warm),
            "edited_hover": summary(edited),
            "edited_diagnostics": summary(checked),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=5)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    print("Loading benchmark project", file=sys.stderr, flush=True)
    project = load_project(args.project)
    path = project.modules[project.config.board_module].path
    text = path.read_text()
    declaration = next(
        n for n in project.board.declaration.body if isinstance(n, (ast.Instance, ast.Net))
    )
    before = text[: declaration.name.span.start_pos]
    position = {
        "line": before.count("\n"),
        "character": len(before.rsplit("\n", 1)[-1].encode("utf-16-le")) // 2,
    }
    result = {
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "project": str(project.config.path),
        "native_binary": str(args.native.resolve()),
        "repeat": args.repeat,
    }
    for label, command in [
        ("python", [sys.executable, "-m", "noscheme.lsp", "--implementation", "python"]),
        ("native", [str(args.native.resolve()), "--python", sys.executable]),
    ]:
        print(f"Measuring {label}", file=sys.stderr, flush=True)
        result[label] = measure(command, project.config.path, path, text, position, args.repeat)
        print(json.dumps({label: result[label]}), file=sys.stderr, flush=True)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
