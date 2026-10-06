"""Regenerate selected KiCad symbol snapshots and the active footprint records.

Run from the repository root with NoScheme and KiCad's libraries installed.
The original Eagle evidence and footprint_overrides.json are not changed.
"""

import json
from pathlib import Path
import shutil
import tempfile

from noscheme.circuit import elaborate_project
from noscheme.kicad.convert import convert_library, footprint_record
from noscheme.resolve import resolve_project

ROOT = Path(__file__).resolve().parents[1]
SYMBOLS = {
    "Device": ["R", "C"],
    "MCU_ST_STM32F3": ["STM32F303K8Tx"],
    "Interface": ["PCA9600D"],
    "Regulator_Switching": ["TPS563200"],
    "Transistor_FET": ["Q_PMOS_GSD"],
    "Connector_Generic": ["Conn_01x02", "Conn_01x03", "Conn_02x05_Odd_Even"],
    "Connector": ["Conn_01x08_Pin"],
}


def refresh_footprints():
    project, definitions = resolve_project(ROOT)
    circuit, warnings = elaborate_project(project, definitions)
    if warnings:
        raise RuntimeError(f"Resolve design warnings before snapshotting: {warnings}")
    names = {p["metadata"]["footprint"]["value"] for p in circuit["parts"]}
    records = {
        name: footprint_record(name, [Path("/usr/share/kicad/footprints")])
        for name in sorted(names)
    }
    missing = [name for name, record in records.items() if not record["exists"]]
    if missing:
        raise RuntimeError(f"Missing KiCad footprints: {missing}")
    (ROOT / "kicad_lib/footprints.json").write_text(
        json.dumps({"schema_version": 1, "footprints": records}, indent=2, sort_keys=True) + "\n"
    )


def main():
    with tempfile.TemporaryDirectory() as temporary:
        for module, symbols in SYMBOLS.items():
            stage = Path(temporary) / module
            convert_library(
                Path("/usr/share/kicad/symbols") / (module + ".kicad_sym"),
                stage,
                symbols=symbols,
                footprint_roots=[Path("/usr/share/kicad/footprints")],
            )
        for module in SYMBOLS:
            destination = ROOT / "kicad_lib" / module
            destination.mkdir(parents=True, exist_ok=True)
            for source in (Path(temporary) / module).iterdir():
                shutil.copy2(source, destination / source.name)
    refresh_footprints()


if __name__ == "__main__":
    main()
