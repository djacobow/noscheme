"""Inspect source syntax, project loading, and resolved definitions."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

from .ast import to_data
from .diagnostics import DiagnosticError, render_json, render_text
from .parser import parse
from .project import load_project, project_summary
from .resolve import resolve_project, resolution_summary
from .evaluate import evaluate_project
from .circuit import elaborate_project
from .erc import apply_erc, report_files
from .kicad.convert import convert_library
from .kicad.export import export_project
from .kicad.export import publish
from .kicad.classes import update_classes


def write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as stream:
            temporary = stream.name
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None):
    cli = argparse.ArgumentParser(
        description="NoScheme compiler, electrical checks, and KiCad tools."
    )
    sub = cli.add_subparsers(dest="command", required=True)
    parser = sub.add_parser("parse", help="validate syntax/structure and emit a typed AST")
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, help="write AST JSON atomically; default is stdout")
    parser.add_argument("--diagnostics", choices=["text", "json"], default="text")
    loader = sub.add_parser("load", help="load a project and bind imports; no elaboration or ERC")
    loader.add_argument("--project", type=Path, default=Path("."))
    loader.add_argument("--diagnostics", choices=["text", "json"], default="text")
    resolver = sub.add_parser(
        "resolve", help="resolve definitions and interfaces; no evaluation, elaboration, or ERC"
    )
    resolver.add_argument("--project", type=Path, default=Path("."))
    resolver.add_argument("--diagnostics", choices=["text", "json"], default="text")
    evaluator = sub.add_parser(
        "evaluate",
        help="evaluate parameterized declarations; no array expansion, connectivity, or ERC",
    )
    evaluator.add_argument("--project", type=Path, default=Path("."))
    evaluator.add_argument("--diagnostics", choices=["text", "json"], default="text")
    for command in ("check", "compile"):
        compiler = sub.add_parser(
            command,
            help="check structure and electrical rules"
            if command == "check"
            else "write circuit.json and ERC reports",
        )
        compiler.add_argument("--project", type=Path, default=Path("."))
        compiler.add_argument("--diagnostics", choices=["text", "json"], default="text")
    converter = sub.add_parser(
        "convert-library", help="explicitly convert a KiCad symbol library and snapshot footprints"
    )
    converter.add_argument("source", type=Path)
    converter.add_argument("--output", type=Path, required=True, help="new output directory")
    converter.add_argument(
        "--symbol", action="append", default=[], help="select a symbol; repeat, or omit for all"
    )
    converter.add_argument("--footprint-root", type=Path, action="append", default=[])
    converter.add_argument("--extra-footprint", action="append", default=[])
    converter.add_argument("--diagnostics", choices=["text", "json"], default="text")
    exporter = sub.add_parser(
        "export-kicad",
        help="write a netlist using converted footprint records and persistent identities",
    )
    exporter.add_argument("--project", type=Path, default=Path("."))
    exporter.add_argument(
        "--footprints", type=Path, required=True, help="explicit converted footprints.json"
    )
    exporter.add_argument("--diagnostics", choices=["text", "json"], default="text")
    classes = sub.add_parser(
        "update-kicad-classes", help="merge memberships into a new KiCad project copy"
    )
    classes.add_argument("project_file", type=Path)
    classes.add_argument("--memberships", type=Path, required=True)
    classes.add_argument("--output", type=Path, required=True)
    classes.add_argument("--diagnostics", choices=["text", "json"], default="text")
    args = cli.parse_args(argv)
    try:
        if args.command == "update-kicad-classes":
            print(
                json.dumps(
                    update_classes(args.project_file, args.memberships, args.output), indent=2
                )
            )
            return 0
        if args.command == "export-kicad":
            project, definitions = resolve_project(args.project)
            circuit, warnings = elaborate_project(project, definitions)
            electrical = apply_erc(circuit)
            report = export_project(project, circuit, args.footprints)
            print(json.dumps(report, indent=2))
            if warnings or electrical:
                renderer = render_json if args.diagnostics == "json" else render_text
                print(renderer((*warnings, *electrical)), file=sys.stderr)
            return 0
        if args.command == "convert-library":
            report = convert_library(
                args.source, args.output, args.symbol, args.footprint_root, args.extra_footprint
            )
            print(json.dumps(report, indent=2))
            return 0
        if args.command in {"check", "compile"}:
            project, definitions = resolve_project(args.project)
            circuit, warnings = elaborate_project(project, definitions)
            electrical = apply_erc(circuit)
            summary = {
                "stage": "circuit",
                "parts": len(circuit["parts"]),
                "nets": len(circuit["nets"]),
                "erc_performed": True,
                "erc_errors": circuit["erc"]["errors"],
                "erc_warnings": circuit["erc"]["warnings"],
                "erc_pending": circuit["erc"]["pending"],
            }
            if args.command == "compile":
                output = project.config.output_dir / "circuit.json"
                protected = {project.config.path, *(m.path for m in project.modules.values())}
                files = {
                    output: json.dumps(circuit, indent=2) + "\n",
                    **report_files(circuit, project.config.output_dir),
                }
                if any(path.resolve() in protected for path in files):
                    raise OSError("circuit output must not overwrite project inputs")
                publish(files)
                summary["output"] = str(output)
            print(json.dumps(summary, indent=2))
            if warnings or electrical:
                renderer = render_json if args.diagnostics == "json" else render_text
                print(renderer((*warnings, *electrical)), file=sys.stderr)
            return int(args.command == "check" and circuit["erc"]["errors"] > 0)
        if args.command == "evaluate":
            report, warnings = evaluate_project(*resolve_project(args.project))
            print(json.dumps(report, indent=2))
            if warnings:
                renderer = render_json if args.diagnostics == "json" else render_text
                print(renderer(warnings), file=sys.stderr)
            return 0
        if args.command == "resolve":
            print(json.dumps(resolution_summary(*resolve_project(args.project)), indent=2))
            return 0
        if args.command == "load":
            print(json.dumps(project_summary(load_project(args.project)), indent=2))
            return 0
        if args.output and args.output.resolve() == args.source.resolve():
            raise OSError("AST output must not overwrite the source file")
        module = parse(args.source.read_text(encoding="utf-8"), str(args.source))
        text = json.dumps({"schema_version": 1, "module": to_data(module)}, indent=2) + "\n"
        if args.output:
            write_atomic(args.output, text)
        else:
            sys.stdout.write(text)
    except DiagnosticError as exc:
        renderer = render_json if args.diagnostics == "json" else render_text
        print(renderer(exc.diagnostics), file=sys.stderr)
        return getattr(exc, "exit_code", 1)
    except (OSError, UnicodeError) as exc:
        if args.diagnostics == "json":
            print(
                json.dumps(
                    {
                        "schema_version": 1,
                        "diagnostics": [
                            {
                                "code": "io.error",
                                "severity": "error",
                                "message": str(exc),
                                "span": None,
                            }
                        ],
                    }
                ),
                file=sys.stderr,
            )
        else:
            print(f"error io.error: {exc}", file=sys.stderr)
        return 2
    return 0
