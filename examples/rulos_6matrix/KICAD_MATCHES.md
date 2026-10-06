# KiCad library matches

Inventory of the installed KiCad **10.0.6** symbol and footprint libraries,
checked October 4, 2026, with remaining-part review October 6, 2026, against this example's Eagle packages and physical pins.
The inventory below describes the original Eagle design. Reviewed substitutions
have now been applied as described next.

## Applied substitutions

All 93 component instances now use 18 standard KiCad footprint types. The selected
ICs and compatible generic interfaces inherit from converted KiCad symbols in
`kicad_lib/`. Original connectivity is preserved except for documented regulator pin remapping;
regulator values and output capacitors are updated as described below. Each LED driver adds
exposed pad 25 connected to ground.
`footprint_overrides.json` records each deliberate package change by its original
Eagle reference, and regression tests require that exact set of substitutions.

All resistors use standard 0603. Capacitors through 1 µF use standard 0603;
those above 1 µF through 10 µF use standard 0805. C8 and C9 now use polarized Panasonic FK 47 µF/25 V aluminum electrolytics. This resolves the through-hole capacitor
package question below by explicitly changing those parts to SMT.

The debug connector uses the STM32 example's
`Connector_PinHeader_1.27mm:PinHeader_2x05_P1.27mm_Vertical_SMD`, intentionally
replacing the original keyed through-hole connector. Its symbol is derived from
`Connector_Generic:Conn_02x05_Odd_Even`; the matrix's unused SWO policy remains.

The LED-driver discrepancy is resolved using the recorded XTR part and a
grounded exposed pad. The 48 displays now use the LTS6760/LTS6780 footprint with their existing
common-anode interface. The regulators now use SOT-223. The inductor uses the Bourns footprint described below; the power jack uses the SMT substitution described below.

## Remaining footprint review — October 6, 2026

No active custom footprints remain. All 93 instances use standard KiCad
footprints; the unused imported Eagle footprint library has been removed. Original
connectivity and package names remain in `upstream/connectivity.json`.

C8 and C9 both use **EEEFK1E470P**, a Panasonic FK-series 47 µF/25 V
aluminum electrolytic, in `Capacitor_SMD:CP_Elec_6.3x5.8`. The
[manufacturer specifications](https://na.industrial.panasonic.com/products/capacitors/lineup/aluminum-electrolytic-capacitors/series/88994/model/89507)
give a 6.3 × 5.8 mm case, ±20% capacitance tolerance, and 240 mA rated ripple
current at 100 kHz. Pad 1 connects to `led_power`, pad 2 to ground. C9's
previously unspecified value is now 47 µF. These changes supersede the earlier
policy of retaining these two packages.

The pair provides 94 µF nominal, above the TPS563200 datasheet's 20–68 µF
example range. Parallel electrolytics reduce impedance and share ripple, but
neither startup/stability nor ripple sharing is validated by the connectivity
tests. Confirm these on hardware before fabrication sign-off; the pair is not
being represented as an electrically identical replacement for the ceramic.

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

**Resolved:** retain STP16CPC26XTR, use
`Package_SO:HTSSOP-24-1EP_4.4x7.8mm_P0.65mm_EP3.2x5mm`, and connect
new pin 25 (`EP`) to the same ground as pin 1 in every driver instance.
The original Eagle snapshot remains unchanged; the regression test explicitly
adds these four ground connections to its expected circuit.

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

**Applied:** `Display_7Segment:7SegmentLED_LTS6760_LTS6780`, retaining
the original values and common-anode pin definitions. The selected mechanical
target is the Inolux part named in the source PDF. Hole fit remains a fabrication
check as explained below. No segment or common-pin remapping is needed.

No exact `SA56-11` or `INND-TS56RAB` symbol/footprint name was found.

- `Display_7Segment:7SegmentLED_LTS6760_LTS6780` and
  `Display_7Segment:D1X8K` have the same numbered hole centers after a 90°
  rotation and translation: 2.54 mm pitch, 15.24 mm between rows. This is a
  useful physical candidate. Both use 0.8 mm drills, matching the imported
  footprint. The LTS footprint's fabrication outline is 19.05 × 12.6 mm;
  D1X8K is 19 × 12.5 mm.
- `Display_Character:KCSA02-105` has the same common-anode segment-to-pin map
  (`1=E, 2=D, 3=CA, 4=C, 5=DP, 6=B, 7=A, 8=CA, 9=F, 10=G`), but describes a
  different, surface-mount device. It is a possible interface source, not a
  physical replacement. Its default footprint must not be inherited here.
- `SA39-11EWA` has different segment numbering, and `DA56-11EWA` is a
  two-digit, 18-pin device. Neither is a direct match despite similar names.
- The D1X8K symbols are common-cathode, so their matching hole pattern does
  not make their symbols electrically interchangeable with this design.

The source drawing names INND-TS56RAB while the Eagle package is named SA56-11.
The [Inolux INND-TS56 datasheet, page 1](https://www.inolux-corp.com/datasheet/Display/Through-Hole-Display/SingleDigit/INND-TS56%20Series_V1.0.pdf)
confirms the common-anode pin map above, a 19 × 12.6 mm body, 2.54 mm pin pitch,
and 15.24 mm row spacing. This supports the LTS footprint for the Inolux target;
its 0.05 mm body-length difference is small. The drawing does not specify a
recommended finished PCB hole size, so its lead fit remains unverified.

Do not treat every SA56-11 variant as mechanically identical: the
[Kingbright SA56-11EWA drawing](https://www.kingbrightusa.com/images/catalog/SPEC/sa56-11ewa.pdf)
recommends 1.0 mm PCB holes, larger than either the imported or candidate
footprints' 0.8 mm holes. Select the intended display before calling this a
production-ready substitution. Retain the current common-anode definition when
changing its footprint; the footprint name need not become the component name.

### Regulator substitutions — 2 parts

The original `7803` and `7810` labels have been replaced with specific SOT-223
parts. Both use `Package_TO_SOT_SMD:SOT-223-3_TabPin2`, retaining the existing
instance identities. Pin remapping is explicit in the source-comparison test.

| Original | Selected part | Pin 1 | Pin 2 and tab | Pin 3 |
|---|---|---|---|---|
| IC3, `7803` | TLV1117LV33DCYR | GND | 3.3 V output | 5 V input |
| IC4, `7810` | LM2937IMP-10/NOPB | 12 V input | GND | 10 V output |

The [TLV1117LV datasheet](https://www.ti.com/lit/ds/symlink/tlv1117lv.pdf)
allows ceramic output capacitors. C2 changes from 1 µF/0603 to 4.7 µF/0805
for capacitance margin; select a voltage rating and dielectric that retain at
least 1 µF under operating conditions. Place it near the regulator.

The [LM2937 datasheet](https://www.ti.com/lit/ds/symlink/lm2937.pdf)
specifies at least 11 V input for the 10 V version and up to 400 mA for SOT-223,
subject to thermal limits. Its output needs at least 10 µF effective capacitance
and 0.01–3 Ω ESR. C4 now uses a polarized 22 µF, 25 V aluminum electrolytic,
Panasonic EEEFK1E220R, with `Capacitor_SMD:CP_Elec_5x5.8`. Pad 1 is positive
and connects to 10 V; pad 2 connects to ground. Its ±20% nominal tolerance
leaves 17.6 µF at the lower limit. See the
[Panasonic FK catalog](https://api.pim.na.industrial.panasonic.com/file_stream/main/fileversion/3128)
for its dimensions and impedance specifications. Verify ESR over the intended
temperature range before fabrication; the room-temperature impedance rating
alone does not establish stability across that range. No tantalum is used.

C2 and C4 are explicit exceptions to the earlier passive-value/package policy.
Original source evidence is unchanged; regression expectations record these
value changes and the regulator pin permutations.

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

**Applied:** Bourns **SRN8040TA-2R2Y**, 2.2 µH ±30%, using the installed
`Inductor_SMD:L_Bourns_SRN8040TA` footprint. It replaces the unspecified
DEM804XC-family part. The value remains `2.2uH`; manufacturer part number is
recorded in custom metadata. Eagle `P$1`/`P$2` become KiCad pads `1`/`2`, with
connectivity preserved and the permutation checked against original evidence.

The [Bourns datasheet](https://www.bourns.com/docs/product-datasheets/srn8040ta.pdf)
lists typical Irms 7.4 A (40 °C rise), typical Isat 9.8 A (30% inductance drop),
and DCR 11.5 mΩ ±20%. The nominal body is 8 × 8 mm. KiCad's lands are
2.85 × 6.6 mm with 8.5 mm outer span, closely following the recommended
2.8 × 6.6 mm lands and 8.5 mm span. This is a new part/layout substitution,
not a match to the old copper. It is semi-shielded; the Toko family is shielded.

The [TPS563200 datasheet](https://www.ti.com/lit/ds/symlink/tps563200.pdf)
allows 2.2 µH for the approximately 2.5 V output selected by 22.6 kΩ/10 kΩ.
At 5 V input, 650 kHz, and 3 A load, the ideal buck equations give about
0.87 A peak-to-peak ripple and 3.44 A peak current. Using the -30% inductance
limit gives about 1.25 A ripple, 3.62 A peak, and 3.02 A RMS. These estimates
support the selection; they do not replace temperature, transient, or EMI
validation of the assembled board.

## Power jack substitution

**Applied:** Same Sky (formerly CUI) **PJ-036AH-SMT-TR**, using
`Connector_BarrelJack:BarrelJack_CUI_PJ-036AH-SMT_Horizontal`. This is an
intentional change from PJ-051 to an unshielded SMT connector, not a mechanical
replacement for the old board. It has a locating peg requiring an unplated hole.

The [manufacturer drawing](https://www.sameskydevices.com/product/resource/pj-036ah-smt-tr.pdf)
specifies a 5.5 mm mating barrel, 2.0 mm center pin, and 8.9 mm insertion depth.
Pin 1 is the center contact (5 V), pin 2 the outer contact (ground), and pin 3
the normally closed outer-contact switch (unused). Original `P$1`/`P$2`/`P$3`
map to `1`/`2`/`3`; the two unused shield pads are removed. Tests explicitly
check these changes against the untouched Eagle snapshot.

The body is 14.5 × 9 × 11 mm. KiCad provides 3.3 × 4.4 mm and 3.9 × 6.2 mm
contact lands and a 2 mm locating hole. Its footprint references an older CUI
drawing; the 2025 drawing has small location differences (including the rear
contact offset, 0.55 mm versus KiCad's 0.7 mm). Review the current supplier CAD
against the selected production lot before fabrication. The named standard
footprint is adopted for this example, with that mechanical check outstanding.

## Remaining migration decisions

The straightforward IC, passive, PMOS, and connector substitutions are complete.
NoScheme identities and source connectivity tests have been retained. The LED-driver exposed-pad correction is recorded separately from the
original Eagle evidence. Remaining choices are listed above.
