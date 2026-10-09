"""Scope completion cases shared with the native protocol regression suite."""

import json
from pathlib import Path

import pytest

from test_lsp_completion import complete

CASES = json.loads((Path(__file__).parent / "fixtures/lsp_scope_completion.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_scope_completion(tmp_path, case):
    suggestions = complete(tmp_path, case["source"])
    assert set(case["include"]) <= suggestions.keys()
    assert not set(case["exclude"]) & suggestions.keys()
    for suggestion in suggestions.values():
        assert suggestion.start <= suggestion.end
