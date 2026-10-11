"""Reference results use resolved identity and current source locations."""

from noscheme.lsp_analysis import analyze
from test_resolve import make_project

LIBRARY = """from noscheme import role, net
bundle Bus:
  net.passive data
component Part:
  params:
    string value = "1k"
  pins:
    1 (signal):
      role: role.passive
block Unit:
  params:
    number count = 2
  ports:
    Bus bus
  net.passive wires[count]
  Part<value: "{0}".format(count)> p[count]:
    signal: bus.data
    metadata:
      value: "{0}".format(count)
extend Unit Derived:
  ports:
    bus:
      placeholder: true
"""

SOURCE = """import library
from library import Unit as Alias
from noscheme import net
block B:
  library.Bus wire
  library.Unit<count: 3> a:
    bus: wire
  Alias b:
    bus.data: wire.data
  library.Derived c:
    bus: wire
block Unrelated:
  ports:
    library.Bus bus
  net.passive wire
"""


def declared(source, declaration, start=0):
    """The offset of the name a `Type name` declaration declares."""
    return source.index(declaration, start) + declaration.index(" ") + 1


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
    spans = result.references_at(path, declared(SOURCE, "Bus wire"), False)
    assert texts(spans) == ["wire", "wire", "wire"]
    assert all(s.start_pos < SOURCE.index("block Unrelated:") for s in spans)
    assert len(result.references_at(path, declared(SOURCE, "Bus wire"), True)) == 4
    assert not result.references_at(path, declared(SOURCE, "net.passive wire"), False)


def test_ports_include_inherited_overrides_and_bindings(tmp_path):
    path, result = setup(tmp_path)
    lib = tmp_path / "library.nosch"
    offset = declared(LIBRARY, "Bus bus")
    spans = result.references_at(lib, offset, False)
    assert texts(spans) == ["bus", "bus", "bus", "bus", "bus"]
    assert not any(
        s.start_pos > SOURCE.index("block Unrelated:") for s in spans if s.filename == str(path)
    )
    derived = SOURCE.index("bus: wire", SOURCE.index("library.Derived c:"))
    assert result.references_at(path, derived, False) == spans


def test_parameter_references_include_dimensions_calls_and_metadata(tmp_path):
    _, result = setup(tmp_path)
    count = declared(LIBRARY, "number count")
    spans = result.references_at(tmp_path / "library.nosch", count, False)
    assert texts(spans) == ["count", "count", "count", "count", "count"]
    assert len(result.references_at(tmp_path / "library.nosch", count, True)) == 6


def test_bundle_field_references_from_declaration_and_each_connection_side(tmp_path):
    path, result = setup(tmp_path)
    lib = tmp_path / "library.nosch"
    spans = result.references_at(lib, declared(LIBRARY, "net.passive data"), False)
    assert texts(spans) == ["data", "data", "data"]
    assert result.references_at(path, SOURCE.index("bus.data") + 4, False) == spans
    assert len(result.references_at(lib, declared(LIBRARY, "net.passive data"), True)) == 4


def test_unsaved_imports_and_comments_do_not_make_false_references(tmp_path):
    path, _ = setup(tmp_path)
    changed = SOURCE.replace("bus.data: wire.data", "bus: wire")
    changed += "// wire.data Alias library.Unit\n"
    result = analyze(path, tmp_path, {path: changed})
    data = declared(LIBRARY, "net.passive data")
    spans = result.references_at(tmp_path / "library.nosch", data, False)
    assert len(spans) == 1
    assert spans[0].filename == str(tmp_path / "library.nosch")
    assert not result.references_at(path, changed.index("// wire") + 3)


def test_physical_pin_alias_references(tmp_path):
    _, result = setup(tmp_path)
    lib = tmp_path / "library.nosch"
    spans = result.references_at(lib, LIBRARY.index("1 (signal)"), False)
    assert texts(spans) == ["signal"]
    assert result.references_at(lib, LIBRARY.index("signal)"), False) == spans


def test_physical_pin_range_queries_collect_all_pins(tmp_path):
    source = """from noscheme import net, role
component Part:
  pins:
    1..2:
      role: role.passive
block B:
  net.passive n
  Part p:
    1..2: n
  Part q:
    2: n
"""
    make_project(tmp_path, source)
    path = tmp_path / "main.nosch"
    result = analyze(path, tmp_path, {path: source})
    spans = result.references_at(path, source.index("1..2:"), False)
    assert texts(spans) == ["1..2", "2"]
