"""Full definition resolution and positional expansion parity."""

import pytest

from test_resolve_parity import CASES, PRELUDE, PYTHON_TESTS, ROOT, compare, source_project
from test_project_parity import make_project
import test_project_parity

probe = test_project_parity.probe


@pytest.mark.parametrize(
    "example", sorted((ROOT / "examples").glob("*/noscheme.toml")), ids=lambda p: p.parent.name
)
def test_examples(probe, example):
    compare(probe, example, resolve_all=True)


@pytest.mark.parametrize("text,code", CASES)
def test_python_errors(probe, tmp_path, text, code):
    result = compare(probe, source_project(tmp_path, text), resolve_all=True)
    assert code in {d["code"] for d in result["diagnostics"]}


PART = """component R:
  params:
    resistance value = 10k
  pins:
    1..2: role.passive
  metadata:
    interchangeable: ["2", "1"]
"""


@pytest.mark.parametrize(
    "arguments", ["a, b", "a, no_connect", "a", "a, b, a", "a, missing", "global.gnd, a"]
)
def test_positional(probe, tmp_path, arguments):
    path = make_project(
        tmp_path,
        {
            "src/main.nosch": PRELUDE
            + PART
            + f"""board B:
  net.passive a
  net.passive b
  R<value: 2.2k> r({arguments})
"""
        },
    )
    compare(probe, path, resolve_all=True)


@pytest.mark.parametrize(
    "expression",
    ["missing", "num.int(x)", "x.foo()", "num.nope(x)", '"{}".format(x)', "x[0]", "(x + 1) << 2"],
)
def test_expressions(probe, tmp_path, expression):
    path = source_project(
        tmp_path,
        f"""component C:
  params:
    number x = 1
    number y = {expression}
""",
    )
    compare(probe, path, resolve_all=True)


def test_inherited_positional_body(probe, tmp_path):
    path = make_project(
        tmp_path,
        {
            "src/main.nosch": PRELUDE
            + """from library import Base
extend Base<value: 20k> Child
board B:
  net.passive a
  Child c(a: a)
""",
            "src/library.nosch": PRELUDE
            + PART
            + """block Base:
  params:
    resistance value = 10k
  ports:
    net.passive a
  R<value: value> r(a, no_connect)
""",
        },
    )
    compare(probe, path, resolve_all=True)


def fixture_source(filename, name="SOURCE"):
    # Reuse source fixtures without importing the Python compiler test modules.
    import ast

    for node in ast.parse((ROOT / "tests" / filename).read_text()).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(name)


def check_source(probe, tmp_path, source, **options):
    return compare(
        probe,
        make_project(tmp_path, {"src/main.nosch": PRELUDE + source}),
        resolve_all=True,
        **options,
    )


@pytest.mark.parametrize("variant", ["basic", "arguments", "inherited", "bad_type"])
def test_multiple_diagnostics(probe, tmp_path, variant):
    source = fixture_source("test_resolution_diagnostics.py")
    if variant == "arguments":
        source = source.replace("1: missing1", "absent: missing1").replace(
            "  R r\n",
            "  R<missing_arg: 1, value: missing_value> r\n",
        )
    elif variant == "inherited":
        source = (
            source.replace("board B:", "block W:")
            + """extend W Derived:
  metadata:
    custom:
      note: "Same body"
component BadDefault:
  params:
    string value = unknown_default
board B:
  W w
"""
        )
    elif variant == "bad_type":
        source += "block BadNet:\n  MissingBundle n\n  R bad(1: n.nonexistent)\n"
    check_source(probe, tmp_path, source)


@pytest.mark.parametrize("selector", ["[1]", "[2]", "[0][1]", "[0:1]", "[1.0]", "[unknown]"])
def test_element_selectors(probe, tmp_path, selector):
    check_source(
        probe, tmp_path, fixture_source("test_element_overrides.py").replace("[1]:", selector + ":")
    )


@pytest.mark.parametrize(
    "change", ["scalar", "identity", "ref", "bad_param", "bad_expr", "bad_pin"]
)
def test_element_metadata(probe, tmp_path, change):
    source = fixture_source("test_element_overrides.py")
    if change == "scalar":
        source = source.replace("caps[2]", "caps")
    elif change in {"identity", "ref"}:
        source = source.replace(
            "        metadata:\n", f'        metadata:\n          {change}: "C1"\n'
        )
    elif change == "bad_param":
        source = source.replace("capacitance: 220n", "missing: 220n")
    elif change == "bad_expr":
        source = source.replace("2p + 3p", "missing + 3p")
    else:
        source = source.replace("            1:", "            9:")
    check_source(probe, tmp_path, source)


@pytest.mark.parametrize(
    "old,new",
    [
        ('power_source: "usb"', 'power_source: "invalid"'),
        ('power_source: "usb"', 'private_connector: "usb"'),
        ("choice(power_mode,", "choice(typo,"),
        ("absent=role.passive", "typo=role.passive"),
        ('header="absent"', 'header="invalid"'),
        ("1..2: [role.passive, unused.never]", "1..2: [role.ground, role.passive, unused.never]"),
    ],
)
def test_choices(probe, tmp_path, old, new):
    source = (
        (ROOT / "examples/power_modes/main.nosch")
        .read_text()
        .replace("board PowerModes:", "board B:")
    )
    assert old in source
    compare(
        probe,
        make_project(tmp_path, {"src/main.nosch": source.replace(old, new, 1)}),
        resolve_all=True,
    )


@pytest.mark.parametrize(
    "instance,code",
    next(
        m.args[1]
        for m in PYTHON_TESTS["test_instance_privacy_and_constraints"].pytestmark
        if m.name == "parametrize"
    ),
)
def test_privacy_and_roles(probe, tmp_path, instance, code):
    source = (
        fixture_source("test_resolve.py", "BASE")
        + "board B:\n  net.passive n\n  "
        + instance
        + "\n"
    )
    result = check_source(probe, tmp_path, source)
    assert result["diagnostics"][0]["code"] == code


@pytest.mark.parametrize(
    "source",
    [
        "bundle A:\n  B b\nbundle B:\n  A a\n",
        "bundle A:\n  A self[2]\n",
        "bundle A:\n  net.passive n\nbundle B:\n  A n\n  A m\n",
        "block W:\n  Missing n\n",
        "block W:\n  role.passive n\n",
        "block W:\n  net.typo n\n",
        "block W:\n  net.digital n[1:2]\n",
        "block W:\n  params:\n    number n = 2\n  net.digital n\n",
        "block W:\n  params:\n    number n = 2\n  ports:\n    net.digital n\n",
        "block W:\n  net.passive a\n  R r[1:2](a, a)\n",
        "block W:\n  net.passive a\n  R r(1.x: a)\n",
        "block W:\n  net.passive a\n  R r(1: global)\n",
        "block W:\n  net.passive a\n  R r(1: a.missing)\n",
        "block W:\n  net.passive a\n  R r(1: a[missing])\n",
    ],
)
def test_types_and_bindings(probe, tmp_path, source):
    check_source(probe, tmp_path, PART + source + "board B:\n  net.passive a\n")


@pytest.mark.parametrize("limit", [0, 1, 3, 128])
def test_expression_depth(probe, tmp_path, limit):
    check_source(
        probe,
        tmp_path,
        PART.replace("10k", "(1 + 2) * 3") + "board B:\n  net.passive a\n",
        max_expression_depth=limit,
    )


def test_custom_map_keys_are_not_internal_encodings(probe, tmp_path):
    check_source(
        probe,
        tmp_path,
        """component C:
  metadata:
    custom:
      node: "Number"
      constraint: "positive"
      type: "number"
board B:
  C c
""",
    )


def test_metadata_diagnostic_order(probe, tmp_path):
    check_source(
        probe,
        tmp_path,
        """component C:
  pins:
    1:
      max_source_current: z_missing
      capacitance: a_missing
  metadata:
    value: v_missing
    current_loads:
      group:
        pins: []
        max_load_current: g_missing
board B:
  C c
""",
    )


@pytest.mark.parametrize(
    "annotation",
    [
        "external_role: role.digital_input",
        "role: role.digital_input",
        "pins: 1",
    ],
)
def test_component_pin_annotations(probe, tmp_path, annotation):
    check_source(
        probe,
        tmp_path,
        PART
        + f"""board B:
  R r:
    metadata:
      pins:
        1:
          {annotation}
""",
    )


@pytest.mark.parametrize(
    "binding",
    [
        "bus.data: n.data",
        "bus.missing: n.data",
        "bus.data: n.missing",
        "[bus]: n",
        "bus[missing].data: n.data",
        "bus.data: global.gnd",
        "bus.data: n.data[missing]",
    ],
)
def test_bundle_fields(probe, tmp_path, binding):
    check_source(
        probe,
        tmp_path,
        f"""bundle Bus:
  net.digital data[4]
block Wrapper:
  ports:
    Bus bus
board B:
  Bus n
  Wrapper w:
    {binding}
""",
    )


@pytest.mark.parametrize(
    "metadata",
    [
        'value: "wrong"',
        'identity: "stable"',
        'custom:\n        note: "ok"',
        "pins:\n        p:\n          external_role: role.digital_input",
        "pins:\n        [p]:\n          role: role.passive",
        "pins:\n        missing:\n          role: role.passive",
    ],
)
def test_block_metadata(probe, tmp_path, metadata):
    check_source(
        probe,
        tmp_path,
        f"""block W:
  ports:
    net.passive p
board B:
  W w:
    metadata:
      {metadata}
""",
    )


def test_inherited_metadata_expression_order(probe, tmp_path):
    result = check_source(
        probe,
        tmp_path,
        """component Base:
  pins:
    1:
      max_source_current: missing_first
      max_load_current: missing_second
extend Base Child:
  pins:
    1:
      max_source_current: missing_override
board B:
  Child c
""",
    )
    assert len(result["diagnostics"]) == 3


def test_positional_does_not_mutate_cached_ast(probe, tmp_path):
    from noscheme.native_bridge import describe_registry
    from noscheme.project import read_config

    path = make_project(
        tmp_path,
        {
            "src/main.nosch": PRELUDE
            + PART
            + """board B:
  net.passive n
  R r(n, no_connect)
"""
        },
    )
    registry = {"version": 1, "registry": describe_registry(read_config(path).erc_registry)}
    request = {"project": str(path), "registry_response": registry}
    before = probe(request | {"resolve_interfaces": True})["definitions"]
    first = probe(request | {"resolve_all": True})["definitions"]
    assert first == probe(request | {"resolve_all": True})["definitions"]
    assert before == probe(request | {"resolve_interfaces": True})["definitions"]
    assert before != first
    filename = str(tmp_path / "src/main.nosch")
    text = (tmp_path / "src/main.nosch").read_text()
    changed = probe(
        request
        | {
            "resolve_all": True,
            "sources": {filename: text.replace("r(n, no_connect)", "r(missing, no_connect)")},
        }
    )
    assert changed["diagnostics"][0]["code"] == "resolve.net"
    assert first == probe(request | {"resolve_all": True})["definitions"]


def test_deep_bundle_chain(probe, tmp_path):
    source = (
        "bundle Z180:\n  net.passive p\n"
        + "".join(f"bundle Z{i}:\n  Z{i + 1} p\n" for i in range(180))
        + "board B:\n  Z0 n\n"
    )
    check_source(probe, tmp_path, source)


def test_instance_choice_diagnostic_order(probe, tmp_path):
    check_source(
        probe,
        tmp_path,
        """block W:
  metadata:
    erc_choices:
      mode: ["on", "off"]
board B:
  W w:
    metadata:
      erc_choices:
        z_bad: "on"
        a_bad: "off"
""",
    )


@pytest.mark.parametrize("shadow", [False, True])
def test_helper_alias_and_shadowing(probe, tmp_path, shadow):
    source = """from noscheme import num as math
component C:
  params:
    number math = 1
    number value = math.ceil(1.2)
board B:
  C c
"""
    if not shadow:
        source = source.replace("    math: number = 1\n", "")
    check_source(probe, tmp_path, source)
