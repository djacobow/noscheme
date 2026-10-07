"""Reference results use resolved identity and current source locations."""

from noscheme.lsp_analysis import analyze
from test_resolve import make_project

LIBRARY = """from noscheme import role, nettype
bundle Bus:
    data: nettype.passive
component Part:
    params:
        value: string = "1k"
    pins:
        1:
            aliases: [signal]
            role: role.passive
block Unit:
    params:
        count: number = 2
    ports:
        bus: Bus
    net wires: nettype.passive[count]
    inst p[count]: Part
        connections:
            signal: bus.data
        params:
            value: "{0}".format(count)
        metadata:
            value: "{0}".format(count)
block Derived extends Unit:
    ports:
        bus:
            placeholder: true
"""

SOURCE = """import library
from library import Unit as Alias
from noscheme import nettype
board B:
    net wire: library.Bus
    inst a: library.Unit
        connections:
            bus: wire
        params:
            count: 3
    inst b: Alias
        connections:
            bus.data: wire.data
    inst c: library.Derived
        connections:
            bus: wire
block Unrelated:
    ports:
        bus: library.Bus
    net wire: nettype.passive
"""


def setup(tmp_path):
    make_project(tmp_path, SOURCE, {"library": LIBRARY})
    path = tmp_path / "main.nosch"
    return path, analyze(path, tmp_path, {path: SOURCE})


def texts(spans):
    from pathlib import Path

    return [Path(s.filename).read_text()[s.start_pos : s.end_pos] for s in spans]


def test_component_aliases_imports_and_qualified_uses(tmp_path):
    path, result = setup(tmp_path)
    lib = tmp_path / "library.nosch"
    offset = LIBRARY.index("Unit:")
    spans = result.references_at(lib, offset, False)
    assert texts(spans) == ["Unit", "Unit", "Alias", "library.Unit", "Alias"]
    all_spans = result.references_at(lib, offset, True)
    assert len(all_spans) == len(spans) + 1
    assert result.references_at(path, SOURCE.index("Alias\n"), False) == spans
    assert len({(s.filename, s.start_pos, s.end_pos) for s in spans}) == len(spans)


def test_local_net_scope_and_declaration_flag(tmp_path):
    path, result = setup(tmp_path)
    spans = result.references_at(path, SOURCE.index("wire:"), False)
    assert texts(spans) == ["wire", "wire", "wire"]
    assert all(s.start_pos < SOURCE.index("block Unrelated:") for s in spans)
    assert len(result.references_at(path, SOURCE.index("wire:"), True)) == 4
    assert not result.references_at(path, SOURCE.rindex("wire:"), False)


def test_ports_include_inherited_overrides_and_bindings(tmp_path):
    path, result = setup(tmp_path)
    lib = tmp_path / "library.nosch"
    offset = LIBRARY.index("bus: Bus")
    spans = result.references_at(lib, offset, False)
    assert texts(spans) == ["bus", "bus", "bus", "bus", "bus"]
    assert not any(
        s.start_pos > SOURCE.index("block Unrelated:") for s in spans if s.filename == str(path)
    )
    derived = SOURCE.index("bus: wire", SOURCE.index("inst c:"))
    assert result.references_at(path, derived, False) == spans


def test_parameter_references_include_dimensions_calls_and_metadata(tmp_path):
    _, result = setup(tmp_path)
    spans = result.references_at(tmp_path / "library.nosch", LIBRARY.index("count:"), False)
    assert texts(spans) == ["count", "count", "count", "count", "count"]
    assert len(result.references_at(tmp_path / "library.nosch", LIBRARY.index("count:"), True)) == 6


def test_bundle_field_references_from_declaration_and_each_connection_side(tmp_path):
    path, result = setup(tmp_path)
    lib = tmp_path / "library.nosch"
    spans = result.references_at(lib, LIBRARY.index("data:"), False)
    assert texts(spans) == ["data", "data", "data"]
    assert result.references_at(path, SOURCE.index("bus.data") + 4, False) == spans
    assert len(result.references_at(lib, LIBRARY.index("data:"), True)) == 4


def test_unsaved_imports_and_comments_do_not_make_false_references(tmp_path):
    path, _ = setup(tmp_path)
    changed = SOURCE.replace("bus.data: wire.data", "bus: wire")
    changed += "// wire.data Alias library.Unit\n"
    result = analyze(path, tmp_path, {path: changed})
    spans = result.references_at(tmp_path / "library.nosch", LIBRARY.index("data:"), False)
    assert len(spans) == 1
    assert spans[0].filename == str(tmp_path / "library.nosch")
    assert not result.references_at(path, changed.index("// wire") + 3)


def test_physical_pin_alias_references(tmp_path):
    _, result = setup(tmp_path)
    lib = tmp_path / "library.nosch"
    spans = result.references_at(lib, LIBRARY.index("1:"), False)
    assert texts(spans) == ["signal"]
    assert result.references_at(lib, LIBRARY.index("signal]"), False) == spans


def test_physical_pin_range_queries_collect_all_pins(tmp_path):
    source = """from noscheme import nettype, role
component Part:
    pins:
        1..2:
            role: role.passive
board B:
    net n: nettype.passive
    inst p: Part
        connections:
            1..2: n
    inst q: Part
        connections:
            2: n
"""
    make_project(tmp_path, source)
    path = tmp_path / "main.nosch"
    result = analyze(path, tmp_path, {path: source})
    spans = result.references_at(path, source.index("1..2:"), False)
    assert texts(spans) == ["1..2", "2"]
