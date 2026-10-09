"""Applications, default replacement, plugin contracts, and conservative reductions."""

from fractions import Fraction
from math import inf

import pytest

from noscheme.diagnostics import DiagnosticError
from noscheme.erc import check_circuit, apply_erc, report_diagnostics
from noscheme.erc_registry import Application, load_registry
from noscheme.erc_primitives import Interval, total, parallel
from test_circuit import compile_source

SOURCE = """from noscheme import std
component Endpoint:
  pins:
    1:
      role: role.digital_output
board B:
  net bus: nettype.digital
    checks:
      contention: std.count(std.active_digital_driver, max=1)
      presence: std.any(std.active_digital_driver)
  inst devices[2]: Endpoint
    1: bus
"""


def circuit(tmp_path, source=SOURCE):
    result = compile_source(tmp_path, source)
    result["erc_rule_sets"] = []
    return result


def test_defaults_replaceable_explicit_rules_remain_and_report_identity(tmp_path):
    c = circuit(tmp_path)
    result = apply_erc(c)
    assert [d.code for d in result] == ["erc.std.count"]
    assert result[0].application == "net:bus:contention"
    assert result == report_diagnostics(c)
    c["erc_rule_sets"] = ["std.default"]
    assert {d.code for d in check_circuit(c)} == {"erc.std.count", "erc.multiple_drivers"}


def test_waiver_targets_one_application_of_same_checker(tmp_path):
    source = SOURCE.replace(
        "      presence:",
        "      another: std.count(std.active_digital_driver, max=0)\n      presence:",
    ).replace(
        "    checks:",
        """    waivers:
      contention: "Intentional shared bus"
    checks:""",
    )
    c = circuit(tmp_path, source)
    records = []
    result = check_circuit(c, waiver_records=records)
    assert [d.application for d in result] == ["net:bus:another"]
    assert records[0]["diagnostics"][0]["application"] == "net:bus:contention"


@pytest.mark.parametrize(
    "policy,severity", [("warning", "warning"), ("error", "error"), ("ignore", None)]
)
def test_unknown_role_policy(tmp_path, policy, severity):
    source = SOURCE.replace("role: role.digital_output", "role: role.unchecked").replace(
        "max=1)", f'max=1, missing="{policy}")'
    )
    result = check_circuit(circuit(tmp_path, source))
    count = [d for d in result if d.application.endswith(":contention")]
    assert [d.severity for d in count] == ([severity] if severity else [])
    assert all(d.severity in {"warning", "error"} for d in result)


def test_reductions_optional_members_and_empty_sets():
    assert total(()).lower == total(()).upper == 0
    assert parallel(()).lower == parallel(()).upper == inf
    result = parallel(
        (
            Interval(Fraction(4700), Fraction(4700)),
            Interval(Fraction(4700), Fraction(4700), optional=True),
        )
    )
    assert (result.lower, result.upper) == (2350, 4700)
    assert not result.complete
    assert total((Interval(-2, 3, optional=True),)).lower == -2


def test_unknown_selector_does_not_turn_optional_resistor_into_short(tmp_path):
    source = """from noscheme import bias, std
component Pull:
  pins:
    1:
      role: role.passive
      bias: bias.pull_up
      pull_resistance: 4700
component Maybe extends Pull:
  pins:
    1:
      bias: bias.none
board B:
  net bus: nettype.passive
    checks:
      strength: std.parallel(pull_resistance, where=bias.pull_up, min=2000)
  inst known: Pull(1: bus)
  inst maybe: Maybe(1: bus)
"""
    c = circuit(tmp_path, source)
    del c["parts"][1]["pins"]["1"]["metadata"]["bias"]
    result = check_circuit(c)
    assert [d.severity for d in result] == ["warning"]


def test_disconnected_pin_context_and_shared_immutable_graph(tmp_path):
    c = circuit(tmp_path, SOURCE.replace("1: bus", "1: no_connect"))
    registry = load_registry()
    seen = []

    def pin_rule(ctx):
        assert ctx.net is None and ctx.other_pins == ()
        assert ctx.pin.component.label == ctx.pin.label.rsplit(".", 1)[0]
        with pytest.raises(TypeError):
            ctx.circuit["parts"][0]["pins"]["1"]["metadata"]["role"]["value"] = ()
        seen.append(ctx.circuit)
        yield ctx.error("pin inspected")

    registry.pin_rule("test.inspect", pin_rule)
    registry.rule_set(
        "test.pins", lambda ctx: [Application("inspect", "test.inspect")] if ctx.pin else []
    )
    c["erc_rule_sets"] = ["test.pins"]
    result = check_circuit(c, registry=registry)
    assert len(seen) == 2 and seen[0] is seen[1]
    assert len([d for d in result if d.code == "erc.test.inspect"]) == 2


@pytest.mark.parametrize(
    "edit",
    [
        ("count(std.active_digital_driver", "count(missing.predicate"),
        ("max=1", 'max="bad"'),
        ("std.count(", "missing.checker("),
        ("max=1", "maximum=1"),
        ("max=1", "max=1, max=2"),
        ("max=1", 'max=1, missing="never"'),
        ("std.any(std.active_digital_driver)", "std.any(std.active_digital_driver, 1)"),
        ("std.any(std.active_digital_driver)", "std.parallel(no_such_field, max=1)"),
        ("std.any(std.active_digital_driver)", "std.sum(capacitance, net=bias, max=1)"),
        ("std.any(std.active_digital_driver)", "std.bias_reference()"),
        ("std.any(std.active_digital_driver)", "std.any"),
    ],
)
def test_invalid_application_is_blocking(tmp_path, edit):
    with pytest.raises(DiagnosticError):
        check_circuit(circuit(tmp_path, SOURCE.replace(*edit)))


def test_custom_reducer_and_predicate_use_same_execution_path(tmp_path):
    c = circuit(tmp_path)
    c["nets"][0]["declarations"][0]["metadata"].pop("checks")
    registry = load_registry()
    registry.predicate(
        "test.driver", lambda pin, ctx: pin.attributes["role"] == ("digital_output",)
    )
    registry.rule_set(
        "test.count",
        lambda ctx: []
        if ctx.pin
        else [
            Application(
                "contention", "std.count", {"what": (("predicate", "test.driver"),), "max": 1}
            )
        ],
    )
    c["erc_rule_sets"] = ["test.count"]
    result = check_circuit(c, registry=registry)
    assert [d.application for d in result] == ["net:bus:contention"]
    registry.reducer(
        "test.maximum",
        lambda samples: Interval(max(s.lower for s in samples), max(s.upper for s in samples)),
    )
    registry.rule_set(
        "test.defaults",
        lambda ctx: [Application("maximum", "test.maximum", {"field": "rating", "max": 5})]
        if not ctx.pin
        else [],
    )
    c["erc_rule_sets"] = ["test.defaults"]
    for part in c["parts"]:
        part["pins"]["1"]["metadata"]["rating"] = {
            "value": 10,
            "span": c["source"],
            "module": "main",
        }
    assert any(d.application == "net:bus:maximum" for d in check_circuit(c, registry=registry))


def test_duplicate_registry_names_and_unknown_rulesets_are_errors(tmp_path):
    registry = load_registry()
    with pytest.raises(ValueError, match="duplicate"):
        registry.reducer("std.sum", total)
    c = circuit(tmp_path)
    c["erc_rule_sets"] = ["unknown.rules"]
    with pytest.raises(DiagnosticError, match="unknown ERC rule set"):
        check_circuit(c, registry=registry)


def test_pin_application_specialization_and_instance_override(tmp_path):
    source = """import test
component Base:
  pins:
    1:
      role: role.passive
      checks:
        limit: test.limit(maximum=20, label="original")
component Child extends Base:
  pins:
    1:
      checks:
        limit: test.limit(maximum=10)
board B:
  net bus: nettype.passive
  inst load: Child
    1: bus
    metadata:
      pins:
        1:
          checks:
            limit: test.limit(maximum=5)
"""
    from test_circuit import make
    from noscheme.resolve import resolve_project
    from noscheme.circuit import elaborate_project

    make(tmp_path, source)
    (tmp_path / "checks.py").write_text("""def register(r):
  r.pin_rule("test.limit", check, parameters={"maximum": "number", "label": "string"})
  r.pin_rule("test.other", check, parameters={"maximum": "number", "label": "string"})
def check(ctx, *, maximum, label):
  assert maximum == 5 and label == "original"
  assert ctx.net.label == "bus" and ctx.other_pins == ()
  yield ctx.error("expected application")
""")
    with (tmp_path / "noscheme.toml").open("a") as stream:
        stream.write('erc_modules = ["checks.py"]\nerc_rule_sets = []\n')
    c = elaborate_project(*resolve_project(tmp_path))[0]
    result = check_circuit(c)
    assert [d.application for d in result] == ["pin:load.1:limit"]
    path = tmp_path / "main.nosch"
    path.write_text(path.read_text().replace("test.limit(", "test.other(", 1))
    with pytest.raises(DiagnosticError, match="cannot change checker"):
        resolve_project(tmp_path)


def test_structural_errors_remain_with_no_rule_sets(tmp_path):
    with pytest.raises(DiagnosticError):
        circuit(tmp_path, SOURCE.replace("1: bus", "1: undeclared"))


def test_empty_any_all_and_count_are_defined(tmp_path):
    source = SOURCE[: SOURCE.index("  inst devices")].replace(
        "std.count(std.active_digital_driver, max=1)", "std.all(std.active_digital_driver)"
    )
    result = check_circuit(circuit(tmp_path, source))
    assert [d.application for d in result] == ["net:bus:presence"]
    assert result[0].severity == "error"


def test_uncertain_all_with_optional_selection_is_unknown(tmp_path):
    c = circuit(
        tmp_path,
        SOURCE.replace(
            "std.count(std.active_digital_driver, max=1)",
            "std.all(std.active_digital_driver, where=tristate_capable)",
        ).replace("role: role.digital_output", "role: role.digital_input"),
    )
    result = check_circuit(c)
    assert next(d for d in result if d.application.endswith(":contention")).severity == "warning"


def test_invalid_reducer_result_blocks_publication(tmp_path):
    registry = load_registry()
    registry.reducer("test.nan", lambda samples: Interval(float("nan"), 1))
    registry.rule_set(
        "test.defaults",
        lambda ctx: [Application("bad", "test.nan", {"field": "capacitance", "max": 1})]
        if not ctx.pin
        else [],
    )
    c = circuit(tmp_path)
    c["erc_rule_sets"] = ["test.defaults"]
    with pytest.raises(DiagnosticError, match="ordered Interval"):
        check_circuit(c, registry=registry)


def test_filter_enum_alias_and_missing_mapping_validation(tmp_path):
    source = SOURCE.replace(
        "component Endpoint:", "from noscheme import role as r\ncomponent Endpoint:"
    ).replace(
        "std.count(std.active_digital_driver, max=1)",
        "std.count([std.active_digital_driver, r.digital_output], max=1)",
    )
    result = check_circuit(circuit(tmp_path, source))
    assert [d.application for d in result] == ["net:bus:contention"]
    with pytest.raises(DiagnosticError, match="selector"):
        circuit(tmp_path, SOURCE.replace("std.any(std.active_digital_driver)", "std.any(true)"))
    with pytest.raises(DiagnosticError, match="one rule call"):
        circuit(
            tmp_path,
            SOURCE.replace(
                "presence: std.any(std.active_digital_driver)",
                'presence:\n        checker: "std.any"',
            ),
        )


def test_unknown_predicate_rejected_on_empty_net(tmp_path):
    source = SOURCE[: SOURCE.index("  inst devices")].replace(
        "std.active_digital_driver", "std.unknown_predicate"
    )
    with pytest.raises(DiagnosticError, match="selector"):
        circuit(tmp_path, source)


def test_default_setup_reused_but_explicit_arguments_and_new_runs_are_validated(
    tmp_path, monkeypatch
):
    import noscheme.erc as erc

    c = circuit(tmp_path)
    c["nets"][0]["declarations"][0]["metadata"].pop("checks")
    registry = load_registry()
    values, validations = [], []

    def check(ctx, limit):
        values.append(limit)
        return ()

    registry.pin_rule("test.defaults", check, parameters={"limit": "number"}, defaults={"limit": 3})
    registry.rule_set(
        "test.defaults",
        lambda ctx: [
            Application("default", "test.defaults"),
            Application("explicit", "test.defaults", {"limit": 7}),
        ]
        if ctx.pin
        else [],
    )
    c["erc_rule_sets"] = ["test.defaults"]
    original = erc.validate_params

    def validate(rule, supplied, span, registry):
        validations.append(supplied)
        return original(rule, supplied, span, registry)

    monkeypatch.setattr(erc, "validate_params", validate)
    assert apply_erc(c, registry=registry) == ()
    assert values == [3, 7, 3, 7]
    assert len(validations) == 3  # one default setup, two explicit applications
    registry.rules["test.defaults"].defaults["limit"] = 5
    values.clear()
    assert apply_erc(c, registry=registry) == ()
    assert values == [5, 7, 5, 7]


def test_project_rules_import_like_module_members(tmp_path):
    from test_circuit import make
    from noscheme.resolve import resolve_project

    source = """from test import limit as bounded
component Part:
  pins:
    1:
      role: role.passive
      checks:
        limit: bounded(maximum=5)
board B:
  net bus: nettype.passive
  inst load: Part
    1: bus
"""
    make(tmp_path, source)
    (tmp_path / "checks.py").write_text(
        'def register(r):\n  r.pin_rule("test.limit", lambda ctx, maximum: (), '
        'parameters={"maximum": "number"})\n'
    )
    with (tmp_path / "noscheme.toml").open("a") as stream:
        stream.write('erc_modules = ["checks.py"]\n')
    _, definitions = resolve_project(tmp_path)
    part = next(d for (module, name), d in definitions.items() if name == "Part")
    checks = part.interface["1"].metadata["checks"].value
    assert checks["limit"].value["checker"].value == "test.limit"
    (tmp_path / "test.nosch").write_text("")
    with pytest.raises(DiagnosticError, match="both an ERC namespace and a source module"):
        resolve_project(tmp_path)
    (tmp_path / "test.nosch").unlink()
    path = tmp_path / "main.nosch"
    path.write_text(path.read_text().replace("from test import limit as bounded\n", ""))
    with pytest.raises(DiagnosticError, match="imported rule"):
        resolve_project(tmp_path)


INNER = """from noscheme import std
component Endpoint:
  pins:
    1:
      role: role.digital_output
block Inner:
  ports:
    bus: nettype.digital
      checks:
        contention: std.count(std.active_digital_driver, max=0)
  inst endpoint: Endpoint
    1: bus
board B:
  net bus: nettype.digital
    waivers:
      contention: "Reviewed: the inner limit is a test fixture."
  inst inner: Inner
    bus: bus
"""


def test_waiver_names_applications_anywhere_on_its_net(tmp_path):
    records = []
    assert check_circuit(circuit(tmp_path, INNER), waiver_records=records) == ()
    assert records[0]["diagnostics"][0]["application"] == "net:inner.bus:contention"
    with pytest.raises(DiagnosticError, match="by its name"):
        circuit(
            tmp_path,
            INNER.replace('contention: "Reviewed', '"net:inner.bus:contention": "Reviewed'),
        )
