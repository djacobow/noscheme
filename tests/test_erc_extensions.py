import json

import pytest

from noscheme.circuit import elaborate_project
from noscheme.cli import main
from noscheme.diagnostics import DiagnosticError
from noscheme.erc import apply_erc, check_circuit
from noscheme.resolve import resolve_project
from test_circuit import compile_source, make

SOURCE = """from noscheme import bias, std
component Pull:
    pins:
        1:
            role: role.passive
            bias: bias.pull_up
            pull_resistance: 4700
            capacitance: 2p
component Device:
    pins:
        1:
            role: role.open_drain
            bias: bias.none
            capacitance: 10p
board B:
    net bus: nettype.open_drain
        checks:
            pull_up: std.parallel(pull_resistance, where=bias.pull_up, min=2500, max=5000)
            capacitance: std.sum(capacitance, net=wiring_capacitance, max=20p)
        wiring_capacitance: 2p
    inst pull[2]: Pull
        connections:
            1: bus
    inst device: Device
        connections:
            1: bus
"""


def check(tmp_path, source=SOURCE):
    return check_circuit(compile_source(tmp_path, source))


def test_parallel_pulls_count_each_physical_pin_once(tmp_path):
    result = check(tmp_path)
    assert [d.code for d in result] == ["erc.std.parallel"]
    assert result[0].severity == "error" and len(result[0].related) == 2
    assert "2.5kOhm" in result[0].message


def test_capacitance_and_resistance_overload(tmp_path):
    result = check(tmp_path, SOURCE.replace("max=20p", "max=15p"))
    assert {d.application for d in result} == {"net:bus:pull_up", "net:bus:capacitance"}


def test_no_limits_means_no_budget_warnings(tmp_path):
    start = SOURCE.index("        checks:")
    end = SOURCE.index("    inst pull", start)
    source = SOURCE[:start] + SOURCE[end:]
    assert check(tmp_path, source) == ()


def test_missing_capacitance_does_not_hide_known_overload(tmp_path):
    source = SOURCE.replace("            capacitance: 10p\n", "").replace("max=20p", "max=5p")
    result = [d for d in check(tmp_path, source) if d.application.endswith(":capacitance")]
    assert {d.severity for d in result} == {"warning", "error"}


def test_missing_pull_resistance_does_not_invent_maximum_violation(tmp_path):
    source = SOURCE.replace("            pull_resistance: 4700\n", "")
    result = check(tmp_path, source)
    assert [d.severity for d in result] == ["warning"]


def test_known_minimum_violation_with_unknown_additional_pull(tmp_path):
    source = SOURCE.replace(
        "board B:",
        """component Unknown:
    pins:
        1:
            role: role.passive
            bias: bias.pull_up
            capacitance: 0p
board B:
    inst unknown: Unknown
        connections:
            1: bus
""",
    )
    result = [d for d in check(tmp_path, source) if d.application.endswith(":pull_up")]
    assert {d.severity for d in result} == {"error", "warning"}


def test_open_source_and_pulldown_symmetry(tmp_path):
    source = SOURCE.replace("open_drain", "open_source").replace("pull_up", "pull_down")
    assert [d.code for d in check(tmp_path, source)] == ["erc.std.parallel"]


@pytest.mark.parametrize(
    "field,bad",
    [
        ("pull_resistance: 4700", "pull_resistance: 0"),
        ("capacitance: 10p", "capacitance: -1p"),
        ("bias: bias.pull_up", "bias: bias.typo"),
    ],
)
def test_invalid_builtin_metadata(tmp_path, field, bad):
    with pytest.raises(DiagnosticError):
        compile_source(tmp_path, SOURCE.replace(field, bad))


def test_bias_satisfies_digital_idle_without_becoming_output(tmp_path):
    source = (
        SOURCE.replace("nettype.open_drain", "nettype.digital")
        .replace("role.open_drain", "role.digital_input")
        .replace("min=2500", "min=2000")
    )
    assert check(tmp_path, source) == ()


PLUGIN = """from noscheme.erc_registry import value

def register(registry):
    registry.fields("net", {"demo": {"max_pins": "nonnegative"}})
    registry.net_rule("demo.pin_count", check)
    from noscheme.erc_registry import Application
    registry.rule_set("demo.defaults", lambda ctx: [Application("demo_pin_count", "demo.pin_count")] if ctx.net and not ctx.pin else [])

def check(ctx):
    annotation = value(ctx.origin, "demo")
    if annotation and len(ctx.endpoints) > annotation["max_pins"]["value"]:
        yield ctx.diagnostic("demo_pin_count", "too many attached physical pins")
"""


def project(tmp_path, plugin=PLUGIN, source=SOURCE):
    make(tmp_path, source)
    (tmp_path / "checks.py").write_text(plugin)
    with (tmp_path / "noscheme.toml").open("a") as f:
        f.write('erc_modules = ["checks.py"]\nerc_rule_sets = ["std.default", "demo.defaults"]\n')
    return elaborate_project(*resolve_project(tmp_path))[0]


def test_extension_schema_and_check_without_grammar_changes(tmp_path):
    source = SOURCE.replace(
        "    net bus: nettype.open_drain",
        "    net bus: nettype.open_drain\n        demo:\n            max_pins: 2",
    )
    circuit = project(tmp_path, source=source)
    result = apply_erc(circuit)
    assert any(d.code == "erc.demo_pin_count" for d in result)
    assert "demo.pin_count" in circuit["erc"]["checks"]
    assert main(["compile", "--project", str(tmp_path)]) == 0
    assert "erc.demo_pin_count" in (tmp_path / "build" / "erc.json").read_text()


@pytest.mark.parametrize("command", ["check", "compile", "export-kicad", "lsp"])
def test_extension_loaded_once_and_checked_once_per_operation(tmp_path, command, capsys):
    plugin = """from pathlib import Path
def event(name):
    with Path(__file__).with_name("events.txt").open("a") as stream:
        stream.write(name + "\\n")
event("load")
""" + PLUGIN.replace(
        "def register(registry):", 'def register(registry):\n    event("register")'
    ).replace("def check(ctx):", 'def check(ctx):\n    event("check")')
    source = SOURCE.replace(
        "    net bus: nettype.open_drain",
        "    net bus: nettype.open_drain\n        demo:\n            max_pins: 2",
    )
    project(tmp_path, plugin=plugin, source=source)
    log = tmp_path / "events.txt"
    log.write_text("")
    records = tmp_path / "footprints.json"
    records.write_text('{"schema_version": 1, "footprints": {}}')
    for _ in range(2):
        if command == "lsp":
            from noscheme.lsp_analysis import analyze

            result = analyze(tmp_path / "main.nosch", tmp_path, {})
            assert any(d.code == "erc.demo_pin_count" for d in result.diagnostics)
        else:
            args = [command, "--project", str(tmp_path)]
            if command == "export-kicad":
                args += ["--footprints", str(records)]
            assert main(args) == (1 if command == "check" else 0)
            assert "erc.demo_pin_count" in capsys.readouterr().err
    assert log.read_text().splitlines() == ["load", "register", "check"] * 2


def test_plugin_unknown_nested_fields_rejected(tmp_path):
    with pytest.raises(DiagnosticError):
        project(
            tmp_path,
            source=SOURCE.replace(
                "    net bus: nettype.open_drain",
                "    net bus: nettype.open_drain\n        demo:\n            max_pnis: 2",
            ),
        )


def test_registered_check_waiver(tmp_path):
    source = SOURCE.replace(
        "    net bus: nettype.open_drain",
        '''    net bus: nettype.open_drain
        demo:
            max_pins: 2
        waivers:
            demo_pin_count: "Reviewed test fixture."''',
    )
    circuit = project(tmp_path, source=source)
    assert not any(d.code == "erc.demo_pin_count" for d in apply_erc(circuit))
    assert circuit["erc"]["waivers"][0]["diagnostics"]


def test_plugin_mutation_isolated(tmp_path):
    plugin = """def register(registry):
    registry.net_rule("demo.mutator", check)
    registry.rule_set("demo.defaults", lambda ctx: [__import__("noscheme.erc_registry", fromlist=["Application"]).Application("mutator", "demo.mutator")] if ctx.net and not ctx.pin else [])
def check(ctx):
    ctx.circuit["parts"].clear()
    ctx.endpoints.clear()
    return []
"""
    circuit = project(tmp_path, plugin)
    before = json.dumps(circuit["parts"], sort_keys=True)
    with pytest.raises(DiagnosticError, match="failed"):
        apply_erc(circuit)
    assert json.dumps(circuit["parts"], sort_keys=True) == before


def test_plugin_failure_preserves_old_outputs(tmp_path):
    project(tmp_path)
    assert main(["compile", "--project", str(tmp_path)]) == 0
    report = tmp_path / "build" / "erc.json"
    before = report.read_bytes()
    (tmp_path / "checks.py").write_text(
        PLUGIN.replace(
            "    annotation =", '    raise RuntimeError("broken check")\n    annotation ='
        )
    )
    assert main(["compile", "--project", str(tmp_path)]) != 0
    assert report.read_bytes() == before


@pytest.mark.parametrize(
    "plugin",
    [
        'raise RuntimeError("bad import")',
        'def register(r):\n    r.fields("net", {"net_class": "string"})',
        'def register(r):\n    r.fields("net", {"new": "typo"})',
    ],
)
def test_bad_plugin_registration_is_blocking(tmp_path, plugin):
    with pytest.raises(DiagnosticError):
        project(tmp_path, plugin)


def test_registered_checks_receive_configuration_context(tmp_path):
    plugin = PLUGIN.replace(
        '    annotation = value(ctx.origin, "demo")',
        """    assert ctx.circuit["erc_configuration"] in {"a", "b"}
    assert ctx.circuit["erc_effective_choices"]
    annotation = value(ctx.origin, "demo")""",
    )
    source = SOURCE.replace(
        "board B:",
        '''board B:
    metadata:
        erc_choices:
            mode: ["one", "two"]
        configurations:
            a:
                mode: "one"
            b:
                mode: "two"''',
    )
    circuit = project(tmp_path, plugin, source)
    result = apply_erc(circuit)
    assert {d.configuration for d in result} == {"a", "b"}
    assert "erc_configuration" not in circuit


def test_inner_port_capacitance_limit_counts_physical_pins_once(tmp_path):
    source = SOURCE.replace(
        "board B:",
        """block Receiver:
    ports:
        bus: nettype.open_drain
            checks:
                capacitance: std.sum(capacitance, net=wiring_capacitance, max=20p)
    inst inner: Device
        connections:
            1: bus
board B:""",
    ).replace("inst device: Device", "inst device: Receiver")
    source = source.replace(
        "    inst device: Receiver\n        connections:\n            1: bus",
        "    inst device: Receiver\n        connections:\n            bus: bus",
    )
    assert [d.code for d in check(tmp_path, source)] == ["erc.std.parallel"]


def test_explicit_zero_capacitance_is_complete(tmp_path):
    source = SOURCE.replace("capacitance: 2p", "capacitance: 0p").replace(
        "capacitance: 10p", "capacitance: 0p"
    )
    assert not [d for d in check(tmp_path, source) if d.application.endswith(":capacitance")]


def test_builtin_checks_do_not_mutate_circuit(tmp_path):
    from copy import deepcopy

    circuit = compile_source(tmp_path, SOURCE)
    before = deepcopy(circuit)
    check_circuit(circuit)
    assert circuit == before


def test_project_callback_stays_isolated_after_builtin_registration(tmp_path):
    from copy import deepcopy
    from noscheme.erc_registry import load_registry

    circuit = compile_source(tmp_path, SOURCE)
    before = deepcopy(circuit)
    registry = load_registry()

    def mutate(ctx):
        ctx.circuit["parts"].clear()
        ctx.net["declarations"].clear()
        return ()

    from noscheme.erc_registry import Application

    registry.net_rule("demo.mutate", mutate)
    registry.rule_set(
        "demo.defaults", lambda ctx: [Application("mutation", "demo.mutate")] if not ctx.pin else []
    )
    circuit["erc_rule_sets"] = ["std.default", "demo.defaults"]
    with pytest.raises(DiagnosticError, match="failed"):
        check_circuit(circuit, registry=registry)
    assert circuit == dict(before, erc_rule_sets=["std.default", "demo.defaults"])
