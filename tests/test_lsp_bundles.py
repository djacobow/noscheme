"""Bundle tools follow public types across modules and incomplete buffers."""

import pytest

from noscheme.lsp_analysis import analyze
from noscheme.lsp_completion import completion_items
from test_resolve import make_project

LIBRARY = """from noscheme import net
bundle SPI:
  net.digital sck
  net.digital mosi
bundle Buses:
  SPI sd[2]
block Peripheral:
  ports:
    Buses bus
  SPI secret
extend Peripheral Specialized:
  metadata:
    custom:
      note: "inherited"
"""

PREFIX = """import interfaces
from noscheme import net
block B:
  global interfaces.Buses shared
  interfaces.Buses local[2]
  net.digital scalar
  interfaces.Specialized p[2]:
"""


def complete(tmp_path, line, suffix=""):
    make_project(tmp_path, PREFIX, {"interfaces": LIBRARY})
    path = tmp_path / "main.nosch"
    source = PREFIX + "    " + line
    offset = source.index("|")
    source = source.replace("|", "") + suffix
    return source, completion_items(path, tmp_path, {path: source}, offset)


@pytest.mark.parametrize(
    "line",
    [
        "bus.sd[0].|",
        "[0].bus.sd[0].|",
        "[0:2].bus.sd[0].|",
        "bus.sd[0].sck: local[0].sd[1].|",
        "bus.sd[0].sck: local[0:2].sd.|",
        "bus.sd[0].sck: global.shared.sd[0].|",
    ],
)
def test_members_on_both_sides_and_array_selections(tmp_path, line):
    _, choices = complete(tmp_path, line)
    assert {item.label for item in choices} == {"sck", "mosi"}


def test_member_prefix_replaces_tail_preserving_connection(tmp_path):
    source, choices = complete(tmp_path, "bus.sd[0].s|ck: scalar\n")
    assert [item.label for item in choices] == ["sck"]
    item = choices[0]
    assert source[item.start : item.end] == "sck"
    assert source[: item.start] + item.text + source[item.end :] == source


@pytest.mark.parametrize(
    "line",
    [
        "bus.sd[0].sck: scalar.|",
        "secret.|",
        "bus.sd[0].sck: p.secret.|",
        "bus.sd[0].sck: shared.sd.|",  # Explicit global access is required.
        "// bus.sd.|",
        "/* bus.sd.|",
        'bus.sd[0].sck: "local.|',
    ],
)
def test_no_private_scalar_implicit_global_or_comment_members(tmp_path, line):
    assert not complete(tmp_path, line)[1]


def test_unsaved_imported_bundle_and_later_definition(tmp_path):
    make_project(tmp_path, PREFIX, {"interfaces": LIBRARY})
    path = tmp_path / "main.nosch"
    source = PREFIX + "    bus.sd[0]."
    offset = len(source)
    source += '\ncomponent Later:\n  metadata:\n    ref_prefix: "R"\n'
    buffers = {
        path: source,
        tmp_path / "interfaces.nosch": LIBRARY.replace("net.digital mosi", "net.digital data#"),
    }
    assert {item.label for item in completion_items(path, tmp_path, buffers, offset)} == {
        "sck",
        "data#",
    }


@pytest.mark.parametrize("grouped", [False, True])
def test_navigation_and_hover_for_nested_members_on_both_sides(tmp_path, grouped):
    source = PREFIX + "    [0].bus.sd[0].sck: global.shared.sd[0].sck\n"
    library = (
        LIBRARY.replace("net.digital sck\n  net.digital mosi", "net.digital sck, mosi")
        if grouped
        else LIBRARY
    )
    make_project(tmp_path, source, {"interfaces": library})
    path = tmp_path / "main.nosch"
    result = analyze(path, tmp_path, {path: source})
    for needle, target in [
        # Navigation selects the field name, after its type.
        ("bus.sd", "sd[2]"),
        ("].sck", "sck"),
        ("shared.sd", "sd[2]"),
    ]:
        offset = source.index(needle) + needle.index(".") + 1
        ref = result.reference_at(path, offset)
        assert ref is not None, needle
        assert ref.target.filename == str(tmp_path / "interfaces.nosch")
        assert ref.target.start_pos == library.index(target)
        assert "bundle field" in ref.description
    last = source.rindex("sck")
    ref = result.reference_at(path, last)
    assert ref and ref.target.start_pos == library.index("sck")


def test_completion_on_block_public_bundle_port(tmp_path):
    source = """import interfaces
block Local:
  ports:
    interfaces.Buses signals
  interfaces.Peripheral p:
    bus.sd[0].sck: signals.sd[1].
"""
    source += "block B:\n  Local b()\n"
    make_project(tmp_path, source, {"interfaces": LIBRARY})
    path = tmp_path / "main.nosch"
    offset = source.index("signals.sd[1].") + len("signals.sd[1].")
    assert {s.label for s in completion_items(path, tmp_path, {path: source}, offset)} == {
        "sck",
        "mosi",
    }
