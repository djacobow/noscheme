"""Small stdio LSP client shared by protocol tests and local benchmarks."""

import json
import queue
import subprocess
import tempfile
import threading
import time


def frame(value):
    body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
    return f"Content-Length: {len(body)}\r\n\r\n".encode() + body


class LspClient:
    def __init__(self, command):
        self.stderr = tempfile.TemporaryFile()  # noqa: SIM115
        self.process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr
        )
        self.queue = queue.Queue()
        self.replies = []
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()

    def read(self):
        try:
            while header := self.process.stdout.readline():
                assert header.startswith(b"Content-Length: "), header
                size = int(header.split(b":")[1])
                while extra := self.process.stdout.readline():
                    if extra in {b"\r\n", b"\n"}:
                        break
                    assert b":" in extra, extra
                reply = json.loads(self.process.stdout.read(size))
                self.replies.append(reply)
                self.queue.put(reply)
        except Exception as exc:
            self.queue.put(exc)
        finally:
            self.queue.put(EOFError("LSP server exited"))

    def send(self, value):
        self.process.stdin.write(frame(value))
        self.process.stdin.flush()

    def until(self, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while True:
            reply = self.queue.get(timeout=max(0, deadline - time.monotonic()))
            if isinstance(reply, EOFError):
                self.stderr.seek(0)
                detail = self.stderr.read().decode(errors="replace")[-4000:]
                raise EOFError(f"LSP exited ({self.process.poll()}): {detail}")
            if isinstance(reply, Exception):
                raise reply
            if predicate(reply):
                return reply

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=5)
        self.reader.join(timeout=5)
        self.process.stdout.close()
        if not self.process.stdin.closed:
            self.process.stdin.close()
        self.stderr.close()
