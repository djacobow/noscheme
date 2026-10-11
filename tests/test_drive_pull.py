"""Directional output capabilities and mandatory idle-level net contracts."""

import pytest

from noscheme.diagnostics import DiagnosticError
from noscheme.erc import check_circuit
from test_circuit import compile_source


def source(nettype="idle_high", drive="low", pull="up", drivers=1, pulls=1):
    return (
        f"""from noscheme import drive, pull, std
component Driver:
  pins:
    1: [drive.{drive}, pull.none]
component Pull:
  pins:
    1: [role.passive, pull.{pull}]
      pull_resistance: 4.7k
      capacitance: 2p
block B:
  net.{nettype} bus
"""
        + (f"  Driver drivers[{drivers}](1: bus)\n" if drivers else "")
        + (f"  Pull pulls[{pulls}](1: bus)\n" if pulls else "")
    )


def diagnostics(tmp_path, text):
    return check_circuit(compile_source(tmp_path, text))


@pytest.mark.parametrize(
    "nettype,drive,pull", [("idle_high", "low", "up"), ("idle_low", "high", "down")]
)
@pytest.mark.parametrize("drivers,pulls", [(1, 1), (3, 2), (0, 2), (2, 0), (0, 0)])
def test_idle_net_requires_both_contributions(tmp_path, nettype, drive, pull, drivers, pulls):
    result = diagnostics(tmp_path, source(nettype, drive, pull, drivers, pulls))
    codes = {d.code for d in result}
    assert ("erc.missing_driver" in codes) == (drivers == 0)
    assert ("erc.missing_pull" in codes) == (pulls == 0)
    if drivers and pulls:
        assert not result
    assert all(d.severity == "error" for d in result)


@pytest.mark.parametrize(
    "nettype,drive,pull", [("idle_high", "low", "down"), ("idle_low", "high", "up")]
)
def test_opposing_passive_pull_is_rejected(tmp_path, nettype, drive, pull):
    result = diagnostics(tmp_path, source(nettype, drive, pull))
    assert {d.code for d in result} == {"erc.missing_pull", "erc.opposing_pull"}


@pytest.mark.parametrize(
    "nettype,drive,pull", [("idle_high", "high", "up"), ("idle_low", "low", "down")]
)
def test_opposing_active_driver_is_rejected(tmp_path, nettype, drive, pull):
    result = diagnostics(tmp_path, source(nettype, drive, pull))
    assert {d.code for d in result} == {"erc.missing_driver", "erc.incompatible_role"}


def test_pull_none_is_local_and_active_and_passive_can_share_a_pin(tmp_path):
    text = source(drivers=2, pulls=0).replace("pull.none", "pull.up")
    assert not diagnostics(tmp_path, text)


def test_numeric_budgets_and_drive_selectors_remain_independent(tmp_path):
    text = source(drivers=2, pulls=2).replace(
        "  net.idle_high bus\n",
        """  net.idle_high bus:
    checks:
      drivers: std.count(what=drive.low, min=2)
      resistance: std.parallel(pull_resistance, where=pull.up, min=3k)
      capacitance: std.sum(capacitance, where=pull.up, max=3p)
""",
    )
    result = diagnostics(tmp_path, text)
    assert {d.code for d in result} == {"erc.std.parallel", "erc.std.sum"}


def test_external_drive_counts_without_changing_connector_role(tmp_path):
    text = (
        source()
        .replace("    1: [drive.low, pull.none]", "    1: [role.passive, pull.none]")
        .replace(
            "  Driver drivers[1](1: bus)",
            """  Driver drivers[1](1: bus):
    metadata:
      pins:
        1: drive.low""",
        )
    )
    assert not diagnostics(tmp_path, text)
    circuit = compile_source(tmp_path, text)
    pin = next(p for p in circuit["parts"] if p["path"][0] == "drivers")["pins"]["1"]
    assert pin["metadata"]["role"]["value"] == ["passive"]
    assert pin["metadata"]["external_drive"]["value"] == "low"


def test_fixed_direction_cannot_be_overridden_on_an_instance(tmp_path):
    text = source().replace(
        "  Driver drivers[1](1: bus)",
        """  Driver drivers[1](1: bus):
    metadata:
      pins:
        1: drive.high""",
    )
    with pytest.raises(DiagnosticError, match="instance drive differs"):
        compile_source(tmp_path, text)


@pytest.mark.parametrize(
    "old",
    ["bias.pull_up", "role.open_drain", "role.open_source", "net.open_drain", "net.open_source"],
)
def test_retired_names_are_rejected(tmp_path, old):
    text = (
        source().replace("drive.low", old)
        if not old.startswith("net.")
        else source().replace("net.idle_high", old)
    )
    with pytest.raises(DiagnosticError):
        compile_source(tmp_path, text)


def test_public_drive_contract_does_not_invent_an_endpoint(tmp_path):
    text = """from noscheme import drive, pull
component Contact:
  pins:
    1: role.passive
component Pull:
  pins:
    1: [role.passive, pull.up]
block Device:
  ports:
    net.idle_high signal:
      drive: drive.low
  Contact contact(1: signal)
block B:
  net.idle_high bus
  Device device(signal: bus)
  Pull resistor(1: bus)
"""
    result = diagnostics(tmp_path, text)
    assert "erc.missing_driver" in {d.code for d in result}
    assert "erc.port_role" in {d.code for d in result}
    assert not diagnostics(tmp_path, text.replace("1: role.passive", "1: drive.low"))


def test_external_port_drive_counts_as_an_endpoint(tmp_path):
    text = """from noscheme import drive, pull
component Pull:
  pins:
    1: [role.passive, pull.up]
block Device:
  ports:
    net.idle_high signal:
      role: role.passive
  Pull resistor(1: signal)
block B:
  net.idle_high bus
  Device device(signal: bus):
    metadata:
      pins:
        signal:
          external_drive: drive.low
"""
    assert not diagnostics(tmp_path, text)


@pytest.mark.parametrize(
    "role", ["analog_input", "analog_output", "power_input", "power_output", "ground"]
)
def test_drive_does_not_hide_an_incompatible_classification(tmp_path, role):
    text = source().replace("1: [drive.low, pull.none]", f"1: [role.{role}, drive.low, pull.none]")
    with pytest.raises(DiagnosticError, match="directional drive requires"):
        compile_source(tmp_path, text)
