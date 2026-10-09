"""Pytest plugin: snapshot every project the Python ERC tests load.

The native ERC parity test replays the snapshots through both engines. Set
NOSCHEME_ERC_CORPUS to the snapshot directory.
"""

import hashlib
import os
from pathlib import Path
import shutil

import noscheme.project

ROOT = Path(__file__).resolve().parents[2]
CORPUS = Path(os.environ["NOSCHEME_ERC_CORPUS"])
original = noscheme.project.load_project


def digest(root):
    files = sorted(p for p in root.rglob("*") if p.is_file() and "build" not in p.parts)
    hasher = hashlib.sha256()
    for path in files:
        hasher.update(str(path.relative_to(root)).encode() + b"\0" + path.read_bytes())
    return hasher.hexdigest()[:16]


def recording(path=".", *args, **kwargs):
    config = Path(path)
    root = (config if config.is_dir() else config.parent).resolve()
    if not root.is_relative_to(ROOT) and (root / "noscheme.toml").is_file():
        target = CORPUS / digest(root)
        if not target.exists():
            shutil.copytree(root, target, ignore=shutil.ignore_patterns("build", "__pycache__"))
    return original(path, *args, **kwargs)


noscheme.project.load_project = recording
