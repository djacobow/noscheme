"""Exercise the real Emacs major mode and Eglot client against both servers."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("implementation", ["python", "native"])
def test_emacs_eglot(tmp_path, implementation):
    emacs = shutil.which("emacs")
    if emacs is None:
        pytest.skip("Emacs 29+ is not installed")
    version = subprocess.check_output(
        [emacs, "-Q", "--batch", "--eval", "(princ emacs-major-version)"], text=True, timeout=10
    )
    if int(version) < 29:
        pytest.skip("Emacs 29+ is required for the bundled Eglot setup")
    command = [sys.executable, "-m", "noscheme.lsp"]
    if implementation == "native":
        native = Path(
            os.environ.get("NOSCHEME_NATIVE_LSP", ROOT / "build/native-release/noscheme-native-lsp")
        )
        if not native.is_file():
            pytest.skip("native LSP not built; set NOSCHEME_NATIVE_LSP to test another build")
        command += ["--implementation", "native", "--native-server", str(native)]
    root = tmp_path / "designs with spaces"
    for name in ("one", "two"):
        project = root / name
        project.mkdir(parents=True)
        (project / "noscheme.toml").write_text(
            'language_version = "0.1"\nboard = "main.B"\nsource_paths = ["."]\n'
            'library_paths = []\noutput_dir = "build"\n'
        )
        (project / "parts.nosch").write_text(
            """from noscheme import role, unused
component Capacitor:
  params:
    capacitance value = 100n
  pins:
    1..2: [role.passive, unused.never]
  metadata:
    interchangeable: [1, 2]
"""
        )
        (project / "main.nosch").write_text(
            """from noscheme import net
from parts import Capacitor as C
board B:
  net.passive wire
  C cap(1: wire, 2: wire)
  C spare
"""
        )
    result = subprocess.run(
        [
            emacs,
            "-Q",
            "--batch",
            "-L",
            str(ROOT / "editors/emacs"),
            "-l",
            "noscheme-tests",
            "-f",
            "ert-run-tests-batch-and-exit",
        ],
        env={
            **os.environ,
            "NOSCHEME_EMACS_TEST_ROOT": str(root),
            "NOSCHEME_EMACS_TEST_COMMAND": json.dumps(command),
        },
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
