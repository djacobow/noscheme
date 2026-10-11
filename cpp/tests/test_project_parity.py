"""Differential project-loading tests; run by Meson with the native probe."""

import json
import os
from pathlib import Path
import subprocess

import pytest

from noscheme.ast import to_data
from noscheme.diagnostics import DiagnosticError
from noscheme.project import erc_exports, load_project, project_summary, read_config

ROOT = Path(__file__).resolve().parents[2]
PROBE = os.environ.get("NOSCHEME_NATIVE_PROBE")
pytestmark = pytest.mark.skipif(not PROBE, reason="native probe supplied by Meson")


def make_project(root, modules=None, **overrides):
    (root / "src").mkdir(exist_ok=True)
    (root / "lib").mkdir(exist_ok=True)
    values = dict(
        language_version="0.1",
        **{"targets.default.root": "main.B"},
        source_paths=["src"],
        library_paths=["lib"],
        output_dir="build",
    )
    values.update(overrides)
    values["targets.default.output_dir"] = values["output_dir"]
    config = root / "noscheme.toml"
    config.write_text("\n".join(f"{k} = {json.dumps(v)}" for k, v in values.items()))
    for filename, text in (modules or {"src/main.nosch": "block B:\n  T a\n"}).items():
        path = root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return config


@pytest.fixture
def probe():
    process = subprocess.Popen([PROBE], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

    def request(payload):
        process.stdin.write(json.dumps(payload) + "\n")
        process.stdin.flush()
        result = process.stdout.readline()
        assert result, f"probe exited: {process.poll()}"
        return json.loads(result)

    yield request
    process.stdin.close()
    process.stdout.close()
    assert process.wait(timeout=15) == 0


def compare(probe, path, sources=None, include_asts=False):
    sources = sources or {}
    request = {
        "project": str(path),
        "sources": {str(k): v for k, v in sources.items()},
        "include_asts": include_asts,
    }
    try:
        config = read_config(path, sources=sources)
        request["erc_exports"] = {
            space: {name: symbol.name for name, symbol in names.items()}
            for space, names in erc_exports(config).items()
        }
        expected = load_project(path, sources=sources)
    except DiagnosticError as error:
        actual = probe(request)
        assert "diagnostics" in actual, actual
        assert [d["code"] for d in actual["diagnostics"]] == [d.code for d in error.diagnostics]
        assert actual["exit_code"] == getattr(error, "exit_code", 1)
        for native, python in zip(actual["diagnostics"], error.diagnostics):
            for native_span, python_span in [
                (native["span"], python.span),
                *[(a["span"], b.span) for a, b in zip(native["related"], python.related)],
            ]:
                filename = Path(python_span.filename)
                text = sources.get(filename)
                if text is None:
                    text = filename.read_text()
                raw = text.encode()
                native_span = native_span | {
                    key: len(raw[: native_span[key]].decode()) for key in ("start_pos", "end_pos")
                }
                assert native_span == to_data(python_span)
            assert len(native["related"]) == len(python.related)
        return actual
    actual = probe(request)
    assert actual.get("project") == project_summary(expected), actual
    if include_asts:
        # These cache tests use ASCII so native byte spans equal Python spans.
        assert actual["asts"] == {
            name: to_data(module.ast) for name, module in expected.modules.items()
        }
    return actual


@pytest.mark.parametrize(
    "example",
    ["blink", "stm32h523", "rulos_6matrix", "wren", "wren/full", "tristate", "erc_extensions"],
)
def test_examples(probe, example):
    compare(probe, ROOT / "examples" / example)


@pytest.mark.parametrize(
    "overrides",
    [
        {"language_version": 0.1},
        {"language_version": "9"},
        {"targets.default.root": "B"},
        {"targets.default.root": "../main.B"},
        {"targets.default.root": "noscheme.B"},
        {"source_paths": "src"},
        {"source_paths": ["absent"]},
        {"source_paths": [], "library_paths": []},
        {"source_paths": ["src", "./src"]},
        {"library_paths": ["src"]},
        {"source_paths": [1]},
        {"output_dir": ""},
        {"output_dir": "\0"},
        {"source_paths": ["\0"]},
        {"typo": "value"},
        {"erc_modules": "x.py"},
        {"erc_modules": ["absent.py"]},
        {"erc_modules": [1]},
        {"erc_rule_sets": "std.default"},
        {"erc_rule_sets": ["x", "x"]},
        {"erc_rule_sets": [""]},
        {"erc_rule_sets": [1]},
    ],
)
def test_invalid_config(probe, tmp_path, overrides):
    compare(probe, make_project(tmp_path, **overrides))


@pytest.mark.parametrize(
    "text",
    [
        'targets.default.root = "main.B"\ntargets.default.output_dir = "build"\n',
        "board = [",
        '\ufefftargets.default.root = "main.B"\ntargets.default.output_dir = "build"',
        '"language_version" = 2\ntargets.default.root = "main.B"\ntargets.default.output_dir = "build"\nsource_paths=["src"]\nlibrary_paths=[]\noutput_dir="build"',
    ],
)
def test_bad_toml(probe, tmp_path, text):
    config = make_project(tmp_path)
    config.write_text(text)
    compare(probe, config)


@pytest.mark.parametrize(
    "prefix,library",
    [
        ("from missing import R", {}),
        ("", {"lib/main.nosch": "block B:\n  T n\n"}),
        ("from noscheme import typo", {}),
        ("from parts import X", {"lib/parts.nosch": "component R:\n  pins:\n    1:\n"}),
        ("from noscheme import num, num", {}),
        ("from parts import B", {"lib/parts.nosch": "block B:\n  T n\n"}),
        ("from main import B", {}),
        ("import other", {"lib/other.nosch": "import main\n"}),
        ("import noscheme.extra", {}),
        ("import noscheme\nimport noscheme", {}),
        ("from noscheme import num as std\nfrom noscheme import std", {}),
        (
            "import parts\nfrom parts import R as parts",
            {"lib/parts.nosch": "component R:\n  pins:\n    1:\n"},
        ),
    ],
)
def test_import_errors(probe, tmp_path, prefix, library):
    compare(
        probe,
        make_project(tmp_path, {"src/main.nosch": prefix + "\nblock B:\n  T n\n", **library}),
    )


@pytest.mark.parametrize(
    "body",
    ["component B:\n  pins:\n    1:\n", "block Other:\n  T n\n", "block B:\n  net n = T\n"],
)
def test_board_errors(probe, tmp_path, body):
    compare(probe, make_project(tmp_path, {"src/main.nosch": body}))


def test_diamond_and_qualified_reexports(probe, tmp_path):
    config = make_project(
        tmp_path,
        {
            "src/main.nosch": "import vendor.parts\nfrom left import R as Left\nfrom right import R as Right\nblock B:\n  T n\n",
            "lib/vendor/parts.nosch": "from shared import R\n",
            "src/left.nosch": "from shared import R\n",
            "src/right.nosch": "from shared import R\n",
            "lib/shared.nosch": "component R:\n  pins:\n    1:\n",
            "lib/unreachable.nosch": "invalid text",
        },
    )
    compare(probe, config)
    assert not (tmp_path / "build").exists()


def test_deep_import_graph(probe, tmp_path):
    modules = {f"src/m{i}.nosch": f"from m{i + 1} import B\n" for i in range(180)}
    modules["src/m180.nosch"] = "block B:\n  T n\n"
    compare(probe, make_project(tmp_path, modules, **{"targets.default.root": "m0.B"}))


def test_symlink_escape(probe, tmp_path):
    config = make_project(tmp_path)
    (tmp_path / "external.nosch").write_text("block B:\n  T n\n")
    (tmp_path / "src/main.nosch").unlink()
    (tmp_path / "src/main.nosch").symlink_to(tmp_path / "external.nosch")
    compare(probe, config)


def test_snapshot_and_cache_invalidation(probe, tmp_path):
    config = make_project(tmp_path)
    main = tmp_path / "src/main.nosch"
    compare(probe, config, include_asts=True)
    compare(probe, config, include_asts=True)
    main.write_text("block B:\n  T changed_on_disk\n")
    compare(probe, config, include_asts=True)
    compare(probe, config, {main: "block B:\n  T unsaved\n"}, include_asts=True)
    compare(probe, config, {main: "block B:\n  net invalid = T\n"})
    compare(probe, config, include_asts=True)
    main.unlink()
    compare(probe, config)
    compare(probe, config, {main: "block B:\n  T only_in_editor\n"}, include_asts=True)
    compare(
        probe,
        config,
        {
            config: config.read_text().replace("main.B", "fresh.Fresh"),
            tmp_path / "src/fresh.nosch": "block Fresh:\n  T n\n",
        },
        include_asts=True,
    )


def test_unicode_error_span(probe, tmp_path):
    config = make_project(
        tmp_path, {"src/main.nosch": "// ☃\nfrom noscheme import nope\nblock B:\n  T n\n"}
    )
    compare(probe, config)


def test_plugin_exports_required(probe):
    result = probe({"project": str(ROOT / "examples/erc_extensions")})
    assert result["diagnostics"][0]["code"] == "native.erc-registry"


def test_config_io_failures(probe, tmp_path):
    missing = probe({"project": str(tmp_path / "absent.toml")})
    assert missing["diagnostics"][0]["code"] == "project.io"
    assert missing["exit_code"] == 2
    config = make_project(tmp_path)
    config.write_bytes(b"\xff")
    invalid = probe({"project": str(config)})
    assert invalid["diagnostics"][0]["code"] == "project.io"
    assert invalid["exit_code"] == 2


def test_output_directory_is_file(probe, tmp_path):
    config = make_project(tmp_path)
    (tmp_path / "build").write_text("not a directory")
    compare(probe, config)


def test_quoted_config_keys_and_crlf(probe, tmp_path):
    config = make_project(tmp_path)
    config.write_bytes(
        config.read_text()
        .replace("targets.default.root =", "targets.default.'root' =")
        .replace("\n", "\r\n")
        .encode()
    )
    compare(probe, config)
    main = tmp_path / "src/main.nosch"
    main.write_bytes(b"block B:\r\n  T n\r\n")
    compare(probe, config, include_asts=True)


def test_plugin_namespace_collision(probe, tmp_path):
    source = ROOT / "examples/erc_extensions"
    import shutil

    shutil.copytree(source, tmp_path, dirs_exist_ok=True)
    config = read_config(tmp_path)
    exports = erc_exports(config)
    namespace = next(iter(exports))
    (tmp_path / "main.nosch").write_text(f"import {namespace}\nblock B:\n  T n\n")
    module = tmp_path / (namespace.replace(".", "/") + ".nosch")
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text("component R:\n  pins:\n    1:\n")
    compare(probe, tmp_path)
