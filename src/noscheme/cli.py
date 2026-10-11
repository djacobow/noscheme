"""Inspect source syntax, project loading, and resolved definitions."""

import argparse
import difflib
import json
import os
from pathlib import Path
import sys
import tempfile

from .ast import to_data
from .diagnostics import DiagnosticError, render_json, render_text
from .formatter import format_source, source_files
from .parser import parse
from .project import load_project, project_summary
from .resolve import resolve_project, resolution_summary
from .evaluate import evaluate_project
from .circuit import elaborate_project
from .lint import unused_parameters
from .erc import apply_erc, report_files, report_diagnostics
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


def format_paths(args, formatter=format_source):
    """Format files in place, or with --check/--diff only report what would change."""
    renderer = render_json if args.diagnostics == "json" else render_text
    changed = failed = 0
    for path in source_files(args.paths):
        source = path.read_text(encoding="utf-8")
        try:
            result = formatter(source, str(path))
        except DiagnosticError as exc:
            print(renderer(exc.diagnostics), file=sys.stderr)
            failed += 1
            continue
        if result == source:
            continue
        changed += 1
        if args.diff:
            sys.stdout.writelines(
                difflib.unified_diff(
                    source.splitlines(True), result.splitlines(True), str(path), str(path)
                )
            )
        if args.check or args.diff:
            print(f"would reformat {path}", file=sys.stderr)
        else:
            write_atomic(path, result)
            print(f"reformatted {path}", file=sys.stderr)
    return int(failed > 0 or (args.check and changed > 0))


def main(argv=None):
    cli = argparse.ArgumentParser(
        description="NoScheme compiler, electrical checks, and KiCad tools."
    )
    sub = cli.add_subparsers(dest="command", required=True)
    from .lsp_launcher import add_arguments, launch

    lsp = sub.add_parser("lsp", help="start a language server over stdio")
    add_arguments(lsp)
    parser = sub.add_parser("parse", help="validate syntax/structure and emit a typed AST")
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, help="write AST JSON atomically; default is stdout")
    parser.add_argument("--diagnostics", choices=["text", "json"], default="text")
    formatter = sub.add_parser("format", help="rewrite sources in the canonical layout")
    formatter.add_argument(
        "paths", nargs="*", type=Path, default=[Path(".")], help="files or directories; default ."
    )
    formatter.add_argument(
        "--check", action="store_true", help="write nothing; fail if a file would change"
    )
    formatter.add_argument("--diff", action="store_true", help="print changes instead of writing")
    formatter.add_argument("--diagnostics", choices=["text", "json"], default="text")
    loader = sub.add_parser("load", help="load a project and bind imports; no elaboration or ERC")
    loader.add_argument("--project", type=Path, default=Path("."))
    loader.add_argument("--diagnostics", choices=["text", "json"], default="text")
    resolver = sub.add_parser(
        "resolve", help="resolve definitions and interfaces; no evaluation, elaboration, or ERC"
    )
    resolver.add_argument("--project", type=Path, default=Path("."))
    resolver.add_argument("--diagnostics", choices=["text", "json"], default="text")
    linter = sub.add_parser("lint", help="opt-in unused-parameter warnings; no elaboration or ERC")
    linter.add_argument("--project", type=Path, default=Path("."))
    linter.add_argument("--diagnostics", choices=["text", "json"], default="text")
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
        compiler.add_argument(
            "--erc-configuration",
            action="append",
            help="check only this named configuration; repeat to select several",
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
    exporter.add_argument(
        "--erc-configuration",
        action="append",
        help="check only this named configuration; repeat to select several",
    )
    exporter.add_argument("--project", type=Path, default=Path("."))
    exporter.add_argument(
        "--footprints", type=Path, required=True, help="explicit converted footprints.json"
    )
    exporter.add_argument("--diagnostics", choices=["text", "json"], default="text")
    bom = sub.add_parser("export-bom", help="write a grouped CSV bill of materials")
    bom.add_argument("--project", type=Path, default=Path("."))
    bom.add_argument("--diagnostics", choices=["text", "json"], default="text")
    classes = sub.add_parser(
        "update-kicad-classes", help="merge memberships into a new KiCad project copy"
    )
    classes.add_argument("project_file", type=Path)
    classes.add_argument("--memberships", type=Path, required=True)
    classes.add_argument("--output", type=Path, required=True)
    classes.add_argument("--diagnostics", choices=["text", "json"], default="text")
    from . import native_cli

    for name in native_cli.COMMANDS:
        native_cli.add_arguments(sub.choices[name])
    for name in (
        "load",
        "resolve",
        "lint",
        "evaluate",
        "check",
        "compile",
        "export-kicad",
        "export-bom",
    ):
        sub.choices[name].add_argument(
            "--target", help="named project target (default: default_target or sole target)"
        )
    args = cli.parse_args(argv)
    if getattr(args, "target", None) == "":
        cli.error("--target requires a nonempty name")
    if args.command == "lsp":
        return launch(args, lsp)
    if getattr(args, "native_tool", None) and args.implementation != "native":
        cli.error("--native-tool requires --implementation native")
    try:
        if getattr(args, "implementation", "python") == "native":
            return native_cli.run(args, cli)
        if args.command == "format":
            return format_paths(args)
        if args.command == "update-kicad-classes":
            print(
                json.dumps(
                    update_classes(args.project_file, args.memberships, args.output), indent=2
                )
            )
            return 0
        if args.command == "export-bom":
            from .bom import export_bom

            project, definitions = resolve_project(args.project, target=args.target)
            circuit, warnings = elaborate_project(project, definitions)
            export_warnings = []
            print(json.dumps(export_bom(project, circuit, warnings=export_warnings), indent=2))
            if warnings or export_warnings:
                renderer = render_json if args.diagnostics == "json" else render_text
                print(renderer((*warnings, *export_warnings)), file=sys.stderr)
            return 0
        if args.command == "export-kicad":
            project, definitions = resolve_project(args.project, target=args.target)
            circuit, warnings = elaborate_project(project, definitions)
            export_warnings = []
            report = export_project(
                project,
                circuit,
                args.footprints,
                configurations=args.erc_configuration,
                warnings=export_warnings,
            )
            electrical = report_diagnostics(circuit)
            print(json.dumps(report, indent=2))
            if warnings or electrical or export_warnings:
                renderer = render_json if args.diagnostics == "json" else render_text
                print(renderer((*warnings, *electrical, *export_warnings)), file=sys.stderr)
            return 0
        if args.command == "convert-library":
            report = convert_library(
                args.source, args.output, args.symbol, args.footprint_root, args.extra_footprint
            )
            print(json.dumps(report, indent=2))
            return 0
        if args.command in {"check", "compile"}:
            project, definitions = resolve_project(args.project, target=args.target)
            circuit, warnings = elaborate_project(project, definitions)
            electrical = apply_erc(
                circuit, args.erc_configuration, registry=project.config.erc_registry
            )
            summary = {
                "stage": "circuit",
                "parts": len(circuit["parts"]),
                "nets": len(circuit["nets"]),
                "erc_performed": True,
                "erc_errors": circuit["erc"]["errors"],
                "erc_warnings": circuit["erc"]["warnings"],
                "erc_pending": circuit["erc"]["pending"],
                "erc_unchecked_nets": circuit["erc"]["unchecked_nets"],
                "erc_configurations": circuit["erc"]["configurations"],
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
        if args.command == "lint":
            _, definitions = resolve_project(args.project, target=args.target)
            warnings = unused_parameters(definitions)
            renderer = render_json if args.diagnostics == "json" else render_text
            print(renderer(warnings))
            return 0
        if args.command == "evaluate":
            report, warnings = evaluate_project(*resolve_project(args.project, target=args.target))
            print(json.dumps(report, indent=2))
            if warnings:
                renderer = render_json if args.diagnostics == "json" else render_text
                print(renderer(warnings), file=sys.stderr)
            return 0
        if args.command == "resolve":
            print(
                json.dumps(
                    resolution_summary(*resolve_project(args.project, target=args.target)), indent=2
                )
            )
            return 0
        if args.command == "load":
            print(
                json.dumps(
                    project_summary(load_project(args.project, target=args.target)), indent=2
                )
            )
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
