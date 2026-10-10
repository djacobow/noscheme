from pathlib import Path
import json

import pytest

from noscheme.diagnostics import DiagnosticError
from noscheme.project import load_project
from noscheme.resolve import Resolver, resolve_project
from noscheme.cli import main


PRELUDE = "from noscheme import role, unused, net, num\n"
BOARD = "\nboard B:\n  net.passive n\n"


def make_project(tmp_path, text, libraries=None):
    (tmp_path / "noscheme.toml").write_text(
        'language_version = "0.1"\nboard = "main.B"\nsource_paths = ["."]\nlibrary_paths = []\noutput_dir = "build"\n'
    )
    (tmp_path / "main.nosch").write_text(text)
    for name, source in (libraries or {}).items():
        (tmp_path / (name + ".nosch")).write_text(source)
    return tmp_path


def resolve(tmp_path, text):
    return resolve_project(make_project(tmp_path, PRELUDE + text + BOARD))[1]


def test_inheritance_merge_alias_replacement_and_defaults(tmp_path):
    definitions = resolve(
        tmp_path,
        """extend Base<value: "2kOhm"> Child:
  pins:
    01 (new):
      unused: unused.always
  metadata:
    custom:
      nested:
        changed: "child"
component Base:
  params:
    string value = "1k"
  pins:
    01 (old):
      unused: unused.never
    1:
      role: role.passive
  metadata:
    custom:
      nested:
        retained: 1
        changed: "base"
""",
    )
    parent, child = definitions["main", "Base"], definitions["main", "Child"]
    assert child.aliases == {"01": "01", "1": "1", "new": "01"}
    assert "old" in parent.aliases
    assert child.interface["01"].metadata["unused"].value == "always"
    assert parent.interface["01"].metadata["unused"].value == "never"
    nested = child.metadata["custom"].value["nested"].value
    assert nested["retained"].value == 1
    assert nested["changed"].value == "child"
    assert parent.metadata["custom"].value["nested"].value["changed"].value == "base"
    assert child.parameters["value"].default.value.value == "2kOhm"


def test_inherited_body_and_default_keep_original_module(tmp_path):
    p = make_project(
        tmp_path,
        PRELUDE
        + 'from library import Base\nextend Base Child:\n  metadata:\n    custom:\n      note: "child"\n'
        + BOARD,
        {
            "library": PRELUDE
            + """component R:
  params:
    string value
  pins:
    1:
      role: role.passive
block Base:
  params:
    string value = "10Ohm"
  ports:
    net.passive a
  R<value: value> r:
    1: a
"""
        },
    )
    definitions = resolve_project(p)[1]
    child = definitions["main", "Child"]
    assert child.body_module == "library"
    assert child.parameters["value"].default.module == "library"
    assert child.interface["a"].type.module == "library"


@pytest.mark.parametrize(
    "text,code",
    [
        (
            "extend Missing C:\n  metadata:\n    custom:\n      a: 1\n",
            "resolve.missing-definition",
        ),
        (
            "extend Z A:\n  metadata:\n    custom:\n      a: 1\nextend A Z:\n  metadata:\n    custom:\n      b: 1\n",
            "resolve.inheritance-cycle",
        ),
        ("component C:\n  params:\n    quantity x\n", "resolve.parameter-type"),
        (
            "component C:\n  pins:\n    01..02:\n      role: role.passive\n",
            "resolve.pin-range",
        ),
        (
            "component C:\n  pins:\n    1..2:\n      role: role.passive\n    2:\n      role: role.passive\n",
            "resolve.duplicate-pin",
        ),
        (
            "component C:\n  pins:\n    1 (other):\n    2 (other):\n",
            "resolve.alias",
        ),
        (
            'component C:\n  pins:\n    1 ("2"):\n    2:\n      role: role.passive\n',
            "resolve.alias",
        ),
        ("component C:\n  metadata:\n    typo: 1\n", "metadata.unknown"),
        ("component C:\n  metadata:\n    footprint: 5\n", "metadata.value"),
        ("component C:\n  pins:\n    1:\n      role: passive\n", "metadata.enum"),
        ("component C:\n  pins:\n    1:\n      role: role.typo\n", "metadata.enum"),
        (
            "component C:\n  pins:\n    1:\n      max_load_current: -1m\n",
            "metadata.value",
        ),
        ("block C:\n  ports:\n    Missing a\n", "resolve.missing-definition"),
        ("block C:\n  ports:\n    net.typo a\n", "metadata.enum"),
        (
            'component C:\n  metadata:\n    waivers:\n      x: "reason"\n',
            "metadata.unknown",
        ),
        ("component C:\n  params:\n    string value = missing\n", "resolve.expression-name"),
        ("component C:\n  params:\n    string value = num.typo(10)\n", "resolve.helper"),
        (
            'component C:\n  params:\n    string value = "x".__class__\n',
            "resolve.expression",
        ),
    ],
)
def test_definition_errors(tmp_path, text, code):
    with pytest.raises(DiagnosticError) as exc:
        resolve(tmp_path, text)
    assert exc.value.diagnostics[0].code == code


@pytest.mark.parametrize(
    "override,code",
    [
        ("pins:\n    2:\n      role: role.passive", "resolve.specialization"),
        ("", "resolve.default"),
        ('pins:\n    1 ("2"):', "resolve.alias"),
    ],
)
def test_invalid_specialization(tmp_path, override, code):
    base = "component Base:\n  pins:\n    1..2:\n      role: role.passive\n"
    # The added-pin case must actually introduce a new pin.
    if code == "resolve.specialization":
        override = override.replace("2:", "3:")
    header = "extend Base<other: 1> Child" if code == "resolve.default" else "extend Base Child:"
    with pytest.raises(DiagnosticError) as exc:
        resolve(tmp_path, base + header + "\n  " + override + "\n")
    assert exc.value.diagnostics[0].code == code


BASE = """component C:
  pins:
    1 (A):
      role: [role.digital_input, role.passive]
      unused: unused.never
block Hidden:
  ports:
    net.passive public:
      placeholder: true
  net.passive private
"""


@pytest.mark.parametrize(
    "instance,code",
    [
        ("C c:\n    9: n", "resolve.private"),
        ("Hidden h:\n    private: n", "resolve.private"),
        ("C c:\n    1: missing", "resolve.net"),
        (
            "C c:\n    1: h.private\n  Hidden h()",
            "resolve.net",
        ),
        (
            "C c:\n    metadata:\n      pins:\n        1:\n          unused: unused.always",
            "metadata.unknown",
        ),
        (
            "C c:\n    metadata:\n      pins:\n        1:\n          role: role.digital_output",
            "metadata.constraint",
        ),
        (
            "C c:\n    metadata:\n      pins:\n        1:\n          role: role.passive\n        A:\n          role: role.passive",
            "resolve.duplicate-pin",
        ),
        ("C<extra: 1> c\n", "resolve.argument"),
    ],
)
def test_instance_privacy_and_constraints(tmp_path, instance, code):
    with pytest.raises(DiagnosticError) as exc:
        resolve_project(make_project(tmp_path, PRELUDE + BASE + BOARD + "  " + instance + "\n"))
    assert exc.value.diagnostics[0].code == code


def test_valid_role_selection_and_global_deferred(tmp_path):
    resolve_project(
        make_project(
            tmp_path,
            PRELUDE
            + BASE
            + BOARD
            + """  C c:
    A: global.gnd
    metadata:
      pins:
        1:
          role: role.digital_input
""",
        )
    )


def test_bundles_and_nested_public_field_selection(tmp_path):
    definitions = resolve(
        tmp_path,
        """bundle Bus:
  net.digital data[4]
block Wrapper:
  ports:
    Bus bus:
      placeholder: true
block Use:
  Bus n
  Wrapper w:
    bus.data: n.data
""",
    )
    assert definitions["main", "Bus"].interface["data"].type


def test_reference_draft_and_example(tmp_path, stm32_libraries):
    root = Path(__file__).resolve().parents[1]
    resolve_project(root / "examples/blink")
    source = (root / "examples/stm32h523/stm32h523.nosch").read_text()

    p = make_project(tmp_path, source)
    config = p / "noscheme.toml"
    config.write_text(config.read_text().replace('main.B"', 'main.STM32H523Reference"'))
    assert ("components", "UartHeaderJST3") in resolve_project(p)[1]


def test_cli_resolve_is_explicit_about_remaining_stages(tmp_path, capsys):
    p = make_project(tmp_path, PRELUDE + BOARD)
    assert main(["resolve", "--project", str(p)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["stage"] == "definition-resolution"
    assert "expression-evaluation" in result["remaining_stages"]
    (p / "main.nosch").write_text(
        PRELUDE + "extend Missing C:\n  metadata:\n    custom:\n      a: 1\n" + BOARD
    )
    assert main(["resolve", "--project", str(p), "--diagnostics", "json"]) == 1
    assert (
        json.loads(capsys.readouterr().err)["diagnostics"][0]["code"]
        == "resolve.missing-definition"
    )


def test_no_inheritance_depth_limit(tmp_path):
    source = "component C0:\n  metadata:\n    custom:\n      x: 0\n"
    source += "".join(
        f"extend C{i - 1} C{i}:\n  metadata:\n    custom:\n      x: {i}\n" for i in range(1, 1050)
    )
    definitions = resolve(tmp_path, source)
    assert definitions["main", "C1049"].metadata["custom"].value["x"].value == 1049


def test_unimported_metadata_namespace(tmp_path):
    p = make_project(
        tmp_path,
        "component C:\n  pins:\n    1:\n      role: role.passive\nboard B:\n  metadata:\n    custom:\n      x: 1\n",
    )
    with pytest.raises(DiagnosticError) as exc:
        resolve_project(p)
    assert exc.value.diagnostics[0].code == "metadata.enum"


def test_recursive_bundle(tmp_path):
    with pytest.raises(DiagnosticError) as exc:
        resolve(tmp_path, "bundle Bus:\n  Bus nested\n")
    assert exc.value.diagnostics[0].code == "resolve.bundle-cycle"


def test_custom_nested_values_and_dnp_are_not_electrical_behavior(tmp_path):
    definitions = resolve(
        tmp_path,
        'component C:\n  metadata:\n    custom:\n      dnp: true\n      tags: ["test", 3]\n',
    )
    custom = definitions["main", "C"].metadata["custom"].value
    assert custom["dnp"].value is True
    assert custom["tags"].value == ("test", 3)


def test_expansion_limit_is_configurable(tmp_path):
    p = make_project(
        tmp_path,
        PRELUDE + "component C:\n  pins:\n    1..4:\n      role: role.passive\n" + BOARD,
    )
    with pytest.raises(DiagnosticError) as exc:
        Resolver(load_project(p), max_pins=3).resolve_all()
    assert exc.value.diagnostics[0].code == "resolve.pin-range"
