"""Regenerate implementation-owned LRM tables; --check detects documentation drift."""

import argparse
from pathlib import Path

from noscheme.erc_registry import load_registry
from noscheme.lsp_hover import BUILTIN_HELP
from noscheme.metadata import SCHEMAS, ROLES

ROOT = Path(__file__).resolve().parents[1]


def table(headers, rows):
    return "\n".join(
        [
            "| " + " | ".join(headers) + " |",
            "|" + "|".join("---" for _ in headers) + "|",
            *("| " + " | ".join(row) + " |" for row in rows),
        ]
    )


def generated_tables():
    assert set(BUILTIN_HELP["role"]) == ROLES
    return {
        "core-fields": table(
            ("Scope", "Fields"),
            (
                (f"`{scope}`", ", ".join(f"`{field}`" for field in sorted(fields)))
                for scope, fields in SCHEMAS.items()
            ),
        ),
        "extension-fields": table(
            ("Scope", "Field", "Type"),
            (
                (
                    f"`{scope}`",
                    f"`{field}`",
                    ", ".join(f"`{kind[0]}.{v}`" for v in sorted(kind[1]))
                    if isinstance(kind, tuple)
                    else f"`{kind}`",
                )
                for scope, fields in sorted(load_registry().schemas.items())
                for field, kind in sorted(fields.items())
            ),
        ),
        "roles": table(
            ("Role", "Meaning"),
            ((f"`{role}`", meaning) for role, meaning in BUILTIN_HELP["role"].items()),
        ),
    }


def update(text):
    for name, content in generated_tables().items():
        start = f"<!-- generated:{name} -->"
        end = f"<!-- /generated:{name} -->"
        first = text.index(start) + len(start)
        last = text.index(end, first)
        text = text[:first] + "\n" + content + "\n" + text[last:]
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = ROOT / "docs/LRM.md"
    original = path.read_text()
    updated = update(original)
    if args.check:
        if original != updated:
            parser.exit(1, "LRM tables are stale; run python -m tools.reference_tables\n")
    else:
        path.write_text(updated)


if __name__ == "__main__":
    main()
