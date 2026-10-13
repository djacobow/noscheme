# Developing a circuit with NoScheme

The idea of NoScheme is to replace schematics for most PCB design tasks. The
language is used to specify the components, their connectivity, and static rule
checking.

KiCad `pcbnew` owns placement, routing, and physical design rules.

The usual loop is to edit `.nosch` files, run electrical checks, review a BOM
and netlist, and update the PCB. Compilation and exports are independent
commands: you do not need to run every compiler stage in order.

This guide uses the Python implementation and shell commands from the NoScheme
checkout root. See the [language reference](docs/LRM.md) for syntax details and
[compiler development](docs/DEVELOPMENT.md) for tests and implementation work.

## 1. Install and select an implementation

You need Python 3.11 or newer and `uv`. Install `uv` from `astral.sh` rather
than using the version from apt.

```sh
uv sync --locked
source .venv/bin/activate
noscheme --help
```

Activation makes the runnable scripts `noscheme` and `noscheme-lsp` available
in that shell, including when you change into a circuit repository.
Alternatively, you can prefix commands run from this checkout with `uv run
--locked`, or use the absolute path to `.venv/bin/noscheme`.

If you want editor language support, configure editors with that absolute
executable path if they do not inherit the activated environment.

Language version `0.1` is experimental. Record the NoScheme Git revision with
your design; the version number alone does not freeze syntax or behavior.

Python is the default. The optional [C++
backend](cpp/README.md#build-and-check) uses the same project files and export
state. After building it, add `--implementation native --native-tool
/absolute/path/to/noscheme-native` to compiler or export commands. Editor
backend selection is documented separately in [Editor
support](docs/EDITOR_SUPPORT.md).

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

`main.Blink` means the `Blink` definition in the `main.nosch` module, found
under one of the configured search directories. Paths in TOML are relative to
the TOML file. Source and library directories both participate in module
lookup; ambiguous module names are errors, rather than the first search path
winning.

The starting design has a power connector and two resistor/LED indicators. Read
`src/main.nosch` for the root connections, `lib/indicator.nosch` for the
reusable block, and `lib/parts.nosch` for physical pin and footprint
definitions. A component is a physical part. A block groups circuitry and
exposes ports. The selected root is a block without public ports; it can
declare global nets and named electrical configurations.

```sh
noscheme check --project my-board
noscheme compile --project my-board
```

Compilation writes `my-board/build/demo/circuit.json`, `erc.txt`, and
`erc.json`. `--project` accepts either a directory or a TOML filename. It
defaults to the current directory; use an explicit path when running from
elsewhere.

## 3. Edit and check the circuit

You will want to install one of the [editor
integrations](docs/EDITOR_SUPPORT.md) for Vim, Neovim, Emacs/Eglot, or VS Code.
Opening a `.nosch` file beneath the project is enough for the language server
to find the ancestor `noscheme.toml` and load its source/library paths. There
is no need to open the TOML first. For files outside that directory tree,
configure the server with `--project` explicitly. Editors analyze
`default_target` (or the sole target); files outside its import graph do not
receive another target's full circuit context.

While editing, the LSP provides completion for types, parameters, connections,
metadata, and footprint hints. For example, `<` after a parameterized component
type requests parameter assistance; `(` after the instance name requests
connection assistance. The syntax checker will also show places where your code
does not parse correctly, and the ERC checker can show you rule violations.

Editor analysis includes unsaved buffers and writes no build files or reference
state. Command-line checks read saved files.

Run these commands during the edit/review cycle:

```sh
noscheme format my-board/src my-board/lib
noscheme check --project my-board
noscheme lint --project my-board
```

Formatting rewrites files. Use `format --diff` to preview or `format --check`
to enforce layout without rewriting. `lint` is optional and currently reports
unused public parameters; it is not electrical checking.

You can (and should) declare electrical roles and constraints to match the
actual circuit. ERC checks those declarations; it does not simulate parts or
infer behavior through resistors and regulators. Review warnings and explicitly
unchecked nets as well as errors. A passing result only covers the modeled
constraints. There are various ERC rules built-in, but the system is
extensible, if you want to create some rules specific to your components.

| Phase | Command | What to review |
|---|---|---|
| Each saved change / CI | `check` | Structure and ERC; electrical errors return failure |
| Inspect expanded arrays and blocks | `compile` | `circuit.json` and ERC reports |
| Procurement review | `export-bom` | Grouped CSV, fitted/DNP quantities, part numbers |
| Design review | `export-hierarchy` | SVG of nested blocks; `--nets` adds port nets |
| Design review | `export-schematic` | One-sheet SVG schematic with every block drawn open |
| PCB handoff | `export-kicad` | Footprint validation, netlist, changes, ERC reports |

`compile` and `export-kicad` can succeed and publish artifacts despite
electrical errors. Use `check` as the electrical gate in automation.
`export-bom` does not run ERC. Structural errors block publication; a failed
operation can leave the previous successful artifacts in place, so check the
command's result before using them. The inspection commands `parse`, `load`,
`resolve`, and `evaluate` are useful for debugging, not required build steps.

Named ERC configurations check different uses of the same physical circuit. All
run by default. For example:

```sh
noscheme check --project examples/power_modes
noscheme check --project examples/power_modes --erc-configuration usb_powered
```

These configurations select electrical assumptions, not different BOMs or
wiring.

## 4. Add parts and library snapshots

You can write a `component` when you know its physical pin map, or you can
explicitly convert selected KiCad symbols. For example, with KiCad libraries
installed at the shown paths, choose an output directory that does not already
exist:

```sh
noscheme convert-library /usr/share/kicad/symbols/Device.kicad_sym \
  --symbol R --symbol C \
  --footprint-root /usr/share/kicad/footprints \
  --output my-board/kicad_lib/device
```

This produces `Device.nosch`, `conversion.json`, and `footprints.json`. Add
`"kicad_lib/device"` to the TOML `library_paths` before importing `Device`.
Review the conversion report, pin roles, connection policies, and footprint
candidates. Use handwritten specializations to select packages and apply your
circuit's electrical metadata. Imported resistor/capacitor values are required
quantity parameters; supply actual values when specializing or instantiating.

Commit converted sources, reports, snapshots, and their licensing/provenance.
Conversion is a library-update step, not something to repeat on every build.
KiCad export validates selected footprints against an explicit snapshot, so it
does not silently depend on the libraries installed on the compiler machine.
The snapshot contains pad records, not footprint geometry: PCB Editor still
needs the actual footprint libraries.

A snapshot must cover every assigned footprint in the design. The R/C
conversion above alone does not cover the starter's LEDs and connector. See the
[STM32 library instructions](examples/stm32h523/kicad_lib/README.md) for a
complete reviewed snapshot and how it is assembled. Footprint hints help
selection but do not establish mechanical suitability.

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

The original `demo` target still produces two indicators in `build/demo`;
`four` produces four in `build/four`. `default_target = "demo"` controls
commands without `--target` and editor analysis. Run checks for each target
before release. A ready-made equivalent is in
[examples/targets](examples/targets/main.nosch).

## 6. Export the bill of materials

```sh
noscheme check --project my-board --target demo
noscheme export-bom --project my-board --target demo
```

The CSV is `my-board/build/demo/bom.csv`. It contains quantity, references,
value, footprint, manufacturer, MPN, supplier, supplier part number, and DNP.
References are allocated using the same persistent state as KiCad export, so a
BOM can be produced before the first PCB export. No footprint snapshot is
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
with the same displayed value but different tolerances remain separate.
References sort naturally (`R2` before `R10`). See the [BOM
reference](docs/LRM.md#134-bills-of-materials) for override and grouping rules.

## 7. Export to KiCad and iterate

The STM32 example supplies a complete footprint snapshot for trying the
handoff:

```sh
noscheme check --project examples/stm32h523
noscheme export-kicad --project examples/stm32h523 \
  --footprints examples/stm32h523/kicad_lib/footprints.json
```

For your project, use the same command with `--project my-board --target demo`
and a snapshot covering your selected footprints. Named-target artifacts go in
`build/demo`; the STM32 example explicitly sets its `default` target output
directory to `build`.

Inspect `erc.txt`, `export.json`, and `changes.txt` before importing
`board.net`. `nets.txt`/`nets.json` and `names.json` help trace physical nets
back to source. Missing footprints are reported and omitted from the netlist;
an assigned footprint absent from the snapshot, or a pin/pad mismatch, blocks
export.

In KiCad PCB Editor, use **File → Import → Netlist…**, choose `board.net`, then
**Load and Test Netlist**. Review the report and select **Update PCB**. The
STM32 example should provide 58 footprints to place. Save the PCB outside
generated `build/`, then perform placement, routing, physical DRC, and
fabrication review in KiCad. NoScheme export does not perform those phases.

After a circuit change, repeat `check`, BOM export if needed, and
`export-kicad`. Review the change report and import the new netlist into the
existing PCB. Keep the previous PCB under version control and inspect layout
preservation; the STM32 reference has demonstrated initial population, not a
routed-board update qualification. Keep component/connectivity changes in
NoScheme source; there is no CAD-to-source synchronization.

Net-class membership requires a separate project update; importing `board.net`
does not assign classes. Declare membership on the source net:

```nosch
net.power supply:
  net_class: "Power"
```

Create the `Power` class and its width/clearance settings in KiCad first. After
exporting, apply `netclasses.json` to a new project copy:

```sh
noscheme update-kicad-classes my-board/board.kicad_pro \
  --memberships my-board/build/demo/netclasses.json \
  --output my-board/updated.kicad_pro
```

The command preserves KiCad class settings and rejects undefined classes.
Review the new project copy before replacing the original project file, then
reopen that project in KiCad. Repeat this step when memberships change. See the
[CAD reference](docs/LRM.md#133-names-and-net-classes) for the membership contract.

## 8. Keep identities and reproducible inputs

Commit these with the circuit:

- `.nosch` sources and `noscheme.toml`, including custom ERC modules if used.
- Converted library snapshots, reports, attribution, and regeneration instructions.
- Each generated `noscheme.<target>.state.json`.
- KiCad PCB/project files stored outside generated output.
- The NoScheme revision and reviewed design assumptions.

Ignore regenerable `build/` output during normal development; retain reviewed
release artifacts when publishing a manufacturing package. Keep identity state
outside that ignored output: deleting it can reassign references and break PCB
associations. BOM exports reserve references but do not replace the previous
CAD export baseline used by `changes.txt`.

An instance path is its default identity. Use `metadata.identity` before manual
renames or moves that must preserve a PCB association. LSP rename preserves
identities for supported root-level instances; it does not cover arbitrary
moves or changes inside reusable blocks. Review the [identity
rules](docs/LRM.md#132-identities-and-references) before a structural refactor
after layout has started.

## 9. Command reference

The installed tools are `noscheme` and `noscheme-lsp`. With the development
environment, prefix commands with `uv run --locked`.

| Command | Result |
|---|---|
| `noscheme parse FILE` | Parse source and emit typed AST JSON |
| `noscheme format [PATH...]` | Rewrite sources in the canonical layout |
| `noscheme load --project PATH` | Load configuration and imports |
| `noscheme resolve --project PATH` | Resolve definitions and public interfaces |
| `noscheme lint --project PATH` | Opt-in unused-parameter warnings; no artifacts |
| `noscheme evaluate --project PATH` | Evaluate parameterized declarations |
| `noscheme check --project PATH` | Check structure and ERC |
| `noscheme compile --project PATH` | Write circuit and ERC reports |
| `noscheme export-bom --project PATH` | Write grouped CSV BOM and update reference reservations |
| `noscheme export-hierarchy --project PATH [--nets] [--parts] [--depth N]` | Draw the block hierarchy as `hierarchy.svg` ([details](docs/LRM.md#135-hierarchy-diagrams)) |
| `noscheme export-schematic --project PATH [--seed N] [--effort X]` | Draw the whole design on one sheet as `schematic.svg` ([details](docs/LRM.md#136-schematic-sheets)) |
| `noscheme export-kicad --project PATH --footprints FILE` | Validate footprints, export netlist, and update state |
| `noscheme convert-library FILE --output NEW_DIRECTORY` | Convert a KiCad symbol library |
| `noscheme update-kicad-classes INPUT --memberships FILE --output NEW_FILE` | Update class membership in a project copy |
| `noscheme-lsp [--project PATH]` | Run the language server over stdin/stdout |

Project commands accept `--target NAME` to select a named target. Without it,
they use `default_target`, or the sole target if there is only one.

`--project` accepts a directory or TOML file and defaults to the current directory
for compiler commands. Diagnostics support `--diagnostics text` and `json`.
`parse` also accepts `--output FILE`. Conversion accepts repeatable `--symbol`,
`--footprint-root`, and `--extra-footprint` options; omit `--symbol` to convert
all symbols. The conversion output directory must not already exist.

`check`, `compile`, and `export-kicad` accept repeated `--erc-configuration NAME`.

`format` accepts files and directories, defaulting to the current directory. It
skips hidden directories and each project's `output_dir`. `--check` writes nothing
and fails if a file would change; `--diff` prints the changes instead of writing
them. Files it cannot format are reported, and the command then fails.
`load`, `resolve`, and `evaluate` are inspection stages, not electrical validation.

Conversion maps KiCad `open_collector` to `drive.low` and `open_emitter`
to `drive.high`, reporting `assumed_drive_direction` so the device datasheet
can be checked for polarity. Conflicting stacked directional types require an
explicit annotation and produce `ambiguous_drive`. `free` and `unspecified` remain unmapped, retain their
original type in custom metadata, and produce conversion issues. They do not
silently become unchecked endpoints.

`noscheme lint --project PATH` reports `lint.unused-parameter` warnings in text,
or JSON with `--diagnostics json`. It runs only when explicitly requested, returns
zero for warnings, and writes no artifacts. Structural errors still fail. It
checks lexical references across loaded definitions, including inherited uses,
default expressions, numeric metadata, array dimensions, and element overrides.
The conventional component `value` parameter is implicitly used unless replaced
by `metadata.value`. Supplying a named argument alone is not a use inside the
component. This is a source lint, not whole-program dead-code analysis; intentional
public compatibility placeholders can remain unused.

### 9.1 Diagnostics, failures, and publication

Diagnostics carry a stable code, severity, message, filename, line/column, and
related source locations. Expanded instance paths and configuration labels are
retained where applicable. Text and JSON reports describe the same checks.

Once interfaces resolve successfully, body checking reports independent errors
across definitions and instances, including separate arguments and connection
statements. It stops a dependent check when its prerequisite is invalid: an
unknown component type, for example, prevents checking that instance's pins.
Namespace/type setup failures stop the affected body. Identical errors in shared
inherited bodies are reported once. Structural errors still block subsequent
evaluation, elaboration, and export. Grammar parsing, interface construction,
and elaboration remain fail-fast. Once a file parses, AST structural validation
can report multiple duplicate sections, fields, or invalid declaration contexts.
This is not general syntax-error recovery.

- `check` returns 1 for electrical errors.
- `compile` and `export-kicad` may publish with electrical errors and return 0.
  Inspect their reports; successful export does not mean the circuit passed ERC.
- Structural/source errors return 1 and block publication.
- Configuration, command, and I/O errors use exit status 2.
- Warnings alone do not block publication. Failed compilation preserves previous
  successful circuit/state artifacts. Editor analysis writes neither.

### 9.2 Output files

Compilation produces `circuit.json`, `erc.txt`, and `erc.json`. Hierarchy export
produces `hierarchy.svg`, and schematic export `schematic.svg`. KiCad export
produces ERC reports, `board.net`, `names.json`, `netclasses.json`, `nets.txt`, `nets.json`,
`changes.txt`, and `export.json`, plus persistent `noscheme.<target>.state.json` outside the
output directory. Change reports compare against the last successful export.
`changes.txt` lists before/after values, footprints, references, paths, and each
changed pin connection. `export.json.probable_renames` lists unique pairs of
removed/added identities with exactly matching footprints, values, and pin nets.
These are advisory hints, not proof of a rename. They never transfer UUIDs,
references, or placement; added and removed identities remain distinct. To retain
identity across a planned source rename, set explicit `identity` metadata before
exporting the renamed design ([CAD identities](docs/LRM.md#13-cad-identities-and-export)).

Every exported physical component has two netlist properties, also exported as
fields so KiCad can copy them into footprint properties:

- `noscheme_path`: its current full source instance path, relative to the board,
  including nested block names and array indices, such as `indicators[0].resistor`.
- `noscheme_identity`: its stable identity used to allocate UUIDs and references.
  Explicit identity metadata can keep this different from the current source
  path after a rename.

Connectivity remains flattened. These properties describe the source hierarchy;
they do not create KiCad sheets or alter footprint placement. After updating
the PCB from the netlist, select a footprint and press `E` to inspect its fields
in Footprint Properties. The user verified both fields after importing the
STM32 example into KiCad.

Explicit `no_connect` bindings and unconnected `use.disallowed` pins are
exported on individual synthetic nets with node `pintype: no_connect`. This
lets KiCad distinguish intentionally unused pads from pins missing in the
netlist, including footprints with duplicate mounting-pad numbers. Names use
the stable component UUID and pin number, with a suffix if needed to avoid a
real net name. They exist only in `board.net`; they do not add source nets,
ERC connections, or entries to `names.json` and the source-net reports.
Other omitted pins retain their existing diagnostics and are not automatically
marked no-connect.

KiCad `libparts` retains the first declared pin alias (or physical pad ID when
there is no alias). A single known role maps to the corresponding KiCad pin type;
analog and digital inputs/outputs share KiCad's input/output types, ground maps
to `power_in`, and explicitly tri-state-capable outputs map to `tri_state`.
Unknown, unresolved, or differing instance types in a shared component entry
become `unspecified`. NoScheme ERC remains authoritative. The field format follows
[KiCad's documented libparts format](https://github.com/KiCad/kicad-doc/blob/master/src/eeschema/eeschema_creating_customized_netlists_and_bom_files.adoc);
PCB Editor display behavior still requires manual validation.

The language server supports diagnostics, go-to-definition, references, rename,
formatted hover, and contextual completion. It uses unsaved buffers and a short typing debounce.
See [Editor support](docs/EDITOR_SUPPORT.md#language-server) for client setup and current limits.
