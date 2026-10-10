"""Native/Python value evaluation, using the Python tests' existing cases too."""

import json
import math
import os
from pathlib import Path
import random
import runpy
import subprocess
from types import SimpleNamespace

import pytest

from noscheme import parse
from noscheme.ast import to_data
from noscheme.diagnostics import DiagnosticError
from noscheme.evaluate import Evaluator, Limits, Number, encoded
from noscheme.units import format_quantity

ROOT = Path(__file__).resolve().parents[2]
PROBE = os.environ.get("NOSCHEME_NATIVE_PROBE")
pytestmark = pytest.mark.skipif(not PROBE, reason="native probe supplied by Meson")
PYTHON_TESTS = runpy.run_path(str(ROOT / "tests/test_evaluate.py"))


def cases(name):
    return next(
        mark.args[1] for mark in PYTHON_TESTS[name].pytestmark if mark.name == "parametrize"
    )


@pytest.fixture(scope="module")
def probe():
    process = subprocess.Popen([PROBE], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

    def request(payload):
        process.stdin.write(json.dumps(payload) + "\n")
        process.stdin.flush()
        line = process.stdout.readline()
        assert line, f"probe exited: {process.poll()}"
        return json.loads(line)

    yield request
    process.stdin.close()
    process.stdout.close()
    assert process.wait(timeout=15) == 0


def compare(probe, text, env=None, symbols=None, **options):
    symbols = symbols if symbols is not None else {"num": {"module": "noscheme", "name": "num"}}
    env = env or {}
    request = {
        "expression": text,
        "symbols": symbols,
        "environment": {k: encoded(v) for k, v in env.items()},
        **options,
    }
    source = "component Eval:\n  params:\n    number x = " + text + "\n"
    evaluator = Evaluator(
        SimpleNamespace(
            modules={
                "main": SimpleNamespace(
                    symbols={k: SimpleNamespace(**v) for k, v in symbols.items()}
                )
            }
        ),
        Limits(**options.get("limits", {})),
    )
    evaluator.instance_path = options.get("instance_path")
    try:
        expr = parse(source, "expression.nosch").items[0].body[0].entries[0].default
        value = evaluator.evaluate(expr, "main", env)
        context = options.get("context")
        if context == "integer":
            value = Number(evaluator.integer(value, expr.span))
        if context == "dimension":
            value = Number(evaluator.dimension(value, expr.span))
        if context == "index":
            value = Number(
                evaluator.index(value, expr.span, options["size"], stop=options.get("stop", False))
            )
        expected = {"value": encoded(value)}
        if options.get("display"):
            expected["display"] = evaluator.display(value, expr.span)
        if "quantity" in options:
            expected["quantity"] = format_quantity(value.value, options["quantity"])
    except DiagnosticError as error:
        expected = {"diagnostics": [to_data(d) for d in error.diagnostics]}
    expected["warnings"] = [to_data(d) for d in evaluator.warnings]
    actual = probe(request)
    assert "error" not in actual, actual
    raw = source.encode()
    for key in ("diagnostics", "warnings"):
        for diag in actual.get(key, []):
            for offset in ("start_pos", "end_pos"):
                diag["span"][offset] = len(raw[: diag["span"][offset]].decode())
        for diag in actual.get(key, []) + expected.get(key, []):
            # Parser/formatter library wording differs; codes and locations don't.
            diag.pop("message", None)
    assert actual == expected, (text, actual, expected)
    if "value" in actual and actual["value"].get("tag") == "double":
        assert float(actual["value"]["value"]).hex() == float(expected["value"]["value"]).hex()


@pytest.mark.parametrize(
    "text",
    [
        row[0]
        for name in [
            "test_numbers",
            "test_invalid_expressions",
            "test_formatting",
            "test_scaled_literals_are_exact",
        ]
        for row in cases(name)
    ],
)
def test_existing_expressions(probe, text):
    compare(probe, text, display=True)


@pytest.mark.parametrize(
    "value,quantity,expected", cases("test_quantity_display_uses_engineering_prefixes")
)
def test_quantities(probe, value, quantity, expected):
    compare(probe, "q", {"q": Number(value)}, quantity=quantity)


@pytest.mark.parametrize(
    "text",
    [
        "(1 << 16000) / (1 << 16000)",
        "(1 << 16000) / 1",
        "(1 << 16000) + 1.0",
        "(1 << 16000) / 0.0",
        "1 / (1 << 1074)",
        "1 / (1 << 1075)",
        "3 / (1 << 1075)",
        "-1 / (1 << 1075)",
        "-9 >> 2",
        "-1 >> 65536",
        "1 << 65535",
        "1 << 65536",
        "1e-999",
        "num.round(2.4999999999999996)",
        "num.int(1e300)",
        "num.round(-0.0)",
        '"{0}".format(4.7 * 3)',
        '"{0:.8f}Ohm".format(1.23456789)',
        '"{0:08.2f}".format(-1.2)',
        '"{0:08.2e}".format(1.2)',
        '"{0:g}".format(1.23456789)',
        '"{0:.0g}".format(1.23456789)',
        '"{0:f}".format(1 << 16000)',
        '"{0}".format(1 << 16000)',
        '"{0:9999999d}".format(1)',
        '"abc}}x".format()',
        '"abc}x".format()',
        '"{0:s}".format("x")',
        '"{0:{1}}".format(1, 2)',
        '"{0}".format("☃😀")',
        "(1 + 2).unknown(1)",
        'num.int("bad")',
        "missing",
        "num",
        "[1,2]",
    ],
)
def test_edges(probe, text):
    compare(probe, text, display=True)


@pytest.mark.parametrize(
    "number", [0.0, -0.0, 1e-5, 1e-4, 1e15, 1e16, 1e20, 1e-300, 5e-324, -1.2345678901234567]
)
def test_double_display(probe, number):
    compare(probe, "x", {"x": Number(number)}, display=True)


def test_rounding_and_random_ratios(probe):
    rng = random.Random(29)
    for _ in range(80):
        a = rng.getrandbits(rng.randrange(1, 64)) * rng.choice([-1, 1])
        b = rng.getrandbits(rng.randrange(1, 64)) or 1
        compare(probe, "a / b", {"a": Number(a), "b": Number(b)})
    for n in [2**53 + 1, 2**53 + 3, 2**63 - 1, -(2**63)]:
        compare(probe, "x + 0.0", {"x": Number(n)})


def test_limits_and_contexts(probe):
    for text in ["3.0", "-2.9", "0", "100001", '"x"', "num.int(3.0)", "1e300", "-1e300"]:
        for context in ["integer", "dimension", "index"]:
            compare(probe, text, context=context, size=3, instance_path="board.r[1]")
    compare(probe, "3", context="index", size=3, stop=True)
    for text in ["1 << 8", "255", "256", "1e3", "num.int(1e3)", "1k"]:
        compare(probe, text, limits={"integer_bits": 8})
    compare(probe, '"☃😀"', limits={"string_length": 2})
    compare(probe, '"{0}{0}".format("☃😀")', limits={"string_length": 3})
    compare(probe, '"{0:20d}".format(1)', limits={"format_width": 10})
    compare(probe, "1+2*3", limits={"expression_depth": 1})


def test_namespace_binding(probe):
    compare(probe, "num.int(1.2)", symbols={})
    compare(probe, "num.int(1.2)", {"num": Number(2)})
    compare(probe, "n.floor(1.2)", symbols={"n": {"module": "noscheme", "name": "num"}})
    compare(
        probe,
        "noscheme.num.ceil(1.2)",
        symbols={"noscheme.num": {"module": "noscheme", "name": "num"}},
    )
    compare(probe, "num.int(1.2)", symbols={"num": {"module": "user", "name": "num"}})


def test_random_quantities(probe):
    rng = random.Random(14)
    for _ in range(50):
        value = rng.uniform(-1000, 1000) * 10.0 ** rng.randrange(-22, 22)
        assert math.isfinite(value)
        compare(probe, "q", {"q": Number(value)}, quantity="capacitance")


@pytest.mark.parametrize(
    "template",
    [
        "{0:00f}",
        "{0:0008.02f}",
        "{0:!f}",
        "{0!}",
        "{0!rr}",
        "{0!s}",
        "{0!x:f}",
        "{0:.}",
        "{0:0.0g}",
        "{0:00000g}",
        "{0:}",
        "{{0}}",
        "{{{0}}}",
        "{0[}",
        "{x{y}}",
        "{0!r:!}",
    ],
)
def test_format_edges(probe, template):
    compare(probe, json.dumps(template) + ".format(1.25)")


def test_more_double_boundaries(probe):
    rng = random.Random(41)
    for _ in range(100):
        value = math.ldexp(rng.uniform(-1, 1), rng.randrange(-1074, 1024))
        compare(probe, "x", {"x": Number(value)}, display=True)
    for value in [999.9995, 999999.5, 0.9999995, 9.999995e-19, 1e-300, (1 << 63) - 1]:
        compare(probe, "x", {"x": Number(value)}, quantity="resistance")


def test_long_literals_and_format_fields(probe):
    literal = "1" + "0" * 5000
    compare(probe, literal, display=True)
    compare(probe, literal + "R", display=True)
    compare(probe, json.dumps("{0:" + "9" * 10000 + "d}") + ".format(1)")
    compare(probe, json.dumps("{" + "9" * 10000 + "}") + ".format(1)")


@pytest.mark.parametrize("text,value", cases("test_signed_integer_boundaries"))
def test_signed_integer_boundaries(probe, text, value):
    compare(probe, text, display=True)


@pytest.mark.parametrize("text", cases("test_integer_overflow_is_diagnostic"))
def test_integer_overflow(probe, text):
    compare(probe, text)
