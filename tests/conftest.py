"""Shared source dependencies for isolated reference-board tests."""

from pathlib import Path

import pytest


@pytest.fixture
def stm32_libraries(tmp_path):
    examples = Path(__file__).resolve().parents[1] / "examples/stm32h523"
    for source in [
        *sorted(p for p in examples.glob("*.nosch") if p.name != "stm32h523.nosch"),
        examples / "kicad_device/Device.nosch",
    ]:
        (tmp_path / source.name).write_text(source.read_text())
