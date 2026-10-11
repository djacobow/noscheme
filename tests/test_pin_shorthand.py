"""Builtin values as pin-entry shorthand, and one instance-level role."""

import pytest

from noscheme.diagnostics import DiagnosticError
from test_circuit import compile_source

SOURCE = """from noscheme import pull, drive
component Contact:
  pins:
    1..2: [role.passive, use.required]
    3:
component Chip:
  pins:
    1: [role.ground, role.power_input]
    2: drive.low
      capacitance: 5p
block Port:
  ports:
    net.passive line
  Contact contact:
    1: line
    2: no_connect
block B:
  net.passive a
  net.passive b
  Contact contact:
    1: a
    2: b
    3: no_connect
    metadata:
      pins:
        1: [role.power_output, pull.none]
        2: role.ground
  Chip chip:
    1: b
    2: a
    metadata:
      pins:
        1: role.ground
  Port port:
    line: a
    metadata:
      pins:
        line:
          external_role: role.digital_input
"""


def pins(circuit):
    return {
        f"{p['identity']}.{name}": {k: v["value"] for k, v in pin["metadata"].items()}
        for p in circuit["parts"]
        for name, pin in p["pins"].items()
    }


def test_shorthand_sets_fields_named_by_value_namespaces(tmp_path):
    result = pins(compile_source(tmp_path, SOURCE))
    assert result["chip.2"]["drive"] == "low"
    assert result["chip.2"]["capacitance"] == 5e-12  # Indented fields still apply.
    assert result["port.contact.1"]["use"] == "required"


def test_instance_role_selects_or_describes_attached_equipment(tmp_path):
    result = pins(compile_source(tmp_path, SOURCE))
    # A passive contact's instance role describes what is attached to it.
    assert result["contact.1"]["role"] == ["passive"]
    assert result["contact.1"]["external_role"] == ["power_output"]
    assert result["contact.1"]["pull"] == "none"
    assert result["contact.2"]["external_role"] == ["ground"]
    # A pin declaring active roles has one selected instead.
    assert result["chip.1"]["role"] == ["ground"]
    assert "external_role" not in result["chip.1"]


@pytest.mark.parametrize(
    "old,new,message",
    [
        ("2: drive.low\n", "2: 5\n", "builtin role, drive, pull, or use"),
        ("2: drive.low\n", "2: [pull.none, pull.up]\n", "takes one value"),
        (
            "2: drive.low\n      capacitance",
            "2: drive.low\n      drive: drive.high\n      capacitance",
            "already set",
        ),
        ("        2: role.ground\n", "        2: use.optional\n", "not allowed"),
        (
            "        1: role.ground\n",
            "        1: role.passive\n",
            "permitted roles",
        ),
        (
            "        2: role.ground\n",
            "        2:\n          external_role: role.ground\n",
            "write role on a component pin",
        ),
    ],
)
def test_shorthand_and_role_errors(tmp_path, old, new, message):
    assert old in SOURCE
    with pytest.raises(DiagnosticError, match=message):
        compile_source(tmp_path, SOURCE.replace(old, new))


def test_shorthand_follows_import_aliases(tmp_path):
    source = "from noscheme import drive as d\n" + SOURCE.replace("2: drive.low\n", "2: d.low\n")
    assert pins(compile_source(tmp_path, source))["chip.2"]["drive"] == "low"
