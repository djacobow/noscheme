"""Exercise editor runtime files using the editor's actual syntax engine."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("editor", ["vim", "nvim"])
def test_noscheme_runtime(tmp_path, editor):
    binary = shutil.which(editor)
    if binary is None:
        pytest.skip(f"{editor} is not installed")
    source = [
        "net.digital buttons#",
        "net.digital #reset:  // TODO test",
        "  bias: bias.pull_up",
        '  value: "https://example/*ok*/\\\\path"',
        "/* block /* does not nest */ global net.digital visible",
        "10.5e-2 << 1 sizes",
        "Device bank[2]:",
        "  01..03: no_connect",
        "Device<value: 1k> probe(1: n)",
        "net# is_an_identifier",
        "/* multiline",
        "net.digital hidden",
        "*/",
        "component Resistor:",
        "  value: num.round(4.7k) + 4k7",
        "extend Resistor<4.7k> Pullup",
    ]
    design = tmp_path / "sample.nosch"
    design.write_text("\n".join(source) + "\n")
    log = tmp_path / "errors.txt"
    commands = [
        "set nocompatible",
        f"execute 'set runtimepath+=' . fnameescape({json.dumps(str(ROOT / 'editors/vim'))})",
        "filetype plugin on",
        "syntax enable",
        f"execute 'edit ' . fnameescape({json.dumps(str(design))})",
        "call assert_equal('noscheme', &filetype)",
        "call assert_equal('noscheme', b:current_syntax)",
        "call assert_equal('// %s', &l:commentstring)",
        "call assert_equal(2, &l:shiftwidth)",
        "call assert_true(&l:expandtab)",
        "call assert_equal('#reset', matchstr('#reset', '\\k\\+'))",
    ]
    for line, token, group in [
        (1, "buttons#", ""),
        (1, "net.digital", "noschemeBuiltin"),
        (2, "#reset", ""),
        (2, "TODO", "noschemeTodo"),
        (3, "bias.pull_up", "noschemeBuiltin"),
        (4, "https", "noschemeString"),
        (4, "/*ok*/", "noschemeString"),
        (4, "\\\\", "noschemeEscape"),
        (5, "block", "noschemeBlockComment"),
        (5, "global", "noschemeKeyword"),
        (6, "10.5e-2", "noschemeNumber"),
        (6, "<<", "noschemeOperator"),
        (8, "01", "noschemeNumber"),
        (8, "no_connect", "noschemeConstant"),
        (10, "net#", ""),
        (12, "net", "noschemeBlockComment"),
        (14, "Resistor", "noschemeDefinition"),
        (15, "num.round", "noschemeFunction"),
        (15, "4.7k", "noschemeNumber"),
        (15, "4k7", "noschemeNumber"),
        (9, "<", "noschemeDelimiter"),
        (16, "extend", "noschemeDeclaration"),
        (16, "Resistor", "noschemeTypeName"),
    ]:
        column = source[line - 1].index(token) + 1
        commands.append(
            f"call assert_equal('{group}', synIDattr(synID({line}, {column}, 1), 'name'), 'line {line}: {token}')"
        )
    commands += [
        "execute b:undo_ftplugin",
        "call assert_equal(&g:shiftwidth, &l:shiftwidth)",
        f"call writefile(v:errors, {json.dumps(str(log))})",
        "if len(v:errors) | cquit | endif",
        "qa!",
    ]
    script = tmp_path / "check.vim"
    script.write_text("\n".join(commands) + "\n")
    result = subprocess.run(
        [binary, "-Nu", "NONE", "-i", "NONE", "-n", "-es", "-S", str(script)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, (
        (log.read_text() if log.exists() else "No assertion log") + result.stdout + result.stderr
    )
