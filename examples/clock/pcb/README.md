# Honest Clock PCB

KiCad 10 project for the clock board. It starts empty except for the
outline, mounting holes, planes, and rules; parts arrive from the NoScheme
netlist.

## Board

- **Outline:** 226 × 64 mm, from the layout mockup. Displays face the front;
  everything else mounts on the back.
- **Stand band:** the bottom 9.525 mm (3/8 in) is a rule area named
  `stand band`. `honest_clock.kicad_dru` rejects any footprint there except
  the mounting holes, on either side.
- **Mounting holes:** five M3 holes (H1–H5): two along the top, three in the
  stand band to screw the board to its base. They are board-only footprints,
  so netlist updates keep them.
- **Origins:** the grid origin is the top-left corner; the drill/place origin
  is the bottom-left corner, for JLCPCB position files.

## Stackup

JLCPCB JLC04161H-7628: 4 layers, 1.6 mm, 1 oz outer and 0.5 oz inner copper.

| Layer | Use | Thickness |
|---|---|---|
| F.Cu | Digits (through-hole), front routing | 35 µm |
| Prepreg 7628 | εr 4.4 | 0.2104 mm |
| In1.Cu | Power: 3V3 left of x = 88 mm, 5V right | 15.2 µm |
| Core | εr 4.6 | 1.065 mm |
| In2.Cu | GND plane | 15.2 µm |
| Prepreg 7628 | εr 4.4 | 0.2104 mm |
| B.Cu | Components and most routing | 35 µm |

- **GND on In2:** the ground plane is the layer next to B.Cu, where the parts
  and fast signals are (USB, RMII, the GPS RF trace). That gives them a return
  path 0.21 mm away.
- **In1 split:** 5V feeds the LED supply, about 0.75 A spread across the
  digits on the right, so it gets the plane there. 3V3 serves the MCU, PHY,
  and GPS on the left, where most of the 3V3 pins and decoupling vias are.
  The drivers' 3V3 logic supply is a few milliamps and can run on traces.
- **Split position:** move the split once placement settles. Keep fast
  signals from crossing it on B.Cu: In2 is their reference, so that is mostly
  a concern for routing on F.Cu.

## Rules (6/6/6)

| Rule | Value |
|---|---|
| Track width, minimum | 0.1524 mm (6 mil) |
| Clearance, minimum | 0.1524 mm (6 mil) |
| Via annular ring, minimum | 0.15 mm (~6 mil) |
| Via | 0.6 mm pad on a 0.3 mm drill (also 0.8 / 0.4 preset) |
| Through-hole drill, minimum | 0.3 mm |
| Hole to hole | 0.5 mm |
| Hole to copper | 0.254 mm |
| Copper to board edge | 0.3 mm |
| Silkscreen text | 1.0 mm high, 0.15 mm stroke |

Net classes:

| Class | Track | Clearance | Via | Members |
|---|---|---|---|---|
| Default | 0.1524 mm (6 mil) | 0.1524 mm | 0.6 / 0.3 | everything else |
| Power | 0.2032 mm (8 mil) | 0.1524 mm | 0.6 / 0.3 | `vbus`, `v5`, `v3v3`, `v3v3_quiet`, `gnd` |

8 mil is a minimum for rails, not a target. Use the planes, or widen tracks
where current flows: VBUS at 12 V, the buck switch nodes and outputs, and the
LED 5 V supply.

## Project footprints

`HonestClock.pretty` holds footprints drawn for this board, registered in the
project's `fp-lib-table` as `HonestClock`:

- **`OLED_SSD1309_2.42in_Module`:** the whole 2.42" OLED module, not just its
  header: the 72 × 43 mm outline on silkscreen and courtyard, the glass and
  active area on F.Fab, the 7-pin header (origin at pin 1, GND), and four
  2.2 mm holes for M2 screws on the module's 68 × 39 mm pattern. Dimensions
  follow QDtech's MC242GX drawing; measure a real module before ordering.
- **`RJ45_HanRun_HY951180A_Vertical`:** the vertical MagJack, from HanRun's
  recommended PCB layout: eight signal pins, four LED pins, two shield tabs,
  and two 3.2 mm locating pegs. Origin at pin 1.

- **`USB_C_Receptacle_Hanxia_HX-TYPE-C-16P-180-LT-H10.0_Vertical`:** the
  vertical USB-C receptacle, from Hanxia's recommended layout: two rows of
  eight SMD pads at 1.00 mm, four SMD shell tabs, and two 0.9 mm peg holes.
  The shell tabs are 0.12 mm shorter than drawn to keep 0.25 mm from the peg
  holes.

`scripts/make_footprints.py` regenerates them from named dimensions.

After changing a footprint here, refresh its record in
`kicad_lib/footprints.json` (see [kicad_lib/README.md](../kicad_lib/README.md)).

## Scripts

`snapshots/` (local, not committed) holds a copy of the board at each
placement step, numbered in order.

Run these from KiCad's scripting console with the board open, or standalone
on a closed board (`python3 scripts/<name>.py honest_clock.kicad_pcb`):

- **`place_clusters.py`:** places the subsystems (USB/PD, bucks, GPS,
  Ethernet, timebase, MCU passives, probes) as clusters: each anchor part at
  a target spot, then its decoupling caps on its power pins and its other
  passives beside the pins or parts they connect to. Parts in `KEEP` stay
  where they were placed by hand. Edit `CLUSTERS` and rerun from a snapshot.
  With `--only REF ...` it re-places just those satellites around their
  anchors' current positions and leaves every other part where it is, which
  suits tidying up after moving parts by hand. Add `--here` to nudge named
  anchors to the nearest free spot around their current positions instead.
- **`place_display_passives.py`:** puts each digit's R-EXT below its bottom
  pin row and its driver's decoupling cap above the driver, both on the back.

## Bring parts in

From the repository root:

```sh
uv run --locked noscheme export-kicad --project examples/clock \
  --footprints examples/clock/kicad_lib/footprints.json \
  --schematic examples/clock/pcb/honest_clock.kicad_sch
```

This writes the KiCad schematic here: `honest_clock.kicad_sch` and one sheet
file per distinct block (the nine digits share `display_Digit.kicad_sch`),
plus `noscheme.kicad_sym`, registered in `sym-lib-table`. They are generated
and committed with the board; edits made in KiCad are overwritten.

1. In KiCad, open `honest_clock.kicad_pro`, then the PCB editor.
2. **Tools → Update PCB from Schematic** (F8). Review the changes, then
   **Update PCB**. The schematic editor need not be open.
3. The plane zones are already tied to `gnd`, `v3v3`, and `v5`; refill zones
   (**B**) once parts are placed.

The board was first populated from `build/board.net`, whose footprint links
differ from the schematic's. The first F8 after that needs **Re-link footprints
to schematic symbols based on their reference designators** ticked.

Net-class membership comes from `net_class: "Power"` in `main.nosch`. The
schematic carries it to the board on every update; the `Power` class's widths
and clearances stay in `honest_clock.kicad_pro`.
