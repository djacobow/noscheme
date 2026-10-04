from pathlib import Path

from tools.reference_tables import update


def test_reference_tables_match_implementation():
    path = Path(__file__).resolve().parents[1] / "docs/LRM.md"
    text = path.read_text()
    assert update(text) == text, "Regenerate with python -m tools.reference_tables"
