"""Canonical source layout: same tree, same comments, stable output."""

from pathlib import Path
import subprocess

import pytest

from noscheme.cli import main
from noscheme.diagnostics import DiagnosticError
from noscheme.formatter import format_source

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [
    ROOT / name
    for name in subprocess.run(
        ["git", "ls-files", "*.nosch"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    # The frozen experiment corpus keeps its historical grammar.
    if "/negative/" not in name and not name.startswith("tools/experiments/")
]


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_source_formats_to_the_same_tree_and_is_stable(path):
    # format_source itself rejects any result whose tree or comments differ.
    result = format_source(path.read_text(), str(path))
    assert format_source(result, str(path)) == result


@pytest.mark.parametrize(
    "path", [p for p in SOURCES if "examples" in p.parts], ids=lambda p: str(p.relative_to(ROOT))
)
def test_examples_are_formatted(path):
    assert format_source(path.read_text(), str(path)) == path.read_text()


def formats(source, expected):
    assert format_source(source) == expected
    assert format_source(expected) == expected


def test_spacing_and_indentation_are_canonical():
    expected = "component R:\n  params:\n    number value = 4k7\n    number scale = (1 + 2) * -3\n"
    formats(
        "component  R :\n  params:\n      number  value=4k7\n      number scale = (1+2)*-3\n",
        expected,
    )
    # Two spaces per level, whatever the source used.
    formats(expected.replace("  ", "    "), expected)
    formats(
        "block B:\n  global   net.ground  gnd\n  T<1k ,x:2>r[ 2 ]( a,b,1:c,* ) :\n    2 :d\n",
        "block B:\n  global net.ground gnd\n  T<1k, x: 2> r[2](a, b, 1: c, *):\n    2: d\n",
    )
    formats("extend  R< 1k >LedR\n", "extend R<1k> LedR\n")


def test_spellings_are_kept():
    source = 'block B:\n  T a:\n    note: "tab\\t\\u00e9"\n    values: [4.7k, 4k7, 4700, 1e3]\n'
    formats(source, source)


def test_blank_lines_separate_definitions_and_never_open_a_block():
    formats(
        "from x import a\ncomponent A:\n\n  params:\n    number v\n\n\n\n"
        "    number w\nextend A B\n\n\n",
        "from x import a\n\ncomponent A:\n  params:\n    number v\n\n    number w\n\nextend A B\n",
    )


def test_long_lists_break_one_per_line_with_a_trailing_comma():
    nets = ", ".join(f"net_{i}" for i in range(12))
    formats(
        f"block B:\n  Resistor<4k7> r({nets})\n",
        "block B:\n  Resistor<4k7> r(\n" + "".join(f"    net_{i},\n" for i in range(12)) + "  )\n",
    )
    values = ", ".join(f"value_{i}: {i}" for i in range(12))
    formats(
        f"extend Resistor<{values}> R\n",
        "extend Resistor<\n" + "".join(f"  value_{i}: {i},\n" for i in range(12)) + "> R\n",
    )


def test_a_trailing_comma_keeps_a_list_broken_and_its_absence_joins_it():
    formats(
        "block B:\n  R r(a,)\n  R s(\n    a,\n    b\n  )\n  R<\n    1k,\n  > t\n",
        "block B:\n  R r(\n    a,\n  )\n  R s(a, b)\n  R<\n    1k,\n  > t\n",
    )


def test_arguments_sections_and_pin_lists_keep_their_form():
    source = (
        "block B:\n  R<value: 1k> r(a, 2: b):\n    metadata:\n"
        "      pins:\n        [1,]: role.passive\n        [3]: role.passive\n"
    )
    formats(source, source)
    formats("block B:\n  R<> r()\n", "block B:\n  R r\n")


def test_comments_stay_with_their_code():
    formats(
        "// header\nblock B: // board\n  // first\n  T a: // net\n"
        "    note: 1\n    // end of net\n  R r(a, // first pin\n"
        "    b)\n// trailing\n",
        "// header\nblock B:  // board\n  // first\n  T a:  // net\n"
        "    note: 1\n    // end of net\n  R r(\n    a,  // first pin\n"
        "    b,\n  )\n// trailing\n",
    )


def test_block_comments_keep_their_text():
    source = "/* line one\n   line two */\nblock B:\n  /* note */\n  T a\n"
    formats(source, source)


@pytest.mark.parametrize(
    "source, message",
    [
        (
            "block B:\n  T a:\n    v: (1 + // c\n      2)\n",
            "inside this expression",
        ),
        ("block B:\n  T a:\n    v: x[ // c\n      0]\n", "inside this expression"),
        ("block B:\n  /* c */ T a\n", "must end its line"),
    ],
)
def test_comments_that_cannot_be_placed_are_reported(source, message):
    with pytest.raises(DiagnosticError) as error:
        format_source(source)
    assert error.value.diagnostics[0].code == "format.comment"
    assert message in error.value.diagnostics[0].message


def test_syntax_errors_are_not_formatted():
    with pytest.raises(DiagnosticError) as error:
        format_source("block B:\n  T a:\n")
    assert error.value.diagnostics[0].code == "syntax.invalid"


def test_cli_checks_diffs_and_writes(tmp_path, capsys):
    source = tmp_path / "main.nosch"
    source.write_text("block B:\n    T   a\n")
    (tmp_path / "noscheme.toml").write_text(
        'language_version = "0.1"\ntargets.default.root = "main.B"\ntargets.default.output_dir = "build"\nsource_paths = ["."]\n'
        'library_paths = []\noutput_dir = "build"\n'
    )
    (tmp_path / "build").mkdir()
    (tmp_path / "build/generated.nosch").write_text("not noscheme\n")
    assert main(["format", "--check", str(tmp_path)]) == 1
    assert main(["format", "--diff", str(source)]) == 0
    assert "+  T a" in capsys.readouterr().out
    assert source.read_text() == "block B:\n    T   a\n"
    assert main(["format", str(tmp_path)]) == 0
    assert source.read_text() == "block B:\n  T a\n"
    assert main(["format", "--check", str(tmp_path)]) == 0
