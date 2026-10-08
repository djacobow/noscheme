"""Native canonical formatting against the Python formatter, including comments."""

import ast
import json
import os
from pathlib import Path
import subprocess

import pytest

from noscheme.diagnostics import DiagnosticError
from noscheme.formatter import format_source
from noscheme.parser import parse

ROOT = Path(__file__).resolve().parents[2]
PROBE = os.environ.get("NOSCHEME_NATIVE_PROBE")
pytestmark = pytest.mark.skipif(not PROBE, reason="native probe supplied by Meson")


def samples():
    for path in sorted(ROOT.glob("examples/**/*.nosch")):
        if not any(p in {"build", ".history"} for p in path.parts):
            yield str(path.relative_to(ROOT)), path.read_text()
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "\n" in node.value
            ):
                try:
                    parse(node.value)
                except DiagnosticError:
                    continue
                yield f"{path.name}:{node.lineno}", node.value


CASES = list(samples())


@pytest.fixture(scope="module")
def probe():
    with subprocess.Popen(
        [PROBE], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    ) as process:

        def call(source):
            process.stdin.write(json.dumps({"source": source, "format": True}) + "\n")
            process.stdin.flush()
            return json.loads(process.stdout.readline())

        yield call
        process.stdin.close()
        assert process.wait(timeout=10) == 0


@pytest.mark.parametrize("name, source", CASES, ids=[name for name, _ in CASES])
def test_formatter_parity(probe, name, source):
    try:
        expected = format_source(source)
    except DiagnosticError as error:
        actual = probe(source)
        assert actual["diagnostics"][0]["code"] == error.diagnostics[0].code, (name, actual)
        return
    actual = probe(source)
    assert actual == {"formatted": expected}, name
    assert probe(expected) == actual
