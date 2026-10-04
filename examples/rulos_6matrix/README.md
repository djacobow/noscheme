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

## What the arrays demonstrate

The circuit has **93 components and 107 nets**, including 48 seven-segment
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
| [footprints/](footprints/) | Imported Eagle footprints and the combined export pad snapshot |

NoScheme assigns new reference designators. Use the instance map to compare with
Eagle, rather than assuming that a generated `U1` is the original `U1`.
Keep `noscheme.state.json` when updating a board to retain NoScheme identities.

## KiCad library alternatives

[KICAD_MATCHES.md](KICAD_MATCHES.md) records standard-library symbol and footprint
candidates, checked pin mappings, and outstanding package decisions. **35 parts
now use standard KiCad footprints**, with unchanged connectivity and value
strings. The MCU, bus buffer, buck regulator, passives, MOSFETs, and connectors
inherit from converted KiCad symbols where their physical interfaces match.

The agreed package policy is:

- All resistors: standard KiCad 0603.
- Capacitors up to and including 1 µF: standard KiCad 0603.
- Capacitors above 1 µF through 10 µF: standard KiCad 0805.
- Capacitors above 10 µF: preserve the source footprint (C8 is 47 µF).
- C9 has no value: preserve its source footprint.
- SWD: the same 10-pin, 1.27 mm SMT Cortex header footprint as the STM32 example.

The four original through-hole capacitor footprints are consequently replaced
by SMT footprints. The LED-driver variant discrepancy, display, regulators,
inductor, and power jack retain their imported package geometry. The original
Eagle data stays intact for comparison.

## Compile and export

From the repository root:

```sh
uv run --locked noscheme check --project examples/rulos_6matrix
uv run --locked noscheme export-kicad \
  --project examples/rulos_6matrix \
  --footprints examples/rulos_6matrix/footprints/footprints.json
```

The netlist is `examples/rulos_6matrix/build/board.net`. All 93 parts have
footprints and are included. No installed Eagle or KiCad libraries are needed
to compile or generate the netlist.

For PCB Editor, install the standard KiCad footprint libraries and create/save
a KiCad project in this example's directory. The
included `fp-lib-table` registers the local `Rulos_RevB` library using
`${KIPRJMOD}`. If using another project directory, add
`footprints/Rulos_RevB.pretty` through **Manage Footprint Libraries**, with the
nickname `Rulos_RevB`. Import `build/board.net`, inspect **Load and Test Netlist**,
then use **Update PCB**. Footprints are ready for placement; the original Eagle
layout, board outline, routing, and four standalone mounting holes are not part
of this schematic transcription.

The retained Eagle footprints were imported using KiCad 10.0.6's Eagle reader.
Back-side placements were flipped to the front, and rotation and location were reset before extracting
library geometry. Board nets, identities, and reference text were removed.
Standard-library substitutions are listed in `footprint_overrides.json`; the
SWD and passive package changes above are intentional departures from Eagle.
PCB Editor import and physical footprint suitability have not been manually
validated for this example.

## Preserved ambiguities and checking limits

- All nets use `nettype.passive`. This first transcription verifies connectivity;
  it does not impose driver requirements, voltage domains, current budgets, or
  switching behavior. Pin roles come from KiCad definitions and explicit
  board-specific annotations. Passing ERC here is not evidence of complete
  electrical validation.
- The four driver current-setting resistors and `C9` have empty values in Eagle.
  Their value strings remain empty. Choose those values before producing a BOM.
- The PDF notes **INND-TS56RAB** displays, while the device/package is named
  `7-SEG_SA56-11` / `SA56-11`. It notes **DMP2035U** row MOSFETs, while the stored
  value is `MOSFET-PCHANNEL`. Both discrepancies are preserved and documented.
- The logic regulator's stored value is `7803`, and the other regulator is
  `7810`; this transcription does not substitute another regulator or infer
  ratings from those labels.
- The final driver serial output and the debug connector's `SWO` are single-pad
  nets in the original board. Their interfaces explicitly allow unused endpoints.
- Unconnected pads remain `no_connect`, including unused MCU pins and power-jack
  switch/mechanical pads. Copper does not propagate through components in ERC.

## Verify or refresh the source evidence

The regression test compares **every pad's connectivity**, including unconnected
pads, all component values, and package assignments against the Eagle snapshot
plus the explicit footprint overrides. It repeats the connectivity comparison on the exported KiCad netlist and checks
local footprint pad sets and hashes:

```sh
uv run --locked pytest -q tests/test_rulos_6matrix.py
```

To regenerate source evidence and footprints, download `6matrix.sch`,
`6matrix.brd`, and `6matrix.pdf` from the pinned links above into a staging
directory. With KiCad 10's `pcbnew` module installed for `/usr/bin/python3`, run:

```sh
uv run --locked python examples/rulos_6matrix/upstream/refresh.py /path/to/staging \
  --revision af52252dce7e3a1ebc82e7e0bd4a026881bf8aeb
```

This rewrites `connectivity.json`, the imported footprint files, and the combined
`footprints.json`, retaining the selected KiCad footprint snapshot. Refresh the
KiCad snapshots separately using [kicad_lib/README.md](kicad_lib/README.md). It does not generate or modify the `.nosch` design or its
instance map. Review source/hash changes and rerun the regression test after
refreshing; a new source revision may require deliberate design changes.
