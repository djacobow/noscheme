# Native compiler and LSP migration

This directory contains the C++23 compiler core, a compiler comparison probe,
and an experimental LSP with project/compiler/ERC diagnostics. The Python compiler and editor
server remain the supported tools.

## Parser choice

We use [ANTLR 4](https://www.antlr.org/) for both lexical recognition and parsing.
The generator and C++ runtime are pinned to **4.13.2**. The runtime has no Java
dependency; Java is used only to generate the C++ source during builds.

`grammar/NoScheme.g4` translates the current Lark grammar. ANTLR cannot directly
read `src/noscheme/noscheme.lark`. During migration, Python/Lark remains the
compatibility reference, and grammar changes must update both descriptions.
ANTLR also supports generating a Python parser from this grammar, but switching
the existing Python compiler is a separate compatibility step, not yet done.

A small token adapter supplies indentation/dedentation and suppresses layout
inside brackets and parentheses. AST construction and structural validation are
NoScheme code. Comment recognition uses the generated lexer. We do not maintain
a handwritten lexer, recursive-descent parser, or custom parsing-table engine.

Parsing uses ANTLR's two-stage strategy: SLL prediction with immediate bail-out,
then a fresh LL attempt if necessary. The LL attempt reports syntax diagnostics;
AST construction and validation run only after a complete parse. Validation
traverses existing nodes by reference, and AST construction moves completed
subtrees into their parents to avoid deep copies.

The lexer preserves original source through the `Source` object. Native spans
use UTF-8 byte offsets; the differential harness converts these to Python's
Unicode-character offsets. Line and column fields use Unicode characters.
The current JSON AST is an internal comparison representation, not a frozen
native compiler API.

## Build and check

Required: a C++23 compiler, Meson >= 1.3, Ninja, CMake (for the upstream ANTLR
runtime subproject), Java >= 11, and the project's Python development environment.
The build uses pinned nlohmann JSON 3.11.3 sources for the internal test protocol
and AST comparison representation, toml++ 3.4.0 for project configuration, and
Boost.Multiprecision/Config 1.90.0 in standalone mode for arbitrary-precision
integers. These are fetched by the same hash-checked dependency command.

```sh
uv sync --locked
python3 -m tools.native_dependencies --fetch
CXX=g++ meson setup build/native --buildtype=release
meson compile -C build/native -j 4
meson test -C build/native --print-errorlogs
```

Downloads are explicit and SHA-256 checked. Runtime and JSON archive hashes are
in `subprojects/*.wrap`; the generator hash is in `dependencies.json`. Generated
C++ files and downloaded archives are ignored. Subsequent builds can run offline
with `--wrap-mode=nodownload` once dependencies are available.

For a separate Clang sanitizer build:

```sh
CXX=clang++ meson setup build/native-asan --buildtype=debug \
  -Db_sanitize=address,undefined -Db_lundef=false
meson compile -C build/native-asan -j 4
meson test -C build/native-asan --print-errorlogs
```

The comparison test exercises valid example files, source fragments drawn from
the Python tests, and Unicode/CRLF cases. It compares every AST field and source
span for valid input, and rejection/diagnostic codes for invalid input. It does
not yet guarantee identical parser-error wording or all invalid-input locations.

`noscheme-native-probe` reads JSON lines containing `source` and `filename` and
returns an `ast` or `diagnostics` object. This protocol is internal to tests.

## Project loading

The native loader reads `noscheme.toml`, parses reachable modules, and binds
imports (including aliases, qualified namespaces, and re-exports). It checks
configuration, duplicate names, import cycles, ambiguous paths, symlink escapes,
and the selected board. Import traversal is iterative. Module ASTs are shared
and immutable; symbol references retain their original declaration locations.

The loader accepts editor-owned source snapshots, including new files that are
not yet on disk. Its caller-owned parse cache compares exact source text before
reuse. Changed files and unsaved buffers are reparsed; deleted files cannot be
resurrected from the cache. Search paths and output paths are relative to the
configuration file, and loading does not create the output directory.

The internal probe accepts `{"project":"examples/blink"}` and returns a
`project` summary comparable to Python's `project_summary`. Optional `sources`
maps filenames to unsaved text, and `include_asts` adds the loaded ASTs. The probe
keeps a parse cache across requests; this is a test interface, not an LSP cache
with an eviction policy.

Projects using Python ERC plugins require an export-provider callback. The
probe supports both supplied `erc_exports` for isolated tests and a real Python
provider selected with `registry_python` (the Python executable filename).
Without a provider, plugin projects fail explicitly with `native.erc-registry`.

Project parity tests compare summaries, diagnostic locations, related locations,
and exit classifications. They also exercise cache invalidation, a deep import
graph, and filesystem errors.

## Python ERC bridge

`python -m noscheme.native_bridge` reads one JSON request from stdin and writes
one JSON response to stdout. Requests use `version: 1`,
`method: "describe_registry"`, `erc_modules` (resolved filenames), and an optional
configuration `span`. Responses contain the version and either `registry`,
`diagnostics`, or a protocol `error`. This is an internal migration protocol.

The snapshot describes merged core/plugin schemas, enums, quantities, rule
signatures, ordered rule parameters, predicates, reducers, rule sets, waivable
checks, and importable plugin exports. Schema fields and parameters are arrays,
so a JSON object implementation cannot reorder positional rule arguments.
Parameters carry `has_default`; their actual defaults and callbacks remain in
Python, including arbitrary plugin-owned default objects.

C++ launches the selected Python executable with an argument vector, validates
the version and snapshot structure, and supplies exports to project loading.
Ordinary plugin logging goes to stderr. The transport has a 30-second default
timeout, a 64 MiB request limit, a 16 MiB response limit, bounded JSON/schema
nesting, and child-process
cleanup on failures. Each snapshot loads plugins afresh, including edits that
preserve timestamps and file sizes. Registry cache ownership and invalidation
will belong to the future native LSP; there is no persistent registry cache yet.

The probe also accepts `registry_response` for isolated snapshot validation,
and `registry_timeout_ms` for transport tests. Parity tests exercise all eight
examples through real subprocesses, plugin edits and failures, invalid versions
and schemas, oversized responses, early exits, and timeouts. The selected Python
environment must have NoScheme installed (the project's `.venv` is suitable).

### Execute checks on a native circuit

The probe accepts `elaborate: true` plus `erc_python` (the Python executable),
optional `configurations` (an array of names), and `erc_timeout_ms`. It sends a
`check_circuit` request with the native circuit, configured plugin filenames,
and the registry description used for resolution. Python reloads the registry
and rejects a changed description, so changed schemas require rebuilding the
circuit. Discovery and execution use separate processes; callback/default objects
never cross the boundary. Callback-only edits take effect on the next request.

Python decodes tagged metadata numbers before calling the existing `apply_erc`
once. Parameter encodings and source ASTs are retained; metadata integers are
lossless up to 65,536 bits without changing Python's global conversion settings.
Native byte offsets and Unicode line/column locations survive the round trip.
All configurations are checked by default; selection uses the existing Python
configuration behavior.

The response contains the ERC report and `erc.json`/`erc.txt` contents, generated
from that same run. Ordinary electrical errors are report data; invalid requests,
plugin failures, and timeouts produce diagnostics. C++ validates the report,
counts, and JSON artifact before accepting it. The probe attaches the report to
its circuit response and returns artifact contents without writing files.

Parity tests cover all eight examples, configuration selection, targeted waivers,
Unicode locations, plugin-owned defaults, exact metadata integers, one callback
per application, edits preserving timestamps, malformed replies, and timeout
recovery. Built-in pin-rule registration is sorted so reports are reproducible
across Python processes with different hash seeds. No editor or production CLI
entry point switches to this bridge yet.

## Value evaluation

The native evaluator handles the expression subset used for parameter values:
integer/double tags, exact scaled literals, arithmetic and shifts, imported
`num` helpers, strings and restricted `.format`, and named environment lookup.
It also supplies integer/index/dimension conversions and their diagnostics,
six-significant-digit quantity display, and the existing resource limits.
Integer division rounds the exact ratio to binary64 once, including ties and
subnormals; large operands do not have to fit in a double first.

The probe accepts `expression`, an encoded `environment`, and an explicit
`symbols` map (`{"num":{"module":"noscheme","name":"num"}}`, for example).
It returns an encoded value or diagnostics, plus warnings. Optional `display`,
`quantity`, `context`, and `limits` exercise the evaluator's support operations.
This remains an internal test interface. Normal compiler callers will supply
the import bindings from the loaded module.

The evaluator parity suite reuses expression cases from the Python tests and
adds deterministic random ratios, floating-point display and quantity checks,
large literals, malformed format fields, namespace shadowing, and limits. It
compares numeric tags and double values exactly, and compares diagnostic codes,
source locations, warnings, and instance paths.

## Parameters and part values

The native parameter resolver follows inheritance and imported aliases, preserves
parameter declaration order, and retains the original module for each default
expression. Binding evaluates supplied arguments in the caller's scope, then
resolves default dependencies in their declaration scopes. Overridden defaults
are not evaluated. Missing arguments, cycles, and type errors retain source
locations, related declarations, and instance paths.

Part-value selection handles inherited `metadata.value`, the `value` parameter
fallback, and quantity formatting for bare quantity parameters. Quantity strings
remain verbatim; arithmetic expressions display as ordinary numbers.

The internal project probe accepts `resolve_parameters: true`. A `bind` object
with `module`, `definition`, and `instance` selects a directly declared instance;
optional encoded `environment` and `instance_path` supply its caller context.
The parameter parity suite compares all eight current examples and focused
binding cases, including Unicode locations, long inheritance/default chains,
namespace aliases, and changed defaults in a persistent process.

The parameter-only entry point projects headers and part-value expressions.
The interface resolver below adds metadata and public-contract validation, using
the same inheritance traversal and parameter builder. The full resolver and
connectivity elaborator below use these bindings. The separate Python
`evaluate_project` template-inspection report has not been ported.

## Metadata interpretation

The native reader uses the registry snapshot for core and plugin field schemas.
It reads scalar values, nested schemas, `custom`/`attributes` mappings, explicit
imported enums, aliases, permitted roles, pin shorthand, and aggregate-load
fields. It also reads check applications, selectors, choices/configurations,
conditional instance roles, waivers with optional pin targets, and grouped current
loads. Rule calls bind positional arguments in registration order, reject unknown
or duplicate arguments, and enforce pin/net scope. Quantities retain sign
constraints; numeric parameter expressions remain
unevaluated with their lexical module and source span. Field maps can be merged
for inheritance while preserving each value's provenance.

Literal numbers use tagged evaluator encodings in the internal metadata JSON,
so large integers survive the comparison protocol without rounding. The registry
snapshot carries Python's decimal-literal conversion limit. This representation
is internal, as is the JSON AST; it is not a public plugin API.

The project probe's `metadata` option selects `module`, `definition`, and `scope`.
An optional `pin` reads that pin's shorthand and nested fields, `locals` models
shadowing, and `parent` supplies previously read metadata to test inheritance.
A `path` can select a nested AST field, and an array of requests compares many
fields after loading a project once. This test interface requires a supplied
registry snapshot or the Python registry provider.

Parity tests compare metadata from all eight examples, including WREN, plus
focused valid/error cases. They check values, constraints, Unicode spans and
whitespace, lexical shadowing, plugin argument order, and inheritance merging.
Inherited applications cannot change checker under the same name.

Reading metadata does not execute checks or evaluate numeric parameter expressions.
The interface resolver validates component load-group members and board
configuration selections; body validation checks instance choice coverage.
Elaboration evaluates numeric parameter expressions. Waiver validation and ERC
execution remain later stages.

## Inherited interfaces

`resolve_interfaces: true` in the project probe builds public contracts using a
registry snapshot or Python provider. It expands physical pin selections, merges
inherited pins/ports and metadata, rebuilds aliases, and enforces specialization
restrictions. Inherited port types, parameter defaults, and reusable-block bodies
keep their original lexical module. Bodies remain immutable AST references owned
by the loaded project.

Current-load groups resolve aliases to physical pads and reject unknown or
repeated members, incompatible roles, and simultaneous individual/grouped loads.
Interchangeable groups require distinct, electrically equivalent pins; aliases
and custom data are excluded from that comparison. Numeric literal equality
matches Python across integer/double tags. Board configurations must select
declared alternatives. Source declaration order survives inherited overrides
so diagnostics select the same offending declaration as Python.

The parity suite compares complete interface summaries for all eight examples,
including WREN, plus ranges/limits, aliases, invalid specializations, lexical
scope, group checks, diagnostic precedence, and deep inheritance. A shared
iterative walker serves both parameter-only and interface resolution.

### Body validation and positional connections

`resolve_all: true` runs the counterpart of Python's complete definition
resolver. It expands positional connections using the target's interchangeable
pin group, retaining explicit named connections and source spans. Expanded nodes
are owned by the resolved definition; the project's cached AST stays unchanged.
Inherited bodies keep their original module for name lookup.

Body validation checks net and bundle types, public field access, declared caller
nets, parameter expressions and numeric helper imports, array dimensions and
element overrides, and instance metadata (including conditional roles and public
ERC choices). Global-net existence, concrete shapes, and required argument
binding are handled by the elaborator below. Bundle-cycle detection is iterative.

Independent errors are collected in Python's traversal order. Errors in shared
inherited bodies are deduplicated; metadata overrides retain the original field
order for diagnostics. The body parity suite compares all eight examples and
focused successes/failures, including repeated resolution and unsaved edits in a
single probe process.

The native compiler is exercised through the probe; the experimental LSP currently
also runs project loading, resolution, elaboration, and the Python ERC bridge.

## Circuit elaboration

`elaborate: true` in the project probe runs full resolution and produces a
CAD-independent circuit. It expands reusable blocks, instance/net arrays, and
bundle fields; binds parameters and element overrides; and joins public ports
and physical pins to caller nets. It preserves explicit `no_connect`, omitted
bindings, global-net references, named bundle types, source locations, identities,
explicit references, quantity display, and footprint warnings.

Numeric metadata evaluates in its component or caller environment as appropriate;
overrides suppress replaced expressions. Named net-type metadata, conditional
roles, choice scopes, and configurations are carried into the circuit for the
later ERC stage. Literal and evaluated metadata numbers keep the internal tagged
encoding described above. The parity tests decode that representation when
comparing against Python's circuit JSON; no public export format is changed.

Union-find with path compression and size weighting tracks connectivity. Public
ports must have internal physical support, an external endpoint, or a placeholder
before caller connections are applied. Recursive instances, overlapping bindings,
shape/type mismatches, missing globals, duplicate identities/references, and
out-of-bounds overrides produce diagnostics with instance paths. Expansion and
expression limits can be supplied as `expansion_limits` and `expression_limits`.

Tests compare complete circuits for all eight examples, including WREN, and
focused cases for connectivity, quantities, array selection, identities, and
limits. Elaboration itself does not run ERC. The optional bridge stage above checks
the resulting circuit; KiCad reference assignment, export, and editor integration
remain outside this probe.

## Parser benchmark

```sh
.venv/bin/python cpp/tests/parser_benchmark.py build/native/noscheme-native-probe
```

Use an optimized build, without sanitizers, and avoid running other tests or
builds concurrently. The benchmark starts fresh Python and native processes for
each source. Warm measurements discard three iterations and report the median
of 21 more, with AST caching disabled. They include source preparation, parsing,
AST construction, structural validation, and AST destruction; they exclude file
I/O and JSON protocol serialization. The first-request measurements additionally
include process startup, imports, grammar initialization, and the small benchmark
protocol exchange. These are single samples, so treat them as approximate.

The native probe also reports phase timings for comment masking, tokenization,
syntax parsing, AST construction, and structural validation. Their sum excludes
source initialization and teardown, so it is lower than the total parse time.

This measures the current implementations: Python builds dataclass AST nodes,
while native code builds an internal JSON AST. It does not isolate ANTLR versus
Lark recognition speed, or measure complete project loading or LSP latency.

### Measured baseline (October 7, 2026)

Intel i7-8565U, GCC 15.2 `-O2 -g` (`debugoptimized`, libstdc++ assertions
enabled), Python 3.13.9 / Lark 1.3.1. No sanitizers, LTO, PGO, or CPU-specific
tuning. Seven measured iterations per file after three warm-ups, with no other
builds or tests running concurrently:

| Source | Python median | Native median | Speedup |
| --- | ---: | ---: | ---: |
| Blink main | 0.98 ms | 0.38 ms | 2.6× |
| STM32 board | 10.90 ms | 4.04 ms | 2.7× |
| Rulos board | 18.54 ms | 5.88 ms | 3.2× |
| WREN parts (90 kB) | 214.12 ms | 68.82 ms | 3.1× |
| WREN board (428 kB) | 722.57 ms | 238.86 ms | 3.0× |

Before removing deep copies and enabling SLL/LL fallback, the native WREN board
parse took roughly 810 ms. Phase measurements identified AST construction and
validation as the largest costs. After the changes, they take about 89 ms and
13 ms respectively; syntax parsing takes about 13 ms. Hardware load and CPU
frequency affect these numbers. They establish a parser baseline, not an LSP
latency promise.

## Remaining migration work

- Expand malformed-input and diagnostic-location coverage; fuzz the front end.
- Port the template-inspection report if needed by native editor requests;
  resolution, parameter binding, and full connectivity elaboration are available.
- Implement native LSP requests, snapshot caching, cancellation, and formatting.
- Compare actual editor latency and memory use, then check Vim, Neovim, and VS Code.

The Python editor command remains the default until the native server reaches feature parity.

## Experimental native LSP

Meson builds `build/native/noscheme-native-lsp`. To test it through the same
launcher used by the Python server:

```sh
.venv/bin/noscheme-lsp --implementation native --native-server "$PWD/build/native/noscheme-native-lsp"
```

Use this command in the editor's server configuration and restart the server.
Switch back by removing the two native options or choosing `--implementation python`.

This first slice implements [LSP 3.17 stdio framing and lifecycle](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/),
full-document synchronization, and native parser/structural diagnostics. It uses
UTF-16 positions and publishes document versions, ignores stale updates, clears
diagnostics on close, and discards closed buffers. It does not write files.
Only implemented capabilities are advertised.

Project analysis now discovers the nearest ancestor `noscheme.toml` for each
open file, or uses an explicit `--project`. All open local files form the source
overlay, including imports and configuration edits; closing a file restores
the disk version for remaining open documents. Standalone files still receive
syntax/structural diagnostics. Multiple discovered projects are checked once
each per update. Workspace-folder scanning is not implemented yet.

Resolution, elaboration warnings, and ERC run without writing artifacts.
Diagnostics include related locations and instance/configuration/application
data; old diagnostics are cleared after fixes or when a project is closed.
The native AST cache reuses exactly matching text and drops entries outside
the current analysis. Registries and callbacks reload for each analysis.
`didSave` reruns analysis so saved plugin changes are picked up; unsaved Python
plugin code is not executed. The launcher supplies `--python` using its own
interpreter. Direct invocation defaults to `python3`, which must have NoScheme
and its dependencies installed.

Analysis runs on one background thread with a copy of the open buffers. A
200 ms debounce coalesces edits; generation checks discard superseded results
before publication. The worker owns the AST cache and the protocol thread stays
responsive during compiler/ERC work. Cancelling a Python bridge request kills
and reaps its process group, checked at most every 50 ms while polling. Native
parsing, resolution, and elaboration finish their current phase before observing
cancellation. Shutdown responds immediately, suppresses further diagnostics,
and joins the worker on exit; a native phase may delay final process exit.

Definition and hover requests now use an owned reference index from the resolved
project snapshot. It covers imported/aliased/qualified types, inheritance, lexical
nets and instances, parameters, globals, and direct public pin bindings (including
pin aliases), plus nested bundle fields on both sides of bindings. Array indices
and slices preserve the declared element type; lookup follows public interfaces
and explicit globals without exposing private block internals. Definitions point
to original declarations across imports. Hover
shows concise declaration descriptions, with public interfaces and parameter
types for component/block definitions, using Markdown when the client supports it.
Field hover shows its type. Common metadata (roles, unused policy, aliases,
footprint, value, reference prefix) includes the module that supplied it.
Parameters show simple default expressions or required status; nets and instances
show their declared types. Long metadata text is truncated at a UTF-8 boundary.

Requests arriving before analysis completes wait for that snapshot. Edits reject
queued requests with ContentModified; `$/cancelRequest` and shutdown cancel them.
The queue is bounded to 128 requests. Cursor and target ranges use UTF-16.
Navigation currently shares the full analysis lane, including ERC; separating
interactive analysis from slow checks remains future work.

Builtin role, nettype, and unused-policy hover shares its descriptions with the
Python server. Registered ERC rules show their signature, scope, and whether
they permit waivers, including project plugins.

Initial completion supports imported types, qualified module names, builtin enum
members, and registered ERC rules/predicates. Connection bindings also offer public
pins, pin aliases, and nested bundle fields, including inherited interfaces and
array indices/slices on either side. Suggestions use declared interfaces; private
block internals are excluded. Interface resolution runs once before body validation,
so unfinished connections do not prevent interface suggestions. Name completion
replaces the current member suffix and excludes comments and strings. For an unfinished member such as
`role.`, a separate recovered parse supplies suggestions while the original
syntax diagnostic remains visible.

Direct component and instance `metadata.footprint` fields also suggest the inherited
default and imported `custom.kicad_footprint_candidates`, without scanning installed
libraries.
Empty values and unfinished quoted strings are supported. Completion replaces
the entire value with a quoted string, and preserves trailing comments.
Candidate hints still require verification against the physical package.
Named connection targets also suggest local nets, public ports, and explicitly
qualified global nets. ERC check calls offer remaining parameter keywords,
selectors, endpoint/net fields, and missing-data policies from the registry.
Keywords already supplied positionally or by name are excluded. Aliased rules
and project plugin parameters use the same path. Unfinished calls can recover a
completion catalog while retaining their original diagnostics.

Empty and unfinished inline instance lists suggest named pins and parameter
keywords. Components with interchangeable pins also suggest positional net
targets. Supplied pins (including aliases) and parameters are excluded, and
completion inserts the appropriate colon or equals sign. ERC rule-name
suggestions are filtered to the pin or net scope of the checks section.
Section-form connections also support empty rows, missing targets, and unfinished
pin or bundle-member names. Suggestions remain within the owning instance and
reuse the same candidate and text-edit logic as inline lists. Cursors inside
index expressions are left alone. Grouped physical pin bindings support empty
and partial members, including multiline lists. Suggestions omit pins already
selected by ID, alias, or numeric range in the current list or other rows of the
same section. List members are inserted without a colon, with quotes when needed.
Quoted members and groups after instance-array selectors are supported, including
unfinished quotes. Exact selector spellings constrain duplicate filtering; the
completion scanner does not evaluate overlapping slices.

Recovery supports dangling members, unfinished footprint strings, inline instance
lists, connection sections, and ERC check calls; arbitrary incomplete source
remains unsupported.
Keyword completion uses lexical context, including in new files without a
project: declarations, valid definition/instance sections, element override
sections, and common `import`, `as`, `extends`, and `global net` continuations.
It inserts bare keywords and leaves punctuation to the author. Comments, strings,
and open argument/index expressions do not receive keyword suggestions.
Completion, navigation, references, and hover use a version-current snapshot
published before ERC callbacks execute. Registry loading and native analysis
still precede the snapshot; full diagnostics arrive after ERC. Edits cancel
superseded checks and invalidate the interactive snapshot.


Interactive requests bypass the native server's typing debounce. The built-in
ERC registry is reused for the server lifetime; restart after updating the
NoScheme installation. User plugin registries reload on each analysis, including
transitive imports. Rename returns versioned document edits when the editor
advertises support, protecting open buffers from stale edit application.

Grouped pin members and range bounds support definition and hover. Find References
uses the same symbol targets and honors inclusion of declarations. Instance
hover shows up to four elaborated identities with values, footprints, and bound
parameters. Navigation remains available for resolved interfaces when circuit
body resolution fails.

A local fallback indexes complete declarations in standalone or partially broken
files. It preserves source positions and original diagnostics, ignores broken
declarations, and resolves local names and builtin imports. Unresolved external
imports are not guessed. Workspace discovery uses the initialized workspace
folders (or root URI) when no ancestor project exists; the most specific source
root wins, and ambiguous matches require an explicit `--project`.

Whole-file formatting uses the native AST, preserving spelling and comments,
with the same canonical layout as Python. It reparses and compares the result
before returning an edit. Range formatting is not supported by either server.
Prepare Rename and Rename match the current Python scope, including import aliases,
nets, parameters, public block ports, bundles, and board-level instances. Physical
pins, board names, module qualifiers, and internal instances are refused. Board
instance renames preserve identity, update waiver paths, and validate the proposed
physical parts/connectivity. Source edits never write CAD output or identity state.

Requests check the exact contents of disk-backed source, configuration, and ERC
plugin inputs before reusing a snapshot, including changes whose timestamps are
preserved. Editor file-watcher notifications also invalidate analysis. Freshness
checks add file I/O to warm requests. Completion candidates are shared by lexical
scope and expanded at the cursor, avoiding a full net list per binding. Diagnostic
source indices are reused within a publication.

Manual editor validation remains before recommending the native server as the
default. Release-build measurements below also show remaining edit-time costs.

Headers are limited to 8 KiB, bodies to 16 MiB, and JSON nesting to 128. Broken
framing terminates the process with a stderr message; malformed JSON returns a
protocol error and permits the next framed message. No log text goes to stdout.
The `native-lsp` test suite exercises both the executable and launcher, document
updates, Unicode ranges, malformed traffic, lifecycle, and example sources.


### Editor-visible benchmark

Run without competing builds or tests:

```sh
.venv/bin/python -m tools.benchmark_lsp examples/stm32h523 \
  --native build/native-release/noscheme-native-lsp --repeat 5
```

This uses the same stdio client for Python and C++, measures cold and warm hover,
and appends comments in unsaved buffers to measure edit-to-hover and full
ERC-diagnostic latency. It writes no design files or export state. Both servers
check dependency freshness; native snapshots become available before ERC.
The benchmark samples Linux process-tree RSS every 50 ms, including Python
workers/bridge children. This is an approximate peak: short peaks can be missed,
and shared pages can be counted in more than one process. Results are local
observations, not CI performance thresholds.

### Local LSP measurements (October 8, 2026)

Intel i7-8565U, Ubuntu 26.04, Python 3.13.9, GCC 15.2 release build (`-O3`).
Measurements used real stdio requests with dependency freshness checks enabled,
without competing builds or tests. STM32 used five edit samples; full WREN used
three. Repeated values below are medians. Cold timings start at `didOpen` and
exclude server initialization (about 413 ms for Python and 3 ms for native).

| Project | Measurement | Python | Native C++ |
| --- | --- | ---: | ---: |
| STM32 | Cold hover | 346.9 ms | 257.3 ms |
| STM32 | Cold diagnostics | 624.3 ms | 483.7 ms |
| STM32 | Warm hover | 15.7 ms | 1.0 ms |
| STM32 | Hover after edit | 90.0 ms | 103.0 ms |
| STM32 | Diagnostics after edit | 379.9 ms | 326.3 ms |
| STM32 | Sampled peak tree RSS | 127.9 MiB | 84.7 MiB |
| WREN, 1,507 parts | Cold hover | 2516.1 ms | 1695.3 ms |
| WREN, 1,507 parts | Cold diagnostics | 9339.0 ms | 3945.6 ms |
| WREN, 1,507 parts | Warm hover | 298.4 ms | 19.9 ms |
| WREN, 1,507 parts | Hover after edit | 2530.0 ms | 1460.5 ms |
| WREN, 1,507 parts | Diagnostics after edit | 9341.8 ms | 3705.9 ms |
| WREN, 1,507 parts | Sampled peak tree RSS | 529.8 MiB | 649.7 MiB |

Both servers reported zero diagnostics for STM32 and the same 1,309 unchecked-net
warnings for WREN. The edit appends an unsaved comment to the board file; these
numbers do not represent every editing operation or plugin-heavy workload.

The native server improves large-design latency, but small-design edit-to-hover
is still slightly slower. WREN native memory remains above Python's. Before
sharing completion candidate lists and reusing diagnostic source indices, WREN
native peaked at 8,869 MiB in the sampled RSS report, took 11.94 s for
edit-to-hover, and 19.02 s for edited diagnostics.
The full WREN project is now exercised by the native LSP regression suite.

Validation for this checkpoint: all 13 native suites, including 223 LSP tests,
342 formatter cases and 36 rename cases; all 1,076 Python tests; Ruff, clang-format,
Meson formatting, and whitespace checks. Address/undefined-behavior sanitizer
suites passed too; parser and LSP initially hit wall-clock limits under concurrent
build/test load, then passed sequential reruns with extra sanitizer time.
