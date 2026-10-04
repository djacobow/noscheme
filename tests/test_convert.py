from pathlib import Path
import json
import pytest
from sexpdata import Symbol

from noscheme.kicad.convert import convert_library, footprint_record
from noscheme.diagnostics import DiagnosticError
from noscheme.resolve import resolve_project
from noscheme.kicad.sexpr import form, dump


def pin(number, name, kind="passive"):
    return [Symbol("pin"), Symbol(kind), Symbol("line"), form("name", name), form("number", number)]


def library(path, *symbols):
    path.write_text(dump(form("kicad_symbol_lib", form("version", 20251024), *symbols)))
    return path


def symbol(name, pins, footprint=""):
    return form(
        "symbol",
        name,
        form("property", "Reference", "U"),
        form("property", "Value", name),
        form("property", "Footprint", footprint),
        form("symbol", name + "_1_1", *pins),
    )


def test_exact_pins_ambiguous_alias_and_inheritance(tmp_path):
    source = library(
        tmp_path / "Test.kicad_sym",
        symbol("Base", [pin("01", "A"), pin("1", "GND"), pin("2", "GND")]),
        form("symbol", "Child", form("extends", "Base"), form("property", "Value", "Child value")),
    )
    out = tmp_path / "converted"
    report = convert_library(source, out, symbols=["Child"])
    assert report["symbols"] == {"Child": "Child"}
    assert any(i["check"] == "ambiguous_alias" and i["alias"] == "GND" for i in report["issues"])
    (tmp_path / "noscheme.toml").write_text(
        'language_version="0.1"\nboard="main.B"\nsource_paths=["."]\nlibrary_paths=["converted"]\noutput_dir="build"\n'
    )
    (tmp_path / "main.nosch").write_text("from Test import Child\nboard B:\n    inst c: Child()\n")
    d = resolve_project(tmp_path)[1]["Test", "Child"]
    assert set(d.interface) == {"01", "1", "2"}
    assert d.aliases["A"] == "01" and "GND" not in d.aliases
    assert d.parameters["value"].default.value.value == "Child value"
    with pytest.raises(FileExistsError):
        convert_library(source, out)


def test_snapshots_include_duplicate_and_unnumbered_pads(tmp_path):
    root = tmp_path / "footprints"
    lib = root / "Test.pretty"
    lib.mkdir(parents=True)
    (lib / "P.kicad_mod").write_text(
        '(footprint "P" (pad "1" smd rect) (pad "1" smd rect) (pad "" np_thru_hole circle) (pad "3" thru_hole circle))'
    )
    record = footprint_record("Test:P", [root])
    assert record["pads"] == ["1", "3"]
    assert record["unnumbered_pads"] == 1
    assert len(record["pad_records"]) == 4
    source = library(
        tmp_path / "Test.kicad_sym", symbol("C", [pin("1", "A"), pin("2", "B")], "Test:P")
    )
    report = convert_library(source, tmp_path / "out", footprint_roots=[root])
    checks = {x["check"] for x in report["issues"]}
    assert {"pin_without_pad", "pad_without_pin"} <= checks
    snapshots = json.loads((tmp_path / "out/footprints.json").read_text())
    assert snapshots["footprints"]["Test:P"]["sha256"] == record["sha256"]


def test_unknown_pin_types_are_preserved_without_guessing(tmp_path):
    source = library(tmp_path / "Test.kicad_sym", symbol("C", [pin("1", "T", "tri_state")]))
    report = convert_library(source, tmp_path / "out")
    assert any(i["check"] == "unmapped_pin_type" for i in report["issues"])
    text = (tmp_path / "out/Test.nosch").read_text()
    assert 'kicad_pin_types: ["tri_state"]' in text
    assert "role: role." not in text


@pytest.mark.parametrize("kind", ["collision", "cycle", "missing", "malformed"])
def test_conversion_failure_does_not_publish_partial_output(tmp_path, kind):
    path = tmp_path / "Test.kicad_sym"
    if kind == "collision":
        library(path, symbol("A-B", []), symbol("A_B", []))
    elif kind == "cycle":
        library(path, form("symbol", "A", form("extends", "A")))
    elif kind == "missing":
        library(path, form("symbol", "A", form("extends", "Missing")))
    else:
        path.write_text("(kicad_symbol_lib (")
    with pytest.raises(DiagnosticError):
        convert_library(path, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_installed_device_library_conversion(tmp_path):
    path = Path("/usr/share/kicad/symbols/Device.kicad_sym")
    if not path.exists():
        pytest.skip("KiCad symbol libraries are not installed")
    report = convert_library(
        path,
        tmp_path / "out",
        symbols=["R", "LED"],
        footprint_roots=[Path("/usr/share/kicad/footprints")],
        extra_footprints=["Resistor_SMD:R_0603_1608Metric", "LED_SMD:LED_0603_1608Metric"],
    )
    assert set(report["symbols"]) == {"R", "LED"}
    records = json.loads((tmp_path / "out/footprints.json").read_text())["footprints"]
    assert all(r["exists"] and r["pads"] == ["1", "2"] for r in records.values())


def test_kicad_no_connect_maps_to_forbidden_connection_role(tmp_path):
    source = library(tmp_path / "Test.kicad_sym", symbol("C", [pin("1", "NC", "no_connect")]))
    report = convert_library(source, tmp_path / "out")
    assert not any(i["check"] == "unmapped_pin_type" for i in report["issues"])
    text = (tmp_path / "out/Test.nosch").read_text()
    assert "role: role.do_not_connect" in text
    assert 'kicad_pin_types: ["no_connect"]' in text
