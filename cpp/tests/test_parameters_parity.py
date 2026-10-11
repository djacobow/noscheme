"""Compare the native parameter projection and binding with Python resolution.

This deliberately does not assert native interface/metadata/body validation.
"""

import os
from pathlib import Path

import pytest

from noscheme.ast import to_data
from noscheme.diagnostics import DiagnosticError
from noscheme.evaluate import Evaluator, Number, encoded
from noscheme.instances import arguments
from noscheme.project import erc_exports, load_project, read_config
from noscheme.resolve import Resolver

from test_project_parity import ROOT, make_project
import test_project_parity

probe = test_project_parity.probe

pytestmark = pytest.mark.skipif(
    not os.environ.get("NOSCHEME_NATIVE_PROBE"), reason="native probe supplied by Meson"
)


def normalized(value, native=False):
    """Convert only native source offsets to Python's Unicode offset unit."""
    if isinstance(value, list):
        return [normalized(x, native) for x in value]
    if isinstance(value, dict):
        if native and {"filename", "start_pos", "end_pos"} <= value.keys():
            raw = Path(value["filename"]).read_text().encode()
            return value | {k: len(raw[: value[k]].decode()) for k in ("start_pos", "end_pos")}
        return {k: normalized(v, native) for k, v in value.items()}
    return value


def compare(probe, path, binding=None):
    config = read_config(path)
    payload = {
        "project": str(path),
        "resolve_parameters": True,
        "erc_exports": {
            space: {name: symbol.name for name, symbol in names.items()}
            for space, names in erc_exports(config).items()
        },
    }
    if binding:
        payload["bind"] = binding
    evaluator = None
    try:
        project = load_project(path)
        resolver = Resolver(project)
        definitions = resolver.resolve_interfaces()
        summary = [
            {
                "module": d.module,
                "name": d.name,
                "parameters": {
                    name: {"type": p.type, "default": to_data(p.default), "span": to_data(p.span)}
                    for name, p in d.parameters.items()
                },
            }
            for d in definitions.values()
        ]
        if binding:
            caller = project.modules[binding["module"]].symbols[binding["definition"]]
            instance = next(
                n
                for n in caller.declaration.body
                if getattr(getattr(n, "name", None), "text", None) == binding["instance"]
            )
            key = resolver.symbol(caller.module, instance.component, {"component", "block"})
            evaluator = Evaluator(project)
            evaluator.instance_path = binding.get("instance_path")
            env = {
                k: Number(int(v["value"]) if v["tag"] == "integer" else v["value"])
                if v["type"] != "string"
                else v["value"]
                for k, v in binding.get("environment", {}).items()
            }
            bound = evaluator.bind(
                definitions[key], arguments(instance), caller.module, env, instance.span
            )
            part_value = evaluator.part_value(definitions[key], bound)
    except DiagnosticError as error:
        actual = probe(payload)
        assert "diagnostics" in actual, actual
        # Messages are not frozen, but all diagnostic locations and context are.
        expected = [to_data(d) for d in error.diagnostics]
        assert normalized(
            [{k: v for k, v in d.items() if k != "message"} for d in actual["diagnostics"]],
            native=True,
        ) == normalized([{k: v for k, v in d.items() if k != "message"} for d in expected])
        return actual
    actual = probe(payload)
    assert "diagnostics" not in actual and "error" not in actual, actual
    assert normalized(actual["parameters"], native=True) == normalized(summary)
    if binding:
        assert actual["bound"] == {k: encoded(v) for k, v in bound.items()}
        assert actual["part_value"] == (encoded(part_value) if part_value is not None else None)
        assert normalized(actual["warnings"], native=True) == normalized(
            [to_data(d) for d in evaluator.warnings]
        )
    return actual


@pytest.mark.parametrize(
    "params,args,metadata",
    [
        ("a: number = b + 1\n    b: number = 4", "", ""),
        ("a: number = b\n    b: number = a", "a=7", ""),
        ("a: number = b\n    b: number = a", "", ""),
        ("z: number\n    a: string", "", ""),
        ("a: number = 1 / 0", "a=10", ""),
        ("a: number = 1 / 0", "", ""),
        ("a: number = missing", "", ""),
        ("a: number = 3", "b=4", ""),
        ("a: number = 3", 'a="oops"', ""),
        ('a: string = "text"', "a=3", ""),
        ('a: number = "wrong"', "", ""),
        ('z: number = "wrong"\n    a: string = 3', "", ""),
        ("value: resistance = 2.2k", "", ""),
        ('value: capacitance = "DNP"', "", ""),
        ("c: capacitance = 100n", "", "value: c"),
        ("c: capacitance = 100n", "", "value: c * 2"),
        ("c: capacitance = 100n", "", "value: (c)"),
        ("c: capacitance = 100n", "", 'value: "{}".format(c)'),
        ('fmt: string = "{n}µF"\n    n: number = 10\n    value: string = fmt.format(n=n)', "", ""),
        ("a: number = num.ceil(b)\n    b: number = 1.2", "", ""),
        ("a: number = 1", "a=external + 1", ""),
        ("a: number = 1", "a=num.floor(3.4)", ""),
        ("a: number = 1", "", "value: 1 / 0"),
    ],
)
def test_bind(probe, tmp_path, params, args, metadata):
    source = f"from noscheme import num\ncomponent Part:\n  params:\n    {params}\n"
    if metadata:
        source += f"  metadata:\n    {metadata}\n"
    source += f"block B:\n  Part p({args})\n"
    path = make_project(tmp_path, {"src/main.nosch": source})
    compare(
        probe,
        path,
        {
            "module": "main",
            "definition": "B",
            "instance": "p",
            "instance_path": "p[2]",
            "environment": {"external": {"type": "number", "tag": "integer", "value": "10"}},
        },
    )


def test_lexical_defaults_and_inheritance(probe, tmp_path):
    path = make_project(
        tmp_path,
        {
            "src/base.nosch": """from noscheme import num as helper
component Base:
  params:
    capacitance value = helper.ceil(1.2) * 1u
    number x = value * 2
  metadata:
    value: value
""",
            "src/derived.nosch": """import base.Base
from noscheme import num as other
extend base.Base<value: other.floor(3.4) * 1u> Derived
""",
            "src/main.nosch": """from derived import Derived as Part
from noscheme import num as caller
block B:
  Part<x: caller.ceil(6.2)> p()
""",
        },
    )
    compare(probe, path, {"module": "main", "definition": "B", "instance": "p"})


@pytest.mark.parametrize(
    "body",
    [
        "extend B<a: 1> A\nextend A<a: 2> B\n",
        "extend Missing<a: 1> A\n",
        "block Other:\n  net.passive n\nextend Other<a: 1> A\n",
        "component A:\n  params:\n    strange a\n",
        "component A:\n  params:\n    1: number\n",
        "component A:\n  params:\n    number a\nextend A<missing: 1> B\n",
        "component A:\n  params:\n    number a\nextend A.hidden<a: 1> B\n",
        "extend net.missing Rail:\n  custom:\n    a: true\n",
    ],
)
def test_resolution_errors(probe, tmp_path, body):
    path = make_project(
        tmp_path,
        {"src/main.nosch": "from noscheme import net\n" + body + "block B0:\n  net.passive n\n"},
        **{"targets.default.root": "main.B0"},
    )
    compare(probe, path)


def test_default_order_and_redeclaration(probe, tmp_path):
    path = make_project(
        tmp_path,
        {
            "src/main.nosch": """component Base:
  params:
    number a = 2
    resistance value = 10k
extend Base<value: 47k> Part:
  params:
    string a = "hello"
block B:
  Part p
"""
        },
    )
    compare(probe, path, {"module": "main", "definition": "B", "instance": "p"})


@pytest.mark.parametrize(
    "example", sorted((ROOT / "examples").glob("*/noscheme.toml")), ids=lambda p: p.parent.name
)
def test_example_parameters(probe, example):
    compare(probe, example)


@pytest.mark.parametrize(
    "kind",
    ["voltage", "current", "resistance", "capacitance", "inductance", "frequency", "time", "power"],
)
def test_quantity_defaults(probe, tmp_path, kind):
    path = make_project(
        tmp_path,
        {
            "src/main.nosch": f"""component Part:
  params:
    {kind} value = 4.7u
block B:
  Part p
"""
        },
    )
    compare(probe, path, {"module": "main", "definition": "B", "instance": "p"})


def test_long_inheritance_chain(probe, tmp_path):
    text = "component P0:\n  params:\n    resistance value = 1k\n"
    for i in range(1, 180):
        text += f"extend P{i - 1}<value: {i}k> P{i}\n"
    text += "block B:\n  P179 p\n"
    path = make_project(tmp_path, {"src/main.nosch": text})
    compare(probe, path, {"module": "main", "definition": "B", "instance": "p"})


def test_long_default_dependencies(probe, tmp_path):
    text = "component Part:\n  params:\n"
    for i in range(150):
        expr = f"p{i + 1} + 1" if i < 149 else "1"
        text += f"    p{i}: number = {expr}\n"
    text += "block B:\n  Part p\n"
    path = make_project(tmp_path, {"src/main.nosch": text})
    compare(probe, path, {"module": "main", "definition": "B", "instance": "p"})


def test_inherited_default_keeps_original_namespace(probe, tmp_path):
    path = make_project(
        tmp_path,
        {
            "src/base.nosch": """from noscheme import num as helper
component Base:
  params:
    resistance value = helper.ceil(1.2) * 1k
  metadata:
    value: "{}".format(helper.floor(value))
""",
            "src/main.nosch": """from base import Base
component helper:
  params:
    number x = 1
extend Base<value: 3.3k> Part
block B:
  Part p
""",
        },
    )
    compare(probe, path, {"module": "main", "definition": "B", "instance": "p"})


def test_probe_observes_changed_defaults(probe, tmp_path):
    path = make_project(
        tmp_path,
        {
            "src/main.nosch": """component Part:
  params:
    resistance value = 1k
block B:
  Part p
"""
        },
    )
    binding = {"module": "main", "definition": "B", "instance": "p"}
    first = compare(probe, path, binding)
    source = tmp_path / "src/main.nosch"
    source.write_text(source.read_text().replace("1k", "2k"))
    second = compare(probe, path, binding)
    assert first["bound"] != second["bound"]


def test_inherited_default_namespace_not_caller_namespace(probe, tmp_path):
    path = make_project(
        tmp_path,
        {
            "src/base.nosch": """from noscheme import num as helper
component Base:
  params:
    resistance value = helper.ceil(1.2) * 1k
""",
            "src/main.nosch": """from base import Base
component helper:
  params:
    number x = 1
extend Base Part:
  metadata:
    custom:
      description: "specialized"
block B:
  Part p
""",
        },
    )
    compare(probe, path, {"module": "main", "definition": "B", "instance": "p"})
