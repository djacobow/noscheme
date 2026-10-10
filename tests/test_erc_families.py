"""Selecting one standard family must not execute unrelated electrical checks."""

from noscheme.erc import check_circuit
from noscheme.erc_registry import Application, load_registry
from noscheme import erc_connections, erc_current, erc_ports, erc_compatibility, erc_drivers
from test_circuit import compile_source

SOURCE = """component Output:
  pins:
    1:
      role: role.digital_output
board B:
  net.digital bus
  Output outputs[2]:
    1: bus
"""


def test_driver_rule_does_not_run_other_families(tmp_path, monkeypatch):
    circuit = compile_source(tmp_path, SOURCE)
    registry = load_registry()
    registry.rule_set(
        "test.drive",
        lambda ctx: [Application("contention", "std.multiple_drivers")] if not ctx.pin else [],
    )
    circuit["erc_rule_sets"] = ["test.drive"]

    def unexpected(*args):
        raise AssertionError("Unselected family executed")

    for family in (erc_connections, erc_current, erc_ports, erc_compatibility):
        monkeypatch.setattr(family, "net_results", unexpected)
    monkeypatch.setattr(erc_current, "grouped_current_loads", unexpected)
    assert [d.code for d in check_circuit(circuit, registry=registry)] == ["erc.multiple_drivers"]


def test_default_family_results_are_computed_once_per_net(tmp_path, monkeypatch):
    circuit = compile_source(tmp_path, SOURCE)
    calls = []
    for family in (erc_connections, erc_current, erc_ports, erc_compatibility, erc_drivers):
        callback = family.net_results

        def counted(ctx, callback=callback, name=family.__name__):
            calls.append((name, ctx.net.label))
            return callback(ctx)

        monkeypatch.setattr(family, "net_results", counted)
    result = check_circuit(circuit)
    assert [d.code for d in result] == ["erc.multiple_drivers"]
    assert len(calls) == len(set(calls)) == 5
