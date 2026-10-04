# KiCad library snapshots for the STM32 example

These files were generated from installed KiCad libraries. They are committed
so the example can compile, offer footprint completion, and validate its export
without a local KiCad installation. They also keep library updates from changing
the example until we review and commit the new conversion.

| File | Contents |
|---|---|
| `device/Device.nosch` | Converted `Device:R` and `Device:C` symbols, including footprint selection hints |
| `device/conversion.json` | Symbol source path, SHA-256 hash, converted names, and conversion issues |
| `device/footprints.json` | Footprint records collected when converting the passive symbols |
| `mcu/MCU_ST_STM32H5.nosch` | Converted `MCU_ST_STM32H5:STM32H523VCTx` symbol |
| `mcu/conversion.json` | MCU source provenance and conversion issues, including omitted ambiguous pin aliases |
| `footprints.json` | Combined snapshot used for board export: selected board footprints and passive footprint candidates |

Footprint records contain pad identifiers, pad technology, source paths, and
hashes. They do not contain the full footprint geometry; PCB Editor still needs
the actual KiCad footprint libraries when importing the netlist. Recorded source
paths are provenance, not paths that must exist to compile this example.

The project adds `device/` and `mcu/` to its library search paths. Handwritten
specializations remain in `../components.nosch` and `../mcu.nosch`. Keep changes
to values, pin roles, and board-specific metadata there.

The original MCU output was named `MCU_ST_STM32H5.nosch` by the converter and
previously stored as `parts.nosch`. Its original footprint records were folded
into the combined snapshot. This directory reorganization did not regenerate
the snapshots. Source hashes in the JSON reports identify the original inputs;
the reports do not record a KiCad package version. Current CI tests KiCad 10.0.

## Regenerate and review

Run from the repository root with KiCad symbol and footprint libraries installed.
Adjust `/usr/share/kicad` if your installation uses a different location. Use a
fresh staging directory: the converter intentionally refuses to overwrite an
existing output directory.

```sh
snapshot_stage=$(mktemp -d)
uv run --locked noscheme convert-library \
  /usr/share/kicad/symbols/Device.kicad_sym \
  --symbol R --symbol C \
  --footprint-root /usr/share/kicad/footprints \
  --output "$snapshot_stage/device"
uv run --locked noscheme convert-library \
  /usr/share/kicad/symbols/MCU_ST_STM32H5.kicad_sym \
  --symbol STM32H523VCTx \
  --footprint-root /usr/share/kicad/footprints \
  --output "$snapshot_stage/mcu"
```

Each conversion produces a `.nosch` module, `conversion.json`, and
`footprints.json`. Review the reports and compare the staged modules with the
committed copies. New converter or KiCad versions can change metadata and
candidate lists, so regeneration need not produce identical files.

Build the combined footprint snapshot from the currently selected board
footprints and the freshly converted libraries:

```sh
uv run --locked python - "$snapshot_stage" <<'PY'
import json
import sys
from pathlib import Path
from noscheme.circuit import elaborate_project
from noscheme.kicad.convert import footprint_record
from noscheme.resolve import resolve_project

stage = Path(sys.argv[1])
project, definitions = resolve_project(Path("examples/stm32h523"))
circuit, _ = elaborate_project(project, definitions)
names = {
    part["metadata"]["footprint"]["value"]
    for part in circuit["parts"]
    if "footprint" in part["metadata"]
}
for module in ("device", "mcu"):
    names.update(json.loads((stage / module / "footprints.json").read_text())["footprints"])
records = {
    name: footprint_record(name, [Path("/usr/share/kicad/footprints")])
    for name in sorted(names)
}
missing = [name for name, record in records.items() if not record["exists"]]
if missing:
    raise SystemExit(f"Missing footprints: {missing}")
(stage / "footprints.json").write_text(
    json.dumps({"schema_version": 1, "footprints": records}, indent=2, sort_keys=True) + "\n"
)
PY
```

After reviewing, copy the staged modules and reports into the corresponding
directories here, replace `device/footprints.json`, and replace the combined
`footprints.json`. The MCU's staged footprint snapshot is already included in
the combined file and does not need a separate committed copy. Then run:

```sh
uv run --locked noscheme check --project examples/stm32h523
uv run --locked pytest -q tests/test_convert.py tests/test_stm32_reference.py
```

Retain `../noscheme.state.json`: it stores persistent board identities and
reference designators, and is not a generated library snapshot.
