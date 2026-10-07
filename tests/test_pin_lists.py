"""Explicit physical pin selections share range and alias semantics."""

import pytest

from noscheme import parse
from noscheme.diagnostics import DiagnosticError
from noscheme.lsp_analysis import analyze
from noscheme.lsp_completion import completion_items
from noscheme.resolve import Resolver
from noscheme.project import load_project
from test_circuit import compile_source, make


PART = """component P:
  pins:
    1:
      aliases: [VDD]
      role: [role.passive, role.power_input]
    [2..3, 7]:
      role: [role.passive, role.power_input]
"""


def design(key="[VDD, 2..3, 7]", rhs="n", extra="", dimensions=""):
    return (
        PART
        + f"""board B:
  net n: nettype.passive
  inst p{dimensions}: P
    connections:
      {key}: {rhs}
{extra}
"""
    )


@pytest.mark.parametrize(
    "key",
    [
        "[VDD, 2..3, 7]",
        "[1..3, 7,]",
        "[1, 2, 3, 7]",
        '["1", 2..3, "7"]',
        "[\n        VDD, // supply\n        2..3,\n        7,\n      ]",
    ],
)
def test_mixed_lists_and_ranges_connect_all_selected_pins(tmp_path, key):
    circuit = compile_source(tmp_path, design(key))
    assert {number: pin["net"] for number, pin in circuit["parts"][0]["pins"].items()} == {
        "1": "n",
        "2": "n",
        "3": "n",
        "7": "n",
    }


@pytest.mark.parametrize(
    "key,selected", [("[1]", {"1"}), ("[1,]", {"1"}), ("[1..3]", {"1", "2", "3"})]
)
def test_singletons_and_bracketed_ranges(tmp_path, key, selected):
    circuit = compile_source(tmp_path, design(key))
    assert {n for n, p in circuit["parts"][0]["pins"].items() if p["net"] == "n"} == selected


def test_instance_metadata_lists_select_aliases_and_ranges(tmp_path):
    source = design(
        extra="""    metadata:
      pins:
        [VDD, 2..3, 7]:
          role: role.passive
"""
    )
    circuit = compile_source(tmp_path, source)
    assert all(
        p["metadata"]["role"]["value"] == ["passive"] for p in circuit["parts"][0]["pins"].values()
    )


@pytest.mark.parametrize("selection", ["[1, 2..3, 7]", "[VDD, 2..3, 7]"])
def test_component_specialization_lists_override_existing_pins(tmp_path, selection):
    source = (
        design()
        .replace(
            "board B:",
            """component Specialized extends P:
  pins:
    [1, 2..3, 7]:
      unused: unused.never
board B:""",
        )
        .replace("inst p: P", "inst p: Specialized")
    )
    source = source.replace("[1, 2..3, 7]:", selection + ":")
    circuit = compile_source(tmp_path, source)
    assert all(
        p["metadata"]["unused"]["value"] == "never" for p in circuit["parts"][0]["pins"].values()
    )


@pytest.mark.parametrize(
    "key,code",
    [
        ("[1, 1]", "resolve.duplicate-pin"),
        ("[1..3, 2]", "resolve.duplicate-pin"),
        ("[1, VDD]", "connection.overlap"),
        ("[1, missing]", "resolve.private"),
        ("[3..1]", "resolve.pin-range"),
        ("[01..3]", "resolve.pin-range"),
    ],
)
def test_invalid_or_overlapping_bindings(tmp_path, key, code):
    with pytest.raises(DiagnosticError) as error:
        compile_source(tmp_path, design(key))
    assert error.value.diagnostics[0].code == code


def test_overlap_between_binding_lines(tmp_path):
    with pytest.raises(DiagnosticError) as error:
        compile_source(tmp_path, design("[1..3]", extra="      [3, 7]: n\n"))
    assert error.value.diagnostics[0].code == "connection.overlap"


def test_metadata_alias_overlap_rejected(tmp_path):
    with pytest.raises(DiagnosticError) as error:
        compile_source(
            tmp_path,
            design(
                extra="""    metadata:
      pins:
        [1, VDD]:
          role: role.passive
"""
            ),
        )
    assert error.value.diagnostics[0].code == "resolve.duplicate-pin"


def test_overlapping_declaration_groups_rejected(tmp_path):
    with pytest.raises(DiagnosticError) as error:
        compile_source(tmp_path, design().replace("[2..3, 7]:", "[1..3, 7]:"))
    assert error.value.diagnostics[0].code == "resolve.duplicate-pin"


@pytest.mark.parametrize("key", ["[]", "[1 + 2]", "[1:3]", "[1.5]", "[[1, 2]]"])
def test_pin_lists_are_literal_selections_not_expressions(tmp_path, key):
    with pytest.raises(DiagnosticError):
        compile_source(tmp_path, design(key))


def test_shape_and_explicit_list_order_match_range_semantics(tmp_path):
    source = design("[7, 1..3]").replace("net n: nettype.passive", "net n: nettype.passive[4]")
    circuit = compile_source(tmp_path, source)
    assert {n: p["net"] for n, p in circuit["parts"][0]["pins"].items()} == {
        "7": "n[0]",
        "1": "n[1]",
        "2": "n[2]",
        "3": "n[3]",
    }
    with pytest.raises(DiagnosticError) as error:
        compile_source(tmp_path, source.replace("passive[4]", "passive[3]"))
    assert error.value.diagnostics[0].code == "connection.shape"


def test_instance_array_selection_and_no_connect(tmp_path):
    circuit = compile_source(
        tmp_path,
        design("[0].[1, 3]", dimensions="[2]", extra="      [1].[1..3, 7]: no_connect\n"),
    )
    assert circuit["parts"][0]["pins"]["1"]["net"] == "n"
    assert circuit["parts"][0]["pins"]["3"]["net"] == "n"
    assert all(p["net"] is None for p in circuit["parts"][1]["pins"].values())


def test_lists_do_not_apply_to_ordinary_metadata_or_parameters():
    for source in [
        'component P:\n  metadata:\n    [value]: "x"\n',
        "component P:\n  params:\n    [n]: number = 2\n",
        "bundle B:\n  [a, b]: nettype.passive\n",
    ]:
        with pytest.raises(DiagnosticError) as error:
            parse(source)
        assert any(d.code == "syntax.pin-list-context" for d in error.value.diagnostics)


def test_aggregate_selection_limit(tmp_path):
    make(tmp_path, design())
    resolver = Resolver(load_project(tmp_path), max_pins=3)
    key = parse(design()).items[-1].body[-1].sections[0].entries[0].endpoint.head
    with pytest.raises(DiagnosticError) as error:
        resolver.keys(key)
    assert error.value.diagnostics[0].code == "resolve.limit"


@pytest.mark.parametrize("pin", ["pins", "01", '"global"', '"a.b"'])
def test_singleton_exact_physical_names(tmp_path, pin):
    source = f"""component P:
  pins:
    [{pin}]:
      role: role.passive
board B:
  net n: nettype.passive
  inst p: P
    connections:
      [{pin}]: n
"""
    circuit = compile_source(tmp_path, source)
    assert circuit["parts"][0]["pins"][pin.strip('"')]["net"] == "n"


def test_lsp_hover_and_references_distinguish_list_entries(tmp_path):
    make(tmp_path, design("[VDD, 7]"))
    path = tmp_path / "main.nosch"
    source = path.read_text()
    result = analyze(path, tmp_path, {path: source})
    offset = source.index("[VDD, 7]")
    first = result.reference_at(path, offset + 1)
    second = result.reference_at(path, offset + 6)
    assert first.identity != second.identity
    assert "P: 1" in first.description and "P: 7" in second.description
    assert source[first.span.start_pos : first.span.end_pos] == "VDD"
    assert source[second.span.start_pos : second.span.end_pos] == "7"


def test_lsp_completion_inside_unfinished_list(tmp_path):
    make(tmp_path, design())
    path = tmp_path / "main.nosch"
    source = path.read_text().replace("[VDD, 2..3, 7]: n", "[2..3, VD")
    offset = source.index("[2..3, VD") + len("[2..3, VD")
    items = completion_items(path, tmp_path, {path: source}, offset)
    assert any(i.label == "VDD" and source[i.start : i.end] == "VD" for i in items)
