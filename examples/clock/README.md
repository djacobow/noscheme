# Honest Millisecond Clock

A GPS-disciplined clock that shows HH:MM:SS.mmm on nine latched,
non-multiplexed 7-segment digits and serves NTP over Ethernet. The design
rationale, timing path, and power budget are in [HMS.md](HMS.md).

This is a design example, not a validated board: it compiles, passes ERC, and
exports a KiCad netlist and BOM. The PCB in `pcb/` is set up but not yet laid
out, and nothing has been built.

## Files

| File | Contents |
|---|---|
| `main.nosch` | Root block `HonestClock`: rails, subsystem wiring, OLED and probe headers, buttons, light sensor |
| `power.nosch` | USB-C + CH224K 12 V PD sink, AP63205/AP63203 bucks, LP5907 quiet LDO |
| `mcu.nosch` | STM32H723VGT6 pin map, decoupling, VCAP, reset, SWD header, do-not-fit HSE crystal |
| `timebase.nosch` | VCTCXO/OCXO, 16-bit EFC chain, 10 MHz SMA in/out, direction and clock jumpers |
| `gps.nosch` | MAX-M10S with active-antenna bias |
| `ethernet.nosch` | DP83848C RMII PHY, 50 MHz reference, HY951180A vertical MagJack |
| `display.nosch` | Nine digits, each with its own TLC5916 driver, in one shift chain |
| `interfaces.nosch` | Shared bundles |
| `components.nosch` | Passives, connectors, and handwritten parts |
| `kicad_lib/` | Converted KiCad symbols and the footprint snapshot; see its [README](kicad_lib/README.md) |
| `pcb/` | KiCad 10 board: outline, holes, 4-layer JLCPCB stackup, planes, 6/6/6 rules; see its [README](pcb/README.md) |

The display chain uses the instance-array slice pattern for a daisy chain:

```nosch
Digit digits[9]:
  [0].data_in: chain.data_in
  [1:9].data_in: link
  [0:8].data_out: link
  [8].data_out: chain.data_out
```

## Build

From the repository root:

```sh
uv run --locked noscheme check --project examples/clock
uv run --locked noscheme export-bom --project examples/clock
uv run --locked noscheme export-kicad --project examples/clock \
  --footprints examples/clock/kicad_lib/footprints.json \
  --schematic examples/clock/pcb/honest_clock.kicad_sch
```

The last command writes the KiCad schematic into the board's project; see
[pcb/README.md](pcb/README.md#bring-parts-in) for updating the board from it.

ERC reports no errors. It does report two `erc.incomplete_budget` warnings,
for the 5 V and 3.3 V rails, because loads are not annotated per pin; HMS.md
has the hand budget.

To see the block structure, with each block's ports and nets:

```sh
uv run --locked noscheme export-hierarchy --project examples/clock --nets
```

To draw the whole design on one sheet, with every block open:

```sh
uv run --locked noscheme export-schematic --project examples/clock
```

Either SVG converts to PNG or PDF with `rsvg-convert -f pdf -o out.pdf in.svg`.

## Fit options

The BOM marks these parts do-not-fit by default:

- **HSE crystal and load capacitors:** fit only when the clock jumper
  sends the 10 MHz to TIM2_ETR and you want a crystal-clocked MCU.
- **OCXO, its coupling capacitor, and its 0 Ω supply links:** to switch from
  the VCTCXO, fit the OCXO, one supply link (12 V or 5 V), and the OCXO
  coupling capacitor, and remove the VCTCXO coupling capacitor.
- **SMA termination (49.9 Ω):** fit only for a terminated 10 MHz input.

## Jumpers

| Jumper | Shunt 1–2 | Shunt 2–3 | No shunt |
|---|---|---|---|
| 10 MHz direction | Output | Input | Output (pulled down) |
| Clock | OSC_IN (HSE bypass) | TIM2_ETR | 10 MHz unused |

## Sourcing

Parts target JLCPCB assembly. Stock was checked on 2026-10-13, through LCSC
and JLCPCB part pages and web search; recheck before ordering.

- Every IC and connector records manufacturer, MPN, supplier, and supplier
  part number in its `bom` metadata.
- Generic 0603/0805/1206 passives carry only values: pick JLC basic parts by
  value.
- The exceptions:
  - **LSHD-5601 digits:** DigiKey, through-hole.
  - **VCTCXO:** DigiKey; consign it or fit it by hand.
  - **OCXO:** user-supplied.
  - **RJ45, SMAs, headers:** through-hole.

Pin assignments use the alternate functions listed in the KiCad STM32H723VGTx
symbol. Confirm them in STM32CubeMX before layout.
