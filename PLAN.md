# NoScheme implementation plan

## Current milestone: initial ERC and reporting

Status: compiler-core steps 1–5 are implemented for the documented initial
metadata schema. Explicit library conversion and the first persistent KiCad
exporter are implemented. Initial physical-pin ERC and text/JSON reporting are
implemented; advanced ERC and production-output GUI validation remain.
The combined suite has 358 passing tests.
The feasibility milestone below is complete;
results and limitations are in [experiments/FINDINGS.md](experiments/FINDINGS.md).
PROJECT.md remains the language authority. Proposed policies below resolve open
items for implementation; they are not retroactive changes to agreed semantics.

### Outcome

Compile a small, complete `.nosch` project into a deterministic, CAD-independent
circuit representation, with source-located diagnostics. The acceptance design
is the same five-part LED circuit used in the KiCad experiments, expressed through
reusable blocks and explicit physical component definitions.

Keep the experiments available as evidence. Production code lives under
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
  structure. Say explicitly that electrical checking is not yet implemented.
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
uv run --locked noscheme parse examples/stm32h523.nosch --output build/stm32h523.ast.json
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

Only the initial schema documented in PROJECT.md is implemented. Advanced ERC
metadata remains explicitly unsupported. Required argument/type evaluation,
shape and binding-overlap checks, global existence in expanded blocks, structural
port reachability, and mandatory-connection ERC belong to subsequent steps.

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

Numeric operations and rounding implement PROJECT.md's rules. Named arguments
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

### Proposed policies for currently open language details

Use these as the starting decisions for review before implementing the affected
steps. Spaces-only indentation is implemented in step 1. String literals use
double quotes and JSON-style escapes; invalid or unpaired surrogate escapes
produce diagnostics. Numeric and formatting policies are implemented in step 3;
the remaining array-shape policies belong to elaboration.

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

The implementation must settle exact metadata keys, formatting bounds/rendering,
and diagnostic identifiers in the corresponding step, with examples and tests.
Do not claim the draft grammar's generic field acceptance implements those schemas.

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
omitted parts). Export now reports `erc_performed: true` with an explicit list of pending checks.
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

The initial role/net-type table is in PROJECT.md. Every scalar declaration keeps
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
Named ERC configurations are implemented; bias/tristate models and aggregate
budgets remain pending. Explicit digital-input ports may tie to power/ground rails while retaining
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

# Completed feasibility milestone

## Objective and boundaries

Prove the KiCad update path and grammar before building a production compiler.
Manual import in PCBNew is acceptable. PROJECT.md remains authoritative; these
experiments must not silently change language semantics or select an untested
fallback backend. The STM32 board remains the eventual acceptance target.

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
