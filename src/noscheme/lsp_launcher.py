"""Select an LSP implementation without changing the editor transport."""

import argparse
import os
from pathlib import Path
import shutil
import sys


def add_arguments(parser):
    parser.add_argument("--project", type=Path, help="Explicit noscheme.toml or project directory")
    parser.add_argument(
        "--implementation",
        choices=("python", "native"),
        default="python",
        help="LSP implementation (default: python)",
    )
    parser.add_argument(
        "--native-server",
        type=Path,
        help="Native server executable (default: noscheme-native-lsp on PATH)",
    )


def launch(args, parser):
    if args.implementation == "python":
        if args.native_server is not None:
            parser.error("--native-server requires --implementation native")
        from .lsp import create_server
        import logging

        logging.basicConfig(level=logging.WARNING)
        create_server(args.project).start_io()
        return
    executable = (
        str(args.native_server.resolve())
        if args.native_server is not None
        else shutil.which("noscheme-native-lsp")
    )
    if executable is None:
        parser.error(
            "native LSP server is not installed; supply --native-server PATH "
            "or select --implementation python"
        )
    command = [executable, "--python", sys.executable]
    if args.project is not None:
        command.extend(["--project", str(args.project)])
    # Replace the launcher, preserving stdio, exit status, and editor signals.
    try:
        os.execv(executable, command)
    except OSError as exc:
        parser.error(f"cannot start native LSP server: {exc}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    launch(parser.parse_args(argv), parser)
