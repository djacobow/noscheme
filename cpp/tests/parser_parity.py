"""Compare native ASTs against the Python compiler, including source locations."""

import ast
import json
import re
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from noscheme.ast import to_data  # noqa: E402
from noscheme.parser import parse  # noqa: E402
from noscheme.diagnostics import DiagnosticError  # noqa: E402


def normalized(value, source):
    if isinstance(value, list):
        return [normalized(x, source) for x in value]
    if isinstance(value, dict):
        if "start_pos" in value:
            raw = source.encode()
            return value | {
                key: len(raw[: value[key]].decode()) for key in ("start_pos", "end_pos")
            }
        return {k: normalized(v, source) for k, v in value.items()}
    return value


def diff(a, b, path=""):
    if type(a) is not type(b):
        return f"{path}: {a!r} != {b!r}"
    if isinstance(a, dict):
        if a.keys() != b.keys():
            return f"{path}: keys {a.keys()} != {b.keys()}"
        for k in a:
            d = diff(a[k], b[k], path + "." + k)
            if d:
                return d
    elif isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: lengths {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            d = diff(x, y, f"{path}[{i}]")
            if d:
                return d
    elif a != b:
        return f"{path}: {a!r} != {b!r}"
    return None


def main():
    process = subprocess.Popen(
        [sys.argv[1]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    failures = []
    count = 0
    invalid_count = 0
    try:
        samples = [
            (str(p), p.read_text())
            for p in sorted(ROOT.glob("examples/**/*.nosch"))
            if not any(x in {"build", ".history"} for x in p.parts)
        ]
        for path in sorted((ROOT / "tests").glob("test_*.py")):
            for node in ast.walk(ast.parse(path.read_text())):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and "\n" in node.value
                ):
                    samples.append((f"{path}:{node.lineno}", node.value))
        samples.extend(
            ("unicode.nosch", s)
            for s in [
                '// 👋 café\ncomponent R:\n  metadata:\n    value: "☃😀"\n',
                '/* 👋 */\ncomponent R:\n  metadata:\n    value: "é" // ☃\n',
                'component R:\r\n  metadata:\r\n    value: "é"\r\n',
            ]
        )
        for path, source in samples:
            expected_error = None
            try:
                expected = to_data(parse(source, str(path)))
            except DiagnosticError as exc:
                if not re.match(
                    r"\s*(?:component|block|board|bundle|nettype|from|import)\b", source
                ):
                    continue
                expected_error = exc.diagnostics
                invalid_count += 1
            process.stdin.write(json.dumps({"source": source, "filename": str(path)}) + "\n")
            process.stdin.flush()
            actual = json.loads(process.stdout.readline())
            count += 1
            if expected_error:
                if "diagnostics" not in actual:
                    failures.append(f"{path}: invalid source accepted: {source[:100]!r}")
                else:
                    expected_codes = [d.code for d in expected_error]
                    actual_codes = [d["code"] for d in actual["diagnostics"]]
                    if expected_codes != actual_codes:
                        failures.append(
                            f"{path}: diagnostic codes {actual_codes} != {expected_codes}"
                        )
                continue
            if "ast" not in actual:
                failures.append(f"{path}: {actual}")
            elif error := diff(expected, normalized(actual["ast"], source)):
                failures.append(f"{path}: {error}")
    finally:
        process.stdin.close()
        returncode = process.wait(timeout=10)
        if returncode:
            failures.append(f"Native probe exited with status {returncode}")
    print(f"{count} sources compared ({invalid_count} invalid); {len(failures)} failures")
    print("\n".join(failures[:15]))
    return bool(failures)


if __name__ == "__main__":
    sys.exit(main())
