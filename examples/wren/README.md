# WREN examples and full-board benchmark

Two NoScheme transcriptions of CERN's VME White Rabbit Event Node (WREN):

- A handwritten **189-component, 32-channel push-pull output subsystem**, showing
  readable reusable blocks and arrays.
- A generated **1,507-component full-board benchmark** in `full/`, preserving
  all physical connectivity from the installed KiCad 10.0.6 demo.

## Attribution and provenance

The original design is by **Paul Peronnard and Tomasz Wlostowski, CERN BE-CEM**,
as credited in the bundled schematic PDF. See the
[WREN project](https://gitlab.com/ohwr/project/wren) and the
[KiCad 10.0.6 demo source](https://gitlab.com/kicad/code/kicad/-/tree/10.0.6/demos/vme-wren).
The NoScheme files adapt the source connectivity and retain its component value
strings, pin numbering, and footprint identifiers. They do not reproduce PCB
placement or routing.

`upstream/connectivity.json` is extracted from KiCad's XML netlist, independently
of NoScheme compilation. It includes SHA-256 hashes of the relevant schematic
files, original component fields (including library author/license notices),
and connections to components outside this subsystem. `instance_map.json` maps
NoScheme instance paths to original reference designators.

The installed demo has no standalone design LICENSE file; do not assume that
NoScheme's repository license replaces the upstream design or library terms.
Original library notices are retained in the reference data. No upstream
schematic, PDF, board geometry, or footprint library is copied here. Confirm
the upstream design's redistribution terms before expanding the bundled source
material or publishing hardware derived from it.

## Handwritten subsystem

- `main.nosch`: four `banks[4]` and the shared output-enable pull-up.
- `drivers.nosch`: each bank contains a level translator and `pairs[4]`; each
  pair contains a quad buffer, four series resistors, two pull-downs, two
  protection diodes, and two bypass capacitors.
- `components.nosch`: interfaces extracted from the source netlist. KiCad pin
  roles and tri-state capability are retained; GND pins are identified explicitly.

Input/output arrays are indexed `[bank][pair][channel]`, with dimensions
`[4][4][2]`. The translator's first two input pins and the bulk capacitor's
orientation follow the upstream netlist exactly. Parallel buffers remain
separated by their individual 4.7-ohm resistors.

This pilot has **189 parts and 164 nets**. Of those, 68 cross its boundary:
32 inputs, 32 outputs, enable, two supply rails, and ground. It is not a
standalone board. All nets explicitly use `nettype.unchecked`: the power sources,
FPGA drive, loads, and electrical operating configurations are outside this
slice. A `SurroundingWren` block supplies modeled external endpoints without
adding physical parts. Compilation reports zero errors and 164 unchecked-net
warnings documenting that incomplete electrical model;
connectivity equivalence is not an ERC or hardware sign-off.

Original footprint IDs are retained as selection data. No footprint snapshot
or library-table setup is supplied yet, so this pilot is for compilation/LSP
and connectivity tests, not a ready-to-import PCB export.

## Try it

From the repository root:

```sh
uv run --locked noscheme compile --project examples/wren
uv run --locked pytest -q tests/test_wren.py
```

Open `examples/wren` in an editor, using the NoScheme checkout's language server.
The project TOML selects the subsystem without any installed KiCad libraries.

## Regenerate the reference

With KiCad's demos installed:

```sh
kicad-cli sch export netlist --format kicadxml \
  -o /tmp/wren.xml /usr/share/kicad/demos/vme-wren/vme-wren.kicad_sch
python3 examples/wren/upstream/extract.py /tmp/wren.xml
```

Pass `--source DIRECTORY` to select a different demo source directory. The
extractor regenerates the reference JSON and `components.nosch`; handwritten
`main.nosch` and `drivers.nosch` are preserved. Review regenerated data when
changing upstream versions. The full board export is needed to retain boundary
connectivity through the parent sheets.

The subsystem can also be measured with:

```sh
uv run --locked python -m tools.benchmark_analysis examples/wren --repeat 5
```

## Full-board benchmark

`full/` is a separate NoScheme project with 1,507 components, 1,309 connected
nets, and 491 explicitly unconnected pins. KiCad reports 1,800 nets because
that count includes its isolated no-connect nets. The importer reconstructs
hierarchy from the physical endpoints of each net and reuses 36 sheet
definitions, with arrays where child definitions match. Multi-unit symbols
belong to the owning sheet selected by KiCad's export; sheets with no owned
physical components are omitted. This is a connectivity reconstruction, not a
reproduction of the schematic's visual organization.

Generated part and net identifiers contain structural hashes. They favor
repeatable extraction over readability; use the handwritten subsystem for
idiomatic DSL examples. `full/instance_map.json` maps expanded instance paths
to original designators. `full/reference.json` retains upstream net names,
physical endpoints, component fields, and source hashes independently of the
NoScheme elaborator. Tests compare every value, footprint, pin, connection,
and no-connect against that reference.

All nets deliberately use `nettype.unchecked`. KiCad electrical pin types and
tri-state capability are retained, but operating configurations and NoScheme
electrical policies have not been modeled. Compilation produces zero errors
and 1,309 unchecked-net warnings. Original footprint identifiers are retained;
footprint geometry and a KiCad library-table setup are not bundled.

```sh
uv run --locked noscheme compile --project examples/wren/full
uv run --locked python -m tools.benchmark_analysis examples/wren/full --repeat 5
```

Open `examples/wren/full` as the editor project to exercise the larger workload.
To regenerate all full-board files from the XML export above:

```sh
python3 examples/wren/upstream/extract_full.py /tmp/wren.xml
```

Both extractors accept `--source`; the full importer also accepts `--output`.
The checked-in fixtures need no installed KiCad libraries for tests. Full-board
regeneration with the same source was verified byte-for-byte identical.

## Performance

Latest recorded full-board results at `f38c836`, October 5, 2026: **3.93 s full
LSP analysis** and **1.37 s isolated ERC**, using five-run medians after parser
and ERC optimizations. Environment: Python 3.13.9, Lark 1.3.1, pygls 2.1.1,
Linux x86-64. Timings were collected separately from tests. They are local
observations, not CI thresholds or editor request-latency guarantees.

[Development](../../docs/DEVELOPMENT.md#wren-performance-workload) documents
repeatable commands, per-sample timings, source hashes, and optional cProfile
output. This benchmark measures complete analysis; it does not directly measure
completion/hover requests or typing responsiveness. Several seconds per analysis
still warrants further work.

### Optimization history

Five-run medians on the same machine and unchanged WREN source:

| Implementation checkpoint | Full LSP analysis |
| --- | ---: |
| Original baseline | 6.65 s |
| Chunk ordinary text in the comment lexer | 5.29 s |
| Also reuse the current file's AST during project loading | 4.44 s |
| Also reduce ERC copying and default parameter setup | 3.93 s |

The initial baseline measured 2.86 s resolve/elaborate, 279 ms circuit deep copy,
and 1.90 s ERC. Resolve/elaborate fell to 2.04 s after comment chunking. The
subsystem's initial measurements, before these optimizations, were 60 ms
resolve/elaborate, 126 ms ERC, and 204 ms full LSP analysis.

The original cProfile run attributed 12.36 s of 20.06 s instrumented analysis to
parsing, including 5.90 s in comment masking. After parsing improvements, it
measured 3.88 s parsing and 5.03 s ERC. These profiled timings include
instrumentation overhead and must not be compared directly with the table.
The main file is now parsed once per analysis instead of twice. AST reuse checks
exact source text and lasts for one analysis; caching across edits remains
unimplemented. Analysis still produces 20,725 references, 5,231 builtin hover
entries, and 1,309 unchecked-net warnings, with no errors.

### ERC comparison

Configuration selection copies only changed role records and their parent
containers. One freeze memo per ERC run shares immutable subgraphs across
configurations, while plugins retain recursively immutable circuit views.
Successful default-parameter validation is reused per checker per configuration;
explicit arguments validate individually. These caches do not survive between
ERC runs. Rule scheduling and electrical policies are unchanged.

Five alternating old/new runs, using the previous ERC implementation from
`cd6d96c` and the same optimized parser, produced these median ERC times:

| Workload | Before | After |
| --- | ---: | ---: |
| WREN: 1,507 parts, unchecked nets | 2.01 s | 1.32 s |
| Scaled power modes: 768 parts, 257 nets, two active configurations | 1.11 s | 0.60 s |

The second workload expanded `examples/power_modes` to 256 independent supply
rails, with USB, expansion, and load connector arrays. It checked both
configurations with zero errors or warnings. It was a local measurement fixture,
not an additional committed example. WREN retained its 1,309 unchecked-net
warnings. Before/after diagnostics and complete reports were compared for
equality, including repeated runs. The full suite at `f38c836` passed 833 tests.
The 1.32 s ERC result and the later 1.37 s result above are separate runs,
illustrating normal timing variation.
