import json
from pathlib import Path

import pytest
from lark import Token

from tools.experiments.parser.parse import SyntaxDiagnostic, located_tree, mask_comments, parse

ROOT = Path(__file__).resolve().parents[3]
CORPUS = ROOT / "tools/experiments/corpus"


@pytest.mark.parametrize(
    "path",
    sorted((CORPUS / "positive").glob("*.nosch")),
)
def test_positive(path):
    tree = parse(path.read_text(), str(path))
    assert tree.data == "start"
    assert located_tree(tree, str(path))["span"]["filename"] == str(path)


@pytest.mark.parametrize(
    "name,expected", json.loads((CORPUS / "negative/expectations.json").read_text()).items()
)
def test_negative_locations(name, expected):
    path = CORPUS / "negative" / name
    with pytest.raises(SyntaxDiagnostic) as failure:
        parse(path.read_text(), str(path))
    error = failure.value
    assert (error.line, error.column) == (expected["line"], expected["column"])
    assert error.filename == str(path)


def test_pin_spelling_and_spans():
    source = 'component C:\n    pins:\n        01:\n            aliases: [A1, "01"]\n'
    tree = parse(source, "pins.nosch")
    tokens = list(tree.scan_values(lambda n: isinstance(n, Token)))
    pin = next(t for t in tokens if t.type == "INT")
    assert str(pin) == "01"
    assert (pin.line, pin.column, pin.end_column) == (3, 9, 11)
    assert source[pin.start_pos : pin.end_pos] == "01"
    for token in tokens:
        assert source[token.start_pos : token.end_pos] == str(token)


def test_comment_mask_preserves_offsets_and_strings():
    source = '"http://x/*y*/" /* hi\n multiline */ // end\n'
    result = mask_comments(source, "comments.nosch")
    assert len(result) == len(source)
    assert result.startswith('"http://x/*y*/"')
    assert [i for i, c in enumerate(source) if c == "\n"] == [
        i for i, c in enumerate(result) if c == "\n"
    ]


def test_comments_do_not_nest():
    assert mask_comments("/* a /* b */ c */", "x") == "             c */"


def test_no_final_newline_and_crlf():
    parse("board B:\r\n    net a: nettype.digital")


@pytest.mark.parametrize("body", ["", "\n    ", "\n        // no sections\n    "])
def test_empty_instance(body):
    parse("board B:\n    inst r: R" + body + "\n")


def test_error_after_comments_points_to_original_source():
    source = (
        "/* first\nsecond */\nboard B:\n    inst r: R\n        connections:\n            1 = bad\n"
    )
    with pytest.raises(SyntaxDiagnostic) as failure:
        parse(source, "original.nosch")
    assert (failure.value.line, failure.value.column) == (6, 15)


def test_precedence_is_structural_not_evaluated():
    tree = parse("component P:\n    params:\n        n: number = 1 + 2 * 3 << 1\n")
    shift = next(tree.find_data("shift"))
    assert shift.children[0].data == "sum"
    assert shift.children[0].children[2].data == "product"


@pytest.mark.parametrize(
    "expression", ["f.nF((10 + 20))", "num.round(-2.5)", "f.nF(\n  10 +\n  20\n)", "1 << 2 >> 1"]
)
def test_expression_delimiters_inside_instance(expression):
    parse("board B:\n    inst r: R\n        params:\n            value: " + expression + "\n")
