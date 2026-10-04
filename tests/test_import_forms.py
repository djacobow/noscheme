"""Qualified imports and aliases share the same definition identity."""

import pytest

from noscheme.circuit import elaborate_project
from noscheme.diagnostics import DiagnosticError
from noscheme.lsp_analysis import reference_index
from noscheme.lsp_completion import completion_items
from noscheme.resolve import resolve_project
from test_resolve import make_project

LIBRARY = """from noscheme import role, nettype
component R:
    params:
        value: string = "1kOhm"
    pins:
        1:
            role: role.passive
    metadata:
        footprint: "Test:R"
bundle Bus:
    signal: nettype.passive
block Wrapper:
    ports:
        bus: Bus
    inst r: R(
        connections:
            1: bus.signal
    )
"""

SOURCE = """import Device
from Device import R as KiCadResistor, Bus as SignalBus
import noscheme
from noscheme import f as units, num as numbers, unused as unconnected
component Specialized extends Device.R:
    pins:
        1:
            unused: unconnected.never
    defaults:
        value: units.kOhm(numbers.int(2.0))
board B:
    net a: noscheme.nettype.passive
    net bus: Device.Bus
    net other: SignalBus
    inst r: Device.R(
        connections:
            1: a
    )
    inst s: Specialized(
        connections:
            1: a
    )
    inst alias: KiCadResistor(
        connections:
            1: a
    )
    inst wrapper: Device.Wrapper(
        connections:
            bus: bus
    )
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
    for occurrence in ["Device.R:", "Device.R(", "KiCadResistor(", "Device.Bus"]:
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
        ("Device.R(", "Device.", {"Device.R", "Device.Wrapper"}),
        ("KiCadResistor(", "KiCad", {"KiCadResistor"}),
        ("noscheme.nettype.passive", "noscheme.nettype.pass", {"passive"}),
    ]:
        source = SOURCE.replace(old, new, 1)
        offset = SOURCE.index(old) + len(new)
        result = completion_items(path, tmp_path, {path: source}, offset)
        assert {item.label for item in result} == expected
        if "Device.R" in expected:
            assert all(source[item.start : item.end] == "Device." for item in result)
    # Imported type still supplies pin and footprint hints in incomplete buffers.
    for section, field, expected in [
        ("connections", "", {"1"}),
        ("metadata", 'footprint: "', {"Test:R"}),
    ]:
        source = "import Device\nboard B:\n    inst r: Device.R(\n"
        source += f"        {section}:\n            {field}"
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
            make_project(
                tmp_path, imports + "\nboard B:\n    inst r: Device.R()\n", {"Device": LIBRARY}
            )
        )


def test_qualified_import_does_not_allow_unqualified_or_private_access(tmp_path):
    for name in ["R", "Device.R.hidden"]:
        with pytest.raises(DiagnosticError):
            resolve_project(
                make_project(
                    tmp_path,
                    f"import Device\nboard B:\n    inst r: {name}()\n",
                    {"Device": LIBRARY},
                )
            )


def test_dotted_module_names_and_sibling_imports(tmp_path):
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor/a.nosch").write_text(LIBRARY)
    (tmp_path / "vendor/b.nosch").write_text(LIBRARY)
    source = (
        "import vendor.a\nimport vendor.b\nboard B:\n"
        "    inst a: vendor.a.R()\n    inst b: vendor.b.R()\n"
    )
    project, definitions = resolve_project(make_project(tmp_path, source))
    assert ("vendor.a", "R") in definitions
    assert ("vendor.b", "R") in definitions
    assert "vendor.R" not in project.modules["main"].symbols


@pytest.mark.parametrize(
    ("kind", "prefix", "expected"),
    [
        ("component", "Device.", {"Device.R"}),
        ("block", "Device.", {"Device.Wrapper"}),
        ("component", "KiCad", {"KiCadResistor"}),
        ("component", "", {"Device.R", "KiCadResistor"}),
    ],
)
@pytest.mark.parametrize(
    "suffix", ["", ":\n    metadata:\n        custom:\n            note: true\n"]
)
def test_extends_completion_recovers_incomplete_headers(tmp_path, kind, prefix, expected, suffix):
    header = f"{kind} New extends {prefix}"
    source = "import Device\nfrom Device import R as KiCadResistor\n" + header
    offset = len(source)
    source += suffix + "\nboard B:\n    inst r: Device.R()\n"
    make_project(tmp_path, source, {"Device": LIBRARY})
    path = tmp_path / "main.nosch"
    choices = completion_items(path, tmp_path, {path: source}, offset)
    assert {item.label for item in choices} == expected
    for item in choices:
        assert source[item.start : item.end] == prefix


def test_extends_completion_preserves_following_definition_and_replaces_tail(tmp_path):
    source = (
        "import Device\ncomponent New extends Device.R:\n"
        "    metadata:\n        custom:\n            note: true\n"
        'component Later:\n    metadata:\n        ref_prefix: "R"\n'
        "board B:\n    inst r: Later()\n"
    )
    make_project(tmp_path, source, {"Device": LIBRARY})
    path = tmp_path / "main.nosch"
    offset = source.index("Device.R:") + len("Device.")
    choices = completion_items(path, tmp_path, {path: source}, offset)
    assert [item.label for item in choices] == ["Device.R"]
    assert source[choices[0].start : choices[0].end] == "Device.R"
    offset = source.index("Device.R:")
    assert "Later" in {s.label for s in completion_items(path, tmp_path, {path: source}, offset)}


def test_extends_completion_ignores_comments_and_disallowed_kinds(tmp_path):
    path = tmp_path / "main.nosch"
    make_project(tmp_path, SOURCE, {"Device": LIBRARY})
    for header in [
        "// component New extends Device.",
        "/* component New extends Device.",
        "board New extends Device.",
        "bundle New extends Device.",
    ]:
        source = "import Device\n" + header
        assert not completion_items(path, tmp_path, {path: source}, len(source))
