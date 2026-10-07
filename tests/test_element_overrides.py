import pytest

from noscheme.diagnostics import DiagnosticError
from noscheme.evaluate import evaluate_project
from noscheme.resolve import resolve_project
from test_circuit import compile_source, make

SOURCE = """component Cap:
  params:
    capacitance: capacitance = 100n
  pins:
    1..2:
      role: role.passive
  metadata:
    value: capacitance
board B:
  net rail: nettype.passive
  inst caps[2]: Cap
    connections:
      1: rail
      2: no_connect
    metadata:
      custom:
        common: "kept"
        placement: "default"
    elements:
      [1]:
        params:
          capacitance: 220n
        metadata:
          custom:
            placement: "beside MCU"
          pins:
            1:
              capacitance: 2p + 3p
"""


def test_element_parameters_and_metadata_merge_without_identity_changes(tmp_path):
    circuit = compile_source(tmp_path, SOURCE)
    first, second = circuit["parts"]
    assert [first["identity"], second["identity"]] == ["caps[0]", "caps[1]"]
    assert [first["value"]["value"], second["value"]["value"]] == ["100nF", "220nF"]
    custom = second["metadata"]["custom"]["value"]
    assert custom["common"]["value"] == "kept"
    assert custom["placement"]["value"] == "beside MCU"
    assert second["pins"]["1"]["metadata"]["capacitance"]["value"] == pytest.approx(5e-12)
    assert "capacitance" not in first["pins"]["1"]["metadata"]


def test_complete_multidimensional_indices_and_common_identity(tmp_path):
    source = SOURCE.replace("caps[2]", "caps[2][2]").replace("[1]:", "[1][0]:")
    source = source.replace(
        "    metadata:\n      custom:",
        '    metadata:\n      identity: "stable"\n      custom:',
        1,
    )
    circuit = compile_source(tmp_path, source)
    assert [p["identity"] for p in circuit["parts"]] == [
        "stable[0][0]",
        "stable[0][1]",
        "stable[1][0]",
        "stable[1][1]",
    ]
    assert [p["value"]["value"] for p in circuit["parts"]] == ["100nF", "100nF", "220nF", "100nF"]


@pytest.mark.parametrize(
    "selector,code",
    [
        ("[2]", "array.index"),
        ("[0][1]", "array.element-rank"),
        ("[0:1]", "array.element-index"),
        ("[1.0]", "array.element-index"),
        ("[unknown]", "array.element-index"),
    ],
)
def test_invalid_element_selectors(tmp_path, selector, code):
    with pytest.raises(DiagnosticError) as error:
        compile_source(tmp_path, SOURCE.replace("[1]:", selector + ":"))
    assert error.value.diagnostics[0].code == code


def test_duplicate_elements_and_scalar_overrides_rejected(tmp_path):
    # The caps instance is last, so its elements section ends the source.
    duplicate = SOURCE + "      [1]:\n        params:\n          capacitance: 330n\n"
    with pytest.raises(DiagnosticError, match="array.element-overlap"):
        compile_source(tmp_path, duplicate)
    with pytest.raises(DiagnosticError, match="array.element-rank"):
        compile_source(tmp_path, SOURCE.replace("caps[2]", "caps"))


@pytest.mark.parametrize("key", ["ref", "identity"])
def test_element_identity_overrides_rejected(tmp_path, key):
    source = SOURCE.replace(
        "        metadata:\n",
        f'        metadata:\n          {key}: "C1"\n',
    )
    with pytest.raises(DiagnosticError, match="array.element-identity"):
        compile_source(tmp_path, source)


def test_evaluate_inspection_reports_explicit_variants(tmp_path):
    report, _ = evaluate_project(*resolve_project(make(tmp_path, SOURCE)))
    records = {p["path"]: p for p in report["instances"]}
    assert records["B.caps"]["parameters"]["capacitance"]["value"] == 1e-7
    assert records["B.caps[1]"]["parameters"]["capacitance"]["value"] == 2.2e-7
    assert records["B.caps[1]"]["dimensions"] == []


def test_each_element_can_supply_required_parameter(tmp_path):
    source = SOURCE.replace("number = 100", "number").replace(
        "    elements:\n",
        "    elements:\n      [0]:\n        params:\n          capacitance: 10n\n",
    )
    circuit = compile_source(tmp_path, source)
    assert [p["value"]["value"] for p in circuit["parts"]] == ["10nF", "220nF"]
    report, _ = evaluate_project(*resolve_project(tmp_path))
    assert len(report["instances"]) == 3


def test_lsp_navigates_element_parameter_name(tmp_path):
    from noscheme.lsp_analysis import analyze

    make(tmp_path, SOURCE)
    path = tmp_path / "main.nosch"
    text = path.read_text()
    result = analyze(path, tmp_path, {path: text})
    offset = text.index("capacitance: 220n")
    reference = result.reference_at(path, offset)
    assert reference is not None
    assert text[reference.target.start_pos : reference.target.end_pos].startswith(
        "capacitance: capacitance"
    )


def test_overrides_apply_to_child_parts_of_block_arrays(tmp_path):
    source = (
        SOURCE.replace(
            "board B:\n",
            """block Unit:
  params:
    capacitance: capacitance = 100n
  ports:
    signal: nettype.passive
  inst cap: Cap
    params:
      capacitance: capacitance
    connections:
      1: signal
      2: no_connect
board B:
""",
        )
        .replace("caps[2]: Cap", "caps[2]: Unit")
        .replace("      1: rail\n      2: no_connect", "      signal: rail")
    )
    source = source.replace(
        "          pins:\n            1:\n              capacitance: 2p + 3p\n",
        "",
    )
    circuit = compile_source(tmp_path, source)
    assert [p["identity"] for p in circuit["parts"]] == ["caps[0].cap", "caps[1].cap"]
    assert [p["value"]["value"] for p in circuit["parts"]] == ["100nF", "220nF"]
