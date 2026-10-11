"""Interchangeable pin groups and one-line instances."""

import pytest

from noscheme.diagnostics import DiagnosticError
from noscheme.kicad.convert import convert_library
from noscheme.kicad.sexpr import form
from test_circuit import compile_source, make
from test_convert import library, pin

SOURCE = """component R:
  params:
    resistance value
  pins:
    1..2: role.passive
  metadata:
    interchangeable: [1, 2]
component Tvs:
  pins:
    [1, 2]: role.passive
    3 (gnd):
      role: role.ground
  metadata:
    interchangeable: [1, 2]
block B:
  net.passive a
  net.passive b
  net.ground g
  R<4.7k> r(a, b)
  R<value: 1k> caps[2](a,
    b)
  Tvs esd(a, no_connect, gnd: g)
"""


def nets(circuit):
    return {
        f"{p['identity']}.{name}": pin.get("net")
        for p in circuit["parts"]
        for name, pin in p["pins"].items()
    }


def test_positional_nets_bind_the_group_in_declared_order(tmp_path):
    circuit = compile_source(tmp_path, SOURCE)
    bound = nets(circuit)
    assert (bound["r.1"], bound["r.2"]) == ("a", "b")
    assert (bound["caps[1].1"], bound["caps[1].2"]) == ("a", "b")
    assert (bound["esd.1"], bound["esd.2"], bound["esd.3"]) == ("a", None, "g")
    values = {p["identity"]: p["value"]["value"] for p in circuit["parts"] if p["value"]}
    assert values["r"] == "4.7kOhm" and values["caps[0]"] == "1kOhm"


@pytest.mark.parametrize(
    "old,new,code",
    [
        ("  R<4.7k> r(a, b)", "  R<4.7k> r(a)", "resolve.positional"),
        (
            "  metadata:\n    interchangeable: [1, 2]\ncomponent Tvs",
            "component Tvs",
            "resolve.positional",
        ),
        (
            "[1, 2]: role.passive",
            "1: role.passive\n    2: role.ground",
            "metadata.interchangeable",
        ),
        (
            "    interchangeable: [1, 2]\nblock",
            "    interchangeable: [1]\nblock",
            "metadata.interchangeable",
        ),
        (
            "    interchangeable: [1, 2]\nblock",
            "    interchangeable: [1, 9]\nblock",
            "metadata.interchangeable",
        ),
        ("esd(a, no_connect, gnd: g)", "esd(a, no_connect, 1: g, gnd: g)", "connection"),
    ],
)
def test_invalid_groups_and_positional_instances(tmp_path, old, new, code):
    assert old in SOURCE
    with pytest.raises(DiagnosticError) as error:
        compile_source(tmp_path, SOURCE.replace(old, new))
    assert any(d.code.startswith(code) for d in error.value.diagnostics)


def test_specialization_can_clear_an_inherited_group(tmp_path):
    pull = """extend R Pull:
  pins:
    2:
      pull: pull.up
block B:"""
    source = "from noscheme import pull\n" + SOURCE.replace("block B:", pull)
    with pytest.raises(DiagnosticError, match="interchangeable: \\[\\]"):
        compile_source(tmp_path, source)
    cleared = source.replace(
        "      pull: pull.up\n",
        "      pull: pull.up\n  metadata:\n    interchangeable: []\n",
    )
    compile_source(tmp_path, cleared)


def test_named_inline_connections_do_not_require_a_group(tmp_path):
    source = SOURCE.replace("interchangeable: [1, 2]", "interchangeable: []")
    source = source.replace("r(a, b)", "r(1: a, 2: b)")
    source = source.replace("caps[2](a,\n    b)", "caps[2](1: a, 2: b,)")
    source = source.replace("esd(a, no_connect, gnd: g)", "esd(1: a, 2: no_connect, gnd: g)")
    assert nets(compile_source(tmp_path, source)) == nets(compile_source(tmp_path, SOURCE))


def test_group_aliases_order_and_inherited_instances(tmp_path):
    source = SOURCE.replace(
        "    1..2: role.passive",
        """    1 (left): role.passive
    2 (right): role.passive""",
    )
    source = source.replace("interchangeable: [1, 2]", "interchangeable: [right, left]", 1)
    source = source.replace(
        "block B:",
        """block Base:
  ports:
    net.passive input
    net.passive output
  R<value: 1k> r(input, output)
extend Base Derived:
  metadata:
    custom:
      note: "Inherited compact instance"
block B:""",
    )
    source += "  Derived block(input: a, output: b)\n"
    bound = nets(compile_source(tmp_path, source))
    assert (bound["r.1"], bound["r.2"]) == ("b", "a")
    assert (bound["block.r.1"], bound["block.r.2"]) == ("b", "a")


@pytest.mark.parametrize(
    "old,new,code",
    [
        ("[1, 2]", '[1, "1"]', "metadata.interchangeable"),
        ("R<4.7k> r(a, b)", "R<4.7k> r(a, b, a)", "resolve.positional"),
        ("R<4.7k> r(a, b)", "R<4.7k> r(missing, b)", "resolve.net"),
        ("R<4.7k> r(a, b)", "R<value: 4.7k, value: 1k> r(a, b)", "syntax.duplicate"),
        ("R<4.7k> r(a, b)", "R<4.7k, value: 1k> r(a, b)", "resolve.parameter"),
    ],
)
def test_compact_instance_errors(tmp_path, old, new, code):
    with pytest.raises(DiagnosticError) as error:
        compile_source(tmp_path, SOURCE.replace(old, new, 1))
    assert any(d.code.startswith(code) for d in error.value.diagnostics)


def test_positional_array_slices_globals_and_bundle_fields(tmp_path):
    source = (
        SOURCE[: SOURCE.index("block B:")]
        + """bundle Pair:
  net.passive a
  net.passive b
block B:
  global net.ground gnd
  net.passive signals[4]
  Pair pair
  R<value: 1k> r[2](signals[1:3], global.gnd)
  R<value: 4.7k> bridge(pair.a, pair.b)
"""
    )
    bound = nets(compile_source(tmp_path, source))
    assert bound["r[0].1"] == "signals[1]"
    assert bound["r[1].1"] == "signals[2]"
    assert bound["r[1].2"] == "gnd"
    assert bound["bridge.1"] == "pair.a"
    assert bound["bridge.2"] == "pair.b"


@pytest.mark.parametrize(
    "arguments",
    [
        "a, b",
        "1: a, 2: b",
        "a, /* keep, this */ b, // keep this too\n  ",
    ],
)
def test_compact_instance_rename_keeps_arguments_and_adds_identity(tmp_path, arguments):
    from noscheme.lsp_rename import RenameSession
    from test_lsp_rename import apply

    source = SOURCE.replace("r(a, b)", f"r({arguments})")
    make(tmp_path, source)
    path = tmp_path / "main.nosch"
    session = RenameSession(tmp_path, {})
    text = session.sources[path]
    result = apply(session.rename(path, text.index(" r(") + 1, "renamed"), session.sources)
    # Arguments, including comments, stay as written; a metadata section follows.
    assert f'R<4.7k> renamed({arguments}):\n    metadata:\n      identity: "r"' in result[path]
    # RenameSession validates elaboration and physical identities before returning edits.
    assert path.read_text() == text


def test_inline_references_support_net_and_parameter_rename(tmp_path):
    from noscheme.lsp_rename import RenameSession
    from test_lsp_rename import apply

    make(tmp_path, SOURCE)
    path = tmp_path / "main.nosch"
    session = RenameSession(tmp_path, {})
    text = session.sources[path]
    target = session.prepare(path, text.index("r(a,") + 2)
    assert target.name == "a"
    result = apply(session.rename(path, target.span.start_pos, "signal"), session.sources)
    assert "r(signal, b)" in result[path] and "esd(signal," in result[path]
    result = apply(session.rename(path, text.index("value: 1k"), "resistance"), session.sources)
    assert "R<resistance: 1k>" in result[path] and "resistance value" not in result[path]


def test_converter_marks_only_symmetric_passive_symbols(tmp_path):
    def symbol(name, reference, names=("~", "~"), description=""):
        return form(
            "symbol",
            name,
            form("property", "Reference", reference),
            form("property", "Value", name),
            form("property", "Description", description),
            form("symbol", name + "_1_1", pin("1", names[0]), pin("2", names[1])),
        )

    source = library(
        tmp_path / "Device.kicad_sym",
        symbol("R", "R"),
        symbol("L", "L"),
        symbol("Ferrite_Bead", "FB"),
        symbol("C", "C", description="Unpolarized capacitor"),
        symbol("C_Nonpolarized", "C", description="Non-polarized capacitor"),
        symbol("C_Polarized", "C", description="Polarized capacitor"),
        symbol("C_Polarised", "C", description="Polarised capacitor"),
        symbol("D_TVS", "D", ("A1", "A2")),
        symbol("D", "D", ("K", "A")),
    )
    convert_library(source, tmp_path / "out")
    text = (tmp_path / "out/Device.nosch").read_text()
    marked = {
        block.split(":", 1)[0]
        for block in text.split("component ")[1:]
        if 'interchangeable: ["1", "2"]' in block
    }
    assert marked == {"R", "L", "Ferrite_Bead", "C", "C_Nonpolarized", "D_TVS"}


def test_arguments_and_sections_describe_one_instance(tmp_path):
    source = SOURCE.replace(
        "  Tvs esd(a, no_connect, gnd: g)\n",
        """  Tvs esd(a, no_connect,):
    gnd: g
    metadata:
      custom:
        dnp: true
""",
    )
    circuit = compile_source(tmp_path, source)
    assert nets(circuit) == nets(compile_source(tmp_path, SOURCE))
    esd = next(p for p in circuit["parts"] if p["identity"] == "esd")
    assert esd["metadata"]["custom"]["value"]["dnp"]["value"] is True


@pytest.mark.parametrize(
    "addition,code",
    [
        ("    params:\n      value: 1k\n", "resolve.parameter"),
        ("    1: b\n", "connection"),
    ],
)
def test_arguments_and_sections_cannot_bind_twice(tmp_path, addition, code):
    source = SOURCE.replace("  R<4.7k> r(a, b)\n", "  R<4.7k> r(a, b):\n" + addition)
    with pytest.raises(DiagnosticError) as error:
        compile_source(tmp_path, source)
    assert any(d.code.startswith(code) for d in error.value.diagnostics)


def test_elements_override_argument_parameters(tmp_path):
    source = SOURCE.replace(
        "  R<value: 1k> caps[2](a,\n    b)\n",
        """  R<value: 1k> caps[2](a, b):
    elements:
      [1]:
        params:
          value: 2.2k
""",
    )
    parts = compile_source(tmp_path, source)["parts"]
    values = {p["identity"]: p["value"]["value"] for p in parts if p["value"]}
    assert (values["caps[0]"], values["caps[1]"]) == ("1kOhm", "2.2kOhm")


def test_bare_instances_still_need_required_parameters(tmp_path):
    for spelling in ("R r", "R r()", "R<> r"):
        source = SOURCE.replace("R<4.7k> r(a, b)", spelling)
        with pytest.raises(DiagnosticError, match="value"):
            compile_source(tmp_path, source)


def test_rename_adds_identity_to_an_existing_metadata_section(tmp_path):
    from noscheme.lsp_rename import RenameSession
    from test_lsp_rename import apply

    source = SOURCE.replace(
        "  R<4.7k> r(a, b)\n",
        '  R<4.7k> r(a, b):\n    metadata:\n      custom:\n        note: "x"\n',
    )
    make(tmp_path, source)
    path = tmp_path / "main.nosch"
    session = RenameSession(tmp_path, {})
    text = session.sources[path]
    result = apply(session.rename(path, text.index(" r(") + 1, "renamed"), session.sources)[path]
    assert '  R<4.7k> renamed(a, b):\n    metadata:\n      identity: "r"\n      custom:\n' in result
