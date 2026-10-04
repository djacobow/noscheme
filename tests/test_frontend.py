import json
from dataclasses import fields, is_dataclass
from pathlib import Path

import pytest

from noscheme import ast as a, parse
from noscheme.cli import main
from noscheme.diagnostics import DiagnosticError, render_json, render_text

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["no_connect", "global", "true", "false", "number", "string"])
@pytest.mark.parametrize(
    "source",
    [
        'component NAME:\n    metadata:\n        ref_prefix: "U"\n',
        "board B:\n    net NAME: nettype.passive\n",
        "board B:\n    inst NAME: Part()\n",
        "component P:\n    params:\n        NAME: number = 1\n",
        "block B:\n    ports:\n        NAME: nettype.passive\n",
        "bundle B:\n    NAME: nettype.passive\n",
        "component P:\n    pins:\n        NAME:\n            role: role.passive\n",
        "from library import R as NAME\n",
        "from library import NAME\n",
        "import NAME\n",
    ],
)
def test_reserved_declaration_names_are_located(source, name):
    source = source.replace("NAME", name)
    with pytest.raises(DiagnosticError) as error:
        parse(source, "reserved.nosch")
    diagnostic = error.value.diagnostics[0]
    if source.startswith("bundle B:\n    global:"):
        # At declaration level the lexer already treats global as a modifier.
        assert diagnostic.code == "syntax.invalid"
    else:
        assert diagnostic.code == "syntax.reserved-name"
        assert source[diagnostic.span.start_pos : diagnostic.span.end_pos] == name


def test_quoted_physical_pins_and_metadata_keys_are_not_reserved():
    parse("""component P:
    pins:
        "global":
            aliases: ["no_connect"]
            role: role.passive
    metadata:
        custom:
            true: false
board B:
    net inst: nettype.passive
    inst net: P(
        connections:
            "global": inst
    )
""")


def nodes(value):
    if isinstance(value, a.Node):
        yield value
    if is_dataclass(value):
        for field in fields(value):
            yield from nodes(getattr(value, field.name))
    elif isinstance(value, tuple):
        for child in value:
            yield from nodes(child)


@pytest.mark.parametrize(
    "path",
    [
        ROOT / "examples/stm32h523/stm32h523.nosch",
        *sorted((ROOT / "tools/experiments/corpus/positive").glob("*.nosch")),
    ],
)
def test_reference_and_corpus_are_typed_and_located(path):
    source = path.read_text()
    module = parse(source, str(path))
    assert isinstance(module, a.Module)
    for node in nodes(module):
        span = node.span
        assert span.filename == str(path)
        assert 0 <= span.start_pos <= span.end_pos <= len(source)
        assert span.line == source[: span.start_pos].count("\n") + 1
        if isinstance(node, (a.Name, a.Number, a.PinKey)):
            spelling = node.text if isinstance(node, a.Name) else node.spelling
            assert source[span.start_pos : span.end_pos] == spelling
    assert json.loads(json.dumps(a.to_data(module)))["node"] == "Module"


def test_ast_preserves_kind_defaults_aliases_and_pin_spelling():
    m = parse(
        'component R:\n    params:\n        value: string = "1k"\n    pins:\n        01:\n            aliases: [A, "EP-1"]\n        1:\n            role: role.passive\ncomponent S extends R:\n    defaults:\n        value: "2k"\n'
    )
    base, child = m.items
    assert base.kind == "component"
    assert base.body[0].entries[0].default.value == "1k"
    pins = base.body[1].entries
    assert [p.key.first.text for p in pins] == ["01", "1"]
    assert pins[0].fields[0].value.items[1].value == "EP-1"
    assert child.base[0].text == "R"
    assert child.body[0].name == "defaults"


def test_expression_associativity_and_postfix():
    m = parse(
        'component P:\n    params:\n        n: number = 8 - 3 - 1 << 2 * 3\n        s: string = "{0}".format(f.nF(10))\n'
    )
    n, s = m.items[0].body[0].entries
    assert n.default.operator == "<<"
    assert n.default.left.operator == "-"
    assert n.default.left.left.operator == "-"
    assert n.default.right.operator == "*"
    assert isinstance(s.default, a.Call)
    assert s.default.callee.name.text == "format"
    assert s.default.arguments[0].callee.name.text == "nF"


def test_endpoint_indices_ranges_and_caller_scope():
    m = parse(
        "board B:\n    inst r[2]: R(\n        connections:\n            [1].bus[2].data: global.n[0:4]\n            11..20: pins\n    )\n"
    )
    inst = m.items[0].body[0]
    first, second = inst.sections[0].entries
    assert first.endpoint.instance_indices[0].start.spelling == "1"
    assert first.endpoint.head.first.text == "bus"
    assert first.endpoint.selectors[0].start.spelling == "2"
    assert first.endpoint.selectors[1].text == "data"
    assert first.target.base.base.text == "global"
    assert first.target.selection.stop.spelling == "4"
    assert second.endpoint.head.last.text == "20"


@pytest.mark.parametrize(
    "source",
    [
        'component C:\n    pins:\n        1:\n            aliases: [a]\n        "1":\n            aliases: [b]\n',
        "component C:\n    metadata:\n        custom:\n            item: 1\n            item: 2\n",
        "component C:\n    pins:\n        1:\n            role: r\n    pins:\n        2:\n            role: r\n",
        'board B:\n    inst r: R(\n        params:\n            value: "a"\n            value: "b"\n    )\n',
    ],
)
def test_duplicates_report_both_locations(source):
    with pytest.raises(DiagnosticError) as failure:
        parse(source, "duplicate.nosch")
    d = failure.value.diagnostics[0]
    assert d.code == "syntax.duplicate"
    assert d.span.line > d.related[0].span.line
    assert "previous declaration" in render_text([d])
    result = json.loads(render_json([d]))["diagnostics"][0]
    assert result["related"][0]["span"]["filename"] == "duplicate.nosch"


@pytest.mark.parametrize(
    "source,code",
    [
        ("component C:\n    net n: T\n", "syntax.declaration-context"),
        ("block B:\n    global net n: T\n", "syntax.global-context"),
        ("board B:\n    pins:\n        1:\n            role: r\n", "syntax.section-context"),
        ('component C:\n    metadata:\n        value: string = "x"\n', "syntax.default-context"),
        ("component C:\n    params:\n        value:\n            nested: 1\n", "syntax.parameter"),
        ("board B:\n\tnet n: T\n", "syntax.indentation"),
        ('component C:\n    metadata:\n        value: "\\q"\n', "syntax.string"),
        ("component C extends P:\n    ports:\n        n: T\n", "syntax.section-context"),
        (
            "bundle B extends A:\n    metadata:\n        custom:\n            x: 1\n",
            "syntax.inheritance-context",
        ),
    ],
)
def test_invalid_structure(source, code):
    with pytest.raises(DiagnosticError) as failure:
        parse(source)
    assert code in [d.code for d in failure.value.diagnostics]


@pytest.mark.parametrize(
    "name,expected",
    json.loads((ROOT / "tools/experiments/corpus/negative/expectations.json").read_text()).items(),
)
def test_negative_corpus_locations(name, expected):
    path = ROOT / "tools/experiments/corpus/negative" / name
    with pytest.raises(DiagnosticError) as failure:
        parse(path.read_text(), str(path))
    span = failure.value.diagnostics[0].span
    assert (span.line, span.column) == (expected["line"], expected["column"])


def test_empty_and_crlf_sources_and_comment_offsets():
    assert parse("").items == ()
    source = "/* a\r\n b */\r\nboard B:\r\n    net n: T"
    m = parse(source)
    assert m.items[0].name.span.line == 3
    assert m.items[0].span.end_pos == len(source)
    assert parse("//\tcomment\nboard B:\n    net n: T\n")


def test_cli_atomic_artifact_and_diagnostics(tmp_path, capsys):
    source = tmp_path / "main.nosch"
    output = tmp_path / "out" / "ast.json"
    source.write_text("board B:\n    net n: T\n")
    assert main(["parse", str(source), "--output", str(output)]) == 0
    before = output.read_bytes()
    assert json.loads(before)["module"]["node"] == "Module"
    source.write_text("board B:\n    net n = bad\n")
    assert main(["parse", str(source), "--output", str(output), "--diagnostics", "json"]) == 1
    assert output.read_bytes() == before
    error = json.loads(capsys.readouterr().err)["diagnostics"][0]
    assert error["span"]["line"] == 2
    assert main(["parse", str(source), "--output", str(source)]) == 2
    assert main(["parse", str(tmp_path / "absent.nosch")]) == 2


def test_syntax_diagnostics_use_source_spellings():
    from noscheme.syntax import expected_syntax, parse, SyntaxDiagnostic

    assert expected_syntax(["_INST_RPAR", "_RPAR", "NAME", "_NL", "$END"]) == [
        "')'",
        "a name",
        "end of file",
        "end of line",
    ]
    with pytest.raises(SyntaxDiagnostic) as error:
        parse("board B\n", "broken.nosch")
    assert "expected ':'" in str(error.value)
    assert "COLON" not in str(error.value)
    assert error.value.filename == "broken.nosch"
    assert error.value.line == 1
