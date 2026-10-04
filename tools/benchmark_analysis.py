"""Measure compilation, isolated circuit copies, and complete LSP analysis.

Run with: python -m tools.benchmark_analysis [project directory] --repeat 5
No build artifacts or export state are written. Timings are local observations,
not CI thresholds. First-call grammar/cache initialization is reported separately.
"""

import argparse
from copy import deepcopy
import json
from pathlib import Path
from statistics import median
from time import perf_counter

from noscheme.circuit import elaborate_project
from noscheme.erc import apply_erc
from noscheme.lsp_analysis import analyze
from noscheme.resolve import resolve_project


def measure(callback, repeat):
    times = []
    for _ in range(repeat):
        start = perf_counter()
        callback()
        times.append((perf_counter() - start) * 1000)
    return {"median_ms": round(median(times), 3), "min_ms": round(min(times), 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", nargs="?", default="examples/stm32h523")
    parser.add_argument("--repeat", type=int, default=5)
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

    result = {
        "project": str(root),
        "parts": len(circuit["parts"]),
        "nets": len(circuit["nets"]),
        "repeat": args.repeat,
        "cold_resolve_elaborate_ms": round(cold, 3),
        "resolve_elaborate": measure(compile_project, args.repeat),
        "circuit_deepcopy": measure(lambda: deepcopy(circuit), args.repeat),
        "erc": measure(
            lambda: apply_erc(circuit, registry=project.config.erc_registry), args.repeat
        ),
        "lsp_analysis": measure(lambda: analyze(path, project.config.path, {}), args.repeat),
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
