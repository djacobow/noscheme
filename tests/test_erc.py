import json
import pytest

from noscheme.erc import check_circuit
from noscheme.cli import main
from test_circuit import compile_source, make, ROOT


def component(role="passive", unused="explicit", extra=""):
    return f"""component P:
    pins:
        1:
            role: role.{role}
            unused: unused.{unused}
{extra}    metadata:
        footprint: "Test:P"
"""


def codes(circuit):
    return [d.code.removeprefix("erc.") for d in check_circuit(circuit)]


@pytest.mark.parametrize("binding", ["", "        connections:\n            1: no_connect\n"])
def test_forbidden_pin_allows_omission_or_explicit_disconnect(tmp_path, binding):
    c = compile_source(
        tmp_path, component("do_not_connect") + f"board B:\n    inst p: P(\n{binding}    )\n"
    )
    assert codes(c) == []


def test_forbidden_pin_rejects_even_empty_net_and_unchecked_type(tmp_path):
    c = compile_source(
        tmp_path,
        component("do_not_connect")
        + """board B:
    net n: nettype.unchecked
    inst p: P(
        connections:
            1: n
        metadata:
            pins:
                1:
                    external_role: role.passive
    )
""",
    )
    diagnostics = check_circuit(c)
    d = next(d for d in diagnostics if d.code == "erc.do_not_connect")
    assert d.instance_path == "p.1"
    assert d.span.filename.endswith("main.nosch") and d.span.line > 1


@pytest.mark.parametrize(
    "unused,binding,expected",
    [
        ("always", "", []),
        ("explicit", "", ["unused_pin"]),
        ("never", "", ["required_connection"]),
        ("explicit", "1: no_connect", []),
        ("never", "1: no_connect", ["required_connection"]),
    ],
)
def test_unused_policies(tmp_path, unused, binding, expected):
    clause = "        connections:\n            " + binding + "\n" if binding else ""
    c = compile_source(
        tmp_path, component(unused=unused) + f"board B:\n    inst p: P(\n{clause}    )\n"
    )
    assert codes(c) == expected


def test_placeholder_cannot_satisfy_required_pin(tmp_path):
    c = compile_source(
        tmp_path,
        component(unused="never")
        + """board B:
    net n: nettype.passive
        placeholder: true
    inst p: P(
        connections:
            1: n
    )
""",
    )
    assert codes(c) == ["required_connection"]


def pair(tmp_path, role1, role2, nettype="digital", metadata1="", metadata2=""):
    parts = component(role1, extra=metadata1).replace("component P:", "component A:")
    parts += component(role2, extra=metadata2).replace("component P:", "component Z:")
    return compile_source(
        tmp_path,
        parts
        + f"""board B:
    net n: nettype.{nettype}
    inst a: A(
        connections:
            1: n
    )
    inst z: Z(
        connections:
            1: n
    )
""",
    )


def test_drivers_and_unknown_roles(tmp_path):
    assert codes(pair(tmp_path, "digital_output", "digital_input")) == []
    assert codes(pair(tmp_path, "digital_input", "digital_input")) == ["missing_driver"]
    c = pair(tmp_path, "digital_output", "digital_output")
    assert codes(c) == ["multiple_drivers"]
    assert len(check_circuit(c)[0].related) == 2
    assert codes(pair(tmp_path, "digital_bidirectional", "digital_input")) == ["incomplete_roles"]


def test_power_ground_conflict(tmp_path):
    assert "power_ground_conflict" in codes(pair(tmp_path, "power_output", "ground", "power"))


def test_current_budget_known_overload_and_missing_load(tmp_path):
    cap = "            max_source_current_mA: 20\n"
    load = "            max_load_current_mA: 30\n"
    assert codes(pair(tmp_path, "power_output", "power_input", "power", cap, load)) == [
        "current_overload"
    ]
    assert codes(pair(tmp_path, "power_output", "power_input", "power", cap)) == [
        "incomplete_budget"
    ]
    assert (
        codes(pair(tmp_path, "power_output", "power_input", "power", cap, load.replace("30", "20")))
        == []
    )


def test_cli_reports_errors_but_compiles_and_exports(tmp_path, capsys):
    make(
        tmp_path,
        component("do_not_connect")
        + """board B:
    net n: nettype.passive
    inst p: P(
        connections:
            1: n
    )
""",
    )
    assert main(["check", "--project", str(tmp_path), "--diagnostics", "json"]) == 1
    err = json.loads(capsys.readouterr().err)
    assert err["diagnostics"][0]["code"] == "erc.do_not_connect"
    assert main(["compile", "--project", str(tmp_path)]) == 0
    report = json.loads((tmp_path / "build/erc.json").read_text())
    assert report["errors"] == 1 and "waivers" not in report["pending"]
    assert "main.nosch:" in (tmp_path / "build/erc.txt").read_text()
    snapshot = tmp_path / "footprints.json"
    snapshot.write_text(
        json.dumps({"schema_version": 1, "footprints": {"Test:P": {"exists": True, "pads": ["1"]}}})
    )
    assert main(["export-kicad", "--project", str(tmp_path), "--footprints", str(snapshot)]) == 0
    assert (tmp_path / "build/board.net").exists()
    assert json.loads((tmp_path / "build/export.json").read_text())["erc_errors"] == 1


def test_uart_and_button_draft_connectivity(tmp_path):
    source = (
        (ROOT / "examples/stm32h523.nosch")
        .read_text()
        .replace("board STM32H523Reference:", "board B:")
    )
    c = compile_source(
        tmp_path,
        source.replace("from noscheme import role, nettype, unused, f", "from noscheme import f"),
    )
    parts = {tuple(p["path"]): p for p in c["parts"]}
    for i in range(2):
        assert parts["buttons", i]["pins"]["1"]["net"] == f"buttons#[{i}]"
        assert parts["buttons", i]["pins"]["2"]["net"] == "gnd"
        assert parts["button_pullups", i]["pins"]["1"]["net"] == "v3v3"
        assert parts["button_pullups", i]["pins"]["2"]["net"] == f"buttons#[{i}]"
    assert parts["boot_header",]["pins"]["1"]["net"] == "v3v3"
    assert parts["boot_header",]["pins"]["2"]["net"] == "boot0"
    assert parts["boot_pulldown",]["pins"]["1"]["net"] == "boot0"
    assert parts["boot_pulldown",]["pins"]["2"]["net"] == "gnd"


def test_net_waiver_preserves_diagnostics_and_reason_in_reports(tmp_path):
    from noscheme.erc import apply_erc, report_files

    c = compile_source(
        tmp_path,
        component("digital_input")
        + """board B:
    net n: nettype.digital
        waivers:
            missing_driver: "Driver is on the far side of a series resistor."
    inst p[2]: P(
        connections:
            1: n
    )
""",
    )
    assert apply_erc(c) == ()
    waiver = c["erc"]["waivers"][0]
    assert waiver["valid_scope"] and waiver["diagnostics"][0]["code"] == "erc.missing_driver"
    assert waiver["net"] == "n"
    files = report_files(c, tmp_path)
    assert "series resistor" in files[tmp_path / "erc.txt"]
    assert "waived error erc.missing_driver" in files[tmp_path / "erc.txt"]
    assert json.loads(files[tmp_path / "erc.json"])["errors"] == 0
    before = json.dumps(c)
    apply_erc(c)
    assert json.dumps(c) == before


@pytest.mark.parametrize(
    "check", ["required_connection", "do_not_connect", "unused_pin", "waiver_scope", "typo"]
)
def test_invalid_or_nonwaivable_check_is_rejected(tmp_path, check):
    from noscheme.diagnostics import DiagnosticError

    with pytest.raises(DiagnosticError) as exc:
        compile_source(
            tmp_path,
            f"""board B:
    net n: nettype.passive
        waivers:
            {check}: "reason"
""",
        )
    assert exc.value.diagnostics[0].code == "metadata.waiver"


@pytest.mark.parametrize("reason", ['""', '"  "', "123", "true"])
def test_waiver_requires_nonempty_string_reason(tmp_path, reason):
    from noscheme.diagnostics import DiagnosticError

    with pytest.raises(DiagnosticError) as exc:
        compile_source(
            tmp_path,
            f"""board B:
    net n: nettype.digital
        waivers:
            missing_driver: {reason}
""",
        )
    assert exc.value.diagnostics[0].code == "metadata.waiver"


def test_waiver_never_suppresses_mandatory_pin_connection(tmp_path):
    c = compile_source(
        tmp_path,
        component("digital_input", "never")
        + """board B:
    net n: nettype.digital
        waivers:
            missing_driver: "External driver pending."
    inst p: P(
        connections:
            1: n
    )
""",
    )
    assert codes(c) == ["required_connection"]


@pytest.mark.parametrize("placement", ["inner", "outer", "both"])
def test_waivers_follow_outermost_net_through_hierarchy(tmp_path, placement):
    from noscheme.erc import apply_erc

    inner = (
        '            waivers:\n                missing_driver: "Inner reason"\n'
        if placement in {"inner", "both"}
        else ""
    )
    outer = (
        '        waivers:\n            missing_driver: "Outer reason"\n'
        if placement in {"outer", "both"}
        else ""
    )
    c = compile_source(
        tmp_path,
        component("digital_input")
        + f"""block W:
    ports:
        signal: nettype.digital
{inner}    inst loads[2]: P(
        connections:
            1: signal
    )
board B:
    net shared: nettype.digital
{outer}    inst w: W(
        connections:
            signal: shared
    )
""",
    )
    found = [d.code for d in apply_erc(c)]
    assert ("erc.waiver_scope" in found) == (placement != "outer")
    assert ("erc.missing_driver" in found) == (placement == "inner")
    if placement != "outer":
        d = next(d for d in check_circuit(c) if d.code == "erc.waiver_scope")
        assert d.related and d.span.line < d.related[0].span.line


def test_private_net_waiver_is_local_to_each_array_element(tmp_path):
    from noscheme.erc import apply_erc

    c = compile_source(
        tmp_path,
        """block W:
    net private: nettype.digital
        waivers:
            missing_driver: "Private test point"
board B:
    inst w[2]: W()
    net unrelated: nettype.digital
""",
    )
    apply_erc(c)
    assert {w["net"] for w in c["erc"]["waivers"]} == {"w[0].private", "w[1].private"}
    assert all(w["valid_scope"] for w in c["erc"]["waivers"])
    remaining = c["erc"]["diagnostics"]
    assert sum(d["code"] == "erc.missing_driver" for d in remaining) == 1
    assert sum(d["code"] == "erc.unconnected_net" for d in remaining) == 3
