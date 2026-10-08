"""Fetch pinned native dependencies explicitly, or run the verified ANTLR generator."""

import argparse
import configparser
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def verified(path, expected):
    return path.is_file() and sha256(path.read_bytes()).hexdigest() == expected


def fetch(url, path, expected):
    if verified(path, expected):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with (
            urlopen(url, timeout=60) as response,
            tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream,
        ):
            temporary = Path(stream.name)
            while chunk := response.read(1024 * 1024):
                stream.write(chunk)
        if not verified(temporary, expected):
            raise ValueError(f"SHA-256 mismatch for {url}")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--fetch", action="store_true")
    modes.add_argument("--generate", nargs=2, metavar=("GRAMMAR", "OUTPUT"))
    args = parser.parse_args()
    generator = json.loads((ROOT / "cpp/dependencies.json").read_text())["antlr_generator"]
    jar = ROOT / "build/native-deps" / generator["filename"]
    if args.fetch:
        fetch(generator["url"], jar, generator["sha256"])
        for wrap in sorted((ROOT / "subprojects").glob("*.wrap")):
            config = configparser.ConfigParser()
            config.read(wrap)
            source = config["wrap-file"]
            fetch(
                source["source_url"],
                ROOT / "subprojects/packagecache" / source["source_filename"],
                source["source_hash"],
            )
        print("Native dependency archives verified.")
        return 0
    if not verified(jar, generator["sha256"]):
        parser.error(
            "ANTLR generator is missing or changed; run python3 -m tools.native_dependencies --fetch"
        )
    grammar, output = args.generate
    return subprocess.call(
        [
            "java",
            "-jar",
            str(jar),
            "-Dlanguage=Cpp",
            "-no-listener",
            "-Xexact-output-dir",
            "-o",
            output,
            grammar,
        ]
    )


if __name__ == "__main__":
    sys.exit(main())
