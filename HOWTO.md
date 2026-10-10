# Developing a circuit with NoScheme

NoScheme owns the parts and connectivity; KiCad owns placement, routing, and
physical design rules. The usual loop is to edit `.nosch` files, run electrical
checks, review a BOM and netlist, and update the PCB. Compilation and exports
are independent commands: you do not need to run every compiler stage in order.

This guide uses the Python implementation and shell commands from the NoScheme
checkout root. See the [language reference](docs/LRM.md) for syntax details and
[compiler development](docs/DEVELOPMENT.md) for tests and implementation work.

## 1. Install and select an implementation

You need Python 3.11 or newer and `uv`. In your checkout:

```sh
uv sync --locked
source .venv/bin/activate
noscheme --help
```

Activation makes `noscheme` and `noscheme-lsp` available in that shell, including
when you change into a circuit repository. Alternatively, prefix commands run
from this checkout with `uv run --locked`, or use the absolute path to
`.venv/bin/noscheme`. Configure editors with that absolute executable path if
they do not inherit the activated environment.

Language version `0.1` is experimental. Record the NoScheme Git revision with
your design; the version number alone does not freeze syntax or behavior.

Python is the default. The optional [C++ backend](cpp/README.md#build-and-check)
uses the same project files and export state. After building it, add
`--implementation native --native-tool /absolute/path/to/noscheme-native` to
compiler or export commands. Editor backend selection is documented separately
in [Editor support](docs/EDITOR_SUPPORT.md).

## 2. Start a project

For a small working starting point, copy the Blink sources and library into a
new directory. These commands assume `my-board` does not already exist:

```sh
mkdir my-board
cp -R examples/blink/src examples/blink/lib my-board/
```

Create `my-board/noscheme.toml`:

```toml
language_version = "0.1"
source_paths = ["src"]
library_paths = ["lib"]
output_dir = "build"
default_target = "demo"

[targets.demo]
root = "main.Blink"
```

The resulting layout is:

```text
my-board/
  noscheme.toml
  src/main.nosch
  lib/parts.nosch
  lib/indicator.nosch
```

`main.Blink` means the `Blink` definition in the `main.nosch` module, found under
one of the configured search directories. Paths in TOML are relative to the
TOML file. Source and library directories both participate in module lookup;
ambiguous module names are errors, rather than the first search path winning.

The starting design has a power connector and two resistor/LED indicators.
Read `src/main.nosch` for the root connections, `lib/indicator.nosch` for the
reusable block, and `lib/parts.nosch` for physical pin and footprint definitions.
A component is a physical part. A block groups circuitry and exposes ports.
The selected root is a block without public ports; it can declare global nets
and named electrical configurations.

```sh
noscheme check --project my-board
noscheme compile --project my-board
```

Compilation writes `my-board/build/demo/circuit.json`, `erc.txt`, and `erc.json`.
`--project` accepts either a directory or a TOML filename. It defaults to the
current directory; use an explicit path when running from elsewhere.

## 3. Edit and check the circuit

Install one of the [editor integrations](docs/EDITOR_SUPPORT.md) for Vim,
Neovim, Emacs/Eglot, or VS Code. Opening a `.nosch` file beneath the project is
enough for the language server to find the ancestor `noscheme.toml` and load
its source/library paths. There is no need to open the TOML first. For files
outside that directory tree, configure the server with `--project` explicitly.
Editors analyze `default_target` (or the sole target); files outside its import
graph do not receive another target's full circuit context.

While editing, use completion for types, parameters, connections, metadata,
and footprint hints. For example, `<` after a parameterized component type
requests parameter assistance; `(` after the instance name requests connection
assistance. Editor analysis includes unsaved buffers and writes no build files
or reference state. Command-line checks read saved files.

Run these commands during the edit/review cycle:

```sh
noscheme format my-board/src my-board/lib
noscheme check --project my-board
noscheme lint --project my-board
```

Formatting rewrites files. Use `format --diff` to preview or `format --check`
to enforce layout without rewriting. `lint` is optional and currently reports
unused public parameters; it is not electrical checking.

Declare electrical roles and constraints to match the actual circuit. ERC
checks those declarations; it does not simulate parts or infer behavior through
resistors and regulators. Review warnings and explicitly unchecked nets as well
as errors. A passing result only covers the modeled constraints.

| Phase | Command | What to review |
|---|---|---|
| Each saved change / CI | `check` | Structure and ERC; electrical errors return failure |
| Inspect expanded arrays and blocks | `compile` | `circuit.json` and ERC reports |
| Procurement review | `export-bom` | Grouped CSV, fitted/DNP quantities, part numbers |
| PCB handoff | `export-kicad` | Footprint validation, netlist, changes, ERC reports |

`compile` and `export-kicad` can succeed and publish artifacts despite electrical
errors. Use `check` as the electrical gate in automation. `export-bom` does not
run ERC. Structural errors block publication; a failed operation can leave the
previous successful artifacts in place, so check the command's result before
using them. The inspection commands `parse`, `load`, `resolve`, and `evaluate`
are useful for debugging, not required build steps.

Named ERC configurations check different uses of the same physical circuit.
All run by default. For example:

```sh
noscheme check --project examples/power_modes
noscheme check --project examples/power_modes --erc-configuration usb_powered
```

These configurations select electrical assumptions, not different BOMs or wiring.

## 4. Add parts and library snapshots

Write a component when you know its physical pin map, or explicitly convert
selected KiCad symbols. For example, with KiCad libraries installed at the shown
paths, choose an output directory that does not already exist:

```sh
noscheme convert-library /usr/share/kicad/symbols/Device.kicad_sym \
  --symbol R --symbol C \
  --footprint-root /usr/share/kicad/footprints \
  --output my-board/kicad_lib/device
```

This produces `Device.nosch`, `conversion.json`, and `footprints.json`. Add
`"kicad_lib/device"` to the TOML `library_paths` before importing `Device`.
Review the conversion report, pin roles, unused-pin policies, and footprint
candidates. Use handwritten specializations to select packages and apply your
circuit's electrical metadata. Imported resistor/capacitor values are required
quantity parameters; supply actual values when specializing or instantiating.

Commit converted sources, reports, snapshots, and their licensing/provenance.
Conversion is a library-update step, not something to repeat on every build.
KiCad export validates selected footprints against an explicit snapshot, so it
does not silently depend on the libraries installed on the compiler machine.
The snapshot contains pad records, not footprint geometry: PCB Editor still
needs the actual footprint libraries.

A snapshot must cover every assigned footprint in the design. The R/C conversion
above alone does not cover the starter's LEDs and connector. See the
[STM32 library instructions](examples/stm32h523/kicad_lib/README.md) for a complete
reviewed snapshot and how it is assembled. Footprint hints help selection but
do not establish mechanical suitability.

## 5. Build multiple boards or variants

Each TOML target selects a root and gets separate output and identity state.
Targets may select unrelated boards or specializations of a shared block.
Parameters on the root need defaults or bindings through `extend`; there is
currently no TOML parameter mapping or command-line `-D` override.

For example, in the starter's `src/main.nosch`, add this at the start of the
`Blink` body and change `indicators[2]` to `indicators[count]`:

```nosch
  params:
    number count = 2
```

Then add a top-level specialization after the block:

```nosch
extend Blink<4> FourIndicators
```

Append another target to `my-board/noscheme.toml`:

```toml
[targets.four]
root = "main.FourIndicators"
```

```sh
noscheme check --project my-board --target four
noscheme compile --project my-board --target four
```

The original `demo` target still produces two indicators in `build/demo`; `four`
produces four in `build/four`. `default_target = "demo"` controls commands without
`--target` and editor analysis. Run checks for each target before release.
A ready-made equivalent is in [examples/targets](examples/targets/main.nosch).
Legacy top-level `board = "module.Root"` configurations still work, using their
original output and `noscheme.state.json` paths; new projects should use targets.

## 6. Export the bill of materials

```sh
noscheme check --project my-board --target demo
noscheme export-bom --project my-board --target demo
```

The CSV is `my-board/build/demo/bom.csv`. It contains quantity, references,
value, footprint, manufacturer, MPN, supplier, supplier part number, and DNP.
References are allocated using the same persistent state as KiCad export, so
a BOM can be produced before the first PCB export. No footprint snapshot is
required; a missing footprint appears as an empty cell.

Add purchasing fields inside a component or physical instance's `metadata`
section, for example:

```nosch
  metadata:
    bom:
      manufacturer: "Example Components"
      mpn: "00123"
      supplier: "Example Distributor"
      supplier_part_number: "00456"
      dnp: false
```

Replace these illustrative strings with reviewed purchasing information. Part
numbers must be strings. Import their columns as text in spreadsheet software
to retain leading zeros. DNP parts appear in separate rows marked `yes`; they
remain in the circuit, ERC, and KiCad export. This is a population annotation,
not a switch that removes circuitry.

Rows group matching definitions, evaluated parameters, and BOM fields, so parts
with the same displayed value but different tolerances remain separate. References
sort naturally (`R2` before `R10`). See the [BOM reference](docs/LRM.md#134-bills-of-materials)
for override and grouping rules.

## 7. Export to KiCad and iterate

The STM32 example supplies a complete footprint snapshot for trying the handoff:

```sh
noscheme check --project examples/stm32h523
noscheme export-kicad --project examples/stm32h523 \
  --footprints examples/stm32h523/kicad_lib/footprints.json
```

For your project, use the same command with `--project my-board --target demo`
and a snapshot covering your selected footprints. Named-target artifacts go in
`build/demo`; the legacy STM32 example writes directly to its `build` directory.

Inspect `erc.txt`, `export.json`, and `changes.txt` before importing `board.net`.
`nets.txt`/`nets.json` and `names.json` help trace physical nets back to source.
Missing footprints are reported and omitted from the netlist; an assigned
footprint absent from the snapshot, or a pin/pad mismatch, blocks export.

In KiCad PCB Editor, use **File → Import → Netlist…**, choose `board.net`, then
**Load and Test Netlist**. Review the report and select **Update PCB**. The STM32
example should provide 58 footprints to place. Save the PCB outside generated
`build/`, then perform placement, routing, physical DRC, and fabrication review
in KiCad. NoScheme export does not perform those phases.

After a circuit change, repeat `check`, BOM export if needed, and `export-kicad`.
Review the change report and import the new netlist into the existing PCB.
Keep the previous PCB under version control and inspect layout preservation;
the STM32 reference has demonstrated initial population, not a routed-board
update qualification. Keep component/connectivity changes in NoScheme source;
there is no CAD-to-source synchronization.

For net-class settings and membership, see [the CAD reference](docs/LRM.md#133-names-and-net-classes).
KiCad retains ownership of widths and clearances.

## 8. Keep identities and reproducible inputs

Commit these with the circuit:

- `.nosch` sources and `noscheme.toml`, including custom ERC modules if used.
- Converted library snapshots, reports, attribution, and regeneration instructions.
- Each generated `noscheme.<target>.state.json` (or legacy `noscheme.state.json`).
- KiCad PCB/project files stored outside generated output.
- The NoScheme revision and reviewed design assumptions.

Ignore regenerable `build/` output during normal development; retain reviewed
release artifacts when publishing a manufacturing package. Keep identity state
outside that ignored output: deleting it can reassign references and break PCB
associations. BOM exports reserve references but do not replace the previous
CAD export baseline used by `changes.txt`.

An instance path is its default identity. Use `metadata.identity` before manual
renames or moves that must preserve a PCB association. LSP rename preserves
identities for supported root-level instances; it does not cover arbitrary moves
or changes inside reusable blocks. Review the [identity rules](docs/LRM.md#132-identities-and-references)
before a structural refactor after layout has started.
