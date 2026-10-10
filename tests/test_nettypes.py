"""User net types: reusable net metadata and checks on a builtin electrical base."""

import pytest

from noscheme.diagnostics import DiagnosticError
from noscheme.erc import apply_erc
from noscheme.parser import parse
from test_circuit import compile_source

SOURCE = """from noscheme import std, bias
extend net.open_drain I2CLine:
  wiring_capacitance: 5p
  checks:
    pull: std.parallel(pull_resistance, where=bias.pull_up, min=1500, max=10000)
    load: std.sum(capacitance, net=wiring_capacitance, max=400p)
extend I2CLine FastI2CLine:
  checks:
    load: std.sum(max=10p)
bundle I2C:
  I2CLine scl
  FastI2CLine sda
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
  I2C bus
  I2CLine extra:
    checks:
      pull: std.parallel(min=5000)
  Pull p[3]:
    [0].1: bus.scl
    [1].1: bus.sda
    [2].1: extra
  Dev d[2]:
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
    I2CLine line
  Dev device:
    1: line
    2: no_connect
board B:""",
        )
        .replace(
            "  I2CLine extra:\n    checks:\n      pull: std.parallel(min=5000)\n",
            "  net.unchecked extra\n",
        )
        .replace("    [1].1: extra\n    [1].2: no_connect\n", "")
    )
    source = source.replace(
        "  Dev d[2]",
        "  Receiver receiver:\n    line: extra\n  Dev d[2]",
    )
    applications = {d.application for d in apply_erc(compile_source(tmp_path, source))}
    # The outer unchecked type skips only standard policies; the port type's checks remain.
    assert "net:receiver.line:pull" not in applications  # 4700 ohm is within the type's bounds
    assert "net:extra:unchecked_net" not in applications
    tightened = source.replace("min=1500, max=10000", "min=5000, max=10000")
    applications = {d.application for d in apply_erc(compile_source(tmp_path, tightened))}
    assert "net:receiver.line:pull" in applications


@pytest.mark.parametrize(
    "edit,code",
    [
        (("extend net.open_drain I2CLine", "extend net I2CLine"), "resolve.missing-definition"),
        (("extend net.open_drain I2CLine", "extend net.sideways I2CLine"), "metadata.enum"),
        (("extend I2CLine FastI2CLine", "extend I2C FastI2CLine"), "syntax.inheritance-context"),
        (("extend I2CLine FastI2CLine", "extend I2CLine<1> FastI2CLine"), "resolve.parameter"),
        (("load: std.sum(max=10p)", "load: std.parallel(max=10)"), "erc.application_checker"),
        (
            (
                "  checks:\n    load: std.sum(max=10p)",
                "  ports:\n    net.passive x",
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
    source = SOURCE.replace("extend net.open_drain I2CLine", "extend FastI2CLine I2CLine")
    with pytest.raises(DiagnosticError, match="inheritance cycle"):
        compile_source(tmp_path, source)


def test_net_and_nettype_are_contextual():
    tree = parse("board B:\n  net.passive net, nettype\n", "main.nosch")
    assert [d.name.text for d in tree.items[0].body[0].declarators] == ["net", "nettype"]


def test_same_type_application_runs_once_per_net(tmp_path):
    source = SOURCE.replace(
        "board B:",
        """block Receiver:
  ports:
    I2CLine line
  Dev device:
    1: line
    2: no_connect
board B:""",
    ).replace("    [1].1: extra\n    [1].2: no_connect\n", "")
    source = source.replace(
        "  Dev d[2]",
        "  Receiver receiver:\n    line: extra\n  Dev d[2]",
    ).replace(
        "  I2CLine extra:\n    checks:\n      pull: std.parallel(min=5000)\n", "  I2CLine extra\n"
    )
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
        "  I2CLine extra\n",
        "  I2CLine extra:\n    checks:\n      pull: std.parallel(min=6000)\n",
    )
    assert pulls(override) == [
        "net:extra:pull",
        "net:receiver.line:pull",
    ]


OWNER = """import test
extend net.passive Line:
  checks:
    limit: test.port_limit(max=0)
component P:
  pins:
    1:
      role: role.passive
      unused: unused.always
block Holder:
  ports:
    Line line
  P p:
    1: line
board B:
  net.passive outer
  Holder holder:
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
    shared = OWNER.replace("  net.passive outer", "  Line outer")
    assert limits(shared) == ([] if deduplicated else ["net:holder.line:limit"])
