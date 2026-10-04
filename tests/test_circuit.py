import json
from pathlib import Path
import pytest

from noscheme.circuit import elaborate_project, ExpansionLimits
from noscheme.cli import main
from noscheme.diagnostics import DiagnosticError
from noscheme.resolve import resolve_project

ROOT = Path(__file__).resolve().parents[1]
PRELUDE = "from noscheme import role, unused, nettype\n"
PART = """component P:
    pins:
        1:
            aliases: [A]
            unused: unused.explicit
        2:
            unused: unused.explicit
    metadata:
        footprint: "Test:Part"
"""


def make(tmp_path, source):
    (tmp_path / "noscheme.toml").write_text(
        'language_version = "0.1"\nboard = "main.B"\nsource_paths = ["."]\nlibrary_paths = []\noutput_dir = "build"\n'
    )
    (tmp_path / "main.nosch").write_text(PRELUDE + source)
    return tmp_path


def compile_source(tmp_path, source, **kwargs):
    return elaborate_project(*resolve_project(make(tmp_path, source)), **kwargs)[0]


def test_blink_connectivity_matches_kicad_fixture():
    circuit, warnings = elaborate_project(*resolve_project(ROOT / "examples/blink"))
    assert len(circuit["parts"]) == 5 and len(circuit["nets"]) == 4
    assert not warnings
    nets = {
        n["name"]: {(tuple(e["instance"]), e["pin"]) for e in n["endpoints"]}
        for n in circuit["nets"]
    }
    assert nets["gnd"] == {
        (("input",), "2"),
        (("indicators", 0, "led"), "1"),
        (("indicators", 1, "led"), "1"),
    }
    assert nets["v5"] == {
        (("input",), "1"),
        (("indicators", 0, "resistor"), "1"),
        (("indicators", 1, "resistor"), "1"),
    }
    for i in range(2):
        assert nets[f"indicators[{i}].anode"] == {
            (("indicators", i, "resistor"), "2"),
            (("indicators", i, "led"), "2"),
        }
    # Independent fixture data used by the completed CAD experiments.
    from tools.experiments.kicad.fixtures import variants

    expected = variants()["baseline"]
    actual = {
        p["identity"]: {pin: data["net"] for pin, data in p["pins"].items()}
        for p in circuit["parts"]
    }
    for p in expected:
        for pin, net in p["pins"].items():
            assert actual[p["identity"]][pin] == net.lstrip("/").replace("/", ".")


def test_two_uart_headers_share_ground_but_not_signals(tmp_path, stm32_libraries):
    source = (ROOT / "examples/stm32h523/stm32h523.nosch").read_text()

    p = make(tmp_path, source)
    (p / "main.nosch").write_text(source)
    config = p / "noscheme.toml"
    config.write_text(config.read_text().replace('main.B"', 'main.STM32H523Reference"'))
    circuit, _ = elaborate_project(*resolve_project(p))
    headers = [p for p in circuit["parts"] if p["path"][0] == "uart_headers"]
    assert len(headers) == 2
    for i, header in enumerate(headers):
        assert header["path"] == ["uart_headers", i]
        assert [header["pins"][str(pin)]["net"] for pin in (1, 2, 3)] == [
            "gnd",
            f"uart_tx[{i}]",
            f"uart_rx[{i}]",
        ]
        assert header["pins"]["2"]["metadata"]["external_role"]["value"] == ["digital_input"]
        assert header["pins"]["3"]["metadata"]["external_role"]["value"] == ["digital_output"]


def test_shapes_scalar_sharing_and_leading_indices(tmp_path):
    c = compile_source(
        tmp_path,
        PART
        + """board B:
    net common: nettype.passive
    net data: nettype.passive[2][3]
    inst p[2][3]: P(
        connections:
            1: data
            [0].2: common
            [1].2: common
    )
""",
    )
    assert len(c["parts"]) == 6
    for p in c["parts"]:
        i, j = p["path"][1:]
        assert p["pins"]["1"]["net"] == f"data[{i}][{j}]"
        assert p["pins"]["2"]["net"] == "common"


def test_port_indices_apply_within_each_instance(tmp_path):
    c = compile_source(
        tmp_path,
        """block W:
    ports:
        data: nettype.digital[3]
            placeholder: true
board B:
    net n: nettype.digital[2]
    inst w[2]: W(
        connections:
            data[1]: n
    )
""",
    )
    for i in range(2):
        block = next(b for b in c["blocks"] if b["path"] == ["w", i])
        assert block["ports"]["data"][1]["net"] == f"n[{i}]"


@pytest.mark.parametrize(
    "net_shape,inst_shape,binding,code",
    [
        ("[4]", "[2][2]", "1: n", "connection.shape"),
        ("[3]", "[2][3]", "1: n", "connection.shape"),
        ("[1]", "", "1: n", "connection.shape"),
        ("[2]", "", "1: n[2]", "array.index"),
        ("[2]", "", "1: n[-1]", "array.index"),
        ("[2]", "", "1: n[1:1]", "array.slice"),
        ("", "", "1: n[0]", "array.scalar-index"),
    ],
)
def test_shape_errors(tmp_path, net_shape, inst_shape, binding, code):
    source = (
        PART
        + f"board B:\n    net n: nettype.passive{net_shape}\n    inst p{inst_shape}: P(\n        connections:\n            {binding}\n    )\n"
    )
    with pytest.raises(DiagnosticError) as exc:
        compile_source(tmp_path, source)
    assert exc.value.diagnostics[0].code == code


@pytest.mark.parametrize(
    "bindings",
    [
        "1: n\n            A: n",
        "1..2: n\n            2: n",
        "1: n\n            [0].1: n",
        "1: no_connect\n            A: n",
    ],
)
def test_alias_range_array_and_no_connect_overlap(tmp_path, bindings):
    with pytest.raises(DiagnosticError) as exc:
        compile_source(
            tmp_path,
            PART
            + "board B:\n    net n: nettype.passive\n    inst p[2]: P(\n        connections:\n            "
            + bindings
            + "\n    )\n",
        )
    assert exc.value.diagnostics[0].code == "connection.overlap"
    assert exc.value.diagnostics[0].related


def test_pin_range_and_slice(tmp_path):
    c = compile_source(
        tmp_path,
        PART
        + """board B:
    net n: nettype.passive[4]
    inst p: P(
        connections:
            1..2: n[1:3]
    )
""",
    )
    assert [c["parts"][0]["pins"][p]["net"] for p in ("1", "2")] == ["n[1]", "n[2]"]


BUNDLES = """bundle A:
    x: nettype.digital
    y: nettype.digital
bundle Other:
    x: nettype.digital
    y: nettype.digital
block W:
    ports:
        bus: A
            placeholder: true
"""


def test_bundle_nominal_types_and_breakout(tmp_path):
    with pytest.raises(DiagnosticError) as exc:
        compile_source(
            tmp_path,
            BUNDLES
            + "board B:\n    net n: Other\n    inst w: W(\n        connections:\n            bus: n\n    )\n",
        )
    assert exc.value.diagnostics[0].code == "connection.bundle-type"
    c = compile_source(
        tmp_path,
        BUNDLES
        + "board B:\n    net n: Other\n    inst w: W(\n        connections:\n            bus.x: n.y\n            bus.y: n.x\n    )\n",
    )
    block = next(b for b in c["blocks"] if b["path"] == ["w"])
    assert [p["net"] for p in block["ports"]["bus"]] == ["n.y", "n.x"]


def test_bundle_whole_and_field_overlap(tmp_path):
    with pytest.raises(DiagnosticError) as exc:
        compile_source(
            tmp_path,
            BUNDLES
            + "board B:\n    net n: A\n    inst w: W(\n        connections:\n            bus: n\n            bus.x: n.x\n    )\n",
        )
    assert exc.value.diagnostics[0].code == "connection.overlap"


def test_caller_connections_cannot_fake_port_implementation(tmp_path):
    with pytest.raises(DiagnosticError) as exc:
        compile_source(
            tmp_path,
            """block Empty:
    ports:
        a: nettype.passive
            placeholder: true
        b: nettype.passive
board B:
    net n: nettype.passive
    inst e: Empty(
        connections:
            a: n
            b: n
    )
""",
        )
    assert exc.value.diagnostics[0].code == "port.unimplemented"


def test_missing_global_only_fails_when_expanded(tmp_path):
    unused = (
        PART
        + """block Global:
    inst p: P(
        connections:
            1: global.missing
    )
"""
    )
    compile_source(tmp_path, unused + "board B:\n    net n: nettype.passive\n")
    with pytest.raises(DiagnosticError) as exc:
        compile_source(tmp_path, unused + "board B:\n    inst g: Global()\n")
    assert exc.value.diagnostics[0].code == "connection.global"


def test_explicit_no_connect_and_omitted_pins(tmp_path):
    c = compile_source(
        tmp_path,
        PART + "board B:\n    inst p: P(\n        connections:\n            1: no_connect\n    )\n",
    )
    pins = c["parts"][0]["pins"]
    assert pins["1"]["binding"] == "no_connect" and pins["1"]["net"] is None
    assert pins["2"]["binding"] == "omitted" and pins["2"]["net"] is None


def test_limits_and_array_explicit_identity(tmp_path):
    source = (
        PART
        + 'board B:\n    inst p[3]: P(\n        metadata:\n            identity: "stable"\n    )\n'
    )
    c = compile_source(tmp_path, source)
    assert [p["identity"] for p in c["parts"]] == ["stable[0]", "stable[1]", "stable[2]"]
    with pytest.raises(DiagnosticError) as exc:
        compile_source(tmp_path, source, limits=ExpansionLimits(instances=2))
    assert exc.value.diagnostics[0].code == "circuit.limit"


def test_compile_repeatability_and_failed_run_preserves_output(tmp_path, capsys):
    p = make(
        tmp_path,
        PART
        + "board B:\n    net n: nettype.passive\n    inst p: P(\n        connections:\n            1: n\n    )\n",
    )
    args = ["compile", "--project", str(p)]
    assert main(args) == 0
    output = p / "build/circuit.json"
    before = output.read_bytes()
    assert main(args) == 0 and output.read_bytes() == before
    assert json.loads(before)["erc_performed"] is True
    (p / "main.nosch").write_text((p / "main.nosch").read_text().replace("1: n", "1: missing"))
    assert main(args) == 1 and output.read_bytes() == before


def test_scalar_bundle_shares_across_instances(tmp_path):
    c = compile_source(
        tmp_path,
        BUNDLES
        + "board B:\n    net n: A\n    inst w[2]: W(\n        connections:\n            bus: n\n    )\n",
    )
    for block in c["blocks"]:
        if block["path"]:
            assert [p["net"] for p in block["ports"]["bus"]] == ["n.x", "n.y"]


def test_multiple_ports_can_share_caller_net_and_count_pins_once(tmp_path):
    c = compile_source(
        tmp_path,
        PART
        + """block W:
    ports:
        a: nettype.passive
        b: nettype.passive
    inst p: P(
        connections:
            1: a
            2: b
    )
board B:
    net n: nettype.passive
    inst w: W(
        connections:
            a: n
            b: n
    )
""",
    )
    assert len(c["nets"]) == 1
    assert len(c["nets"][0]["endpoints"]) == 2
    assert c["nets"][0]["name"] == "n"


def test_net_declaration_does_not_implement_a_port(tmp_path):
    with pytest.raises(DiagnosticError) as exc:
        compile_source(
            tmp_path,
            "block W:\n    ports:\n        a: nettype.passive\n    net unused: nettype.passive\nboard B:\n    inst w: W()\n",
        )
    assert exc.value.diagnostics[0].code == "port.unimplemented"


def test_external_port_is_explicit_structural_support(tmp_path):
    c = compile_source(
        tmp_path,
        """block W:
    ports:
        a: nettype.digital
board B:
    net n: nettype.digital
    inst w: W(
        connections:
            a: n
        metadata:
            pins:
                a:
                    external_role: role.digital_input
    )
""",
    )
    assert len(c["parts"]) == 0
    assert not c["warnings"]
    assert any("external_role" in d["metadata"] for d in c["nets"][0]["declarations"])


def test_source_reordering_preserves_semantic_circuit(tmp_path):
    first = (
        PART
        + "board B:\n    net a: nettype.passive\n    net b: nettype.passive\n    inst p: P(\n        connections:\n            1: a\n            2: b\n    )\n"
    )
    second = (
        first.replace("    net a: nettype.passive\n    net b: nettype.passive\n", "").replace(
            "            1: a\n            2: b", "            2: b\n            1: a"
        )
        + "    net b: nettype.passive\n    net a: nettype.passive\n"
    )

    def semantic(value):
        if isinstance(value, dict):
            return {
                k: semantic(v)
                for k, v in value.items()
                if k not in {"source", "span", "binding_span", "warnings"}
            }
        if isinstance(value, list):
            return [semantic(v) for v in value]
        return value

    assert semantic(compile_source(tmp_path, first)) == semantic(compile_source(tmp_path, second))


def test_no_connect_on_block_port_preserved(tmp_path):
    c = compile_source(
        tmp_path,
        PART
        + """block W:
    ports:
        a: nettype.passive
    inst p: P(
        connections:
            1: a
    )
board B:
    inst w: W(
        connections:
            a: no_connect
    )
""",
    )
    assert (
        next(b for b in c["blocks"] if b["path"] == ["w"])["ports"]["a"][0]["binding"]
        == "no_connect"
    )
    assert c["parts"][0]["pins"]["1"]["net"] == "w.a"


def test_explicit_identity_preserves_descendant_identity_on_rename(tmp_path):
    source = (
        PART
        + """block W:
    inst p: P()
board B:
    inst original: W(
        metadata:
            identity: "stable"
    )
"""
    )
    before = compile_source(tmp_path, source)
    after = compile_source(tmp_path, source.replace("inst original:", "inst renamed:"))
    assert before["parts"][0]["identity"] == after["parts"][0]["identity"] == "stable.p"
    assert before["parts"][0]["path"] != after["parts"][0]["path"]
