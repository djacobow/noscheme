# KiCad library matches

Inventory of the installed KiCad **10.0.6** symbol and footprint libraries,
checked October 4, 2026 against this example's Eagle packages and physical pins.
The inventory below describes the original Eagle design. Reviewed substitutions
have now been applied as described next.

## Applied substitutions

35 component instances now use 11 standard KiCad footprint types. The selected
ICs and compatible generic interfaces inherit from converted KiCad symbols in
`kicad_lib/`. All original pad connections and values are preserved.
`footprint_overrides.json` records each deliberate package change by its original
Eagle reference, and regression tests require that exact set of substitutions.

All resistors use standard 0603. Capacitors through 1 µF use standard 0603;
those above 1 µF through 10 µF use standard 0805. C8 (47 µF) and C9 (unspecified)
retain their original footprints. This resolves the through-hole capacitor
package question below by explicitly changing those parts to SMT.

The debug connector uses the STM32 example's
`Connector_PinHeader_1.27mm:PinHeader_2x05_P1.27mm_Vertical_SMD`, intentionally
replacing the original keyed through-hole connector. Its symbol is derived from
`Connector_Generic:Conn_02x05_Odd_Even`; the matrix's unused SWO policy remains.

The LED-driver package discrepancy, displays, regulators, inductor, and power
jack remain as originally transcribed. Their candidate assessments below still
apply.

## Original inventory summary

Of **18 distinct Eagle packages covering 93 components**:

- **11 have strong standard-library candidates**, covering **32 components**.
- **6 need a package, part-number, or pin-mapping decision**, covering **60 components**.
- **1 has no direct footprint match found**, the single CUI PJ-051 power jack.

“Strong candidate” means matching package family and pin functions/numbering,
not identical copper geometry. KiCad and Eagle often use different pad sizes,
solder allowances, outlines, and origins. The tables distinguish exact named
symbols from generic symbols and alternate devices.

## Strong candidates

| Eagle package / parts | Qty | KiCad symbol | KiCad footprint |
|---|---:|---|---|
| `R0603` | 8 | `Device:R` | `Resistor_SMD:R_0603_1608Metric` |
| `C0603` | 7 | `Device:C` | `Capacitor_SMD:C_0603_1608Metric` |
| `C0805` | 2 | `Device:C` | `Capacitor_SMD:C_0805_2012Metric` |
| `C1210` | 1 | `Device:C` | `Capacitor_SMD:C_1210_3225Metric` |
| `QFP80P900X900X160-32N`, STM32F303K8T6 | 1 | `MCU_ST_STM32F3:STM32F303K8Tx` | `Package_QFP:LQFP-32_7x7mm_P0.8mm` |
| `SO08`, PCA9600 | 1 | `Interface:PCA9600D` | `Package_SO:SO-8_3.9x4.9mm_P1.27mm` |
| `SOT95P280X110-6N`, TPS563200 | 1 | `Regulator_Switching:TPS563200` | `Package_TO_SOT_SMD:SOT-23-6` |
| `SOT23`, row MOSFETs | 6 | `Transistor_FET:Q_PMOS_GSD` (generic) | `Package_TO_SOT_SMD:SOT-23` |
| `AMPHENOL_54602-908LF` | 3 | `Connector:Conn_01x08_Pin` (generic) | `Connector_RJ:RJ45_Amphenol_54602-x08_Horizontal` |
| `22-23-2021` | 1 | `Connector_Generic:Conn_01x02` | `Connector_Molex:Molex_KK-254_AE-6410-02A_1x02_P2.54mm_Vertical` |
| `22-23-2031` | 1 | `Connector_Generic:Conn_01x03` | `Connector_Molex:Molex_KK-254_AE-6410-03A_1x03_P2.54mm_Vertical` |

The MCU, PCA9600, and TPS563200 symbols have matching physical pin functions at
every pad. The PMOS generic symbol has exactly `1=G, 2=S, 3=D`; no exact
DMP2035U symbol was found. Generic connector symbols preserve the pad numbers;
board-specific roles would still be supplied by the example.

Numbered RJ45 pad centers and drill diameters match after translating origins.
The Molex candidates have the same numbered pin centers but 1.19 mm holes rather
than Eagle's 1.00 mm. Standard SMD footprints differ in pad dimensions; the MCU's
pad-center offset is 0.025 mm and the SO-8 lands differ by about 0.50 mm after
rotation. These are package candidates for a newly laid-out board, not exact
replacements for already routed copper.

## Decisions and partial matches

### LED drivers — 4 parts

The stored value is **STP16CPC26XTR**, but the Eagle package
`SOP65P640X120-24` has only pads 1–24.

- `Driver_LED:STP16CPC26T` plus
  `Package_SO:TSSOP-24_4.4x7.8mm_P0.65mm` matches the 24 signal-pin functions
  and the existing package pattern, but identifies the **T** package variant.
- `Driver_LED:STP16CPC26X` plus
  `Package_SO:HTSSOP-24-1EP_4.4x7.8mm_P0.65mm_EP3.2x5mm` matches the **X**
  part number and adds pin 25, the exposed pad.

ST confirms the T/X package distinction. The exposed pad, when present, should
be soldered to an electrically isolated or grounded metal land. Selecting the
X package therefore requires an explicit additional-pad decision; silently
changing to T would alter the recorded part number.
[STP16CPC26 datasheet](https://www.st.com/resource/en/datasheet/stp16cpc26.pdf).

### Seven-segment displays — 48 parts

No exact `SA56-11` or `INND-TS56RAB` symbol/footprint name was found.

- `Display_7Segment:7SegmentLED_LTS6760_LTS6780` and
  `Display_7Segment:D1X8K` have the same numbered hole centers after a 90°
  rotation and translation: 2.54 mm pitch, 15.24 mm between rows. This is a
  useful physical candidate, pending body, drill, and selected-part checks.
- `Display_Character:KCSA02-105` has the same common-anode segment-to-pin map
  (`1=E, 2=D, 3=CA, 4=C, 5=DP, 6=B, 7=A, 8=CA, 9=F, 10=G`), but describes a
  different, surface-mount device. It is a possible interface source, not a
  physical replacement. Its default footprint must not be inherited here.
- `SA39-11EWA` has different segment numbering, and `DA56-11EWA` is a
  two-digit, 18-pin device. Neither is a direct match despite similar names.
- The D1X8K symbols are common-cathode, so their matching hole pattern does
  not make their symbols electrically interchangeable with this design.

Retaining the current display definition while replacing only its footprint
with a verified mechanical equivalent may be clearest.

### TO-92 regulators — 2 parts

`Regulator_Linear:L78L10_TO92` and `L78L33_TO92` have the source pin ordering
`1=VO, 2=GND, 3=VI`. However, `7803` does not establish the precise 3.3 V device
or rating, and `7810` does not establish a particular manufacturer/variant.

The Eagle footprint has pin 2 displaced 1.905 mm from pins 1 and 3; KiCad's
`Package_TO_SOT_THT:TO-92` uses 1.27 mm. The symbols' default `TO-92_Inline`
footprint differs further. Confirm actual regulator parts and lead forming.

### Through-hole capacitors — 4 parts

`Device:C` is a direct symbolic match. The Eagle `C025-025X050` description says
2.5 × 5 mm body and 2.5 mm grid; its actual holes are 2.54 mm apart. Nearby
KiCad candidates include `Capacitor_THT:C_Rect_L4.0mm_W2.5mm_P2.50mm` and the
4.6 mm MKS02/FKP02 series. No exact outline match was established. The source
symbol is nonpolarized; selecting a radial electrolytic footprint just because
its pitch matches would add an unsupported assumption.

### Debug header — 1 part

`Connector_Generic:Conn_02x05_Odd_Even` matches the pad numbering.
`Connector_PinHeader_1.27mm:PinHeader_2x05_P1.27mm_Vertical` matches numbered
hole centers after rotation, but has 0.65 mm drills rather than 0.60 mm and is
an **unshrouded** header. The Eagle description specifies a keyed CNC Tech
`3220-10-0100-00` connector. Its shroud/keepout still needs matching.

### Inductor — 1 part

`Device:L` provides the generic symbol. No exact DEM804XC footprint was found.
`Inductor_SMD:L_Changjiang_FXL0840` and `L_Sunlord_MWSA0804S` have the same
6.75 mm pad-center separation, but body dimensions and lands still need review.
Their pads are `1`/`2`; Eagle uses `P$1`/`P$2`, requiring explicit remapping.
This is a footprint assessment, not a substitute-inductor electrical selection.

## No direct footprint match found

The CUI PJ-051 jack uses `P$1`, `P$2`, `P$3`, `P$G1`, and `P$G2`. KiCad has
`Connector:Barrel_Jack_Switch`, but its three numbered contacts cannot simply
replace this five-pad interface. The installed CUI jack footprints cover other
models (PJ-036AH, PJ-063AH, PJ-079BH, PJ-102AH); no PJ-051 was found. Keep the
imported footprint and explicit contact mapping unless a mechanical match is
established.

## Remaining migration decisions

The straightforward IC, passive, PMOS, and connector substitutions are complete.
NoScheme identities and source connectivity tests have been retained. Resolve the LED
driver variant before changing its physical interface; record any deliberate
source corrections separately from the original Eagle evidence.
