"""Qualified imports and aliases share the same definition identity."""

import pytest

from noscheme.circuit import elaborate_project
from noscheme.diagnostics import DiagnosticError
from noscheme.lsp_analysis import reference_index
from noscheme.lsp_completion import completion_items
from noscheme.resolve import resolve_project
from test_resolve import make_project

LIBRARY = """from noscheme import role, net
component R:
  params:
    resistance value = 1k
  pins:
    1:
      role: role.passive
  metadata:
    footprint: "Test:R"
bundle Bus:
  net.passive signal
block Wrapper:
  ports:
    Bus bus
  R r:
    1: bus.signal
"""

SOURCE = """import Device
from Device import R as KiCadResistor, Bus as SignalBus
import noscheme
from noscheme import num as numbers, use as unconnected
extend Device.R<value: numbers.int(2.0) * 1k> Specialized:
  pins:
    1:
      use: unconnected.required
block B:
  noscheme.net.passive a
  Device.Bus bus
  SignalBus other
  Device.R r:
    1: a
  Specialized s:
    1: a
  KiCadResistor alias:
    1: a
  Device.Wrapper wrapper:
    bus: bus
"""


def test_qualified_and_aliased_types_helpers_and_navigation(tmp_path):
    project, definitions = resolve_project(make_project(tmp_path, SOURCE, {"Device": LIBRARY}))
    symbols = project.modules["main"].symbols
    assert symbols["Device.R"] is symbols["KiCadResistor"]
    assert "R" not in symbols
    circuit, _ = elaborate_project(project, definitions)
    assert len(circuit["parts"]) == 4
    assert next(p for p in circuit["parts"] if p["identity"] == "s")["value"]["value"] == "2kOhm"
    references = reference_index(project, definitions)
    source_path = tmp_path / "main.nosch"
    for occurrence in ["Device.R<", "Device.R r", "KiCadResistor alias", "Device.Bus"]:
        offset = SOURCE.index(occurrence) + (
            len(occurrence.split(".")[0]) + 1 if "." in occurrence else 0
        )
        matches = [
            r
            for r in references
            if r.span.filename == str(source_path) and r.span.start_pos <= offset < r.span.end_pos
        ]
        assert matches, occurrence
        assert matches[0].target.filename == str(tmp_path / "Device.nosch")


def test_qualified_completion_and_aliases(tmp_path):
    make_project(tmp_path, SOURCE, {"Device": LIBRARY})
    path = tmp_path / "main.nosch"
    for old, new, expected in [
        ("Device.R r:", "Device. r:", {"R", "Wrapper", "Bus"}),
        ("KiCadResistor alias", "KiCad alias", {"KiCadResistor"}),
        ("noscheme.net.passive", "noscheme.net.pass", {"passive"}),
    ]:
        source = SOURCE.replace(old, new, 1)
        offset = SOURCE.index(old) + len(new.split(" ")[0])
        result = completion_items(path, tmp_path, {path: source}, offset)
        # Device also re-exports the builtin net namespace it imports.
        assert {item.label for item in result if not item.label.startswith("net.")} == expected
        if "R" in expected:
            assert all(source[item.start : item.end] == "" for item in result)
    # Imported type still supplies pin and footprint hints in incomplete buffers.
    for section, field, expected in [
        ("connections", "", {"1"}),
        ("metadata", 'footprint: "', {"Test:R"}),
    ]:
        source = "import Device\nblock B:\n  Device.R r:\n"
        source += f"    {section}:\n      {field}"
        assert {
            s.label for s in completion_items(path, tmp_path, {path: source}, len(source))
        } == expected


@pytest.mark.parametrize(
    "imports",
    [
        "import Device\nimport Device",
        "import Device\nfrom Device import R as Device",
        "from Device import R as Device\nimport Device",
        "from Device import R as X, Bus as X",
    ],
)
def test_import_binding_collisions(tmp_path, imports):
    with pytest.raises(DiagnosticError, match="import.duplicate-name"):
        resolve_project(
            make_project(tmp_path, imports + "\nblock B:\n  Device.R r()\n", {"Device": LIBRARY})
        )


def test_qualified_import_does_not_allow_unqualified_or_private_access(tmp_path):
    for name in ["R", "Device.R.hidden"]:
        with pytest.raises(DiagnosticError):
            resolve_project(
                make_project(
                    tmp_path,
                    f"import Device\nblock B:\n  {name} r()\n",
                    {"Device": LIBRARY},
                )
            )


def test_dotted_module_names_and_sibling_imports(tmp_path):
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor/a.nosch").write_text(LIBRARY)
    (tmp_path / "vendor/b.nosch").write_text(LIBRARY)
    source = "import vendor.a\nimport vendor.b\nblock B:\n  vendor.a.R a()\n  vendor.b.R b()\n"
    project, definitions = resolve_project(make_project(tmp_path, source))
    assert ("vendor.a", "R") in definitions
    assert ("vendor.b", "R") in definitions
    assert "vendor.R" not in project.modules["main"].symbols


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [
        ("Device.", {"R", "Wrapper"}),
        ("KiCad", {"KiCadResistor"}),
        ("", {"B", "Device.R", "Device.Wrapper", "KiCadResistor"}),
    ],
)
@pytest.mark.parametrize("suffix", ["", " New:\n  metadata:\n    custom:\n      note: true\n"])
def test_extend_completion_recovers_incomplete_headers(tmp_path, prefix, expected, suffix):
    source = "import Device\nfrom Device import R as KiCadResistor\nextend " + prefix
    offset = len(source)
    source += suffix + "\nblock B:\n  Device.R r()\n"
    make_project(tmp_path, source, {"Device": LIBRARY})
    path = tmp_path / "main.nosch"
    choices = completion_items(path, tmp_path, {path: source}, offset)
    assert {item.label for item in choices if "net." not in item.label} == expected
    for item in choices:
        assert source[item.start : item.end] == prefix.rsplit(".", 1)[-1]


def test_extend_completion_preserves_following_definition_and_replaces_tail(tmp_path):
    source = (
        "import Device\nextend Device.R New:\n"
        "  metadata:\n    custom:\n      note: true\n"
        'component Later:\n  metadata:\n    ref_prefix: "R"\n'
        "block B:\n  Later r()\n"
    )
    make_project(tmp_path, source, {"Device": LIBRARY})
    path = tmp_path / "main.nosch"
    offset = source.index("Device.R New") + len("Device.")
    choices = completion_items(path, tmp_path, {path: source}, offset)
    labels = [item.label for item in choices if not item.label.startswith("net.")]
    assert labels == ["R", "Wrapper"]
    assert all(source[item.start : item.end] == "R" for item in choices)
    offset = source.index("Device.R New")
    assert "Later" in {s.label for s in completion_items(path, tmp_path, {path: source}, offset)}


def test_extend_completion_ignores_comments(tmp_path):
    path = tmp_path / "main.nosch"
    make_project(tmp_path, SOURCE, {"Device": LIBRARY})
    for header in ["// extend Device.", "/* extend Device."]:
        source = "import Device\n" + header
        assert not completion_items(path, tmp_path, {path: source}, len(source))
