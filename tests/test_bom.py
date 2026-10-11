"""A BOM groups physical parts and shares references with CAD exports."""

import csv
from io import StringIO
import json
from pathlib import Path

import pytest

from noscheme.bom import COLUMNS, export_bom
from noscheme.circuit import elaborate_project
from noscheme.cli import main
from noscheme.diagnostics import DiagnosticError
from noscheme.kicad.export import export_project
from noscheme.project import read_config
from noscheme.resolve import resolve_project

SOURCE = """from noscheme import role, use
component Resistor:
  params:
    resistance value = 10k
    number tolerance = 1
  pins:
    1..2: [role.passive, use.optional]
  metadata:
    ref_prefix: "R"
    footprint: "Test:R"
    bom:
      manufacturer: "Example, Inc."
      mpn: "00123"
      supplier: "A shop"
      supplier_part_number: "00456"
block Circuit:
  Resistor common[12]
  Resistor spare:
    metadata:
      bom:
        dnp: true
  Resistor alternate:
    metadata:
      bom:
        mpn: "different"
  Resistor<tolerance: 5> loose
extend Circuit Variant
"""
CONFIG = """language_version = "0.1"
source_paths = ["."]
library_paths = []
output_dir = "build"
default_target = "main"
[targets.main]
root = "main.Circuit"
[targets.variant]
root = "main.Variant"
"""


def make_project(path, source=SOURCE):
    (path / "main.nosch").write_text(source)
    (path / "noscheme.toml").write_text(CONFIG)
    return path


def build(path, target=None):
    project, definitions = resolve_project(path, target=target)
    circuit, _ = elaborate_project(project, definitions)
    return project, circuit


def read_rows(report):
    with Path(report["output"]).open(newline="") as stream:
        return list(csv.DictReader(stream))


def test_grouping_inheritance_overrides_and_natural_references(tmp_path):
    make_project(tmp_path)
    project, circuit = build(tmp_path)
    report = export_bom(project, circuit)
    assert (report["parts"], report["groups"], report["fitted"], report["dnp"]) == (15, 4, 14, 1)
    rows = read_rows(report)
    common = next(row for row in rows if row["Quantity"] == "12")
    assert common["References"].split(", ") == [f"R{i}" for i in range(2, 14)]
    assert common["Manufacturer"] == "Example, Inc."
    assert common["MPN"] == "00123" and common["Supplier Part Number"] == "00456"
    assert common["Value"] == "10kOhm" and common["Footprint"] == "Test:R"
    assert len([row for row in rows if row["MPN"] == "00123" and row["DNP"] == "no"]) == 2
    before = Path(report["output"]).read_bytes(), project.config.state_path.read_bytes()
    assert export_bom(project, circuit) == report
    assert before == (Path(report["output"]).read_bytes(), project.config.state_path.read_bytes())
    assert json.loads(before[1])["previous_parts"] == {}


def test_bom_and_kicad_share_references_and_preserve_cad_baseline(tmp_path):
    make_project(tmp_path)
    project, circuit = build(tmp_path)
    export_bom(project, circuit)
    state = json.loads(project.config.state_path.read_text())
    records = tmp_path / "footprints.json"
    records.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "footprints": {
                    "Test:R": {"exists": True, "pads": ["1", "2"]},
                },
            }
        )
    )
    cad = export_project(project, circuit, records)
    assert len(cad["changes"]["added"]) == 15
    cad_state = json.loads(project.config.state_path.read_text())
    assert state["entries"] == cad_state["entries"]
    source = (tmp_path / "main.nosch").read_text().replace("common[12]", "common[13]")
    (tmp_path / "main.nosch").write_text(source)
    project, circuit = build(tmp_path)
    export_bom(project, circuit)
    bom_state = json.loads(project.config.state_path.read_text())
    assert bom_state["previous_parts"] == cad_state["previous_parts"]
    cad = export_project(project, circuit, records)
    assert len(cad["changes"]["added"]) == 1
    assert not cad["changes"]["removed"]


def test_csv_quoting_missing_footprints_and_target_isolation(tmp_path, capsys):
    source = SOURCE.replace('    footprint: "Test:R"\n', "").replace(
        '"Example, Inc."', json.dumps('A "quoted" company\nSecond line')
    )
    make_project(tmp_path, source)
    for target in ("main", "variant"):
        assert main(["export-bom", "--project", str(tmp_path), "--target", target]) == 0
        output = capsys.readouterr()
        report = json.loads(output.out)
        assert "footprint.unspecified" in output.err
        rows = read_rows(report)
        assert rows[0]["Manufacturer"] == 'A "quoted" company\nSecond line'
        assert all(row["Footprint"] == "" for row in rows)
        assert Path(report["output"]) == tmp_path / "build" / target / "bom.csv"
    first = json.loads(read_config(tmp_path).state_path.read_text())
    second = json.loads(read_config(tmp_path, target="variant").state_path.read_text())
    assert first["namespace"] != second["namespace"]


def test_empty_bom(tmp_path):
    make_project(
        tmp_path,
        'block Circuit:\n  metadata:\n    custom:\n      note: "empty"\nextend Circuit Variant\n',
    )
    report = export_bom(*build(tmp_path))
    assert report["parts"] == report["groups"] == 0
    assert list(csv.reader(StringIO(Path(report["output"]).read_text()))) == [list(COLUMNS)]


@pytest.mark.parametrize("field", ["mpn: 123", 'dnp: "yes"', 'manufactuer: "typo"'])
def test_bom_metadata_is_validated(tmp_path, field):
    make_project(tmp_path, SOURCE.replace('mpn: "00123"', field))
    with pytest.raises(DiagnosticError):
        build(tmp_path)


def test_failed_publication_rolls_back_csv_and_state(tmp_path, monkeypatch):
    from noscheme.kicad import export

    make_project(tmp_path)
    project, circuit = build(tmp_path)
    report = export_bom(project, circuit)
    output = Path(report["output"])
    output.write_text("old BOM\n")
    before = output.read_bytes(), project.config.state_path.read_bytes()
    original = export.os.replace

    def fail_state(src, dst):
        if Path(dst) == project.config.state_path:
            raise OSError("state write failed")
        return original(src, dst)

    monkeypatch.setattr(export.os, "replace", fail_state)
    with pytest.raises(OSError, match="state write failed"):
        export_bom(project, circuit)
    assert before == (output.read_bytes(), project.config.state_path.read_bytes())


def test_bom_metadata_on_block_instance_is_rejected(tmp_path):
    source = (
        SOURCE
        + """block Outer:
  Circuit child:
    metadata:
      bom:
        dnp: true
"""
    )
    make_project(tmp_path, source)
    config = tmp_path / "noscheme.toml"
    config.write_text(CONFIG.replace('root = "main.Circuit"', 'root = "main.Outer"'))
    with pytest.raises(DiagnosticError) as error:
        build(tmp_path)
    assert error.value.diagnostics[0].code == "metadata.scope"


def test_bom_cannot_overwrite_input_through_symlink(tmp_path):
    make_project(tmp_path)
    project, circuit = build(tmp_path)
    project.config.output_dir.mkdir(parents=True)
    source = tmp_path / "main.nosch"
    (project.config.output_dir / "bom.csv").symlink_to(source)
    with pytest.raises(DiagnosticError) as error:
        export_bom(project, circuit)
    assert error.value.diagnostics[0].code == "export.overwrite"
    assert source.read_text() == SOURCE
    assert not project.config.state_path.exists()


def test_array_element_bom_override_merges_purchasing_fields(tmp_path):
    source = SOURCE.replace(
        "  Resistor common[12]",
        """  Resistor common[12]:
    elements:
      [1]:
        metadata:
          bom:
            dnp: true""",
    )
    make_project(tmp_path, source)
    project, circuit = build(tmp_path)
    report = export_bom(project, circuit)
    assert report["parts"] == 15 and report["dnp"] == 2
    dnp = next(row for row in read_rows(report) if row["DNP"] == "yes")
    assert dnp["Quantity"] == "2"
    assert dnp["MPN"] == "00123" and dnp["Manufacturer"] == "Example, Inc."
    state = json.loads(project.config.state_path.read_text())
    assert state["entries"]["common[1]"]["reference"] in dnp["References"].split(", ")
