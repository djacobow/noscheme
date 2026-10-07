"""Interchangeable pin groups and one-line instances."""

import pytest

from noscheme.diagnostics import DiagnosticError
from noscheme.kicad.convert import convert_library
from noscheme.kicad.sexpr import form
from test_circuit import compile_source, make
from test_convert import library, pin

SOURCE = """component R:
    params:
        value: resistance
    pins:
        1..2: role.passive
    metadata:
        interchangeable: [1, 2]
component Tvs:
    pins:
        [1, 2]: role.passive
        3:
            aliases: [gnd]
            role: role.ground
    metadata:
        interchangeable: [1, 2]
board B:
    net a: nettype.passive
    net b: nettype.passive
    net g: nettype.ground
    inst r: R(a, b, value=4.7k)
    inst caps[2]: R(a,
        b, value=1k)
    inst esd: Tvs(a, no_connect, gnd: g)
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
        ("    inst r: R(a, b, value=4.7k)", "    inst r: R(a, value=4.7k)", "resolve.positional"),
        (
            "    metadata:\n        interchangeable: [1, 2]\ncomponent Tvs",
            "component Tvs",
            "resolve.positional",
        ),
        (
            "[1, 2]: role.passive",
            "1: role.passive\n        2: role.ground",
            "metadata.interchangeable",
        ),
        (
            "        interchangeable: [1, 2]\nboard",
            "        interchangeable: [1]\nboard",
            "metadata.interchangeable",
        ),
        (
            "        interchangeable: [1, 2]\nboard",
            "        interchangeable: [1, 9]\nboard",
            "metadata.interchangeable",
        ),
        ("Tvs(a, no_connect, gnd: g)", "Tvs(a, no_connect, 1: g, gnd: g)", "connection"),
    ],
)
def test_invalid_groups_and_positional_instances(tmp_path, old, new, code):
    assert old in SOURCE
    with pytest.raises(DiagnosticError) as error:
        compile_source(tmp_path, SOURCE.replace(old, new))
    assert any(d.code.startswith(code) for d in error.value.diagnostics)


def test_specialization_can_clear_an_inherited_group(tmp_path):
    pull = """component Pull extends R:
    pins:
        2:
            bias: bias.pull_up
board B:"""
    source = "from noscheme import bias\n" + SOURCE.replace("board B:", pull)
    with pytest.raises(DiagnosticError, match="interchangeable: \\[\\]"):
        compile_source(tmp_path, source)
    cleared = source.replace(
        "            bias: bias.pull_up\n",
        "            bias: bias.pull_up\n    metadata:\n        interchangeable: []\n",
    )
    compile_source(tmp_path, cleared)


def test_named_inline_connections_do_not_require_a_group(tmp_path):
    source = SOURCE.replace("interchangeable: [1, 2]", "interchangeable: []")
    source = source.replace("R(a, b, value=4.7k)", "R(1: a, value=4.7k, 2: b)")
    source = source.replace("R(a,\n        b, value=1k)", "R(1: a, 2: b, value=1k,)")
    source = source.replace("Tvs(a, no_connect, gnd: g)", "Tvs(1: a, 2: no_connect, gnd: g)")
    assert nets(compile_source(tmp_path, source)) == nets(compile_source(tmp_path, SOURCE))


def test_group_aliases_order_and_inherited_instances(tmp_path):
    source = SOURCE.replace(
        "        1..2: role.passive",
        """        1: role.passive
            aliases: [left]
        2: role.passive
            aliases: [right]""",
    )
    source = source.replace("interchangeable: [1, 2]", "interchangeable: [right, left]", 1)
    source = source.replace(
        "board B:",
        """block Base:
    ports:
        input: nettype.passive
        output: nettype.passive
    inst r: R(input, output, value=1k)
block Derived extends Base:
    metadata:
        custom:
            note: "Inherited compact instance"
board B:""",
    )
    source += "    inst block: Derived(input: a, output: b)\n"
    bound = nets(compile_source(tmp_path, source))
    assert (bound["r.1"], bound["r.2"]) == ("b", "a")
    assert (bound["block.r.1"], bound["block.r.2"]) == ("b", "a")


@pytest.mark.parametrize(
    "old,new,code",
    [
        ("[1, 2]", '[1, "1"]', "metadata.interchangeable"),
        ("R(a, b, value=4.7k)", "R(a, b, a, value=4.7k)", "resolve.positional"),
        ("R(a, b, value=4.7k)", "R(missing, b, value=4.7k)", "resolve.net"),
        ("R(a, b, value=4.7k)", "R(a, b, value=4.7k, value=1k)", "syntax.duplicate"),
    ],
)
def test_compact_instance_errors(tmp_path, old, new, code):
    with pytest.raises(DiagnosticError) as error:
        compile_source(tmp_path, SOURCE.replace(old, new, 1))
    assert any(d.code.startswith(code) for d in error.value.diagnostics)


def test_positional_array_slices_globals_and_bundle_fields(tmp_path):
    source = (
        SOURCE[: SOURCE.index("board B:")]
        + """bundle Pair:
    a: nettype.passive
    b: nettype.passive
board B:
    global net gnd: nettype.ground
    net signals: nettype.passive[4]
    net pair: Pair
    inst r[2]: R(signals[1:3], global.gnd, value=1k)
    inst bridge: R(pair.a, pair.b, value=4.7k)
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
        "a, b, value=4.7k",
        "1: a, value=4.7k, 2: b",
        "a, /* keep, this */ b,\n        value=4.7k, // keep this too\n    ",
        'a, b, value="{0}".format(4700)',
    ],
)
def test_compact_instance_rename_keeps_arguments_and_adds_identity(tmp_path, arguments):
    from noscheme.lsp_rename import RenameSession
    from test_lsp_rename import apply

    source = SOURCE.replace("a, b, value=4.7k", arguments)
    make(tmp_path, source)
    path = tmp_path / "main.nosch"
    session = RenameSession(tmp_path, {})
    text = session.sources[path]
    result = apply(session.rename(path, text.index("r: R"), "renamed"), session.sources)
    # Arguments, including comments, stay as written; a metadata section follows.
    assert (
        f'inst renamed: R({arguments})\n        metadata:\n            identity: "r"'
        in result[path]
    )
    # RenameSession validates elaboration and physical identities before returning edits.
    assert path.read_text() == text


def test_inline_references_support_net_and_parameter_rename(tmp_path):
    from noscheme.lsp_rename import RenameSession
    from test_lsp_rename import apply

    make(tmp_path, SOURCE)
    path = tmp_path / "main.nosch"
    session = RenameSession(tmp_path, {})
    text = session.sources[path]
    target = session.prepare(path, text.index("R(a,") + 2)
    assert target.name == "a"
    result = apply(session.rename(path, target.span.start_pos, "signal"), session.sources)
    assert "R(signal, b," in result[path] and "Tvs(signal," in result[path]
    result = apply(session.rename(path, text.index("value=4.7k"), "resistance"), session.sources)
    assert "resistance=4.7k" in result[path] and "resistance=1k" in result[path]


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
        "    inst esd: Tvs(a, no_connect, gnd: g)\n",
        """    inst esd: Tvs(
        a,
        no_connect,
    )
        connections:
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
        ("        params:\n            value: 1k\n", "syntax.duplicate"),
        ("        connections:\n            1: b\n", "connection"),
    ],
)
def test_arguments_and_sections_cannot_bind_twice(tmp_path, addition, code):
    source = SOURCE.replace(
        "    inst r: R(a, b, value=4.7k)\n", "    inst r: R(a, b, value=4.7k)\n" + addition
    )
    with pytest.raises(DiagnosticError) as error:
        compile_source(tmp_path, source)
    assert any(d.code.startswith(code) for d in error.value.diagnostics)


def test_elements_override_argument_parameters(tmp_path):
    source = SOURCE.replace(
        "    inst caps[2]: R(a,\n        b, value=1k)\n",
        """    inst caps[2]: R(a, b, value=1k)
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
    for spelling in ("R", "R()"):
        source = SOURCE.replace("R(a, b, value=4.7k)", spelling)
        with pytest.raises(DiagnosticError, match="value"):
            compile_source(tmp_path, source)


def test_rename_adds_identity_to_an_existing_metadata_section(tmp_path):
    from noscheme.lsp_rename import RenameSession
    from test_lsp_rename import apply

    source = SOURCE.replace(
        "    inst r: R(a, b, value=4.7k)\n",
        '    inst r: R(a, b, value=4.7k)\n        metadata:\n            custom:\n                note: "x"\n',
    )
    make(tmp_path, source)
    path = tmp_path / "main.nosch"
    session = RenameSession(tmp_path, {})
    text = session.sources[path]
    result = apply(session.rename(path, text.index("r: R"), "renamed"), session.sources)[path]
    assert (
        '    inst renamed: R(a, b, value=4.7k)\n        metadata:\n            identity: "r"\n'
        "            custom:\n" in result
    )
