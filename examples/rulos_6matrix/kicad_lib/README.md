# Selected KiCad library snapshots

These modules were converted from installed **KiCad 10.0.6** libraries using
NoScheme's `convert-library` tool. They are committed so compilation, editor
navigation/completion, and export validation work without KiCad installed.
Handwritten specializations live in `../components.nosch`.

The `Device` snapshot includes generated interchangeable-pin groups for `R` and
`C`, allowing their instances to pass positional nets as arguments. Source values
and physical pad assignments are preserved by the example's regression tests.

| Directory | Converted symbols |
|---|---|
| `Device/` | `R`, `C` |
| `MCU_ST_STM32F3/` | `STM32F303K8Tx` |
| `Interface/` | `PCA9600D` |
| `Regulator_Switching/` | `TPS563200` |
| `Transistor_FET/` | `Q_PMOS_GSD` |
| `Connector_Generic/` | `Conn_01x02`, `Conn_01x03`, `Conn_02x05_Odd_Even` |
| `Connector/` | `Conn_01x08_Pin` |

Each directory contains the generated `.nosch` module, `conversion.json` with
source hashes and conversion issues, and a footprint snapshot including library
selection candidates. This directory's `footprints.json` records the **18
standard footprints actually selected** by the board. Supply this file directly to `export-kicad`.

Snapshot paths record provenance; compilation does not read those installed
library paths. PCB Editor needs the standard KiCad footprint libraries to instantiate the geometry. KiCad library sources are
available from the official [symbol](https://gitlab.com/kicad/libraries/kicad-symbols)
and [footprint](https://gitlab.com/kicad/libraries/kicad-footprints) repositories.
The converted snapshots remain under the KiCad libraries license (CC-BY-SA 4.0
with a design exception); see [LICENSE.md](LICENSE.md).

## Regenerate

From the repository root, with KiCad symbol/footprint libraries installed at
`/usr/share/kicad`:

```sh
uv run --locked python examples/rulos_6matrix/kicad_lib/refresh.py
uv run --locked pytest -q tests/test_rulos_6matrix.py
```

The script converts the explicit symbol list into temporary directories before
copying the generated files here. It then snapshots the design's selected KiCad
footprints. Review the diff before
accepting library changes. It does not change the original Eagle evidence,
`footprint_overrides.json`, or `noscheme.state.json`.
