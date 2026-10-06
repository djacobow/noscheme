"""Measure compilation, isolated circuit copies, and complete LSP analysis.

Run with: python -m tools.benchmark_analysis [project directory] --repeat 5
Only an explicitly requested profile is written; export state is untouched.
Timings are local observations, not CI thresholds. First-call grammar/cache
initialization is reported separately. Run without competing test workloads.
"""

import argparse
import asyncio
from copy import deepcopy
import cProfile
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
import platform
from statistics import median
from time import perf_counter

from noscheme.circuit import elaborate_project
from noscheme.erc import apply_erc
from noscheme.lsp_analysis import analyze
from noscheme.lsp_transport import WorkerLane
from noscheme.resolve import resolve_project


def measure(callback, repeat):
    times = []
    for _ in range(repeat):
        start = perf_counter()
        callback()
        times.append((perf_counter() - start) * 1000)
    return {
        "median_ms": round(median(times), 3),
        "min_ms": round(min(times), 3),
        "samples_ms": [round(t, 3) for t in times],
    }


async def measure_workers(project, repeat):
    """Include subprocess startup, snapshot validation, and private pipe transport."""
    path = project.modules[project.config.board_module].path
    source = path.read_text()
    request = dict(
        operation="analysis",
        generation=0,
        sources={path: source},
        roots=[project.config.path.parent],
        explicit=project.config.path,
        paths=[path],
    )
    interactive, checker = WorkerLane(), WorkerLane()

    async def sample(lane, count, edit=False):
        times = []
        for _ in range(count):
            if edit:
                request["generation"] += 1
                request["sources"] = {path: source + f"\n// edit {request['generation']}\n"}
            start = perf_counter()
            response = await lane.call(request)
            times.append((perf_counter() - start) * 1000)
            assert response["valid"]
            errors = [
                d
                for result in response["value"].values()
                for d in result.diagnostics
                if d.severity == "error"
            ]
            if errors:
                raise RuntimeError(f"Worker benchmark has errors: {errors[0]}")
        return {"median_ms": round(median(times), 3), "samples_ms": [round(t, 3) for t in times]}

    try:
        result = {"cold_semantic": await sample(interactive, 1)}
        result["warm_semantic"] = await sample(interactive, repeat)
        result["edited_semantic"] = await sample(interactive, repeat, edit=True)
        request["paths"] = sorted(m.path for m in project.modules.values())
        result["all_files_open_semantic"] = await sample(interactive, repeat)
        request["operation"] = "diagnostics"
        result["cold_diagnostics"] = await sample(checker, 1)
        result["edited_diagnostics"] = await sample(checker, repeat, edit=True)
        return result
    finally:
        await interactive.close()
        await checker.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", nargs="?", default="examples/stm32h523")
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--profile", type=Path, help="Write a separate full LSP cProfile run")
    parser.add_argument(
        "--workers", action="store_true", help="Also measure persistent LSP workers"
    )
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    root = Path(args.project).resolve()
    start = perf_counter()
    project, definitions = resolve_project(root)
    circuit, _ = elaborate_project(project, definitions)
    cold = (perf_counter() - start) * 1000
    board_module = project.config.board_module
    path = project.modules[board_module].path

    def compile_project():
        loaded, definitions = resolve_project(root)
        elaborate_project(loaded, definitions)

    def analyze_project():
        analysis = analyze(path, project.config.path, {})
        errors = [d for d in analysis.diagnostics if d.severity == "error"]
        if errors:
            raise RuntimeError(f"LSP benchmark has {len(errors)} errors: {errors[0]}")
        return analysis

    result = {
        "project": str(root),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "dependencies": {name: version(name) for name in ("lark", "pygls")},
        },
        "source_sha256": {
            str(p.relative_to(root)) if p.is_relative_to(root) else str(p): sha256(
                p.read_bytes()
            ).hexdigest()
            for p in sorted({project.config.path, *(m.path for m in project.modules.values())})
        },
        "parts": len(circuit["parts"]),
        "nets": len(circuit["nets"]),
        "repeat": args.repeat,
        "cold_resolve_elaborate_ms": round(cold, 3),
        "resolve_elaborate": measure(compile_project, args.repeat),
        "circuit_deepcopy": measure(lambda: deepcopy(circuit), args.repeat),
        "erc": measure(
            lambda: apply_erc(circuit, registry=project.config.erc_registry), args.repeat
        ),
        "lsp_analysis": measure(analyze_project, args.repeat),
    }
    if args.workers:
        result["workers"] = asyncio.run(measure_workers(project, args.repeat))
    if args.profile:
        args.profile.parent.mkdir(parents=True, exist_ok=True)
        profile = cProfile.Profile()
        analysis = profile.runcall(analyze_project)
        profile.dump_stats(str(args.profile))
        result["profile"] = str(args.profile)
        result["analysis_counts"] = {
            "references": len(analysis.references),
            "hovers": len(analysis.hovers),
            "diagnostics": len(analysis.diagnostics),
        }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
