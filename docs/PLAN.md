# NoScheme implementation plan

## Current status

As of `f38c836` (October 5, 2026), the compiler, extensible ERC, KiCad export,
and Vim/Neovim/VS Code integrations are implemented. The last full validation
reported 833 passing tests. The WREN benchmark is complete, and parser/ERC
optimizations reduced local full LSP analysis from 6.65 s to 3.93 s.

Caching unchanged ASTs across edits is a proposed next performance step, not an
implemented feature. Background cancellation and incremental parsing are also
unimplemented. The [deferred review list](#deferred-work) records the remaining
review proposals; it does not undo the performance work resumed below.

This file retains milestone history. Old test counts, benchmark timings, and
implementation-stage plans describe those checkpoints. Use the
[LRM](LRM.md) for current behavior and [Development](DEVELOPMENT.md) for current
commands; recent work and measurements are listed first.

## Recent completed work

Requested October 5, 2026. These are separate from the deferred review backlog.

- [x] **Build a larger real-design benchmark from VME-WREN.**
  [examples/wren](../examples/wren/README.md) includes a readable 189-part
  subsystem and a generated full-board project with 1,507 parts, 1,309 connected
  nets, and 491 intentional no-connect pins. Tests compare every value,
  footprint, physical pin, and connection against independently extracted KiCad
  reference data. Source hashes, metadata, reference mappings, and attribution
  are recorded. No upstream schematic or geometry is bundled. Electrical
  policies remain unchecked: zero errors and 1,309 explicit warnings.
  Initial full-board five-run medians on October 5, before optimization:
  resolve/elaborate 2.86 s, ERC 1.90 s,
  full LSP analysis 6.65 s. These are local observations, not CI thresholds.
- [x] **Profile full-board LSP analysis using WREN.** Baseline collected after
  test completion; tool records samples, input hashes, and environment versions.
  A separate cProfile run attributes 12.36 s of 20.06 s instrumented analysis
  to parsing and 5.03 s to ERC. At that checkpoint the main file was parsed
  twice; comment masking cost 5.90 s across the three parse calls. These include instrumentation
  overhead and are distinct from the 6.65-second uninstrumented median.
- [x] **Reduce full-board parsing overhead.** Comment lexing now consumes
  ordinary text in chunks, and project loading reuses the current file's AST
  when its source text matches. Five-run WREN LSP medians: 6.65 s baseline,
  5.29 s after chunking, 4.44 s with both changes (about 33% less time).
  Resolve/elaborate: 2.86 s to 2.04 s. All 831 tests pass, with regression
  coverage for positions, comments, stale ASTs, and unimported editor buffers.
  The new profile has two parse calls rather than three; ERC is now the largest
  cumulative stage. Reuse is limited to one analysis; persistent AST caching
  and incremental parsing remain unimplemented.

- [x] **Reduce ERC copying and default setup.** Configuration views copy only
  changed role records and share immutable conversion work within one ERC run.
  Default parameters are validated/frozen once per checker per configuration;
  explicit parameters retain individual validation. Five alternating before/
  after runs produced identical diagnostics and reports: WREN ERC 2.01 s to
  1.32 s; 768-part active power-mode workload with two configurations 1.11 s to
  0.60 s. All 833 tests pass, including nested plugin immutability and edits
  between ERC runs. Rule scheduling is unchanged. Full WREN LSP analysis
  measured 3.93 s after these changes, down from 4.44 s.

- [x] **Move CI to Ubuntu 26.04.** Both jobs now use the explicit
  `ubuntu-26.04` label, generally available since September 17, 2026. Verified
  that the KiCad 10 PPA publishes symbols, footprints, and demos at version
  `10.0.6~ubuntu26.04.1` for `resolute`. Python matrix, library integration,
  and installed-wheel checks passed in
  [CI run 37248148487](https://github.com/djacobow/noscheme/actions/runs/37248148487).
- [x] **Test the LSP in Neovim and VS Code.** Help the user find and install a
  recent stable Neovim release, checking official release information rather
  than assuming Ubuntu's apt package is current. Document the tested editor and
  client versions and setup instructions. Exercise diagnostics on unsaved edits,
  completion (including unfinished strings), cross-file definition/references,
  hover, and rename with identity preservation in both editors. Include a
  repeatable smoke-test checklist and record any client-specific issues.
  Neovim 0.12.5 is installed and configured with its built-in LSP client;
  headless checks passed for attachment, cross-file definition, hover,
  references, unfinished-string completion, and unsaved diagnostics. Setup and
  a manual checklist are in [EDITOR_SUPPORT.md](EDITOR_SUPPORT.md#neovim-client-setup).
  The user confirmed Neovim completion, cross-file navigation/hover, and that
  board-instance rename retains `metadata.identity`. VS Code 1.140.0 and the
  local NoScheme client extension 0.1.0 are installed. The user confirmed
  highlighting, hover, definition, completion, identity metadata preservation
  on rename, and unsaved diagnostics appearing and clearing in VS Code.
  Restricted Mode initially disabled the extension; trusting the project
  resolved it and this is documented. The user also confirmed VS Code
  references and the post-rename export comparison, completing these checks.
- [x] **Export full source hierarchy as instance attributes.** Add a property
  such as `noscheme_path` to every exported physical component, containing its
  full current hierarchical source name, including array indices. Retain the
  existing `noscheme_identity` property: an explicit stable identity can differ
  from the current source path after a rename. Keep connectivity flattened and
  preserve UUID/reference allocation. Test nested blocks, arrays, and renamed
  instances, and verify how the property appears after KiCad import.
  Implemented `noscheme_path` alongside `noscheme_identity` on every exported
  physical component, including KiCad `fields` entries for footprint display
  (generic netlist properties alone are not copied into footprint fields).
  Export tests cover nested block arrays, multidimensional
  indices, source names containing `#`, and path changes while preserving
  identity, UUIDs, references, and connectivity. The user confirmed that both
  fields appear in KiCad Footprint Properties after reimport.

- [x] **Represent intentionally unconnected pins in KiCad export.** The STM32
  import previously reported 49 warnings for 41 unused MCU pads, two SWD pads, two USB-C
  pads, and four JST mounting pads. Check KiCad's expected representation and
  preserve no-connect semantics without introducing electrical connections.
  Verify the resulting import and repeated updates in PCB Editor.
  Implemented isolated synthetic net nodes with KiCad's `no_connect` pin type
  for explicit no-connect bindings and disconnected do-not-connect pins.
  Tests cover stable names, collisions with real nets, accidental omissions,
  and all 47 unused logical pins (49 footprint pads) on the STM32 example.
  The user verified the PCB update with zero warnings/errors, including
  separate nets for duplicate JST mounting pads. Reimporting the same netlist
  reported no changes and zero warnings/errors.

## Completed reference-board milestone: physical STM32 and KiCad handoff

Compiler-core steps 1–5, explicit library conversion, persistent KiCad export,
role/configuration ERC, grouped and aggregate currents, and opt-in extensible
bias/resistance/capacitance checks are implemented. The Vim/Neovim runtime is
implemented with installation instructions in [EDITOR_SUPPORT.md](EDITOR_SUPPORT.md).

The STM32 reference now compiles and exports 58 physical parts on 52 nets with
14 validated footprint types, no omitted parts, and zero active ERC errors or
warnings in USB-powered and header-5-V-powered configurations. The MCU symbol
was explicitly converted from the installed KiCad library; physical pin roles
are specialized in the example. Added the HC49 crystal/load capacitors, all MCU
supply decoupling, local SD/display decoupling, concrete connectors, and a
ceramic-stable TLV1117LV33 supply. Direct header/LDO power connections are retained
for this operator-controlled POC. No additional protection circuitry is planned.

The user confirmed that the generated STM32 netlist populated the KiCad board
correctly. This completes the initial manual import check. Placement and routing
are deferred at the user's request; layout-preserving updates on this reference
board remain unverified. Automated coverage checks MCU pins/supplies, VCAP isolation, buses,
USB contacts, local capacitors, all footprint pads, and repeat export stability.
Crystal capacitor tuning, firmware clock/boot settings, and physical layout
validation remain hardware tasks.
The completed feasibility experiment findings remain in
[experiments/FINDINGS.md](experiments/FINDINGS.md).

### Tri-state capability checking: implemented

`nettype.tristate` allows inputs and passive attachments; output-capable digital
endpoints require explicit boolean `tristate_capable` pin metadata. Multiple
capable drivers are allowed without enable-state or arbitration modeling. The
check uses effective roles, preserves hierarchical constraints, supports normal
waivers, and leaves ordinary digital-net checks intact. KiCad conversion now
preserves explicit tri-state output capability. `examples/tristate` demonstrates
the rule; tests cover arrays, ports, inheritance, overrides, configurations,
invalid domains, diagnostics, and conversion. The ERC pending-check list is empty;
this does not imply hardware validation.

### Language server: implemented

`noscheme-lsp` uses pygls over stdio and reuses compiler loading, parsing,
resolution, elaboration, and ERC. The loader accepts in-memory source overlays;
editor checks never publish compiler/export artifacts. Full checks are debounced
by 200 ms and serialized. Diagnostics include related locations and configuration
labels, use negotiated position encoding, and clear obsolete results.

Definition and hover lookup use AST spans, imported symbols, lexical names,
and resolved pin/port aliases. Files outside a loaded board graph receive syntax
checks only. The server discovers parent/workspace projects or accepts --project;
ambiguous workspace ownership is reported. [EDITOR_SUPPORT.md](EDITOR_SUPPORT.md) includes Vim client setup.
Stdio integration tests cover initialization/shutdown, unsaved changes, syntax
recovery, imports, dependency close/revert, and Unicode incremental edits.

Completion covers visible component/block names, public connection pins and
ports (including inherited aliases), explicitly imported role/nettype values,
footprint strings, and registered ERC application names/parameters. It recovers
incomplete statements in a private source snapshot and uses current unsaved
imports. Comments and unrelated strings are excluded. Stdio tests exercise completion.
The user confirmed diagnostics, navigation, hover, and completion work through
their Vim setup.

Richer hover metadata is implemented below. Background analysis/cancellation and incremental compiler
caches can follow when project size makes serialized checks too slow.

### Vim/Neovim highlighting

The shared runtime in `editors/vim` detects `.nosch`, colors language constructs,
and supplies buffer-local indentation/comment/identifier settings. Tests use
headless Vim and, when installed, Neovim. With vim-plug, register the runtime as
a local `Plug` entry; do not prepend it before a no-argument `plug#begin()`.

## Historical compiler-core milestones

The following sequence records the original milestone and its
historical implementation steps. Current behavior is specified in [LRM.md](LRM.md);
[PROJECT.md](PROJECT.md) records goals and design rationale.

### Outcome

Compile a small, complete `.nosch` project into a deterministic, CAD-independent
circuit representation, with source-located diagnostics. The acceptance design
is the same five-part LED circuit used in the KiCad experiments, expressed through
reusable blocks and explicit physical component definitions.

Keep the experiments under `tools/experiments/` as evidence. Production code lives under
`src/noscheme/`, tests under `tests/`, with a root `pyproject.toml` and a pinned
development environment. Use Lark for parsing; do not depend on KiCad or PCBNew
to run compiler-core tests.

This milestone includes structural validation and expression warnings. Electrical
compatibility, required-pin ERC, current budgets, library conversion, KiCad export,
stable reference allocation, and persistent mapping commits follow later. Retain
their metadata and source information in the circuit representation now.

### Project and command interface

```toml
language_version = "0.1"
board = "main.Blink"
source_paths = ["src"]
library_paths = ["lib"]
output_dir = "build"
```

Resolve every configured path relative to `noscheme.toml`. Both source and
library paths participate in one unambiguous module lookup: `parts.passives`
maps to `parts/passives.nosch`. Reject duplicate matches, duplicate imported
names, import cycles, and import attempts that escape configured roots.
Reserve the module `noscheme` for explicitly imported builtins.

Implemented loader: all five configuration keys are required; version `"0.1"`
is supported. Search paths must be existing, distinct directories, with at least
one source or library path. Empty library paths are allowed. The output directory
is resolved but not created during loading. Reachable modules are loaded once;
unused files are not parsed. Named imports can re-export imported bindings while
preserving their original definition identity. Missing names, duplicate imported
names, local-definition collisions, and cycles are errors.

```sh
uv run --locked noscheme load --project examples/blink
uv run --locked noscheme load --project examples/blink/noscheme.toml --diagnostics json
```

The `load` command writes a deterministic project/module summary to stdout and
diagnostics to stderr. It selects the configured board and binds imports, but
does not resolve definition bodies, evaluate expressions, expand instances,
check electrical rules, or write circuit/state files. `examples/blink` supplies
the five-part circuit split across source and library modules for later stages.

Original compiler-core command scope (ERC additions are documented below):

- `noscheme check --project PATH`: parse, resolve, elaborate, and validate
  structure. ERC was not part of this initial milestone; current `check`
  includes electrical checking.
- `noscheme compile --project PATH`: do the same and write `circuit.json` on
  success. This is an inspectable compiler artifact, not a KiCad export.
- Both support text or JSON diagnostics, with exit 0 on success, 1 for blocking
  source errors, and 2 for command/configuration/I/O errors. Warnings do not block.

Accept a config file or project directory as PATH; default to the current
directory. Write circuit output atomically after successful compilation. A failed
run must leave the previous successful circuit artifact intact. Diagnostic
output may describe a failed run. No persistent identity/reference state is
written in this milestone.

### Implementation sequence

#### 1. Package, typed AST, and diagnostics

Implemented in `src/noscheme/`, with the `noscheme parse` inspection command.
The reference draft and positive corpus produce typed ASTs; duplicate sections
and fields and invalid declaration contexts produce source-located diagnostics.
The suite has 66 passing tests (26 front-end tests and 40 experiment tests).
The CLI successfully emits the reference AST; source and wheel distributions
build with both grammar resources included. Metadata schema checking and alias
or selector overlap resolution still belong to steps 2 and 4 respectively.

```sh
uv sync --locked
uv run --locked noscheme parse examples/stm32h523/stm32h523.nosch --output build/stm32h523.ast.json
uv run --locked pytest -q
```

`parse` checks syntax and declaration structure only. It is intentionally named
separately from the future project-wide `check` and `compile` commands. Use
`--diagnostics json` for machine-readable errors on stderr. Failed parsing leaves
an existing output file intact.

- Promote the demonstrated Lark grammar and comment handling into the package.
  Replace schema-neutral syntax nodes with typed declarations, expressions,
  endpoint selectors, and named section entries.
- Retain filename, start/end offsets, line and column on every meaningful node.
  Keep physical pin token spelling separate from numeric expression values.
- Reject duplicate sections and fields rather than losing them in dictionaries.
  Diagnose invalid construct placement and unsupported syntax explicitly.
- Establish diagnostic codes, severity, primary span, related spans, and optional
  instance path. Produce matching text and JSON diagnostics.

Acceptance: the reference draft still parses; existing lexical edge cases retain
their locations; `01` and `1` remain distinct pin identifiers; duplicate entries
produce diagnostics showing both locations.

#### 2. Project loading and definition resolution

Project loading is implemented in `src/noscheme/project.py`; definition resolution
and the initial metadata registry are in `resolve.py` and `metadata.py`. All 140
tests pass, including 40 resolver tests. Both the split five-part example and the
STM32 reference draft resolve successfully. The draft still has placeholders and
has not been evaluated, expanded, or electrically validated.

```sh
uv run --locked noscheme resolve --project examples/blink
```

The command emits resolved definitions with interfaces, canonical physical pin
IDs and aliases, merged metadata, and unevaluated defaults. Values retain source
spans and their lexical module; inherited bodies retain their original module.
Inheritance traversal is iterative and tested beyond 1,000 levels. Recursive
bundle types, alias collisions, invalid specialization, private-interface access,
unknown helpers/parameters, and invalid metadata are diagnosed.

At this milestone only the initial metadata schema was implemented. Argument
evaluation, shape checks, port reachability, and ERC were added in the subsequent
steps below. The current metadata schema is documented in [LRM §8](LRM.md#8-metadata-reference).

- Implement configuration validation, deterministic module discovery, explicit
  imports, and separate namespaces for builtins and ordinary definitions.
- Collect declarations before resolving uses so block order does not matter.
  Resolve lexical names and public interfaces without exposing private internals.
- Resolve single inheritance with cycle detection; recursively merge metadata,
  replace lists, apply parameter defaults, and reject structural modifications.
- Validate resulting aliases against physical pin IDs and other aliases.
- Create a metadata schema registry describing field types and legal scopes.
  Cover the reference draft and agreed structural fields; allow arbitrary data
  only under `custom`. Unimplemented electrical constructs must receive an
  explicit unsupported-feature diagnostic, never be silently accepted and lost.

Acceptance: ambiguous module lookup, missing imports, cycles, alias collisions,
invalid specializations, and private-member access fail with useful locations.
Valid inherited defaults and metadata overrides resolve independently of order.

#### 3. Expressions and parameters

Implemented in `src/noscheme/evaluate.py`; the suite now has 200 passing tests,
including 60 evaluator tests. `evaluate` traverses instance declarations, binds
parameters, evaluates values/dimensions/selectors, and reports conversion warnings:

```sh
uv run --locked noscheme evaluate --project examples/blink
```

The JSON output explicitly represents unexpanded array templates. The example's
two Indicator elements are reported once with dimension `[2]`, with their child
declarations and bound parameters. This is not the flattened circuit artifact.
Integer payloads use decimal strings with an integer tag to preserve precision
in JSON consumers. Warnings are included in the report and rendered on stderr;
warnings do not change success status. Failed evaluation produces no success
report or persistent state changes.

Defaults resolve by dependency, regardless of declaration order. Explicit
arguments are evaluated in caller scope; inherited defaults in their origin
module. Overridden defaults are not evaluated. Cycles among defaults still needed
for binding are errors. Recursive block instantiation is diagnosed.

Numeric operations and rounding implement [LRM §4](LRM.md#4-parameters-and-expressions). Named arguments
are now supported for `.format()`; numeric/unit helpers take one positional
argument. Formatting permits explicit indices/names, escaped braces, and bounded
numeric width/precision; object traversal, implicit capture, conversion flags,
and nested format fields are rejected.

Default implementation limits: 65,536 integer bits and string characters,
format width 4,096, precision 100, expression depth 128, dimensions 100,000,
and 10,000 visited instance declarations. Evaluator limits are configurable through
the Python API. Definition resolution has its own configurable expression-depth
limit. Upper index bounds, exact array shapes, total expanded instance count,
and structural port connectivity are checked in the next elaboration step.

- Implement tagged integer/double evaluation and the agreed conversion warnings,
  arithmetic, shifts, rounding helpers, and display-unit helpers.
- Bind required/defaulted parameters by name, check their public number/string
  types, and detect parameter-default dependency cycles.
- Enforce permitted parameter uses: dimensions, indices, values, and forwarding
  through parameters. Do not permit parameter-driven connectivity or metadata
  changes outside those contexts.
- Implement restricted formatting through an explicit evaluator, with no Python
  `eval`, object traversal, or arbitrary callable access.

Acceptance: negative rounding ties, integral doubles in integer contexts,
negative shifts, division by zero, missing arguments, unsafe format fields, and
parameter-use restrictions have direct behavioral tests.

#### 4. Hierarchy, shapes, and circuit representation

Implemented in `src/noscheme/circuit.py`. Concrete paths retain string/integer
segments. Physical pins are counted once; port/net origins and binding states
remain in the output. Structural port reachability is checked before applying
caller bindings. Resource limits cover instance/leaf counts and nesting.
An explicit identity on an instance array receives its element indices as a
suffix; an explicit block identity anchors descendant identities across renames.
Requested reference and identity collisions are diagnosed; automatic CAD reference
allocation is reserved for the exporter.

- Expand physical instances and reusable blocks, detecting recursive block
  instantiation. Keep definition origins and expansion paths for diagnostics.
- Resolve board globals explicitly; ports declare internal connections. Diagnose
  a missing global only when expanding a block that actually uses it.
- Expand arrays, bundle fields, physical ranges, and selectors into concrete
  endpoints while retaining their shapes and named bundle-type identities.
- Check binding overlap after resolving aliases and selectors. Allow exact-shape
  binding and scalar sharing; reject partial broadcasting and independent-net
  merging. Whole bundles match nominal type and fields by name.
- Flatten hierarchical connectivity, naming nets from the outermost bound caller
  and retaining internal names as provenance. Count physical endpoints once.
- Preserve omitted bindings, explicit `no_connect`, external endpoint annotations,
  and placeholders as distinct information for subsequent ERC. Structural port
  reachability is checked now; electrical policy violations are deferred to ERC.

The representation includes physical parts, physical pin IDs, flat nets and
endpoint membership, evaluated values, metadata, requested identities/references,
net classes, explicit pair relationships, and source/expansion provenance.
Keep hierarchical paths as structured segments, not just dot-joined strings.
It contains no KiCad name escaping, footprint UUID allocation, or layout data.
Retain physical parts with unspecified footprints.

Acceptance: hand-written expected connectivity for nested blocks, shared scalar
connections, multidimensional arrays, alias overlaps, bundle breakouts, globals,
placeholders, and `no_connect`. Include failures for equal-size but unequal-shape
arrays, missing pins, and attempts to connect independent nets through one pin.

#### 5. End-to-end acceptance and handoff

Implemented commands:

```sh
uv run --locked noscheme check --project examples/blink
uv run --locked noscheme compile --project examples/blink
```

The example compiles to five parts and four nets. Its connectivity matches the
independent KiCad fixture. Tests cover repeated output, source reordering,
preserving successful output on failure, arrays, bundles, ranges, slices,
overlapping bindings, placeholders, globals, and explicit disconnection.
`circuit.json` is written atomically under the configured output directory and
explicitly reports `erc_performed: false`. It contains no CAD UUIDs or automatic
reference assignments. Unspecified footprints warn without dropping physical
instances from the circuit.

The STM32 draft also expands, including two JST UART headers instantiated as an
array with independent TX/RX and shared ground. That is a structural test, not
hardware or electrical validation; MCU and connector choices remain pending.

- Compile the five-part LED design with two Indicator instances, inherited part
  defaults, an explicit header, and complete pin-to-net expectations.
- Compare its physical connectivity with the existing feasibility fixture,
  independently of reference allocation and backend naming.
- Reorder independent declarations and bindings: semantic output must remain
  identical. Diagnostic/source-location changes are permitted. Repeating a
  compile on unchanged inputs must produce byte-identical circuit output.
- Verify that a failed compile preserves the previous successful artifact.
- Keep the STM32 draft as a parser regression; do not claim it is a complete
  semantic or electrical acceptance design while placeholder libraries remain.
- Document the CLI, artifact schema version, implemented subset, and remaining
  checks. Run the compiler tests and existing experiment regressions.

### Original policy proposals (historical)

These were the starting decisions before implementation. The LRM now specifies
the adopted policies, including array shapes, metadata, numeric tags, JSON-style
string escapes, formatting, and resource limits. This table preserves the
original proposal rather than listing open decisions.

| Area | Proposal |
|---|---|
| Indentation | Spaces only for indentation; diagnose tabs. Preserve significant indentation inside instance parentheses. |
| Numeric representation | Arbitrary-precision integers and finite IEEE-754 binary64 doubles. Diagnose non-finite results and failed conversions. |
| Literals and precedence | Decimal integer/fraction/exponent forms from the experiment; unary signs, then multiply/divide, add/subtract, shifts. Operators at each binary level associate left. No hex or bitwise operators beyond agreed shifts initially. |
| Arrays and slices | Positive dimensions; support arbitrary rank subject to resource limits. Reject negative indices, empty slices, omitted bounds, strides, and descending physical ranges initially. |
| Formatting | Positional arguments with `{0}` and named arguments with `{name}`; escaped braces, bounded width/precision, numeric `d`, `f`, `e`, `g` formats. No implicit capture of scope, attribute/index traversal, conversion flags, or nested replacement fields. Add named helper-call arguments to the grammar. |
| Unit helpers | Start with helpers exercised in the draft: Ohm/kOhm/MOhm, pF/nF/uF, nH/uH/mH. Fixed unit suffix, deterministic magnitude rendering; no automatic rescaling. |
| Shadowing | Reject duplicates in one scope; allow ordinary nested lexical shadowing. Reserve builtin module identity and grammatical tokens; imports remain explicit. |
| Resource limits | Apply configurable compiler limits to expansion, nesting, shifts, and format sizes, with source diagnostics. Limits are tooling safeguards rather than silent language truncation. |

The resulting metadata keys, formatting bounds/rendering, and diagnostics are
implemented and documented in the LRM. The historical draft grammar's generic
field acceptance is not the production metadata schema.

### Following milestone

Connect this circuit representation to explicit library conversion and the proven
KiCad adapter. Carry forward the feasibility findings: preserve UUID paths,
validate pins against converted footprint records, report partial exports,
preserve KiCad class settings, and do not rely on schematic parity or KiCad's
import error count for circuit validation.

### Library conversion and export: implemented first pass

The `convert-library` command reads an explicitly named `.kicad_sym` file and
writes a fresh directory containing a NoScheme module, `conversion.json`, and
`footprints.json`. It can select symbols or convert the entire library. Footprint
roots and extra footprints are explicit command arguments. Existing conversion
directories are never overwritten; specializations belong in separate source files.

Conversion preserves physical pin text, folds repeated representations of one
physical pin, follows symbol inheritance, and retains raw pin types and source
properties under `custom`. Only unambiguous aliases are emitted. Unsupported
electrical types are reported without guessing a role. KiCad symbols marked
off-board are reported and omitted. Identifier normalization is recorded and
collisions are errors. Footprint records retain hashes, numbered pads, duplicates,
and unnumbered pad counts. Mismatches are reported for correction.

```sh
uv run --locked noscheme convert-library /usr/share/kicad/symbols/Device.kicad_sym \
  --output build/converted-device --symbol R --symbol LED \
  --footprint-root /usr/share/kicad/footprints \
  --extra-footprint Resistor_SMD:R_0603_1608Metric \
  --extra-footprint LED_SMD:LED_0603_1608Metric \
  --extra-footprint Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical
uv run --locked noscheme export-kicad --project examples/blink \
  --footprints build/converted-device/footprints.json
```

These commands were exercised against the installed KiCad libraries. The example
exports five parts and four nets without omitted footprints. Its hand-written
part definitions use the converter's footprint snapshots; the converter also
emits reusable R and LED definitions. The generated `board.net` follows the
version E structure demonstrated in the feasibility trials. Importing this
production-generated artifact in PCBNew remains a manual validation step.

`noscheme.state.json` is created beside the configuration and should be version
controlled. It stores a project UUID namespace, stable per-identity UUIDs,
references and historical reference reservations, and the previous exported
design for change reporting. Explicit references are reserved before automatic
allocation. Deleted entries remain reserved. An explicit cleanup command is
still to be implemented; export never reclaims reservations automatically.

Export validates only converted footprint records, never live CAD libraries.
Assigned but unknown/mismatched footprints block export. Unspecified footprints
remain in state and reports but are omitted from the netlist. All numbered pads
currently require matching physical pin IDs; mechanical exceptions need explicit
modeling before support can be broadened.

Outputs include `board.net`, reversible `names.json`, `netclasses.json`,
`nets.txt`/`nets.json`, `changes.txt`, and `export.json` (structured changes and
omitted parts). Export reports `erc_performed: true`; the current `pending`
list is empty, without implying complete electrical or hardware validation.
All files are staged before publication; the atomic state replacement is last.
Ordinary replacement failures roll back earlier outputs, covered by an injected
I/O-failure test. Abrupt process/machine failure is not an all-files transaction.

`update-kicad-classes INPUT.kicad_pro --memberships netclasses.json --output NEW.kicad_pro`
merges memberships into a new project copy, rejects undefined classes, and
preserves class definitions and other project data. It never edits the input.

Format reference: [KiCad S-expression documentation](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/).
KiCad `no_connect` pins now convert to `role.do_not_connect`: omission or an
explicit `no_connect` binding is allowed; assigning any net is an ERC error.

### ERC compatibility and public contracts

The role/net-type table is in [LRM §9](LRM.md#9-electrical-roles-and-net-types). Every scalar declaration keeps
its type constraints; missing/alternative roles are never silently selected.
Public-port checks inspect only endpoints inside the block instance, enforce
caller-side unused/required policies, and retain definition and binding locations.
Explicit external endpoints count once, while ordinary ports add no endpoints.
Only an outermost unchecked declaration can disable net-wide driver/current checks.

Regression cases cover mixed domains, fixed physical inputs on rails, open-drain
versus active drivers, incompatible selectable roles, nested output contracts,
caller drivers that cannot satisfy internal promises, array/bundle boundaries,
external endpoints, and waiver interaction. Compatibility and role diagnostics
remain nonblocking for export; required connections cannot be waived.

### Named ERC configurations

Boards and blocks expose public choices under `metadata.erc_choices`; pin role
annotations and child-choice bindings may use exhaustive `choice`/`cases`
mappings. Boards assign their own choices in `metadata.configurations`. Choice
references remain lexical, and nested blocks forward them explicitly. No grammar
extension or private-instance access is needed. Invalid mappings/values are source
errors; omitted selections retain uncertainty rather than choosing a role.

All named configurations run by default. `--erc-configuration NAME` may be repeated
on check/compile/export to select a subset. Reports retain per-configuration
counts, selections, and names on active and waived diagnostics. Electrical errors
remain nonblocking for export; invalid selections preserve previous artifacts.

`examples/power_modes` compiles three headers and two nets. USB-powered and
header-powered assumptions use the same parts, connectivity, netlist bytes,
reference assignments, and identity state. Tests also cover arrayed sources,
nested forwarding, missing selections, private-path rejection, permitted-role
constraints, per-configuration waivers, and CLI selection/failure behavior.

```sh
uv run --locked noscheme check --project examples/power_modes
uv run --locked noscheme compile --project examples/power_modes --erc-configuration usb_powered
```

### ERC: grouped component current loads

Implemented `metadata.current_loads` with named physical-pin groups and explicit
maximum load in mA. Aliases resolve to physical pads; groups cannot overlap or
mix individual and grouped demands. Every member must connect to the same net.
The checker counts each group once per instance, checks effective roles per ERC
configuration, and reports split/disconnected groups with member locations.
Metadata subclasses can override a group amount without repeating membership.

Tests cover arrays, aliases, inheritance, separate supply groups, ordinary loads,
missing capacity, invalid metadata, disconnected/split groups, external roles,
and named configurations.

Block supply ports now support `aggregate_load.max_load_current_mA`. Descendant
loads on the same net are checked against that aggregate before their demands
are replaced for parent accounting. Nested blocks are processed from the inside
outward; internal overloads and incomplete budgets remain visible. Same-scope
overlapping boundaries and internal same-net sources are rejected. Tests cover
nested/arrayed blocks, grouped physical loads, inheritance, named configurations,
unchecked nets, source locations, and compile with electrical errors.
### ERC: opt-in electrical budgets and extensions

Implemented a project-local registry for metadata schemas and net checks. Optional
`erc_modules` in TOML loads Python files exposing `register(registry)`. Built-in
pull-resistance and capacitance checks use this API, activate only on declared
net/port limits, and report unknown contributions without inventing zero loads.
Pin bias annotations follow physical connections; open-source roles mirror
open-drain roles. The STM32 draft now explicitly annotates its existing OLED,
button, and BOOT0 bias resistors; bus budget limits await hardware selection.

Callbacks now receive shared immutable circuit/net/endpoint views per configuration (see the unified ERC implementation below).
Registered checks support source-located diagnostics and scoped waivers; plugin
failures block publication. Tests cover applicability, parallel pulls, incomplete
budgets, hierarchy, configuration context, schema validation, callback isolation,
and preservation of previous artifacts on failure.

```sh
uv run --locked noscheme check --project examples/erc_extensions
```

Tolerance modeling remains deferred. Numeric parameter expressions and explicit
voltage ranges are now implemented, and named `checks` applications supersede
the original budget activation fields; see LRM sections 8, 10, and 12. The example
uses simplified physical pin models.

### ERC: implemented first pass

`check`, `compile`, and `export-kicad` run physical pin policies, known driver
conflicts, digital/power driver requirements, and simple per-net current budgets.
Source-located diagnostics retain instance paths and related driver locations.
Physical endpoints are counted once across hierarchy; connector external roles
replace the contact role for driver/load accounting. No component traversal or
inference of pull-up behavior is performed.

`compile` and `export-kicad` publish `erc.txt` and `erc.json` alongside their other
outputs. Electrical errors do not block these commands or persistent export state.
`check` returns 1 when electrical errors exist. Reports enumerate implemented and
pending checks; the circuit now records `erc_performed: true` after checking.
The elaboration-only Python API still returns `erc_performed: false`.

Role/net-type compatibility and public-port contracts are now implemented.
Named ERC configurations and hierarchical aggregate budgets are implemented;
tri-state capability checking is implemented. Explicit digital-input ports may tie to power/ground rails while retaining
their internal input contracts and the rail checks.
Waivers now require the outermost declaration of the flattened net. Inner-port
waivers produce a scope error and cannot suppress checks on caller circuitry.
Waivers name implemented net checks and require literal reasons; pin constraints
cannot be waived. Text/JSON reports retain reasons and suppressed diagnostics.

The STM32 draft additionally has two arrayed active-low buttons and 0603 pull-ups,
and a two-pin 0.1-inch BOOT0 header with a pull-down and jumper to 3.3 V.
10 kOhm bias values are provisional; switch footprints, real MCU GPIO/BOOT0 pad
mapping, and boot configuration still need verification.

---

## Historical feasibility milestone (completed)

## Objective and boundaries

Prove the KiCad update path and grammar before building a production compiler.
This completed milestone used manual import in PCBNew and the design decisions
then recorded in PROJECT.md. The STM32 board was the later acceptance target.
The experiments are historical evidence; [LRM.md](LRM.md) now specifies production
behavior, including decisions made after these experiments.

## Deliverables

1. Isolated Python environment with pinned Lark, pytest, and sexpdata dependencies.
2. A grammar-driven parser experiment with source spans and positive/negative
   fixtures, including the complete STM32 syntax draft.
3. Deterministic KiCad netlists for a five-part circuit and controlled mutations.
4. Saved-board inspection, semantic comparisons, and manual editor instructions.
5. Recorded versions, test output, findings, and pass/fail/pending status for each
   acceptance check. Generated fixtures alone do not prove editor integration.

## KiCad experiment

Use a two-pin header, two 0603 resistors, and two 0603 LEDs. Generate baseline,
reordered, added/removed part, changed connection, identity-preserving rename,
reference-change, and invalid-footprint/pin variants from explicit Python data.
Use stable experimental identities and UUIDs; this is not yet the production IR.

Import the baseline, place and route a branch, save it, and run each mutation
against a fresh baseline copy. The user performs editor operations; scripts inspect
saved results. Compare footprint identity, placement, rotation, layer, pad nets,
and unaffected track geometry. Exercise CAD-side reannotation as a separate case.

Also test source-like names (#, hierarchy, indices), differential-pair naming,
class membership without losing KiCad-owned class settings, and CLI DRC with and
without schematic parity. Record actual import options and reports. Never use
whole-file equality as the layout-preservation test.

Acceptance: no duplicates on unchanged/reordered import; compatible layout
survives; connectivity changes as requested; explicit identity survives source
renaming and reference changes; class settings survive membership updates.
Record limitations and reproducible failures rather than assume success.

## Parser experiment

Use Lark LALR with contextual lexing, configurable indentation, and a grammar for
comment recognition. Distinguish instance parentheses from expression delimiters.
Parse only: no name resolution, evaluation, expansion, ERC, or export.

Cover definitions, imports, defaults/arguments, numeric/string pin identifiers,
aliases, # names, arrays, bundles, indexed bindings, ranges/slices, metadata,
no_connect, placeholders, numeric expressions, and helpers. Test comments, blank
lines, nested/multiline expressions, closing delimiters, malformed indentation,
missing delimiters, bad bindings, and unterminated strings/comments.

Acceptance: reference draft and positive corpus parse; malformed cases fail at
useful source locations; token spelling and filename/line/column spans survive.
Any experiment-only syntax choices must be identified in the findings.

## Completion and later milestones

Close with findings describing observed behavior, exact import settings, required
customization, remaining failures, and recommended next steps. Manual checks stay
pending until performed on saved boards. Keep experiments separate from production.

Then plan these milestones in detail using the results:

1. Compiler core: grammar, source-located AST, imports, resolution, specialization,
   hierarchy, arrays, and a CAD-independent circuit representation.
2. Library conversion and export: symbols/footprint records, persistent identity
   and reference mapping, and the proven KiCad adapter.
3. ERC and reports: metadata/compatibility tables, waivers, public ERC choices,
   current budgets, and text/JSON reports.
4. Reference board: verified MCU pinout, support parts, and full layout update test.
5. Editor tooling: basic highlighting with the first usable compiler, then LSP
   and canonical formatting.

### Footprint selection hints (implemented)

Library conversion saves filter matches with matching physical pin/pad IDs in
component custom metadata and the footprint snapshot. LSP completion offers
defaults and inherited candidates inside footprint strings, including unfinished
buffers, without reading installed KiCad libraries. Tests cover filtering,
offline completion, inheritance, replacement ranges, and the stdio protocol.

### Qualified imports and aliases (implemented)

Added module imports and per-name aliases without changing definition identity.
Compiler resolution, builtin aliases, bundle types, LSP navigation/completion,
and Vim highlighting support the forms documented in the LRM. The reference
board now uses `import Device` and `Resistor extends Device.R`.

Inheritance completion now recovers unfinished `extends` headers, filters by
component/block kind, supports aliases and qualified names, and excludes the
definition being edited. Unit and stdio tests cover missing colons/bodies and
replacement ranges.


### Bundle-member editor support (implemented)

Completion and definition/hover navigation resolve public bundle members on both
sides of connections, including nested bundles, indexed/sliced arrays, inherited
ports, explicit globals, imported aliases/qualified types, and unsaved sources.
Incomplete connection recovery preserves local nets and public interfaces while
omitting instance bodies in a private analysis snapshot. Shared lookup uses
compiler-resolved bundle interfaces and does not expose private instance nets.
Unit and stdio tests cover replacement ranges, imported declaration locations,
and incomplete buffers. The STM32 SWD interface was checked directly.


### Find references (implemented)

The LSP references endpoint groups source occurrences by resolved identity,
honors declaration inclusion, deduplicates source locations, and follows imports,
aliases, inherited interfaces, parameters, globals, and bundle fields across the
selected project's reachable graph. Results use current unsaved buffers and
negotiated position encoding. Tests cover scope separation, physical pin ranges,
metadata expressions, declaration queries, cross-file protocol requests,
unsaved edits, and UTF-16 locations. Vim exposes this as `:LspReferences`.


### Rename (implemented first pass)

Prepare-rename and rename return source edits for definitions, nets, parameters,
public block ports, and bundle fields. Named import aliases have separate naming
identities, including re-exports; qualified references retain their prefixes.
The proposed source snapshot must parse and resolve before edits are returned.
Tests cover collisions, lexical scope, alias semantics, inherited ports,
unsaved buffers, UTF-16 ranges, and versioned workspace edits. Physical pin
IDs/aliases, internal instance names, board entry points, and module/file names
remain excluded. Board-level instance rename now preserves identities as described
in the follow-up below.
The server performs no file or artifact writes.


### Rich hover details (implemented)

Resolved definition and interface hovers show parameter defaults, required
values, inherited core metadata with source provenance, roles, pin policies,
and summarized custom metadata. Instance hovers use existing elaborated circuit
records for actual values/footprints/parameters, including arrays and reused
declarations. Binding hovers show effective physical pin roles and connected
nets; net hovers include endpoint counts. Failed elaboration is explicitly
distinguished from evaluated instance data. Tests cover inherited and unsaved
metadata, overrides, role choices, bounded candidate summaries, evaluation
failures, the STM32 indicator block, and protocol refresh after unsaved edits.
Hover negotiates Markdown or plain text with the client, formats headings and
lists, and provides import-aware help for all role, nettype, and unused members.
Builtin help does not create definition, reference, or rename targets.

### Supply/ground distinction and targeted waivers (implemented)

Power-input pins no longer satisfy ground-net or ground-port constraints. KiCad
`power_in` conversion preserves supply/return ambiguity as a role list; authors
select the intended role. Net waivers can name exact physical pin paths, resolved
relative to their declaring scope, with invalid targets reported explicitly.
Regression tests cover isolated exceptions, unrelated grounded supplies, arrays,
hierarchy, unselected roles, and text/JSON reporting. The STM32 MCU snapshot uses
the updated conversion and its board specialization retains explicit roles.

### Review: reserved names, reference warnings, and ERC execution (implemented)

The six magic names `no_connect`, `global`, `true`, `false`, `number`, and `string`
are rejected in declarations and import bindings, while quoted physical pin IDs
remain supported. Conversion and LSP rename use the same reserved-name set.
Leading `#` in assigned KiCad references is a warning, retained in the export
report and CLI diagnostics without blocking publication.

CLI/LSP analysis and export reuse the project's cached ERC registry. Export runs
ERC once and reuses that report for stderr and artifacts. Instrumented extension
tests verify one load, registration, and callback per net per configuration in
each operation, with fresh registry loading on subsequent operations.

### Independent resolution diagnostics (implemented, limited scope)

After interfaces resolve, body checking collects independent failures across
definitions, instances, parameter expressions, arguments, and bindings. A failed
binding stops that binding; an unknown instance type skips that instance's
dependent checks. Shared inherited bodies do not repeat identical diagnostics.
Any collected structural errors still block evaluation, expansion, and export.
Parsing, interface construction, and elaboration remain fail-fast; this pass
does not introduce parser recovery or partial circuit state.

### Physical pin lists (implemented)

Explicit lists such as `[1, 3, VDD]`, bracketed ranges `[1..3]`, and mixtures
`[1..3, 7]` work for component declarations/specializations, connections, and
instance pin metadata. Existing shape and overlap rules apply, including alias
overlap; named pin groups remain deferred. LSP references retain individual
selection spans and completion supports unfinished connection lists. The STM32
example groups its power/ground declarations and shared/unused bindings, with
the elaborated circuit verified unchanged apart from source locations.

### Example concision and defaults (implemented)

The STM32 example groups identical GPIO roles, connector pin declarations,
shared connections, and instance pin metadata using physical pin lists/ranges.
Redundant `unused.explicit` annotations are omitted in handwritten components
and converter output; the committed Device and MCU modules were regenerated.
The normalized circuit, including effective pin policies and ERC results, is
unchanged. Existing parameter defaults, specialization, and pin lists cover
most repetition here; component-wide `pin_defaults` remain deferred pending
a stronger use case. Unknown electrical roles remain unspecified.

## TODO: remaining implementation-review follow-up

Recorded from the October 2026 implementation review and subsequent discussion.
The agreed review fixes are implemented. All remaining follow-up items are
deferred by user decision as of October 5, 2026, except the larger-design
performance work explicitly resumed in the recent completed work above.
Deferred proposals should not be treated as implementation commitments.

### Deferred work

Revisit these only when the user chooses to resume them:

- Further diagnostic collection and recovery during parsing, evaluation, and
  elaboration.
- Further plugin-heavy performance workloads and interactive LSP latency checks.
  WREN and a scaled active two-configuration workload have now been measured;
  caching across edits is a proposed follow-up, while incremental parsing remains
  unimplemented.
- Footprint candidate sidecars and their storage/discovery convention.
- Manual KiCad inspection of the practical benefit of exported pin names/types.
- Voltage-domain and logic-level compatibility checks beyond existing explicit
  rail ranges and pin limits.
- Static typing of the ERC API and a broader type-checking rollout.
- Identity-preserving internal instance renames and moves between scopes.
- Differential-pair support.
- Physical quantity types and further syntax extensions, including index-based
  array expressions, named pin groups, pin defaults, and additional operators.

### Values and language usability

- [x] Fix float noise in `f.*` part-value strings. Doubles render to six
  significant digits without exponent notation; integers remain exact. Fixed
  suffixes, arithmetic, and `.format()` behavior are unchanged. Regression tests
  cover rounding, tiny/large numbers, negative zero, and output limits.
- [x] Allow numeric parameter expressions in numeric ERC fields, including
  nested extension schemas. Values and quantities can share a parameter without
  units or quantity syntax; independent explicit metadata overrides remain possible.
- [x] Add explicit `elements:` overrides for parameters and metadata. Full
  literal multidimensional indices are supported; index variables and per-element
  identity/ref overrides are deferred. Existing STM32 identities remain unchanged.
- [x] Keep current role-selection syntax (user decision). No compact role
  annotations or inference from KiCad bidirectional pins.
- [x] Use unambiguous MCU pin aliases in the STM32 example, retaining physical
  pad lists where appropriate for shared supplies and ambiguous aliases.
- [x] Keep explicit pin lists/ranges; no example currently requires named
  groups. Revisit groups and component-wide `pin_defaults` only with a concrete
  case that existing lists and specialization cannot express clearly.
- [x] Assess numeric helper demand: current examples and explicit element
  overrides do not require new operators. Keep `num.floor`/`num.int` and existing
  arithmetic; defer modulo/min/max until a concrete use case. `//` remains a comment.
- [x] Keep current array-dimension placement, choice references, and params
  naming (user decision). Revisit only with a concrete benefit.

### Documentation and diagnostics

- [x] Fix stale claims about the AST, tri-state ERC, and LSP diagnostics,
  completion, references, rename, hover, and bundle-member navigation.
- [x] Make the LRM authoritative for behavior; reduce PROJECT.md to goals,
  rationale, hardware decisions, and links. Keep language tables in the LRM.
- [x] Generate LRM role and metadata tables from the implementation, with a
  regeneration command and drift test.
- [x] Document pure-wiring adapter limitations and the intended `unused.always`
  idiom for intentionally lone endpoints. Clarify the historical review's scope.
- [x] Replace internal parser token names with readable syntax diagnostics.
  Keep broader parser recovery deferred; independent resolution diagnostics
  are already implemented.
- [x] Add opt-in unused-parameter lint as `noscheme lint`; default compiler
  and LSP diagnostics are unchanged.
- [x] Document experimental 0.1 revision pinning and an explicit future freeze;
  breaking language changes after the freeze require a new language version.

### KiCad conversion, export, and refactoring

- [x] Map `open_emitter` to `open_source`. Keep `free` and `unspecified`
  unmapped with conversion issues; neither safely asserts a known role.
- [x] Measure footprint candidate source size and assess sidecars (below).
  Keep self-contained metadata until a discovery/versioning contract is agreed.
- [x] Preserve useful aliases/roles in exported libparts with consensus for
  differing instance selections.
- Deferred: manually verify the practical benefit of pin names/types in PCB inspection.
- [x] Make `changes.txt` describe changed values, footprints, connections,
  references, and paths.
- [x] Add advisory probable-rename hints for unique exact footprint/value/net
  matches. Never transfer identities or PCB placement based on a heuristic.
- [x] Implement identity-preserving board-level instance rename (revised user
  decision). Keep the broader reusable-block identity model deferred; no
  `local_identity` field has been added.

### Further ERC capabilities and example clarity

- [x] Implement opt-in explicit net voltage intervals and absolute pin limits
  through the registry. Supply-domain and logic-threshold inference remain deferred.
- [x] Add opt-in `bias_reference_pin` checks against actual power/ground nets.
  Use a registered pin rule selected by the standard rule set; annotate STM32 pulls.
- [x] Defer differential-pair support (user decision); the metadata remains
  explicitly unsupported.
- [x] Clarify the one-pin pull-up in the extension example and point readers to
  the two-terminal pattern. Explain or annotate the microSD DAT pull-ups.
- [x] Retain the agreed direct input-to-rail behavior; any proposal for an
  additional strap marker needs a concrete use case and a separate decision.

### Implementation maintenance

- [x] Freeze the historical experiment grammar and test only its retained
  corpus. Current examples and language features use production parser tests.
- [x] Assess typed views; defer whole-circuit wrapping. New voltage checks live
  in their own check-family module, preserving the JSON interchange representation.
- [x] Measure circuit-copying and LSP analysis costs; retain isolation and full
  analysis for now. Repeatable benchmark added (see assessment below).
- [x] Enable clean UP/SIM and selected bug-prevention Ruff rules; add branch
  coverage reports in CI. Broader B rules and type-checking rollout need a
  focused follow-up rather than blanket suppression of existing diagnostics.

### Rulos six-line matrix example (implemented)

`examples/rulos_6matrix/` transcribes Jeremy Elson's Rev B schematic and Eagle
board with attribution and pinned source evidence. Four two-column block
instances contain arrays of six digits; bundles share row buses and sliced
connections form the driver chain. All 93 parts and 107 nets are compared
against independently extracted Eagle connectivity, including the exported
KiCad netlist. Eighteen original packages are imported into a local footprint
library. Missing source values and the deliberately limited passive-net ERC
model are documented in the example README. Manual PCB Editor validation and
stronger electrical modeling remain future work.

The matrix example now derives compatible parts from selected KiCad snapshots
and changes 35 instances to standard footprints. Resistors use 0603; capacitors
through 1 µF use 0603 and above 1 µF through 10 µF use 0805. Larger/unspecified
capacitors retain their source packages. SWD uses the STM32 example's SMT Cortex
header footprint. The original source evidence and an explicit substitution map
verify unchanged connectivity, values, and identities. Unresolved package
choices are documented in the example's `KICAD_MATCHES.md`.

### Historical review maintenance assessment (before unified ERC)

Initial five-repeat medians on the machine used at that checkpoint, before
the new reference checks:

| Example | Resolve/elaborate | Circuit deepcopy | ERC | Full LSP analysis |
|---|---:|---:|---:|---:|
| STM32, 58 parts / 52 nets | 172 ms | 17 ms | 38 ms | 255 ms |
| Rulos, 93 parts / 107 nets | 187 ms | 30 ms | 36 ms | 262 ms |

These examples did not stress many active extension callbacks. At that time,
callbacks copied their contexts for isolation. The unified ERC engine below
superseded that design with shared recursively immutable views. The benchmark in
`tools/benchmark_analysis.py` makes follow-up measurements repeatable.

Footprint candidate lines account for 24,243 of 41,992 generated source bytes in
the STM32 libraries and 71,686 of 89,504 in Rulos. A sidecar would reduce source
noise, but would require a discovery/versioning contract for conversion, compiler,
and LSP. Current measured analysis times do not require the migration. Keep the
self-contained metadata until that contract is agreed.

Broad Ruff B/UP/SIM evaluation found 29 B diagnostics (mainly loop closures and
explicit zip lengths); UP and SIM were clean. Enable UP/SIM plus checks for mutable
defaults, calls in defaults, unused comparisons, and unused expressions. Leave a
full B-rule rollout and gradual static typing for dedicated work. Introducing
wrappers around the entire circuit solely to satisfy a type checker would be a
larger change than these fixes; start with typed helper boundaries when a concrete
ERC change needs them. Budget checks already have a separate module; further
splitting should follow a check family change so behavior stays reviewable.

Exported libparts now retain aliases and consistent KiCad pin types, with tests
for conflicting instance selections. Manual PCB Editor inspection is still needed
to establish the visible benefit; no placement or connectivity change is intended.

After adding the STM32 reference-terminal annotations, repeated full-circuit copies
inside compiler-owned checks increased local ERC time to roughly 510 ms and LSP
analysis to 674 ms. Compiler-owned callbacks now use the circuit directly under
the same read-only contract as core ERC; project callbacks still receive isolated
copies. Five-repeat medians after this change were 38 ms ERC and 261 ms LSP
analysis. Tests verify both built-in nonmutation and project-plugin isolation.

### Unified ERC implementation

The user reopened ERC work after the review batch and approved a common plugin
architecture for built-in and project rules. Implemented:

- [x] Registry APIs for net rules, pin rules, predicates, reducers, rule sets,
  and descriptive fields; qualified names and duplicate detection.
- [x] Named `checks` applications on nets, public ports, physical pins, and
  instance overlays, with typed parameters and per-application missing-data policy.
- [x] Same-name specialization/overlay merging with checker changes rejected.
- [x] `erc_rule_sets` configuration with replaceable `std.default`; structural
  compiler validation remains independent.
- [x] Shared recursively immutable graph and parameter views for every callback.
  Built-in electrical policies moved out of the scheduler into registered rules.
- [x] Sum, parallel resistance, count, any, and all assertions; three-valued
  predicates and conservative interval/optional-contributor handling.
- [x] Application identities in text/JSON diagnostics and scoped waivers.
- [x] Direct migration of budget annotations, examples, tests, and extension API;
  retired fields produce actionable diagnostics.
- [x] LSP completion for rule/predicate/reducer strings and rule parameters,
  including unfinished quotes; documentation and regression coverage.

Earlier copy/isolation timing notes above describe the superseded implementation.
The shared-view engine removes per-callback copies for both built-in and project
rules. Differential pairs and internal instance refactoring remain deferred. Manual PCB
Editor inspection of exported pin names/types is still outstanding.

Validation of the unified ERC implementation: **799 tests passed, 1 skipped**;
branch-aware coverage is 89%. Ruff lint/format, generated reference tables,
local documentation links, and both ERC example checks pass. STM32 and Rulos
regression tests verify physical connectivity, values, footprints, and repeatable
export identities.

Five-repeat local medians after sharing frozen subgraphs and contexts:

| Example | ERC | Full LSP analysis |
|---|---:|---:|
| STM32, 58 parts / 52 nets | 106 ms | 350 ms |
| Rulos, 93 parts / 107 nets | 115 ms | 365 ms |

These are observations, not CI thresholds. Rich views and individually scheduled
applications add overhead versus the earlier core-only implementation; sharing
subgraphs reduced the first migrated STM32 measurement from about 195 ms to
106 ms. Each configuration has its own effective view. Subsequent WREN work removed
full configuration copies and shares unchanged frozen data across configurations.


### Standard ERC organization and board-level identity refactoring

- Split standard ERC into connection policies, public port contracts, endpoint
  compatibility, driver rules, and current budgets. Each family runs lazily when
  selected; shared immutable facts and grouped-load calculations are cached per
  configuration. Registry names, application names, diagnostics, and waivers remain
  unchanged. Selecting a driver rule no longer computes unrelated current/port checks.
- Implement board-level component/block instance rename using existing absolute
  `identity` metadata. Insert the old name only when needed; preserve existing
  explicit identities, array suffixes, and descendant identities.
- Update semantic waiver paths during rename. Validate proposed source and
  physical identities/properties/net membership in memory, without changing files,
  export state, or artifacts. Export regression coverage verifies stable UUIDs and
  reference designators.
- Keep internal instance rename and moves between scopes deferred, as requested.
  The proposed `local_identity` model was not adopted.

Validation: **814 tests passed, 1 skipped**, with 90% branch-aware coverage.
Ruff lint/format, reference-table drift, local documentation links, and diff
whitespace checks pass. Tests exercise family isolation/caching, rename edits
through the real LSP protocol, arrays and block descendants, repeated renames,
waiver paths, and an export round trip with stable UUIDs/reference designators.

### ERC language: concise, extensible checks

Goal (user direction): ERC is a set of static assertions between nets and pins.
It should be extensible with basic language support, and attaching an assertion
should be concise, without growing the language. Planned in three steps.

Step 1, implemented:

- [x] Each `checks` entry is one call to an imported rule, using the existing
  expression grammar: `load: std.sum(capacitance_pF, net=wiring_capacitance_pF, max=400)`.
  The multi-line `checker`/`params`/`missing` form is removed; it now reports
  a migration diagnostic.
- [x] Rules are imported names. `from noscheme import std` exposes the standard
  namespace; a project ERC namespace imports like a module (`import demo` or
  `from demo import pin_count`). An ERC namespace that collides with a source
  module is an ambiguous import.
- [x] The rule's declared parameter types decide how each argument is read. New
  argument kinds are `field` (a bare endpoint field name), `net_fields`, and
  `selector`. Field names are checked against registered fields and are never
  evaluated as expressions.
- [x] A single selector argument replaces `where` and `predicate`. Each term is
  a predicate, a `role` or `bias` value, or a boolean field name.
- [x] Every reducer is also a net rule with the same name (`std.sum`,
  `std.parallel`), which replaces `std.reduce`. `std.count`, `std.any`, and `std.all`
  take selectors.
- [x] Overlays merge arguments by name, and changing the rule under an
  inherited name is still rejected.
- [x] LSP: completion of rule names, `name=` keywords, and typed argument values
  (including across lines); rule hover; field-name arguments are excluded from
  references and rename.

Step 2, implemented:

- [x] `nettype Name extends base:` definitions whose bodies hold net metadata,
  checks included. Every chain ends at a builtin `nettype` value, which keeps role
  compatibility and the standard policies. Net types work on nets, ports, and
  bundle fields; metadata merges like specialization, so a declaration can
  override one argument of an inherited check.
- [x] Identical applications that reach one flattened net through several
  declarations of the same type (for example, a board net and a block port) run
  once, on the outermost declaration.
- [x] Decision on `unchecked`: an outer `nettype.unchecked` skips only the
  standard policies, as before. Checks from inner port types still run, consistent
  with the existing rule that explicit applications always run.
- [x] LSP navigation, hover (base and electrical type), references, rename, and
  completion of `extends`; Vim and VS Code highlighting.
- [x] STM32 example: an `I2CLine` type checks both I2C buses' pull-ups. The MCU
  and header I2C pins declare no pull of their own.
- Not done, deliberately: re-expressing the builtin net types and the 23 standard
  check names as library declarations. The standard policies are net-wide: they
  combine the types of every declaration on a flattened net (role compatibility is
  intersected, driver requirements are united) and run once per net. Attaching them
  to each declaration would duplicate or change diagnostics and rename the
  application names that existing waivers use. They remain the `std.default` rule
  set, which is replaceable.

Step 3, implemented:

- [x] A waiver names applications by name anywhere on its flattened net,
  including inner port, net type, and pin applications; `pins:` restricts it.
  Quoted identity keys such as `"pin:pull.2:bias_reference"` are rejected with
  guidance. A waiver that matches no waivable application reports
  `erc.waiver_target` instead of failing as a plugin error.
- [x] Nets and pins use one voltage field pair, `min_voltage_V`/`max_voltage_V`;
  the former net names are rejected with guidance.
- [x] No check is triggered by a field. `std.voltage_limits` runs on every checked
  net; it is silent without data. The `bias_reference_pin` field became the
  argument of an explicit pin check, `reference: std.bias_reference("1")`.

### Numbers with units

User direction: SI prefixes on literals, physical quantities for parameters and
fields, no scale in field names, and no formatting helpers. Dimensional algebra
was considered and declined as too much type machinery for now.

- [x] Literals take SI prefixes `a f p n u m k M G T P`, or use the prefix (or `R`)
  as the decimal mark: `4.7k`, `4k7`, `100n`, `4R7`. Values are exact in decimal;
  integral results keep the integer tag. One decimal mark only, and no unit text.
- [x] Quantity parameter types `voltage`, `current`, `resistance`, `capacitance`,
  `inductance`, `frequency`, `time`, and `power` hold base SI values. A numeric
  part value displays with an engineering prefix and unit, as `4.7kOhm`, in reports
  and the netlist. A quantity also accepts a verbatim string (`"10k 1%"`, `""`),
  which keeps the Rulos example's source values exact.
- [x] The `f` helpers are removed; importing `f` names the replacement.
- [x] Electrical fields hold base SI values and declare quantity kinds:
  `capacitance`, `wiring_capacitance`, `pull_resistance`, `max_load_current`,
  `max_source_current`, `min_voltage`, `max_voltage`. Every former unit-suffixed
  name is rejected with its replacement. Extensions cannot register field names
  ending in a unit. ERC messages show limits with units.
- [x] The KiCad converter types two-terminal `R`, `C`, and `L` symbols' values as
  quantities. The STM32 and Rulos `Device` snapshots were regenerated; only those
  parameter lines changed, and all 58 STM32 netlist values are unchanged.

### Review follow-up (numbers and net types)

- [x] Per-net deduplication of identical applications is limited to rules
  registered `net_only=True`. Owner-dependent custom rules keep one application per
  declaration; a regression test reproduces the review's port-limit case.
- [x] A displayed number has a unit only when its expression is a bare quantity
  parameter, resolved in the scope where it is written (the caller, for instance and
  element overrides). `value / 1k` now displays `4.7`, not `4.7Ohm`.
- [ ] Open trade-off: a waiver name matches applications anywhere on its net, so
  a common name such as `limit` can waive unrelated rules from different libraries.

### Pin metadata shorthand and instance roles

- [x] A pin entry may list builtin values whose namespace names the field:
  `3: [role.open_drain, bias.none]`, `1..2: [role.passive, unused.never]`, combined
  with indented fields when needed. Works in definitions, specializations, and
  instance overlays; follows import aliases.
- [x] One instance-level `role`: on a component pin declaring no role or only
  `passive` (a contact), it describes the attached equipment and elaborates to
  the `external_role` attribute; elsewhere it selects a declared role.
  `external_role` remains only for block ports' modeled external endpoints (a
  block port's `role` is its contract, so the two meanings stay distinct there).
- [x] Example sources migrated mechanically; elaborated pin and port roles, biases,
  and unused policies are identical to the previous commit for every example.
- [x] Removed migration diagnostics for retired fields and the `f` import (private
  single-developer repository: breaking changes need no compatibility messages).
  Old names are now ordinary unknown-field or unknown-import errors.
