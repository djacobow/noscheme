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
    (tmp_path / "main.nosch").write_text("from Test import Child\nboard B:\n  inst c: Child()\n")
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
    source = library(tmp_path / "Test.kicad_sym", symbol("C", [pin("1", "T", "unspecified")]))
    report = convert_library(source, tmp_path / "out")
    assert any(i["check"] == "unmapped_pin_type" for i in report["issues"])
    text = (tmp_path / "out/Test.nosch").read_text()
    assert 'kicad_pin_types: ["unspecified"]' in text
    assert "role: role." not in text


def test_power_input_conversion_preserves_supply_or_ground_ambiguity(tmp_path):
    source = library(
        tmp_path / "Test.kicad_sym",
        symbol("C", [pin("1", "VCC", "power_in"), pin("2", "GND", "power_in")]),
    )
    convert_library(source, tmp_path / "out")
    text = (tmp_path / "out/Test.nosch").read_text()
    assert text.count("role: [role.ground, role.power_input]") == 2


def test_converter_renames_reserved_symbols_and_module(tmp_path):
    source = library(tmp_path / "global.kicad_sym", symbol("true", [pin("1", "P", "passive")]))
    report = convert_library(source, tmp_path / "out")
    assert report["symbols"] == {"true": "C_true"}
    assert report["module"] == "C_global"
    assert "component C_true:" in (tmp_path / "out/C_global.nosch").read_text()


@pytest.mark.parametrize("types,capable", [(["tri_state"], True), (["tri_state", "output"], False)])
def test_tristate_conversion_preserves_explicit_capability(tmp_path, types, capable):
    source = library(tmp_path / "Test.kicad_sym", symbol("C", [pin("1", "T", t) for t in types]))
    report = convert_library(source, tmp_path / "out")
    assert not any(i["check"] == "unmapped_pin_type" for i in report["issues"])
    text = (tmp_path / "out/Test.nosch").read_text()
    assert "role: role.digital_output" in text
    assert ("tristate_capable: true" in text) == capable


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


def test_filter_candidates_saved_and_completed_offline(tmp_path):
    from noscheme.lsp_completion import completion_items

    part = symbol("Part", [pin("1", "a"), pin("2", "b")], "Test:Z")
    part.append(form("property", "ki_fp_filters", "Test:R?2*"))
    source = library(tmp_path / "Parts.kicad_sym", part)
    root = tmp_path / "footprints"
    folder = root / "Test.pretty"
    folder.mkdir(parents=True)
    for name, pads in {
        "Z": ["1", "2"],
        "R2": ["1", "2", "2", ""],
        "R_2": ["1", "2"],
        "R_2_wrong": ["1", "3"],
        "R_2_extra": ["1", "2", "MP"],
        "Unrelated": ["1", "2"],
    }.items():
        (folder / (name + ".kicad_mod")).write_text(
            dump(
                form(
                    "footprint",
                    name,
                    *(form("pad", p, Symbol("smd"), Symbol("rect")) for p in pads),
                )
            )
        )
    (folder / "R_2_broken.kicad_mod").write_text("(")
    out = tmp_path / "converted"
    report = convert_library(source, out, footprint_roots=[root])
    records = json.loads((out / "footprints.json").read_text())["footprints"]
    assert set(records) == {"Test:Z", "Test:R2", "Test:R_2"}
    assert any(i["check"] == "invalid_footprint_candidate" for i in report["issues"])
    root.rename(tmp_path / "unavailable")
    (tmp_path / "noscheme.toml").write_text(
        'language_version="0.1"\nboard="main.B"\nsource_paths=["."]\nlibrary_paths=["converted"]\noutput_dir="build"\n'
    )
    path = tmp_path / "main.nosch"
    text = 'from Parts import Part\nboard B:\n  inst p: Part\n    metadata:\n      footprint: "'
    choices = completion_items(path, tmp_path, {path: text}, len(text))
    assert [s.label for s in choices] == ["Test:Z", "Test:R2", "Test:R_2"]


@pytest.mark.parametrize(
    ("name", "pattern", "expected"),
    [
        ("Test:R_2", "R?2", True),
        ("Test:R2", "R?2", True),
        ("Test:R__2", "R?2", False),
        ("Test:R2", "Other:*", False),
        ("Test:R2", "test:r*", True),
        ("Test:R[2]", "R[2]", True),
    ],
)
def test_footprint_filter_globs(name, pattern, expected):
    from noscheme.kicad.convert import footprint_matches

    assert footprint_matches(name, pattern) is expected


@pytest.mark.parametrize(
    "kind,role", [("open_emitter", "open_source"), ("open_collector", "open_drain")]
)
def test_open_output_conversion(tmp_path, kind, role):
    source = library(tmp_path / "Test.kicad_sym", symbol("C", [pin("1", "OUT", kind)]))
    report = convert_library(source, tmp_path / "out")
    assert not any(i["check"] == "unmapped_pin_type" for i in report["issues"])
    assert f"role: role.{role}" in (tmp_path / "out/Test.nosch").read_text()


def test_free_pin_type_does_not_silently_disable_erc(tmp_path):
    source = library(tmp_path / "Test.kicad_sym", symbol("C", [pin("1", "X", "free")]))
    report = convert_library(source, tmp_path / "out")
    assert any(i["check"] == "unmapped_pin_type" for i in report["issues"])
    assert "role: role.unchecked" not in (tmp_path / "out/Test.nosch").read_text()
