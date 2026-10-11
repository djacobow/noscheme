# Rulos six-line matrix, Rev B

A NoScheme transcription of **Jeremy Elson's 6-Line Matrix Rev B**, from the
[rulos project](https://github.com/jelson/rulos). The original circuit, schematic,
and package designs belong to their original authors. This example adapts the
circuit into reusable NoScheme blocks and arrays; it does not reproduce the
original board placement or routing.

## Attribution and sources

- Author/project: [Jeremy Elson (jelson)](https://github.com/jelson),
  [rulos — Ravenna Ultra-Low-Altitude Operating System](https://github.com/jelson/rulos).
- [Original schematic PDF](https://github.com/jelson/rulos/blob/af52252dce7e3a1ebc82e7e0bd4a026881bf8aeb/Schematics/6-Line%20Matrix/Rev%20B/6matrix.pdf).
- [Eagle schematic](https://github.com/jelson/rulos/blob/af52252dce7e3a1ebc82e7e0bd4a026881bf8aeb/Schematics/6-Line%20Matrix/Rev%20B/6matrix.sch)
  and [Eagle board](https://github.com/jelson/rulos/blob/af52252dce7e3a1ebc82e7e0bd4a026881bf8aeb/Schematics/6-Line%20Matrix/Rev%20B/6matrix.brd).
- Source revision: `af52252dce7e3a1ebc82e7e0bd4a026881bf8aeb`.
  The PDF was generated June 10, 2019; the transcription was added October 2026.
- The upstream repository includes the GNU GPL version 3. Its license text is
  preserved in [upstream/LICENSE](upstream/LICENSE); this adapted example is
  provided under GPLv3. Embedded Eagle packages also retain their original
  library/package provenance in the component metadata and source files above.

The PDF supplies the circuit structure and design notes. The Eagle schematic
supplies physical pin mappings and aliases. The Eagle board supplies the original flattened pad connectivity, package
assignments, and component value strings. Reviewed package substitutions are
recorded separately in `footprint_overrides.json`.
`upstream/connectivity.json` records that board data and SHA-256 hashes of all
three source files. It is independent of the NoScheme compiler output.

Passive values use unquoted quantity literals (`100n`, `1u`, `22.6k`). Typed
`capacitance`, `inductance`, and `resistance` parameters provide units in exported
values. Part numbers remain strings; the selected current-setting resistors
use `1k`.

## What the arrays demonstrate

The circuit has **93 components and 106 nets**, including 48 seven-segment
indicators arranged as six rows by eight columns.

- `Column` contains `digits[6]`. Segment nets broadcast to every digit; indexed
  bindings connect each digit's two common pins to its own row.
- `TwoColumns` contains two columns, one 16-channel driver, its bypass capacitor,
  and its current-setting resistor. It corresponds to the Eagle `2COL-7SEG` module.
- `banks[4]` repeats that module. The scalar `Rows` bundle broadcasts six shared
  row nets across all four banks. This avoids partial array broadcasting, which
  the language intentionally does not support.
- `serial_data[0:4]` and `serial_data[1:5]` connect the driver inputs and outputs
  into a daisy chain. The MCU feeds element 0; element 4 retains the original
  final driver's otherwise unused serial-output net.
- `row_switch[6]`, `bus_ports[3]`, `i2c_pullups[2]`, and `buck_input_caps[2]`
  demonstrate smaller component arrays.

| File | Purpose |
|---|---|
| [main.nosch](main.nosch) | Board nets, controller, power, bus interfaces, and four display banks |
| [display.nosch](display.nosch) | Reusable column and two-column blocks |
| [interfaces.nosch](interfaces.nosch) | Shared row and segment bundles |
| [components.nosch](components.nosch) | KiCad-derived components, board roles, and retained custom components |
| [kicad_lib/](kicad_lib/README.md) | Converted KiCad symbol and footprint snapshots |
| [upstream/instance_map.json](upstream/instance_map.json) | Every NoScheme part identity mapped to its Eagle reference |

NoScheme assigns new reference designators. Use the instance map to compare with
Eagle, rather than assuming that a generated `U1` is the original `U1`.
Keep `noscheme.default.state.json` when updating a board to retain NoScheme identities.

## KiCad library alternatives

[KICAD_MATCHES.md](KICAD_MATCHES.md) records standard-library symbol and footprint
candidates, checked pin mappings, and outstanding package decisions. **All 93 parts
now use standard KiCad footprints**. Original connections are preserved with documented regulator, inductor, and jack pin remapping;
regulator values and output capacitors change as described in KICAD_MATCHES.md.
The four LED drivers additionally connect exposed pad 25 to ground. The MCU, bus buffer, buck regulator, passives, MOSFETs, and connectors
inherit from converted KiCad symbols where their physical interfaces match.

The agreed package policy is:

- All resistors: standard KiCad 0603.
- Capacitors up to and including 1 µF: standard KiCad 0603.
- Capacitors above 1 µF through 10 µF: standard KiCad 0805.
- C8 and C9: Panasonic FK EEEFK1E470P, 47 µF/25 V SMT aluminum electrolytics
  in `Capacitor_SMD:CP_Elec_6.3x5.8`, positive terminals on `led_power`.
- Regulator substitution exceptions: C2 is 4.7 µF/0805; C4 is a polarized
  22 µF/25 V aluminum electrolytic in `Capacitor_SMD:CP_Elec_5x5.8`.
- SWD: the same 10-pin, 1.27 mm SMT Cortex header footprint as the STM32 example.

The four original through-hole capacitor footprints are consequently replaced
by SMT footprints. The LED drivers use the standard HTSSOP-24 exposed-pad footprint for the
recorded STP16CPC26XTR part, with pad 25 tied to ground. The 48 displays use `Display_7Segment:7SegmentLED_LTS6760_LTS6780` with
the original common-anode pin mapping. Its hole pattern and body outline match
the Inolux target; confirm the retained 0.8 mm drills against actual leads before
fabrication. The regulators use SOT-223. The inductor is a Bourns SRN8040TA-2R2Y using its standard KiCad footprint.
The power jack is PJ-036AH-SMT-TR in its standard KiCad SMT footprint.
All active footprints are now from standard KiCad libraries. The original
Eagle data stays intact for comparison.

## Compile and export

From the repository root:

```sh
uv run --locked noscheme check --project examples/rulos_6matrix
uv run --locked noscheme export-kicad \
  --project examples/rulos_6matrix \
  --footprints examples/rulos_6matrix/kicad_lib/footprints.json
```

The netlist is `examples/rulos_6matrix/build/board.net`. All 93 parts have
footprints and are included. No installed Eagle or KiCad libraries are needed
to compile or generate the netlist.

C8 and C9 now provide 94 µF nominal on the buck output. This exceeds TI’s
20–68 µF design table and uses aluminum electrolytics instead of the original
ceramic. Buck stability, ripple, and startup remain bench-validation items.

For PCB Editor, install the standard KiCad footprint libraries and create/save
a KiCad project. Import `build/board.net`, inspect **Load and Test Netlist**,
then use **Update PCB**. No custom footprint library or project library table
is needed. The original placement, routing, board outline, and standalone
mounting holes are not included.

## Changes from the original design

Some parts were deliberately changed to use standard KiCad footprints:

- LED drivers retain STP16CPC26XTR and now have grounded exposed pads.
- Regulators are TLV1117LV33DCYR and LM2937IMP-10/NOPB in SOT-223.
- The inductor is Bourns SRN8040TA-2R2Y; the jack is PJ-036AH-SMT-TR.
- C2 is 4.7 µF/0805; C4 is a Panasonic FK 22 µF/25 V aluminum electrolytic.
- C8 and C9 are Panasonic FK 47 µF/25 V aluminum electrolytics.
- The four previously unspecified LED current-setting resistors are now 1 kΩ.
- The isolated SWO header pad is now explicitly unconnected.
- Displays, passives, SWD, and other connectors use the reviewed footprints
  listed in [KICAD_MATCHES.md](KICAD_MATCHES.md).

The original Eagle connectivity evidence remains in `upstream/`. The obsolete
imported footprint library has been removed. `footprint_overrides.json` records
all selected packages; regression tests account for deliberate pin, value, and
connection changes. Physical suitability and the electrical checks noted above
still need validation before fabrication.

## Preserved ambiguities and checking limits

- Ground and supply rails now use `ground`/`power` net types. Control signals,
  UART, and the serial chain use `digital`; SWDIO uses `bidirectional`; reset
  and both sides of the I²C buffer use `idle_high`. `TwoColumns` supply, ground,
  and digital ports have matching types and explicit interface roles.
- Connector roles model the UART adapter, debug probe, and open-drain bus
  peers. The debug probe senses VCC and drives SWCLK/reset. SWO is explicitly
  unconnected because PB3 drives the LED clock.
- Six power configurations cover 5 V from the jack or header and 12 V from
  any one of RJ45 ports 0–2. The board declares the two choices and ERC checks
  every combination, named like `supply_12v=port0,supply_5v=jack`. Other supply
  connectors are modeled
  as loads/pass-through connections. A source RJ45 uses pin 4 as the single
  modeled source and pin 7 as its parallel passive contact, avoiding counting
  the same external supply twice. These configurations assume both rails are
  present, with exactly one independent source per rail.
- All six configurations run by default. Select one with, for example,
  `--erc-configuration supply_12v=port0,supply_5v=jack`. All six pass with zero errors and warnings
  under the documented demo allocations below. No waivers suppress diagnostics.
  This validates the declared model, not all electrical behavior of the board.
- `BuckOutputInductor` specializes the passive inductor with a power-output
  role on pad 2, explicitly asserting the assembled converter's output.
  Its pads are bound by name and are not interchangeable in this model.
  It declares the TPS563200's 3 A device ceiling. A project-local ERC checker
  compares that with the shared display load, as described below.
  This does not simulate regulation or propagate input power.
  The generic `Inductor` remains passive and interchangeable.
- The bootstrap, switching, feedback, current-setting, and multiplexed LED
  row/segment nodes remain passive pending suitable circuit models. Switched-path current
  models and the remaining voltage limits are future work;
  this example does not yet establish electrical correctness.
- The four driver current-setting resistors have empty values in Eagle; they
  are now 1 kΩ. C9 was also blank in Eagle; it is now explicitly 47 µF.
- The PDF notes **INND-TS56RAB** displays, while the device/package is named
  `7-SEG_SA56-11` / `SA56-11`. It notes **DMP2035U** row MOSFETs, while the stored
  value is `MOSFET-PCHANNEL`. Both discrepancies are preserved and documented.
- Original regulators `7803` and `7810` are replaced with TLV1117LV33DCYR
  and LM2937IMP-10/NOPB. See KICAD_MATCHES.md for pin remapping and capacitor
  requirements. C4 is now a 22 µF/25 V SMT aluminum electrolytic; C2 is 4.7 µF/0805.
- The final driver serial output remains a single-pad net whose interface
  allows an unused endpoint. The original isolated `SWO` header net is removed;
  header pin 6 is `no_connect`. MCU PB3/pin 26 remains the LED-driver clock.
- Unconnected pads remain `no_connect`, including unused MCU pins and the power-jack
  switch contact. Copper does not propagate through components in ERC.

## Pull-up and voltage checks

The local SDA/SCL pull-ups now carry their resistance and a reference-pin
check requiring the other terminal on a power net. Their net type checks
parallel external passive pull-up resistance against a 1.8 kΩ minimum.
The [PCA9600 datasheet](https://www.nxp.com/docs/en/data-sheet/PCA9600.pdf)
specifies up to 1 mA sourced internally at SX/SY. Reserving that from a 3 mA
sink budget leaves 2 mA for external resistors: using 3.6 V gives 1.8 kΩ.
The internal current source is accounted for in this budget, not treated as
a resistor. The installed 2 kΩ values pass. Firmware must disable MCU internal
pull-ups. This uses nominal resistance and does not establish rise time,
capacitance, or resistor-tolerance compliance. The high-side bus needs its
external pull-up values and voltage before an equivalent resistance check can be
added. The connector annotations explicitly assume that attached equipment
provides pull-ups on SDA and SCL; their resistance remains unknown. Both active
low drive and passive pull-up are allowed on the same modeled endpoint.

The fitted part is a **PCA9600**, not a PCA9615 differential buffer. Its TX/TY
outputs need external pull-ups. The SX/SY low-voltage current contribution is
not modeled as a passive pull-up establishing the high level: the local 2 kΩ
resistors provide that function. The PCA9600 datasheet's input-current table
specifies that contribution for SX/SY at or below 1 V, and only leakage above
1.4 V. By comparison, the [PCA9615 datasheet](https://www.nxp.com/docs/en/data-sheet/PCA9615.pdf)
requires external single-ended SDA/SCL pull-ups and a differential termination
and bias network.

NRST is annotated with both `drive.low` and `pull.up`; the
[STM32F303x6/x8 datasheet](https://www.st.com/resource/en/datasheet/stm32f303r6.pdf),
section 6.3.15, specifies its permanent internal pull-up. No nominal resistance
is invented for an unchecked reset resistance budget.

Rail declarations assume steady-state ±5% envelopes around 5 V, 12 V, 10 V,
3.3 V, and the approximately 2.5 V LED supply. Ground is 0 V; local SDA/SCL
span 0–3.465 V. These are design assumptions, not calculated regulator
tolerances or transient simulations.

Absolute voltage limits are checked on:

- TLV1117LV input/output: −0.3–6 V
  ([datasheet](https://www.ti.com/lit/ds/symlink/tlv1117lv.pdf)).
- LM2937 input: at most 26 V
  ([datasheet](https://www.ti.com/lit/ds/symlink/lm2937.pdf)).
- PCA9600 supply: −0.3–18 V (datasheet above).
- STP16CPC26 supply: 0–7 V
  ([datasheet](https://www.st.com/resource/en/datasheet/stp16cpc26.pdf)).
- TPS563200 VIN/EN: −0.3–19 V
  ([datasheet](https://www.ti.com/lit/ds/symlink/tps563200.pdf)).

These checks do not establish recommended operating conditions, regulator
dropout, or logic-level compatibility. MCU limits, supply-relative signal
limits, switched nodes, and capacitor differential-voltage ratings remain
unmodeled. Reference-terminal checking verifies the rail type, not that a
pull-up reaches the correct voltage domain. Regression tests inject a grounded
pull-up, excessive pull strength, and an overvoltage input in all six configurations.

## Current and capacitance budgets

The LDO output budgets use the device ratings (1 A TLV1117LV, 500 mA
LM2937) less a 1 mA steady leakage reserve per rail: **999 mA** and
**499 mA** available to modeled loads. These are ceilings before thermal
derating, not guaranteed usable currents on this PCB. Startup capacitor
charging is excluded.

The demo uses these explicit allocations, approved as assumptions to validate:

| Load | Allocation | Representation |
| --- | ---: | --- |
| MCU, including analog and GPIO demand | 100 mA total | One current-load group for pins 1, 5, 17 |
| Four LED drivers' logic supplies | 20 mA each | VCC pin; LED sink current is budgeted separately |
| Debug probe's voltage sense | 1 mA | Header pin 1; probe must be independently powered |
| PCA9600 at 10 V | 20 mA | Supply pin 8, including bus activity |

These are not guaranteed datasheet maxima for every operating mode.
The [MCU datasheet, section 6.3.5](https://www.st.com/resource/en/datasheet/stm32f303k8.pdf)
specifies consumption under particular firmware, peripheral, and I/O conditions.
The STP16CPC26 table gives 11–15 mA maxima at particular R_EXT values; 20 mA
is our logic-supply allocation. The PCA9600 specifies supply current at 5 V
and 15 V and additional active-output current at 5.5 V; our 20 mA at 10 V
is a design allowance, not an interpolation guaranteed by NXP.

Pull-up supply pins are power inputs with
`max_load_current: 3.465 / (value * 0.99)`, using the upper rail voltage,
1% resistor tolerance, and a low signal. Each 2 kΩ pull-up contributes
1.75 mA. Both count simultaneously, giving **184.5 mA** total modeled 3.3 V
demand and **20 mA** on 10 V. Both current budgets pass in all six
configurations. The 1 mA reserves cover otherwise unmodeled steady leakage;
validate them along with the allocations. Input-side regulator demand and
external supply/pass-through capacities remain outside these output budgets.

### I²C capacitance allocations

Local SDA/SCL use a 400 pF sum limit; buffered SDA/SCL use 4 nF.
The PCA9600 datasheet gives 10 pF maximum per signal pin. Other contributions
are explicitly approved demo allowances to validate, not component guarantees:

| Contribution, per signal | Local side | Buffered side |
| --- | ---: | ---: |
| PCA9600 pins | 10 pF | 20 pF (joined RX/TX or RY/TY) |
| MCU pin | 10 pF | — |
| Pull-up parasitic | 2 pF | Included in downstream allowance |
| On-board wiring | 50 pF | 100 pF |
| Each RJ45 branch, including connector, cable, and downstream devices | — | 1 nF × 3 |
| **Total** | **72 pF** | **3.12 nF** |
| **Limit** | **400 pF** | **4 nF** |

The MCU's published 5 pF value is typical; our 10 pF allocation is an
engineering assumption. Wiring is specified through `wiring_capacitance`.
Each RJ45 signal receives its branch allowance at the connector instance,
including everything downstream of that port; do not count those loads again
as board wiring. All three branches are counted simultaneously, independently
of which port supplies power. No cable length or particular remote device is
implied: the installed branch must meet its 1 nF allowance.

All four sums pass in all six configurations, removing the final 24 warnings.
Tests inject LDO and I²C overloads, double all per-port allowances, and remove
MCU, wiring, or branch capacitance data to verify incomplete budgets still warn.
Passing these lumped capacitance limits does not establish bus timing:
external pull-up values, bus speed, cable topology, and propagation delay still
need review for a physical installation.

### LED current selection

The [Inolux INND-TS56 datasheet](https://www.inolux-corp.com/datasheet/Display/Through-Hole-Display/SingleDigit/INND-TS56%20Series_V1.0.pdf)
rates the red display at 25 mA continuous per segment at 25 °C. The chosen
20 mA target is 80% of that rating. The STP16CPC26 datasheet, table 7, specifies
1 kΩ for approximately 20 mA; all four R_EXT resistors now use `value=1k`.
Use 1% resistors. This selects for the Inolux part named in the source drawing;
the legacy Eagle display value remains for traceability.

With one row active, eight columns × eight segments × 20 mA gives 1.28 A
nominal LED-rail demand. Individual LEDs average about 3.33 mA at 1/6 duty;
the supply still sees approximately 1.28 A with every row fully lit.
The LED rail applies `matrix.shared_sink_budget` from [checks.py](checks.py),
loaded through `erc_modules` in `noscheme.toml`. It compares an explicit
aggregate load against the single physical source's `max_source_current`.
The load is `64 * 20m * 1.10 + 1m` = **1.409 A**, below the **3 A**
TPS563200 device ceiling: 1.591 A of nominal budget headroom.

The 10% allowance is a project assumption for current-setting error, including
the specified 1% resistor selection; it is not a guaranteed tolerance over
all operating conditions. The extra 1 mA allocates roughly 0.081 mA for the
feedback divider at the upper rail bound and the remainder for steady leakage.
Capacitor charging at startup is excluded. Thermal/layout validation can require
lowering the declared source capacity.

The four drivers share 64 sink channels across the six rows. Their currents
are counted once, without multiplying by six or reducing the supply demand by
the LED duty cycle. This assumes regulated sink behavior; the checker cannot
verify firmware sequencing, channel count, resistor selection, or fault currents.
Keep the declared 20 mA budget synchronized with R_EXT changes. Row FETs remain
passive in generic ERC; their conducted load is represented by this aggregate.
The checker rejects additional power-input endpoints so that new loads cannot
silently bypass the aggregate accounting. Additional passive loads still require
updating the declared total.

Missing/ambiguous source capacity warns, and a load above capacity errors in
every power configuration. Regression tests exercise both overload paths
(increased load and reduced capacity) and missing capacity. This output budget
does not establish the buck's 5 V input demand or the external supply capacity.

The LED rating derates by 0.33 mA/°C above 25 °C; even nominal 20 mA reaches
that continuous-current limit near 40 °C, before driver/resistor tolerances.
The separate 90 mA pulse rating requires 1/10 duty and 0.1 ms pulses and is
not used. The roughly 2.5 V LED rail also cannot guarantee regulation for
the specified 2.8 V maximum LED forward voltage plus sink/FET drops.
Brightness, temperature, and compliance voltage still require review.

## Verify or refresh the source evidence

The regression test compares **every pad's connectivity**, including unconnected
pads, all component values, and package assignments against the Eagle snapshot
plus the documented substitutions. It repeats the connectivity comparison on
the exported KiCad netlist and checks that the standard footprint snapshot
covers every selected part and pad:

```sh
uv run --locked pytest -q tests/test_rulos_6matrix.py
```

To regenerate source evidence, download `6matrix.sch`,
`6matrix.brd`, and `6matrix.pdf` from the pinned links above into a staging
directory, run:

```sh
uv run --locked python examples/rulos_6matrix/upstream/refresh.py /path/to/staging \
  --revision af52252dce7e3a1ebc82e7e0bd4a026881bf8aeb
```

This rewrites only `upstream/connectivity.json`, including source hashes. It
requires no KiCad installation and does not recreate the old footprints.
Refresh the KiCad snapshots separately using [kicad_lib/README.md](kicad_lib/README.md).
Neither script modifies the design or instance map. Review changes and rerun
the regression test after refreshing.
