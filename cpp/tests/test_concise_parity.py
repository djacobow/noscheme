"""Native parity for the concise syntax: instance bodies, `*`, grouped nets,
voltage ranges, inline aliases, choice(), flat(), and descending ranges."""

import sys

import pytest

from test_circuit_parity import check_source, probe  # noqa: F401
from test_project_parity import ROOT

sys.path.insert(0, str(ROOT / "tests"))
from test_concise_syntax import SOURCES  # noqa: E402


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_concise_syntax(probe, tmp_path, name):  # noqa: F811
    check_source(probe, tmp_path, SOURCES[name])
