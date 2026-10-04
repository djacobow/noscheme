import json

import pytest

from noscheme.circuit import elaborate_project
from noscheme.cli import main
from noscheme.diagnostics import DiagnosticError
from noscheme.erc import apply_erc, check_circuit
from noscheme.resolve import resolve_project
from test_circuit import compile_source, make

SOURCE = """from noscheme import bias
component Pull:
    pins:
        1:
            role: role.passive
            bias: bias.pull_up
            pull_resistance_ohm: 4700
            capacitance_pF: 2
component Device:
    pins:
        1:
            role: role.open_drain
            capacitance_pF: 10
board B:
    net bus: nettype.open_drain
        min_pull_up_resistance_ohm: 2500
        max_pull_up_resistance_ohm: 5000
        max_capacitance_pF: 20
        wiring_capacitance_pF: 2
    inst pull[2]: Pull(
        connections:
            1: bus
    )
    inst device: Device(
        connections:
            1: bus
    )
"""


def check(tmp_path, source=SOURCE):
    return check_circuit(compile_source(tmp_path, source))


def test_parallel_pulls_count_each_physical_pin_once(tmp_path):
    result = check(tmp_path)
    assert [d.code for d in result] == ["erc.pull_resistance_budget"]
    assert result[0].severity == "error" and len(result[0].related) == 2
    assert "2500" in result[0].message


def test_capacitance_and_resistance_overload(tmp_path):
    result = check(tmp_path, SOURCE.replace("max_capacitance_pF: 20", "max_capacitance_pF: 15"))
    assert {d.code for d in result} == {"erc.pull_resistance_budget", "erc.capacitance_budget"}


def test_no_limits_means_no_budget_warnings(tmp_path):
    source = (
        "\n".join(
            line
            for line in SOURCE.splitlines()
            if not any(
                key in line
                for key in (
                    "min_pull_",
                    "max_pull_",
                    "max_capacitance",
                    "capacitance_pF",
                    "pull_resistance_ohm",
                )
            )
        )
        + "\n"
    )
    assert check(tmp_path, source) == ()


def test_missing_capacitance_does_not_hide_known_overload(tmp_path):
    source = SOURCE.replace("            capacitance_pF: 10\n", "").replace(
        "max_capacitance_pF: 20", "max_capacitance_pF: 5"
    )
    result = [d for d in check(tmp_path, source) if d.code == "erc.capacitance_budget"]
    assert {d.severity for d in result} == {"warning", "error"}


def test_missing_pull_resistance_does_not_invent_maximum_violation(tmp_path):
    source = SOURCE.replace("            pull_resistance_ohm: 4700\n", "")
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
            capacitance_pF: 0
board B:
    inst unknown: Unknown(
        connections:
            1: bus
    )""",
    )
    result = [d for d in check(tmp_path, source) if d.code == "erc.pull_resistance_budget"]
    assert {d.severity for d in result} == {"error", "warning"}


def test_open_source_and_pulldown_symmetry(tmp_path):
    source = SOURCE.replace("open_drain", "open_source").replace("pull_up", "pull_down")
    assert [d.code for d in check(tmp_path, source)] == ["erc.pull_resistance_budget"]


@pytest.mark.parametrize(
    "field,bad",
    [
        ("pull_resistance_ohm: 4700", "pull_resistance_ohm: 0"),
        ("capacitance_pF: 10", "capacitance_pF: -1"),
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
        .replace("min_pull_up_resistance_ohm: 2500", "min_pull_up_resistance_ohm: 2000")
    )
    assert check(tmp_path, source) == ()


PLUGIN = """from noscheme.erc_registry import value

def register(registry):
    registry.fields("net", {"demo": {"max_pins": "nonnegative"}})
    registry.check("demo_pin_count", check)

def check(ctx):
    annotation = value(ctx.origin, "demo")
    if annotation and len(ctx.endpoints) > annotation["max_pins"]["value"]:
        yield ctx.diagnostic("demo_pin_count", "too many attached physical pins")
"""


def project(tmp_path, plugin=PLUGIN, source=SOURCE):
    make(tmp_path, source)
    (tmp_path / "checks.py").write_text(plugin)
    with (tmp_path / "noscheme.toml").open("a") as f:
        f.write('erc_modules = ["checks.py"]\n')
    return elaborate_project(*resolve_project(tmp_path))[0]


def test_extension_schema_and_check_without_grammar_changes(tmp_path):
    source = SOURCE.replace(
        "    net bus: nettype.open_drain",
        "    net bus: nettype.open_drain\n        demo:\n            max_pins: 2",
    )
    circuit = project(tmp_path, source=source)
    result = apply_erc(circuit)
    assert any(d.code == "erc.demo_pin_count" for d in result)
    assert "demo_pin_count" in circuit["erc"]["checks"]
    assert main(["compile", "--project", str(tmp_path)]) == 0
    assert "erc.demo_pin_count" in (tmp_path / "build" / "erc.json").read_text()


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
    registry.check("mutator", check)
def check(ctx):
    ctx.circuit["parts"].clear()
    ctx.endpoints.clear()
    return []
"""
    circuit = project(tmp_path, plugin)
    before = json.dumps(circuit["parts"], sort_keys=True)
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
            max_capacitance_pF: 20
    inst inner: Device(
        connections:
            1: bus
    )
board B:""",
    ).replace("inst device: Device(", "inst device: Receiver(")
    source = source.replace(
        "    inst device: Receiver(\n        connections:\n            1: bus",
        "    inst device: Receiver(\n        connections:\n            bus: bus",
    )
    assert [d.code for d in check(tmp_path, source)] == ["erc.pull_resistance_budget"]


def test_explicit_zero_capacitance_is_complete(tmp_path):
    source = SOURCE.replace("capacitance_pF: 2", "capacitance_pF: 0").replace(
        "capacitance_pF: 10", "capacitance_pF: 0"
    )
    assert not [d for d in check(tmp_path, source) if d.code == "erc.capacitance_budget"]
