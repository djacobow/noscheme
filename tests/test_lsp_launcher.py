"""The implementation selector preserves both editor entry points."""

import pytest
import sys

from noscheme import cli, lsp, lsp_launcher


@pytest.mark.parametrize("entry", [cli.main, lsp.main, lsp_launcher.main])
def test_python_default(entry, monkeypatch):
    projects = []

    class Server:
        def start_io(self):
            projects.append("started")

    def create(project):
        projects.append(project)
        return Server()

    monkeypatch.setattr(lsp, "create_server", create)
    entry((["lsp"] if entry is cli.main else []) + ["--project", "demo"])
    assert str(projects[0]) == "demo"
    assert projects[1] == "started"


def test_native_preserves_arguments(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(lsp_launcher.os, "execv", lambda *args: calls.append(args))
    executable = tmp_path / "server with spaces"
    cli.main(
        [
            "lsp",
            "--implementation",
            "native",
            "--native-server",
            str(executable),
            "--project",
            "project with spaces",
        ]
    )
    assert calls == [
        (
            str(executable),
            [str(executable), "--python", sys.executable, "--project", "project with spaces"],
        )
    ]


@pytest.mark.parametrize("explicit", [False, True])
def test_unavailable_native_is_error(explicit, monkeypatch, capsys):
    monkeypatch.setattr(lsp_launcher.shutil, "which", lambda _: None)

    def fail(*args):
        raise FileNotFoundError("missing executable")

    monkeypatch.setattr(lsp_launcher.os, "execv", fail)
    with pytest.raises(SystemExit) as exc:
        lsp_launcher.main(
            ["--implementation", "native"]
            + (["--native-server", "/missing/server"] if explicit else [])
        )
    assert exc.value.code == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "native LSP server" in output.err


def test_native_option_requires_native(capsys):
    with pytest.raises(SystemExit):
        lsp_launcher.main(["--native-server", "anything"])
    assert "requires --implementation native" in capsys.readouterr().err
