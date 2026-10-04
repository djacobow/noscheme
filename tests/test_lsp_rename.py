"""Rename plans must preserve binding semantics and never write source files."""

from pathlib import Path
import pytest

from noscheme.lsp_rename import RenameSession
from noscheme.project import load_project
from noscheme.resolve import Resolver
from test_lsp_references import LIBRARY, SOURCE
from test_resolve import make_project


def session(tmp_path, source=SOURCE, library=LIBRARY):
    make_project(tmp_path, source, {"library": library})
    return RenameSession(tmp_path, {})


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
    assert "extends Assembly:" in result[lib]
    assert "from library import Assembly as Alias" in result[tmp_path / "main.nosch"]
    assert "inst a: library.Assembly(" in result[tmp_path / "main.nosch"]
    assert "inst b: Alias(" in result[tmp_path / "main.nosch"]
    assert lib.read_text() == LIBRARY


def test_alias_rename_keeps_imported_symbol(tmp_path):
    s = session(tmp_path)
    path = tmp_path / "main.nosch"
    result = apply(s.rename(path, SOURCE.index("Alias("), "LocalUnit"), s.sources)
    assert "from library import Unit as LocalUnit" in result[path]
    assert "inst b: LocalUnit(" in result[path]
    assert "inst a: library.Unit(" in result[path]
    assert result[tmp_path / "library.nosch"] == LIBRARY


@pytest.mark.parametrize(
    ("file", "needle", "new", "count"),
    [
        ("main", "wire:", "connection#", 4),
        ("library", "count:", "size", 6),
        ("library", "bus: Bus", "signals", 6),
        ("library", "data:", "signal", 4),
    ],
)
def test_scoped_symbols_and_inherited_ports(tmp_path, file, needle, new, count):
    s = session(tmp_path)
    path = tmp_path / (file + ".nosch")
    original = s.sources[path]
    edits = s.rename(path, original.index(needle), new)
    assert len(edits) == count
    result = apply(edits, s.sources)
    Resolver(load_project(tmp_path, sources=result)).resolve_all()
    if file == "main":
        assert "net wire: nettype.passive" in result[path]  # Other lexical scope.
    assert path.read_text() == original


@pytest.mark.parametrize(
    ("file", "needle", "new"),
    [
        ("main", "wire:", "a"),  # Instance in the same scope.
        ("main", "Alias(", "library"),  # Existing module namespace.
        ("library", "Unit:", "Part"),
        ("library", "count:", "bus"),  # Existing port.
        ("library", "data:", "a.b"),
        ("library", "data:", "no_connect"),
        ("library", "data:", "0abc"),
    ],
)
def test_invalid_names_and_collisions_are_rejected(tmp_path, file, needle, new):
    s = session(tmp_path)
    path = tmp_path / (file + ".nosch")
    with pytest.raises(ValueError):
        s.rename(path, s.sources[path].index(needle), new)
    assert path.read_text() == s.sources[path]


@pytest.mark.parametrize(
    ("file", "needle"),
    [
        ("library", "1:"),
        ("library", "signal]"),
        ("main", "B:"),
        ("main", "a: library"),
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
    inst a: Alias()
    inst b: Separate()
""",
        {"library": LIBRARY, "middle": "from library import Unit as Alias\n"},
    )
    s = RenameSession(tmp_path, {})
    middle = tmp_path / "middle.nosch"
    result = apply(s.rename(middle, s.sources[middle].index("Alias"), "Exported"), s.sources)
    assert result[middle] == "from library import Unit as Exported\n"
    assert "from middle import Exported" in result[tmp_path / "main.nosch"]
    assert "inst a: Exported()" in result[tmp_path / "main.nosch"]
    assert "inst b: Separate()" in result[tmp_path / "main.nosch"]


def test_prepare_and_rename_use_unsaved_buffers_and_hash_names(tmp_path):
    s = session(tmp_path)
    path = tmp_path / "main.nosch"
    text = SOURCE.replace("wire", "wire#")
    s = RenameSession(tmp_path, {path: text})
    position = text.index("wire#:")
    target = s.prepare(path, position)
    assert target.name == "wire#"
    assert text[target.span.start_pos : target.span.end_pos] == "wire#"
    assert s.rename(path, position, "wire#") == []
    assert len(s.rename(path, position, "#reset")) == 4
    assert path.read_text() == SOURCE


def test_port_metadata_overlay_is_renamed_with_the_port(tmp_path):
    source = """from noscheme import nettype, role
block Unit:
    ports:
        input: nettype.digital
            role: role.digital_input
board B:
    net n: nettype.digital
    inst p: Unit(
        connections:
            input: n
        metadata:
            pins:
                input:
                    role: role.digital_input
    )
"""
    make_project(tmp_path, source)
    s = RenameSession(tmp_path, {})
    path = tmp_path / "main.nosch"
    result = apply(s.rename(path, source.index("input:"), "signal"), s.sources)
    assert "input:" not in result[path]
    assert result[path].count("signal:") == 3


def test_bundle_rename_reaches_port_named_custom(tmp_path):
    source = """from noscheme import nettype
bundle Bus:
    x: nettype.passive
block Unit:
    ports:
        custom: Bus
board B:
    inst p: Unit()
"""
    make_project(tmp_path, source)
    s = RenameSession(tmp_path, {})
    path = tmp_path / "main.nosch"
    result = apply(s.rename(path, source.index("Bus:"), "Signals"), s.sources)
    assert "custom: Signals" in result[path]
    assert "bundle Signals:" in result[path]


def test_global_net_rename_reaches_other_modules(tmp_path):
    source = """from noscheme import nettype
import child
board B:
    global net supply: nettype.passive
    inst block: child.Unit()
"""
    child = """from noscheme import role
component Pin:
    pins:
        1:
            role: role.passive
block Unit:
    inst p: Pin(
        connections:
            1: global.supply
    )
"""
    make_project(tmp_path, source, {"child": child})
    s = RenameSession(tmp_path, {})
    path = tmp_path / "main.nosch"
    result = apply(s.rename(path, source.index("supply:"), "rail"), s.sources)
    assert "global net rail:" in result[path]
    assert "global.rail" in result[tmp_path / "child.nosch"]
