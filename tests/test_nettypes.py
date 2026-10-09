"""User net types: reusable net metadata and checks on a builtin electrical base."""

import pytest

from noscheme.diagnostics import DiagnosticError
from noscheme.erc import apply_erc
from noscheme.parser import parse
from test_circuit import compile_source

SOURCE = """from noscheme import std, bias
nettype I2CLine extends nettype.open_drain:
  wiring_capacitance: 5p
  checks:
    pull: std.parallel(pull_resistance, where=bias.pull_up, min=1500, max=10000)
    load: std.sum(capacitance, net=wiring_capacitance, max=400p)
nettype FastI2CLine extends I2CLine:
  checks:
    load: std.sum(max=10p)
bundle I2C:
  scl: I2CLine
  sda: FastI2CLine
component Pull:
  pins:
    1:
      role: role.passive
      bias: bias.pull_up
      pull_resistance: 4700
      capacitance: 2p
component Dev:
  pins:
    1..2:
      role: role.open_drain
      bias: bias.none
      capacitance: 10p
board B:
  net bus: I2C
  net extra: I2CLine
    checks:
      pull: std.parallel(min=5000)
  inst p[3]: Pull
    [0].1: bus.scl
    [1].1: bus.sda
    [2].1: extra
  inst d[2]: Dev
    [0].1: bus.scl
    [0].2: bus.sda
    [1].1: extra
    [1].2: no_connect
"""


def failures(circuit):
    return sorted((d.application, d.severity) for d in apply_erc(circuit) if d.severity == "error")


def test_net_types_attach_checks_through_bundles_inheritance_and_overrides(tmp_path):
    circuit = compile_source(tmp_path, SOURCE)
    assert failures(circuit) == [("net:bus.sda:load", "error"), ("net:extra:pull", "error")]
    declarations = {d["path"][-1]: d for n in circuit["nets"] for d in n["declarations"]}
    assert declarations["sda"]["type"] == "open_drain"
    assert declarations["sda"]["nettype"] == "main.FastI2CLine"
    assert declarations["extra"]["metadata"]["wiring_capacitance"]["value"] == 5e-12


def test_port_type_checks_run_on_the_flattened_net_even_when_outer_net_is_unchecked(tmp_path):
    source = (
        SOURCE.replace(
            "board B:",
            """block Receiver:
  ports:
    line: I2CLine
  inst device: Dev
    1: line
    2: no_connect
board B:""",
        )
        .replace(
            "  net extra: I2CLine\n    checks:\n      pull: std.parallel(min=5000)\n",
            "  net extra: nettype.unchecked\n",
        )
        .replace("      [1].1: extra\n      [1].2: no_connect\n", "")
    )
    source = source.replace(
        "  inst d[2]: Dev",
        "  inst receiver: Receiver\n    line: extra\n  inst d[2]: Dev",
    )
    applications = {d.application for d in apply_erc(compile_source(tmp_path, source))}
    # The outer unchecked type skips only standard policies; the port type's checks remain.
    assert "net:receiver.line:pull" not in applications  # 4700 ohm is within the type's bounds
    assert "net:extra:unchecked_net" in applications
    tightened = source.replace("min=1500, max=10000", "min=5000, max=10000")
    applications = {d.application for d in apply_erc(compile_source(tmp_path, tightened))}
    assert "net:receiver.line:pull" in applications


@pytest.mark.parametrize(
    "edit,code",
    [
        (
            ("nettype I2CLine extends nettype.open_drain:", "nettype I2CLine:"),
            "syntax.nettype-base",
        ),
        (("extends nettype.open_drain:", "extends nettype.sideways:"), "metadata.enum"),
        (
            ("nettype FastI2CLine extends I2CLine:", "nettype FastI2CLine extends I2C:"),
            "resolve.kind",
        ),
        (("load: std.sum(max=10p)", "load: std.parallel(max=10)"), "erc.application_checker"),
        (
            (
                "  checks:\n    load: std.sum(max=10p)",
                "  ports:\n    x: nettype.passive",
            ),
            "syntax.section-context",
        ),
        (("  wiring_capacitance: 5p\n", '  footprint: "x"\n'), "metadata.unknown"),
    ],
)
def test_invalid_net_types(tmp_path, edit, code):
    with pytest.raises(DiagnosticError) as error:
        compile_source(tmp_path, SOURCE.replace(*edit))
    assert code in {d.code for d in error.value.diagnostics}


def test_net_type_inheritance_cycle(tmp_path):
    source = SOURCE.replace("extends nettype.open_drain:", "extends FastI2CLine:")
    with pytest.raises(DiagnosticError, match="inheritance cycle"):
        compile_source(tmp_path, source)


def test_nettype_is_contextual():
    tree = parse("board B:\n  net nettype: nettype.passive\n", "main.nosch")
    assert tree.items[0].body[0].name.text == "nettype"


def test_same_type_application_runs_once_per_net(tmp_path):
    source = SOURCE.replace(
        "board B:",
        """block Receiver:
  ports:
    line: I2CLine
  inst device: Dev
    1: line
    2: no_connect
board B:""",
    ).replace("      [1].1: extra\n      [1].2: no_connect\n", "")
    source = source.replace(
        "  inst d[2]: Dev",
        "  inst receiver: Receiver\n    line: extra\n  inst d[2]: Dev",
    ).replace("    checks:\n      pull: std.parallel(min=5000)\n", "")
    tight = source.replace("min=1500, max=10000", "min=5000, max=10000")

    def pulls(source):
        return [
            a
            for a, _ in failures(compile_source(tmp_path, source))
            if a.endswith(":pull") and "bus" not in a
        ]

    assert pulls(tight) == ["net:extra:pull"]
    # An overridden argument is a different assertion and runs separately.
    override = tight.replace(
        "  net extra: I2CLine\n",
        "  net extra: I2CLine\n    checks:\n      pull: std.parallel(min=6000)\n",
    )
    assert pulls(override) == [
        "net:extra:pull",
        "net:receiver.line:pull",
    ]


OWNER = """import test
nettype Line extends nettype.passive:
  checks:
    limit: test.port_limit(max=0)
component P:
  pins:
    1:
      role: role.passive
      unused: unused.always
block Holder:
  ports:
    line: Line
  inst p: P
    1: line
board B:
  net outer: nettype.passive
  inst holder: Holder
    line: outer
"""

PLUGIN = """def register(r):
  r.net_rule("test.port_limit", limit, parameters={"max": "number"})
  r.net_rule("test.net_limit", limit, parameters={"max": "number"}, net_only=True)
def limit(ctx, *, max):
  if ctx.owner["kind"] == "port" and len(ctx.pins) > max:
    yield ctx.error("port has too many endpoints")
"""


@pytest.mark.parametrize("rule,deduplicated", [("port_limit", False), ("net_limit", True)])
def test_only_net_only_rules_are_deduplicated(tmp_path, rule, deduplicated):
    from noscheme.circuit import elaborate_project
    from noscheme.resolve import resolve_project
    from test_circuit import make

    def limits(source):
        make(tmp_path, source.replace("port_limit", rule))
        (tmp_path / "checks.py").write_text(PLUGIN)
        with (tmp_path / "noscheme.toml").open("a") as stream:
            stream.write('erc_modules = ["checks.py"]\nerc_rule_sets = []\n')
        circuit = elaborate_project(*resolve_project(tmp_path))[0]
        return [d.application for d in apply_erc(circuit)]

    # Only the port declares the type: its owner-dependent check fails either way.
    assert limits(OWNER) == ["net:holder.line:limit"]
    # The board net uses the type too. An owner-dependent rule keeps the port's
    # application; a net-only rule runs once, on the outer net.
    shared = OWNER.replace("  net outer: nettype.passive", "  net outer: Line")
    assert limits(shared) == ([] if deduplicated else ["net:holder.line:limit"])
