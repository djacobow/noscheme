import json
from pathlib import Path
import pytest

from noscheme import parse
from noscheme.diagnostics import DiagnosticError
from noscheme.evaluate import Evaluator, Limits, Number, evaluate_project
from noscheme.resolve import resolve_project
from noscheme.project import load_project


def make(tmp_path, declarations="", board="board B:\n  net.passive n\n"):
    (tmp_path / "noscheme.toml").write_text(
        'language_version = "0.1"\nboard = "main.B"\nsource_paths = ["."]\nlibrary_paths = []\noutput_dir = "build"\n'
    )
    (tmp_path / "main.nosch").write_text("from noscheme import num, net\n" + declarations + board)
    return tmp_path


@pytest.fixture
def evaluator(tmp_path):
    return Evaluator(load_project(make(tmp_path)))


def expression(text):
    return (
        parse("component C:\n  params:\n    number x = " + text + "\n")
        .items[0]
        .body[0]
        .entries[0]
        .default
    )


def run(evaluator, text, env=None):
    return evaluator.evaluate(expression(text), "main", env or {})


@pytest.mark.parametrize(
    "text,value,tag",
    [
        ("3", 3, "integer"),
        ("3e0", 3.0, "double"),
        ("4 / 2", 2.0, "double"),
        ("2 + 3.0", 5.0, "double"),
        ("8 - 3 - 1", 4, "integer"),
        ("-8 >> 2", -2, "integer"),
        ("1 << 5", 32, "integer"),
        ("num.int(-2.9)", -2, "integer"),
        ("num.ceil(-2.9)", -2, "integer"),
        ("num.floor(-2.1)", -3, "integer"),
        ("num.round(2.5)", 3, "integer"),
        ("num.round(-2.5)", -3, "integer"),
        ("num.round(2.4999999999999996)", 2, "integer"),
        ("num.round(4503599627370495.0)", 4503599627370495, "integer"),
    ],
)
def test_numbers(evaluator, text, value, tag):
    result = run(evaluator, text)
    assert result.value == value and result.tag == tag
    assert evaluator.warnings == []


def test_integer_context_warns_even_when_integral(evaluator):
    expr = expression("3.0")
    assert evaluator.dimension(evaluator.evaluate(expr, "main", {}), expr.span) == 3
    assert evaluator.warnings[0].code == "number.implicit-integer"
    assert evaluator.integer(run(evaluator, "-2.9"), expr.span) == -2
    count = len(evaluator.warnings)
    assert evaluator.dimension(run(evaluator, "num.int(3.0)"), expr.span) == 3
    assert len(evaluator.warnings) == count


@pytest.mark.parametrize(
    "text,code",
    [
        ("1 / 0", "number.divide-by-zero"),
        ("1e309", "number.nonfinite"),
        ("1e308 * 1e308", "number.nonfinite"),
        ("3.0 << 1", "number.shift"),
        ("1 << -1", "number.shift"),
        ("1 << 70000", "number.limit"),
        ('"a" + 1', "expression.type"),
        ('num.int("x")', "expression.type"),
        ("num.round()", "expression.arguments"),
        ("num.int(1,2)", "expression.arguments"),
        ("num.int(x=1)", "expression.arguments"),
        ("f.nF(10)", "expression.call"),
        ("4.7k7", "syntax.number"),
        ("num.typo(1)", "expression.call"),
        ('"{x.__class__}".format(x=3)', "format.field"),
        ('"{0[0]}".format("x")', "format.field"),
        ('"{0!r}".format(1)', "format.field"),
        ('"{}".format(1)', "format.field"),
        ('"{x}".format(1)', "format.argument"),
        ('"{0:{1}}".format(1,2)', "format.spec"),
        ('"{0:.2d}".format(1)', "format.type"),
        ('"{0:d}".format(1.0)', "format.type"),
        ('"{0:999999f}".format(1)', "format.limit"),
        ('"{0:.101f}".format(1)', "format.limit"),
        ('"{".format(1)', "format.invalid"),
    ],
)
def test_invalid_expressions(evaluator, text, code):
    with pytest.raises(DiagnosticError) as exc:
        run(evaluator, text)
    assert exc.value.diagnostics[0].code == code


@pytest.mark.parametrize(
    "text,result",
    [
        ('"{{{0}}}".format(3)', "{3}"),
        ('"{resistance:.1f} Ohm".format(resistance=4.75)', "4.8 Ohm"),
        ('"{0:04d}".format(-3)', "-003"),
        ('"{label}: {0}".format(10,label="R")', "R: 10"),
    ],
)
def test_formatting(evaluator, text, result):
    assert run(evaluator, text) == result


@pytest.mark.parametrize(
    "text,value",
    [
        ("4.7k", 4700),
        ("4k7", 4700),
        ("1k0", 1000),
        ("47R", 47),
        ("4R7", 4.7),
        ("0R1", 0.1),
        ("100n", 1e-7),
        ("1u5", 1.5e-6),
        ("10f", 1e-14),
        ("1a", 1e-18),
        ("2.2M", 2200000),
        ("3P", 3 * 10**15),
        ("-10m", -0.01),
        ("2 * 4.7k", 9400),
    ],
)
def test_scaled_literals_are_exact(evaluator, text, value):
    result = run(evaluator, text)
    assert result.value == value and type(result.value) is type(value)


@pytest.mark.parametrize(
    "value,quantity,text",
    [
        (4.7 * 3 * 1000, "resistance", "14.1kOhm"),
        ((0.1 + 0.2) * 1e-6, "capacitance", "300nF"),
        (1.23456789, "resistance", "1.23457Ohm"),
        (999999.9, "resistance", "1MOhm"),
        (-1.23456789e-12, "capacitance", "-1.23457pF"),
        (-0.0, "inductance", "0H"),
        (0.12, "inductance", "120mH"),
        (123456789, "resistance", "123.457MOhm"),
        (3.3 * 1.05, "voltage", "3.465V"),
        (1e-7, "capacitance", "100nF"),
        (1e19, "frequency", "10000PHz"),
    ],
)
def test_quantity_display_uses_engineering_prefixes(value, quantity, text):
    from noscheme.units import format_quantity

    assert format_quantity(value, quantity) == text


def test_arithmetic_and_explicit_formats_are_not_rounded(evaluator):
    assert run(evaluator, "4.7 * 3").value == 14.100000000000001
    assert run(evaluator, '"{0}".format(4.7 * 3)') == "14.100000000000001"
    assert run(evaluator, '"{0:.8f}Ohm".format(1.23456789)') == "1.23456789Ohm"


def test_big_integers_and_output_limits(evaluator):
    result = run(evaluator, "1 << 16000")
    assert result.value.bit_length() == 16001
    text = evaluator.display(result, expression("0").span)
    assert len(text) > 4300
    literal = run(evaluator, text)
    assert literal == result
    evaluator.limits = Limits(string_length=10)
    with pytest.raises(DiagnosticError, match="length limit"):
        run(evaluator, '"{0}{0}".format("123456")')


def test_named_argument_syntax():
    for text in ['"x".format(a=1,a=2)', '"x".format(a=1,2)']:
        with pytest.raises(DiagnosticError):
            expression(text)


def test_binding_order_forwarding_and_override(tmp_path):
    p = make(
        tmp_path,
        """component C:
  params:
    number a = b * 2
    number b = 3
    string label = "{0}".format(a)
  metadata:
    value: label
""",
        """board B:
  C<b: 4> c
""",
    )
    report, warnings = evaluate_project(*resolve_project(p))
    params = report["instances"][1]["parameters"]
    assert params["a"]["value"] == "8"
    assert params["label"]["value"] == "8"
    assert not warnings


@pytest.mark.parametrize(
    "params,args,code",
    [
        ("number x", "", "parameter.required"),
        ("number x", 'x: "bad"', "parameter.type"),
        ("string x = 3", "", "parameter.type"),
        ("number x = y\n    number y = x", "", "parameter.cycle"),
    ],
)
def test_binding_errors(tmp_path, params, args, code):
    body = f"board B:\n  C<{args}> c\n" if args else "board B:\n  C c\n"
    p = make(tmp_path, "component C:\n  params:\n    " + params + "\n", body)
    with pytest.raises(DiagnosticError) as exc:
        evaluate_project(*resolve_project(p))
    assert exc.value.diagnostics[0].code == code
    assert exc.value.diagnostics[0].instance_path == "B.c"


def test_override_does_not_evaluate_bad_default(tmp_path):
    p = make(
        tmp_path,
        "component C:\n  params:\n    number x = 1 / 0\n",
        "board B:\n  C<x: 2> c\n",
    )
    assert (
        evaluate_project(*resolve_project(p))[0]["instances"][1]["parameters"]["x"]["value"] == "2"
    )


def test_blink_template_evaluation():
    root = Path(__file__).resolve().parents[1]
    report, _ = evaluate_project(*resolve_project(root / "examples/blink"))
    assert len(report["instances"]) == 5  # Board, header, array template, resistor, LED.
    indicator = next(x for x in report["instances"] if x["path"] == "Blink.indicators")
    assert indicator["dimensions"] == [2]
    assert indicator["parameters"]["resistance"]["value"] == "1000"
    resistor = next(x for x in report["instances"] if x["path"] == "Blink.indicators.resistor")
    assert resistor["values"]["value"]["value"] == "1kOhm"
    assert report["array_representation"] == "unexpanded declaration templates"


def test_indices_and_dimensions(evaluator):
    span = expression("0").span
    assert evaluator.index(Number(3.9), span, 4) == 3
    assert evaluator.index(Number(4), span, 4, stop=True) == 4
    for value in [Number(-1), Number(4)]:
        with pytest.raises(DiagnosticError):
            evaluator.index(value, span, 4)
    for value in [Number(0), Number(-1)]:
        with pytest.raises(DiagnosticError):
            evaluator.dimension(value, span)


def test_instance_value_override_uses_caller_scope(tmp_path):
    p = make(
        tmp_path,
        """component C:
  params:
    string x = "child"
  metadata:
    value: 1 / 0
block Wrapper:
  params:
    string x = "caller"
  C c:
    metadata:
      value: x
""",
        "board B:\n  Wrapper w()\n",
    )
    report, _ = evaluate_project(*resolve_project(p))
    child = report["instances"][-1]
    assert child["parameters"]["x"]["value"] == "child"
    assert child["values"]["value"]["value"] == "caller"


def test_inherited_defaults_evaluate_in_origin_module(tmp_path):
    p = make(
        tmp_path,
        'from lib import C\nextend C D:\n  metadata:\n    custom:\n      note: "child"\n',
        "board B:\n  D d()\n",
    )
    (p / "main.nosch").write_text(
        (p / "main.nosch").read_text().replace("num, f, nettype", "nettype")
    )
    (p / "lib.nosch").write_text("component C:\n  params:\n    capacitance value = 10n\n")
    report, _ = evaluate_project(*resolve_project(p))
    assert report["instances"][1]["values"]["value"]["value"] == "10nF"


def test_cli_warnings_are_nonblocking(tmp_path, capsys):
    from noscheme.cli import main

    p = make(
        tmp_path,
        "component C:\n  params:\n    number x = 1\n",
        "board B:\n  C c[2.0]()\n",
    )
    assert main(["evaluate", "--project", str(p), "--diagnostics", "json"]) == 0
    result = capsys.readouterr()
    assert json.loads(result.out)["instances"][1]["dimensions"] == [2]
    warning = json.loads(result.err)["diagnostics"][0]
    assert warning["code"] == "number.implicit-integer"
    assert warning["instance_path"] == "B.c"


def test_connection_index_evaluation_and_warning(tmp_path):
    p = make(
        tmp_path,
        'component C:\n  pins:\n    1:\n      custom:\n        note: "pin"\n',
        "board B:\n  net.passive n[4]\n  C c:\n    1: n[3.0]\n",
    )
    report, warnings = evaluate_project(*resolve_project(p))
    assert report["instances"][0]["connection_selections"]["c"][0]["start"] == 3
    assert warnings[0].code == "number.implicit-integer"


def test_recursive_instantiation_reports_error(tmp_path):
    p = make(
        tmp_path,
        "block Recursive:\n  Recursive child()\n",
        "board B:\n  Recursive r()\n",
    )
    with pytest.raises(DiagnosticError) as exc:
        evaluate_project(*resolve_project(p))
    assert exc.value.diagnostics[0].code == "instance.cycle"
