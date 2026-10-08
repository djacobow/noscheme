"""Measure uncached parsing, AST construction, and structural validation.

Run with the Python development environment and an optimized native probe.
First-request timings include process startup; warm timings exclude JSON I/O.
"""

import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def python_samples(source, filename, iterations):
    from noscheme.parser import parse

    samples = []
    for _ in range(iterations):
        start = time.perf_counter()
        result = parse(source, filename)
        del result
        samples.append(time.perf_counter() - start)
    return samples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("probe", nargs="?", default="build/native/noscheme-native-probe")
    parser.add_argument("--python-worker", action="store_true")
    parser.add_argument("--iterations", type=int, default=21)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 9997:
        parser.error("--iterations must be 1..9997")
    if args.python_worker:
        request = json.load(sys.stdin)
        print(json.dumps(python_samples(**request)))
        return

    paths = [
        "examples/blink/src/main.nosch",
        "examples/stm32h523/stm32h523.nosch",
        "examples/rulos_6matrix/main.nosch",
        "examples/wren/full/parts.nosch",
        "examples/wren/full/main.nosch",
    ]
    for filename in paths:
        source = (ROOT / filename).read_text()
        request = {"source": source, "filename": filename}
        # Each file gets a fresh process on both sides. First three parses warm
        # grammar/lexer caches; AST caching is never activated.
        start = time.perf_counter()
        cold_native = subprocess.run(
            [args.probe],
            input=json.dumps(request | {"benchmark_iterations": 1}) + "\n",
            text=True,
            capture_output=True,
            check=True,
        )
        native_cold = time.perf_counter() - start
        assert "seconds" in json.loads(cold_native.stdout), cold_native.stdout
        start = time.perf_counter()
        subprocess.run(
            [sys.executable, __file__, "--python-worker"],
            input=json.dumps(request | {"iterations": 1}),
            text=True,
            capture_output=True,
            check=True,
        )
        python_cold = time.perf_counter() - start
        native = subprocess.run(
            [args.probe],
            input=json.dumps(request | {"benchmark_iterations": args.iterations + 3}) + "\n",
            text=True,
            capture_output=True,
            check=True,
        )
        native_data = json.loads(native.stdout)
        native_times = native_data["seconds"][3:]
        python = subprocess.run(
            [sys.executable, __file__, "--python-worker"],
            input=json.dumps(request | {"iterations": args.iterations + 3}),
            text=True,
            capture_output=True,
            check=True,
        )
        python_times = json.loads(python.stdout)[3:]
        row = {
            "file": filename,
            "bytes": len(source.encode()),
            "python_median_ms": statistics.median(python_times) * 1000,
            "native_median_ms": statistics.median(native_times) * 1000,
            "python_first_request_ms": python_cold * 1000,
            "native_first_request_ms": native_cold * 1000,
        }
        row["speedup"] = row["python_median_ms"] / row["native_median_ms"]
        row["native_phases_ms"] = {
            key: statistics.median(sample[key] for sample in native_data["phases"][3:]) * 1000
            for key in native_data["phases"][0]
        }
        print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
