"""Async private compiler lanes, separate from the editor's LSP connection."""

import asyncio
from contextlib import suppress
import pickle
import sys

from .lsp_worker import HEADER


class WorkerLane:
    def __init__(self):
        self.process = None
        self.lock = asyncio.Lock()

    async def close(self):
        process, self.process = self.process, None
        if process is not None:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.terminate()
            await process.wait()

    async def call(self, request):
        async with self.lock:
            try:
                if self.process is None or self.process.returncode is not None:
                    self.process = await asyncio.create_subprocess_exec(
                        sys.executable,
                        "-m",
                        "noscheme.lsp_worker",
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        # Inherit server stderr; plugin output cannot enter protocol stdout.
                        stderr=None,
                    )
                process = self.process
                payload = pickle.dumps(request, protocol=pickle.HIGHEST_PROTOCOL)
                process.stdin.write(HEADER.pack(len(payload)) + payload)
                await process.stdin.drain()
                header = await process.stdout.readexactly(HEADER.size)
                payload = await process.stdout.readexactly(HEADER.unpack(header)[0])
                response = pickle.loads(payload)
                if "error" in response:
                    raise ValueError(response["error"])
                return response
            except BaseException:
                # Cancellation or broken framing must not poison the next response.
                await self.close()
                raise
