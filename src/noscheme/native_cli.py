"""Optional native compiler behind the existing CLI and atomic publisher.

The private worker protocol is shared only by matching repository revisions.
Python still owns argument parsing, diagnostic presentation, and filesystem
publication. It does not parse or elaborate NoScheme in native mode.
"""

import json
from pathlib import Path
import shutil
import subprocess
import sys

from . import ast as a
from .diagnostics import Diagnostic, DiagnosticError, Related, render_json, render_text
from .kicad.export import publish, read_json, read_state, state_warnings


COMMANDS = {
    "parse",
    "format",
    "load",
    "resolve",
    "lint",
    "evaluate",
    "check",
    "compile",
    "export-kicad",
    "export-bom",
}


def add_arguments(parser):
    parser.add_argument("--implementation", choices=("python", "native"), default="python")
    parser.add_argument(
        "--native-tool",
        type=Path,
        help="native compiler executable (default: noscheme-native on PATH)",
    )


def public_offsets(response, sources):
    """Convert native UTF-8 byte offsets to the CLI's character-offset schema."""
    positions = {}

    def walk(value):
        if isinstance(value, list):
            return [walk(item) for item in value]
        if not isinstance(value, dict):
            return value
        if {
            "filename",
            "start_pos",
            "end_pos",
            "line",
            "column",
            "end_line",
            "end_column",
        } == value.keys():
            filename = value["filename"]
            if filename not in positions:
                try:
                    text = (
                        sources[filename]
                        if filename in sources
                        else Path(filename).read_text(encoding="utf-8")
                    )
                except (OSError, UnicodeError):
                    return value
                if text.isascii():
                    positions[filename] = None
                    return value
                offsets, total = {0: 0}, 0
                for index, char in enumerate(text, 1):
                    total += len(char.encode("utf-8"))
                    offsets[total] = index
                positions[filename] = offsets
            offsets = positions[filename]
            if offsets is None:
                return value
            return value | {
                key: offsets.get(value[key], value[key]) for key in ("start_pos", "end_pos")
            }
        return {key: walk(item) for key, item in value.items()}

    response = walk(response)
    for file in response.get("files", []):
        if Path(file[0]).suffix == ".json":
            file[1] = json.dumps(walk(json.loads(file[1])), indent=2, sort_keys=True) + "\n"
    return response


class Worker:
    def __init__(self, executable):
        self.process = subprocess.Popen(
            [executable], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8"
        )

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.process.stdin.close()
        self.process.stdout.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()

    def request(self, command, **values):
        try:
            self.process.stdin.write(
                json.dumps({"command": command, "python": sys.executable, **values}) + "\n"
            )
            self.process.stdin.flush()
            line = self.process.stdout.readline()
        except BrokenPipeError as exc:
            raise OSError("native compiler exited unexpectedly") from exc
        if not line:
            raise OSError("native compiler exited without a response")
        try:
            response = json.loads(line)
        except ValueError as exc:
            raise OSError("invalid native compiler response") from exc
        response = public_offsets(
            response, {values["filename"]: values["source"]} if "source" in values else {}
        )
        if "io_error" in response:
            raise OSError(response["io_error"])
        diagnostics = tuple(
            Diagnostic(
                **{k: v for k, v in d.items() if k not in {"span", "related"}},
                span=a.Span(**d["span"]),
                related=tuple(
                    Related(r["message"], a.Span(**r["span"])) for r in d.get("related", [])
                ),
            )
            for d in response.get("diagnostics", [])
        )
        if "result" not in response:
            error = DiagnosticError(diagnostics)
            error.exit_code = response.get("exit_code", 1)
            raise error
        response["diagnostics"] = diagnostics
        return response


def run(args, parser):
    from .cli import format_paths, write_atomic

    executable = (
        str(args.native_tool.resolve()) if args.native_tool else shutil.which("noscheme-native")
    )
    if executable is None:
        parser.error(
            "native compiler is not installed; supply --native-tool PATH or select --implementation python"
        )
    with Worker(executable) as worker:
        if args.command == "format":
            return format_paths(
                args,
                formatter=lambda source, filename: worker.request(
                    "format", source=source, filename=filename
                )["result"],
            )
        if args.command == "parse":
            if args.output and args.output.resolve() == args.source.resolve():
                raise OSError("AST output must not overwrite the source file")
            response = worker.request(
                "parse", source=args.source.read_text(encoding="utf-8"), filename=str(args.source)
            )
            text = json.dumps(response["result"], indent=2) + "\n"
            if args.output:
                write_atomic(args.output, text)
            else:
                sys.stdout.write(text)
            return 0
        request = {
            "project": str(args.project),
            "target": args.target or "",
            "configurations": getattr(args, "erc_configuration", None),
        }
        if args.command in {"export-kicad", "export-bom"}:
            from .project import read_config

            config = read_config(args.project, target=args.target)
            request["state_warnings"] = [a.to_data(w) for w in state_warnings(config)]
            request["state"] = read_state(config.state_path)
        if args.command == "export-kicad":
            request.update(
                footprints=read_json(args.footprints),
                records=str(args.footprints),
                records_text=args.footprints.read_bytes().decode("utf-8"),
            )
            if args.schematic:
                request["schematic"] = str(args.schematic)
        response = worker.request(args.command, **request)
        if args.command == "resolve":
            from .native_bridge import decode_metadata

            for definition in response["result"]["definitions"]:
                definition["metadata"] = decode_metadata(definition["metadata"])
                for pin in definition["interface"].values():
                    pin["metadata"] = decode_metadata(pin["metadata"])
        if args.command == "evaluate":
            from .native_bridge import decode_metadata

            for instance in response["result"]["instances"]:
                for selections in instance["connection_selections"].values():
                    for selection in selections:
                        selection["start"] = decode_metadata(selection["start"])
                        selection["stop"] = decode_metadata(selection["stop"])
        if args.command == "lint":
            renderer = render_json if args.diagnostics == "json" else render_text
            print(renderer(response["diagnostics"]))
            return 0
        # The worker validates protected paths; the shared publisher stages all
        # artifacts and rolls back on ordinary replacement failures. State is last.
        files = {Path(path): text for path, text in response.get("files", [])}
        stale = []
        if getattr(args, "schematic", None) and "schematic" in response["result"]:
            from .kicad.export import before_state, schematic_upkeep

            rendered = {Path(p) for p in response["result"]["schematic"]}
            extra, stale = schematic_upkeep(args.schematic, rendered)
            files = before_state(files, Path(response["result"]["state"]), extra)
        publish(files)
        for path in stale:
            path.unlink()
        if getattr(args, "schematic", None) and "schematic" in response["result"]:
            response["result"]["schematic_removed"] = [str(p) for p in stale]
        print(json.dumps(response["result"], indent=2))
        if response["diagnostics"]:
            renderer = render_json if args.diagnostics == "json" else render_text
            print(renderer(response["diagnostics"]), file=sys.stderr)
        return response.get("exit_code", 0)
