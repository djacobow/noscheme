# KiCad library snapshots for the clock example

These modules were converted from the KiCad 10.0.6 symbol libraries installed at
`/usr/share/kicad` on 2026-10-13. They are committed so the example compiles
and exports without a local KiCad installation, and so library updates cannot
change the design unreviewed. Each directory holds the converted `.nosch`
module, a `conversion.json` report with source hashes and conversion issues,
and that conversion's `footprints.json`.

| Directory | Symbols |
|---|---|
| `74xGxx` | 74LVC1G125, 74LVC1G17, 74LVC1G3157 |
| `Amplifier_Operational` | MCP6001-OT |
| `Analog_DAC` | DAC8551IxDGK |
| `Connector` | USB_C_Receptacle_USB2.0_16P, Conn_ARM_JTAG_SWD_10, RJ45_Hanrun_HR911105A_Horizontal, Conn_Coaxial |
| `Device` | R, C, LED, Crystal_GND24 |
| `Driver_LED` | TLC5916 |
| `Interface_USB` | CH224K |
| `Jumper` | Jumper_3_Open |
| `MCU_ST_STM32H7` | STM32H723VGTx |
| `Oscillator` | OCXO-14 |
| `Power_Protection` | USBLC6-2SC6 |
| `RF_GPS` | MAX-M10S |
| `Reference_Voltage` | REF3025 |
| `Regulator_Linear` | LP5907MFX-3.3 |
| `Regulator_Switching` | AP63205WU, AP63203WU |
| `Switch` | SW_Push |

`footprints.json` at this level is the combined snapshot that `export-kicad`
validates against. It holds every footprint the design assigns plus each
conversion's candidates.

Some parts are handwritten in the example sources instead:

- **DP83848C (`ethernet.nosch`):** the KiCad symbol omits pads 8–12, which the
  LQFP-48 footprint has.
- **Inductor and TVS (`components.nosch`):** two-terminal parts whose KiCad
  symbols carry roughly 900 footprint candidates each.
- **Digit, VCTCXO, 50 MHz oscillator, light sensor, and headers:** no matching
  KiCad symbol exists.

The DAC8551 specialization replaces the symbol's VSON footprint with VSSOP-8,
which is what the DGK package suffix means.

## Regenerate

Use a fresh staging directory; the converter refuses to overwrite one. For each
library above:

```sh
uv run --locked noscheme convert-library /usr/share/kicad/symbols/<Library>.kicad_sym \
  --symbol <Symbol> [--symbol <Symbol> ...] \
  --footprint-root /usr/share/kicad/footprints \
  --output "$stage/<Library>"
```

Then rebuild the combined `footprints.json` as in the
[STM32 example's instructions](../../stm32h523/kicad_lib/README.md#regenerate-and-review),
looping over every converted directory here. Review the reports, then run
`noscheme check --project examples/clock`.

The snapshots remain under the KiCad libraries license; see
[LICENSE.md](LICENSE.md).
