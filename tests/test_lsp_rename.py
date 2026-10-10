"""Rename plans must preserve binding semantics and never write source files."""

from pathlib import Path
import pytest

from noscheme.lsp_rename import RenameSession
from noscheme.project import load_project
from noscheme.resolve import Resolver
from test_lsp_references import LIBRARY, SOURCE, declared
from test_resolve import make_project


def session(tmp_path, source=SOURCE, library=LIBRARY):
    make_project(tmp_path, source, {"library": library})
    return RenameSession(tmp_path, {})


def position(source, needle):
    """A `Type name` needle locates its name; any other needle, its own start."""
    return declared(source, needle) if " " in needle.strip() else source.index(needle)


def apply(edits, sources):
    result = dict(sources)
    for span, text in sorted(edits, key=lambda e: (e[0].filename, e[0].start_pos), reverse=True):
        path = Path(span.filename)
        old = result[path]
        result[path] = old[: span.start_pos] + text + old[span.end_pos :]
    return result


def test_definition_rename_preserves_explicit_alias_and_qualification(tmp_path):
    s = session(tmp_path)
    lib = tmp_path / "library.nosch"
    edits = s.rename(lib, LIBRARY.index("Unit:"), "Assembly")
    result = apply(edits, s.sources)
    assert "block Assembly:" in result[lib]
    assert "extend Assembly Derived:" in result[lib]
    assert "from library import Assembly as Alias" in result[tmp_path / "main.nosch"]
    assert "library.Assembly<count: 3> a" in result[tmp_path / "main.nosch"]
    assert "Alias b" in result[tmp_path / "main.nosch"]
    assert lib.read_text() == LIBRARY


def test_alias_rename_keeps_imported_symbol(tmp_path):
    s = session(tmp_path)
    path = tmp_path / "main.nosch"
    result = apply(s.rename(path, SOURCE.index("Alias\n"), "LocalUnit"), s.sources)
    assert "from library import Unit as LocalUnit" in result[path]
    assert "LocalUnit b" in result[path]
    assert "library.Unit<count: 3> a" in result[path]
    assert result[tmp_path / "library.nosch"] == LIBRARY


@pytest.mark.parametrize(
    ("file", "needle", "new", "count"),
    [
        ("main", "Bus wire", "connection#", 4),
        ("library", "number count", "size", 6),
        ("library", "Bus bus", "signals", 6),
        ("library", "net.passive data", "signal", 4),
    ],
)
def test_scoped_symbols_and_inherited_ports(tmp_path, file, needle, new, count):
    s = session(tmp_path)
    path = tmp_path / (file + ".nosch")
    original = s.sources[path]
    edits = s.rename(path, position(original, needle), new)
    assert len(edits) == count
    result = apply(edits, s.sources)
    Resolver(load_project(tmp_path, sources=result)).resolve_all()
    if file == "main":
        assert "net.passive wire" in result[path]  # Other lexical scope.
    assert path.read_text() == original


@pytest.mark.parametrize(
    ("file", "needle", "new"),
    [
        ("main", "Bus wire", "a"),  # Instance in the same scope.
        ("main", "Alias\n", "library"),  # Existing module namespace.
        ("library", "Unit:", "Part"),
        ("library", "number count", "bus"),  # Existing port.
        ("library", "net.passive data", "a.b"),
        ("library", "net.passive data", "no_connect"),
        ("library", "net.passive data", "0abc"),
    ],
)
def test_invalid_names_and_collisions_are_rejected(tmp_path, file, needle, new):
    s = session(tmp_path)
    path = tmp_path / (file + ".nosch")
    with pytest.raises(ValueError):
        s.rename(path, position(s.sources[path], needle), new)
    assert path.read_text() == s.sources[path]


@pytest.mark.parametrize(
    ("file", "needle"),
    [
        ("library", "1:"),
        ("library", "signal]"),
        ("main", "B:"),
        ("library", "p[count]:"),
        ("main", "library.Unit"),
    ],
)
def test_unsupported_ids_instances_boards_and_module_qualifiers(tmp_path, file, needle):
    s = session(tmp_path)
    path = tmp_path / (file + ".nosch")
    with pytest.raises(ValueError):
        s.prepare(path, s.sources[path].index(needle))


def test_alias_reexport_chain(tmp_path):
    make_project(
        tmp_path,
        """from middle import Alias
from library import Unit as Separate
board B:
  Alias a()
  Separate b()
""",
        {"library": LIBRARY, "middle": "from library import Unit as Alias\n"},
    )
    s = RenameSession(tmp_path, {})
    middle = tmp_path / "middle.nosch"
    result = apply(s.rename(middle, s.sources[middle].index("Alias"), "Exported"), s.sources)
    assert result[middle] == "from library import Unit as Exported\n"
    assert "from middle import Exported" in result[tmp_path / "main.nosch"]
    assert "Exported a()" in result[tmp_path / "main.nosch"]
    assert "Separate b()" in result[tmp_path / "main.nosch"]


def test_prepare_and_rename_use_unsaved_buffers_and_hash_names(tmp_path):
    s = session(tmp_path)
    path = tmp_path / "main.nosch"
    text = SOURCE.replace("wire", "wire#")
    s = RenameSession(tmp_path, {path: text})
    offset = declared(text, "Bus wire#")
    target = s.prepare(path, offset)
    assert target.name == "wire#"
    assert text[target.span.start_pos : target.span.end_pos] == "wire#"
    assert s.rename(path, offset, "wire#") == []
    assert len(s.rename(path, offset, "#reset")) == 4
    assert path.read_text() == SOURCE


def test_port_metadata_overlay_is_renamed_with_the_port(tmp_path):
    source = """from noscheme import net, role
block Unit:
  ports:
    net.digital input:
      role: role.digital_input
board B:
  net.digital n
  Unit p:
    input: n
    metadata:
      pins:
        input:
          role: role.digital_input
"""
    make_project(tmp_path, source)
    s = RenameSession(tmp_path, {})
    path = tmp_path / "main.nosch"
    result = apply(s.rename(path, source.index("input:"), "signal"), s.sources)
    assert "input:" not in result[path]
    assert result[path].count("signal:") == 3


def test_bundle_rename_reaches_port_named_custom(tmp_path):
    source = """from noscheme import net
bundle Bus:
  net.passive x
block Unit:
  ports:
    Bus custom
board B:
  Unit p()
"""
    make_project(tmp_path, source)
    s = RenameSession(tmp_path, {})
    path = tmp_path / "main.nosch"
    result = apply(s.rename(path, source.index("Bus:"), "Signals"), s.sources)
    assert "Signals custom" in result[path]
    assert "bundle Signals:" in result[path]


def test_global_net_rename_reaches_other_modules(tmp_path):
    source = """from noscheme import net
import child
board B:
  global net.passive supply
  child.Unit block()
"""
    child = """from noscheme import role
component Pin:
  pins:
    1:
      role: role.passive
block Unit:
  Pin p:
    1: global.supply
"""
    make_project(tmp_path, source, {"child": child})
    s = RenameSession(tmp_path, {})
    path = tmp_path / "main.nosch"
    result = apply(s.rename(path, declared(source, "net.passive supply"), "rail"), s.sources)
    assert "global net.passive rail" in result[path]
    assert "global.rail" in result[tmp_path / "child.nosch"]


def compile_buffers(root, sources):
    from noscheme.circuit import elaborate_project

    project = load_project(root, sources=sources)
    return project, elaborate_project(project, Resolver(project).resolve_all())[0]


@pytest.mark.parametrize("name", ["a", "b", "c"])
def test_board_block_rename_preserves_descendants_and_array_identities(tmp_path, name):
    s = session(tmp_path)
    path = tmp_path / "main.nosch"
    _, before = compile_buffers(tmp_path, s.sources)
    result = apply(s.rename(path, SOURCE.index(f" {name}:") + 1, "assembly"), s.sources)
    assert f'identity: "{name}"' in result[path]
    _, after = compile_buffers(tmp_path, result)
    assert {p["identity"] for p in before["parts"]} == {p["identity"] for p in after["parts"]}
    assert any(p["path"][0] == "assembly" for p in after["parts"])
    assert path.read_text() == SOURCE


@pytest.mark.parametrize(
    "body",
    [
        "  Part old() // keep trailing ) comment\n",
        "  Part old\n",
        "  Part<value: (1 << 3)> old\n",
        "  Part<\n    value: (8 >> 1),\n  > old // keep\n",
        '  Part<value: "keep > string"> old // keep: comment\n',
        '  Part old[2][3]:\n    params:\n      value: "keep ) string"\n',
        '  Part old:\n    metadata:\n      // Keep this comment.\n      value: "keep me"\n',
        '  Part old:\n    metadata:\n      identity: "already_stable"\n',
        '  Part old[2]:\n    elements:\n      [1]:\n        params:\n          value: "2k"\n',
    ],
)
def test_board_instance_identity_insertion_formats_and_existing_identity(tmp_path, body):
    source = "from library import Part\nboard B:\n" + body
    s = session(tmp_path, source, LIBRARY.replace("string value", "resistance value"))
    path = tmp_path / "main.nosch"
    result = apply(s.rename(path, source.index("old"), "new"), s.sources)
    _, before = compile_buffers(tmp_path, s.sources)
    _, after = compile_buffers(tmp_path, result)
    assert {p["identity"] for p in before["parts"]} == {p["identity"] for p in after["parts"]}
    assert result[path].count("identity:") == 1
    if "already_stable" in body:
        assert 'identity: "already_stable"' in result[path]
    else:
        assert 'identity: "old"' in result[path]
    if "// keep trailing ) comment" in body:
        assert "): // keep trailing ) comment" in result[path]
    if "keep > string" in body:
        assert '<value: "keep > string"> new: // keep: comment' in result[path]
    if "keep ) string" in body:
        assert 'value: "keep ) string"' in result[path]
    # A second rename retains the original identity, rather than the previous name.
    second = RenameSession(tmp_path, result)
    twice = apply(second.rename(path, result[path].index("new"), "third"), result)
    assert twice[path].count("identity:") == 1
    _, again = compile_buffers(tmp_path, twice)
    assert {p["identity"] for p in before["parts"]} == {p["identity"] for p in again["parts"]}


def test_board_rename_preserves_newlines_and_nonstandard_indentation(tmp_path):
    source = (
        'from library import Part\r\nboard B:\r\n  Part old:\r\n    params:\r\n      value: "a"\r\n'
    )
    session(tmp_path, source)
    path = tmp_path / "main.nosch"
    s = RenameSession(tmp_path, {path: source})
    result = apply(s.rename(path, source.index("old"), "new"), s.sources)[path]
    assert 'value: "a"\r\n    metadata:\r\n      identity: "old"\r\n' in result
    assert result.replace("\r\n", "").count("\n") == 0


def test_board_rename_updates_waiver_targets(tmp_path):
    source = """from noscheme import role, net
component Part:
  pins:
    1:
      role: role.power_input
board B:
  net.ground gnd:
    waivers:
      incompatible_role:
        reason: "old is intentionally grounded"
        pins: ["old[0].1", "old[1].1"]
  Part old[2]:
    1: gnd
    metadata:
      custom:
        note: "old"
"""
    s = session(tmp_path, source)
    path = tmp_path / "main.nosch"
    result = apply(s.rename(path, source.index("old[2]"), "new"), s.sources)[path]
    assert 'pins: ["new[0].1", "new[1].1"]' in result
    assert 'reason: "old is intentionally grounded"' in result
    assert 'note: "old"' in result
    from noscheme.erc import apply_erc

    _, renamed = compile_buffers(tmp_path, {**s.sources, path: result})
    assert apply_erc(renamed) == ()


def test_board_rename_keeps_export_uuid_and_reference_without_writing_state(tmp_path):
    import json
    from noscheme.kicad.export import export_project

    source = """from library import Part
board B:
  Part old[2]:
    metadata:
      footprint: "Test:OnePad"
"""
    s = session(tmp_path, source)
    path = tmp_path / "main.nosch"
    footprints = tmp_path / "footprints.json"
    footprints.write_text(
        json.dumps(
            {"schema_version": 1, "footprints": {"Test:OnePad": {"exists": True, "pads": ["1"]}}}
        )
    )
    project, before = compile_buffers(tmp_path, s.sources)
    export_project(project, before, footprints)
    state = tmp_path / "noscheme.state.json"
    saved = state.read_bytes()
    result = apply(s.rename(path, source.index("old"), "new"), s.sources)
    assert state.read_bytes() == saved
    project, after = compile_buffers(tmp_path, result)
    report = export_project(project, after, footprints)
    assert report["changes"]["added"] == report["changes"]["removed"] == []
    first, second = json.loads(saved)["entries"], json.loads(state.read_bytes())["entries"]
    assert first.keys() == second.keys()
    for identity in first:
        assert first[identity]["uuid"] == second[identity]["uuid"]
        assert first[identity]["reference"] == second[identity]["reference"]


def test_net_type_rename_updates_extensions_and_uses(tmp_path):
    from test_nettypes import SOURCE as nettypes

    source = "from noscheme import role, unused, net\n" + nettypes
    s = session(tmp_path, source)
    path = tmp_path / "main.nosch"
    result = apply(s.rename(path, source.index("I2CLine:"), "BusLine"), s.sources)[path]
    assert "extend net.open_drain BusLine" in result
    assert "extend BusLine FastI2CLine" in result
    assert "BusLine scl" in result and "BusLine extra" in result
    assert "I2CLine" not in result.replace("FastI2CLine", "")
