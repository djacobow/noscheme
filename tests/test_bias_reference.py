import pytest

from noscheme.erc import check_circuit
from test_circuit import compile_source

SOURCE = """from noscheme import bias, std
component Pull:
    pins:
        1:
            aliases: [reference]
            role: role.passive
        2:
            role: role.passive
            bias: bias.pull_up
            checks:
                reference: std.bias_reference("reference")
component Driver:
    pins:
        1:
            role: role.open_drain
board B:
    net supply: nettype.power
    net bus: nettype.open_drain
    inst pull: Pull(
        connections:
            1: supply
            2: bus
    )
    inst driver: Driver(
        connections:
            1: bus
    )
"""


def results(tmp_path, source=SOURCE):
    return [
        d for d in check_circuit(compile_source(tmp_path, source)) if d.code == "erc.bias_reference"
    ]


def test_pull_reference_accepts_alias_and_number(tmp_path):
    assert not results(tmp_path)
    assert not results(tmp_path, SOURCE.replace('"reference"', '"1"'))
    assert not results(
        tmp_path, SOURCE.replace("pull_up", "pull_down").replace("nettype.power", "nettype.ground")
    )


@pytest.mark.parametrize(
    "old,new,reason",
    [
        ("nettype.power", "nettype.ground", "declared power"),
        ("nettype.power", "nettype.passive", "declared power"),
        ('"reference"', '"missing"', "unknown reference"),
        ("1: supply", "1: no_connect", "unconnected"),
        ("1: supply", "1: bus", "signal net itself"),
        ("bias.pull_up", "bias.none", "requires bias"),
    ],
)
def test_invalid_reference_is_source_located(tmp_path, old, new, reason):
    diagnostics = results(tmp_path, SOURCE.replace(old, new))
    assert len(diagnostics) == 1
    assert reason in diagnostics[0].message
    assert diagnostics[0].instance_path == "pull.2"
    assert diagnostics[0].span.filename.endswith("main.nosch")


def test_no_reference_annotation_retains_existing_behavior(tmp_path):
    source = SOURCE.replace(
        '            checks:\n                reference: std.bias_reference("reference")\n', ""
    ).replace("nettype.power", "nettype.ground")
    assert not results(tmp_path, source)


def test_reference_check_supports_targeted_waiver(tmp_path):
    source = SOURCE.replace("nettype.power", "nettype.ground").replace(
        "    net bus: nettype.open_drain\n",
        """    net bus: nettype.open_drain
        waivers:
            reference:
                reason: "Intentional test arrangement"
                pins: ["pull.2"]
""",
    )
    assert not results(tmp_path, source)
