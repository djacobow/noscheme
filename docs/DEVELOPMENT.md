# Compiler development

For developing circuits, start with the [workflow HOWTO](../HOWTO.md). This
document covers development and validation of NoScheme itself. Run its commands
from the repository root. For syntax and
electrical rules, see the [language reference](LRM.md).

## Environment and checks

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run:

```sh
uv sync --locked
uv run --locked pytest -q
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked noscheme format --check examples
uv run --locked noscheme compile --project examples/blink
```

`noscheme format examples` rewrites the example sources; the test suite also fails
when an example is not in the canonical layout.

Compilation writes `circuit.json`, `erc.txt`, and `erc.json` under the project's
configured output directory. Electrical errors allow compilation/export;
`noscheme check` returns a failing status when it finds electrical errors.
Structural errors block compilation.

### Emacs integration tests

With Emacs 29+ installed, `pytest` also runs the real Eglot client against the
Python server and an available native build. Run only these checks with:

```sh
.venv/bin/python -m pytest -q tests/test_emacs.py
```

Set `NOSCHEME_NATIVE_LSP` to select a native binary; otherwise the test uses
`build/native-release/noscheme-native-lsp`. Missing Emacs or native binaries are
reported as skips. CI installs Emacs explicitly so editor validation runs there.
See [Emacs setup](EDITOR_SUPPORT.md#emacs-eglot) for interactive use.

### Trying the native CLI

Python remains the default. To use the C++ compiler, first follow the
[native build instructions](../cpp/README.md#build-and-check), then run:

```sh
.venv/bin/noscheme check --project examples/blink \
  --implementation native --native-tool "$PWD/build/native/noscheme-native"
```

The same options work with `parse`, `load`, `resolve`, `lint`, `evaluate`,
`compile`, `format`, `export-bom`, and `export-kicad`. Existing command options, including
`--diagnostics json`, `--erc-configuration`, and `--footprints`, still apply.
Remove the implementation options to return to Python. Both exporters share
identity state; keep that file when switching implementations.

Run `meson test -C build/native --print-errorlogs` for native regression tests,
including CLI parity and export-state compatibility. Run the Python suite too
when changing shared front-end or publication code. Native export uses OpenSSL
(`libssl-dev` on Ubuntu) for UUIDv5 and artifact hashes.

### LSP tests in restricted sandboxes

The stdio LSP uses pygls' worker thread to read stdin. Python's asyncio loop
wakes after that read through a local socket pair. Some sandboxes permit
`socket.socketpair()` but reject `send()` with `EPERM`. In that environment,
initialization stalls even though the server does not contact a network service.
The protocol test client checks this capability before starting the server and
reports the restriction immediately. It does not skip these tests.

Run the full suite in a normal terminal or with sandbox approval. Increasing
the response timeout does not solve this restriction. A minimal probe is:

```sh
python3 -c 'import socket; a,b=socket.socketpair(); print(a.send(b"x")); a.close(); b.close()'
```

Success prints `1`. On the affected sandbox this raises `PermissionError`.

### WREN performance workload

The [WREN example](../examples/wren/README.md#full-board-benchmark) includes a
1,507-component project and independent connectivity regression tests. Before
measuring changes, run `uv run --locked pytest -q tests/test_wren.py`. Run timings
without simultaneous tests or other benchmark processes:

```sh
uv run --locked python -m tools.benchmark_analysis examples/wren/full --repeat 5 \
  --profile examples/wren/full/build/analysis.prof > /tmp/wren-baseline.json
python3 -c 'import pstats; pstats.Stats("examples/wren/full/build/analysis.prof").strip_dirs().sort_stats("cumulative").print_stats(30)'
```

The JSON includes individual timing samples, Python/dependency versions, and
input hashes. The optional profile is a separate analysis after the timed runs;
its instrumentation overhead is excluded from the medians. Analysis errors
abort the benchmark. Explicitly unchecked nets are counted in the ERC report without warnings.
No export state is changed, and profile files under `build/` are ignored.

Comment masking consumes ordinary text in chunks while preserving source
offsets and newlines. During LSP analysis, the open file's parsed AST is passed
to the project loader for reuse when the filename and source text match.
Persistent LSP workers also retain ASTs across edits, keyed by exact filename and
source text. Each changed file is still parsed in full. The opt-in cache leaves
ordinary CLI compilation uncached. Each worker retains at most 256 parse entries
and 16 MiB of source text (AST overhead is additional); eviction affects only
performance. Resolved projects and semantic indexes are scoped to one editor/disk
snapshot, with a 128-entry memo threshold before the next request resets them.
Disk content is compared directly, so preserving a file's size and timestamp does
not hide an edit. New/deleted modules and project configuration changes invalidate
the snapshot too.

The protocol loop delegates interactive work and full ERC diagnostics to separate
persistent Python subprocesses. Private framed pipe messages carry source snapshots
and results; plugins run in their worker's own interpreter. The background lane
runs once per project, coalesces edits, and discards outdated results. Interactive
cancellation kills its worker to avoid consuming an old response on a later request.
Both workers close on LSP shutdown and restart on demand after failure. Plugin
stdout is redirected to stderr to keep both protocols intact.

ERC configuration views share unchanged circuit records and copy the paths to
records whose selected roles change. A freeze memo local to each `apply_erc`
call reuses immutable subgraphs across configurations; plugins still receive
recursively immutable views. Default rule arguments are validated and frozen
once per rule per configuration. Explicit arguments are validated at each
application. Neither cache survives a new ERC run, so source and registry edits
are observed on the next call. Rule scheduling and electrical policies are
unchanged by these optimizations.

## Example projects and CAD validation

The [HOWTO](../HOWTO.md) covers checking, compilation, BOM export, library
conversion, and the KiCad import/update workflow. The [STM32 design notes](PROJECT.md#reference-board)
record pin assignments and hardware assumptions; its
[library README](../examples/stm32h523/kicad_lib/README.md) records conversion provenance.
Other worked designs and their validation limits are documented in
[Rulos](../examples/rulos_6matrix/README.md), [WREN](../examples/wren/README.md),
and the [ERC extension example](../examples/erc_extensions/README.md).

The retained [experiment harness](experiments/README.md) provides manual KiCad
checks and regression fixtures. It is historical feasibility tooling, not the
production exporter. Its findings do not replace manual checks on a routed board.

## ERC extension lifecycle

ERC extension modules are loaded once per loaded project configuration, shared
by metadata validation and electrical checking. CLI export runs ERC once and
uses the same diagnostics for its reports and stderr. Each fresh CLI operation
reloads extensions. LSP workers reuse a registry within an unchanged snapshot and
reload configured ERC modules when their source changes. Entry modules are read
directly, avoiding stale timestamp-based Python bytecode. Imported Python helper
modules follow Python's normal module cache; restart the server after editing
those helpers. Plugin registration runs in both workers for schema resolution,
but ERC callbacks run only in the diagnostics worker.
`erc_rule_sets` selects automatic policies; the default is `["std.default"]`.
Explicit `checks` applications run independently of that selection. Every rule
receives the same recursively immutable graph for its configuration, with no
per-callback circuit copy. Schema/registration/callback failures block artifact
publication; ordinary electrical diagnostics retain the existing compile/export
behavior. See [LRM §12](LRM.md#12-extending-erc) for the public API.

## CI

GitHub Actions runs on pushes, pull requests, and manual dispatch. Both jobs use
the explicit `ubuntu-26.04` runner label, which is
[generally available](https://github.blog/changelog/2026-09-17-ubuntu-26-generally-available-and-latest-migration/):

- Python 3.11 and 3.14: locked dependencies, Ruff formatting/lint, all tests
  (including the NoScheme layout of every example),
  distribution builds, and installed-wheel parser/compiler smoke checks.
- Python 3.12 with KiCad 10.0 library data: all tests, including conversion
  and saved-board fixtures that otherwise skip when KiCad data is unavailable.

CI does not replace PCBNew's manual import/update checks. The KiCad data job uses
the official `ppa:kicad/kicad-10.0-releases` repository, following
[KiCad's Ubuntu installation instructions](https://www.kicad.org/download/details/ubuntu/).
It installs symbols, footprints, and demo boards, logs their versions, and fails
if any package is outside the 10.0 series. Patch releases follow the PPA's latest
available packages. The job does not install or exercise the KiCad GUI.
The PPA publishes all three packages for Ubuntu 26.04 (`resolute`); version
10.0.6 was available when the runner configuration was updated on October 5, 2026.
Action revisions are pinned to commits, and workflow permissions are read-only.
The uv setup follows its [official GitHub Actions guide](https://docs.astral.sh/uv/guides/integration/github/).

## Local search with st

The optional `.st_config.py` configures [st](https://github.com/djacobow/st) for
this repository. Its search-path key is the Git remote repository name,
`noscheme`; `nosch` is a file-type selector. Default searches cover source,
tests, examples, and documentation while excluding generated output and caches.
Use explicit scopes to narrow searches or include generated files:

```sh
st f erc -py -src
st g STM32H523Reference -nosch -f
st p examples
st f erc -py -build
```

The config requires a Git repository with an `origin` remote. Exclusion regexes
are attached to each search scope; a standalone `EXCLUDE` variable is not read
by st.

## Reference tables, coverage, and performance

The LRM's core metadata, built-in extension metadata, and role tables are generated
from schemas and the shared hover descriptions. After changing these definitions:

```sh
uv run --locked python -m tools.reference_tables
uv run --locked python -m tools.reference_tables --check
```

A regression test checks that the committed tables match. Edit explanatory prose
outside the generated markers; regenerate table changes from their source.

CI reports statement and branch coverage for the production package. There is no
coverage threshold yet; use missing lines to identify worthwhile tests. Coverage
is for the test process; separately launched LSP subprocesses are not measured.

```sh
uv run --locked coverage run -m pytest -q
uv run --locked coverage report
uv run --locked coverage html
```

To measure resolve/elaborate, circuit copying, ERC, and complete uncached LSP analysis:

```sh
uv run --locked python -m tools.benchmark_analysis examples/stm32h523 --repeat 5
uv run --locked python -m tools.benchmark_analysis examples/rulos_6matrix --repeat 5
uv run --locked python -m tools.benchmark_analysis examples/wren/full --repeat 5
```

Add `--workers` to include persistent subprocess measurements: cold semantic
analysis, unchanged snapshots, single-file edits, all project files open, and
background ERC. These include process startup where applicable, source validation,
and pipe serialization. Semantic timings measure preparation of navigation/hover
indexes, not editor rendering or an end-to-end UI latency. Edits append a comment
to an in-memory board buffer; no source files are changed.

The benchmark writes no export state. It reports cold initialization separately
from repeated operations. Timings are machine-dependent observations, not CI
thresholds; extension-heavy projects can behave differently from these examples.
The [WREN performance notes](../examples/wren/README.md#performance) separate
the current measurements from earlier optimization checkpoints.

### LSP worker measurements

October 6, 2026; Python 3.13.9, Lark 1.3.1, pygls 2.1.1, Linux x86-64.
Three-run medians with tests stopped; cold worker startup is a single sample.
Measurements include private pipe serialization and disk snapshot checks.
The baseline is the existing full uncached `analyze()` path in the same build.

| Workload | Uncached full analysis | Warm semantic | Edited semantic | Edited full diagnostics |
| --- | ---: | ---: | ---: | ---: |
| STM32 | 229 ms | 8 ms | 81 ms | 142 ms |
| Rulos | 611 ms | 8 ms | 103 ms | 504 ms |
| WREN full | 3,756 ms | 179 ms | 2,150 ms | 3,706 ms |

Cold semantic worker requests measured 334 / 365 / 2,150 ms respectively;
cold diagnostics measured 385 / 753 / 3,363 ms. With all project files included
in the open-file set, repeated unchanged semantic requests measured
10 / 9 / 229 ms. The first expanded open-file set rebuilds the index, so those
samples are reported separately in the benchmark's sample arrays.

Separating ERC makes interactive work independent of a slow checker; it does
not reduce the checker's own runtime. WREN's large edited board still requires
full parsing and semantic reconstruction. Repeated WREN navigation also pays
for transferring the full reference/hover index from the worker. These are
remaining optimization targets. The slow-checker regression test uses a release
barrier to verify navigation responds while ERC is blocked, without a fragile
millisecond threshold.

## Parameterized ERC example

[examples/parameterized_erc](../examples/parameterized_erc/README.md) combines a
numeric resistor parameter, displayed values, per-element overrides, and pull
reference-terminal checks. Run `noscheme check --project examples/parameterized_erc`;
the abstract endpoint's missing-footprint warning is intentional.

Optional source lint is separate from compiler/CI electrical checks:

```sh
uv run --locked noscheme lint --project examples/stm32h523
uv run --locked noscheme lint --project examples/parameterized_erc --diagnostics json
```

It reports unreferenced public parameters without assuming they should be removed.

## Standard ERC check families

`erc_standard.py` registers standard names and default applications. Policies live
in `erc_connections.py`, `erc_ports.py`, `erc_compatibility.py`, `erc_drivers.py`,
and `erc_current.py`; `erc_facts.py` supplies shared immutable facts and diagnostic
construction. A selected family is evaluated once per net/configuration, and
unselected families do not execute. Grouped current facts are shared between net
budget checks and the component's group-validation application. Tests cover both
behavior and execution isolation.

## Native C++ development

The C++23 migration is under `cpp/`, using Meson/Ninja and ANTLR-generated
lexing/parsing, toml++ project loading, Boost.Multiprecision value evaluation,
definition resolution, circuit elaboration, and native standard ERC rules, with a
subprocess bridge to Python for ERC plugins. See
[native build and parity tests](../cpp/README.md). The default Python CLI and
optional native CLI each implement standard ERC, so a change to a standard rule, its messages, or report text belongs in both
engines; the ERC parity tests replay every project the Python ERC tests load. The separate experimental native LSP
currently supports project/compiler/ERC diagnostics, definition/hover, references,
context-aware completion, whole-file formatting, and identity-aware rename.
Interactive snapshots are available before ERC callbacks run. Python remains the
default while native editor validation and performance work continue. Native tests
are separate Meson targets and do not replace the Python suite.

Use `tools.benchmark_lsp` for actual stdio request timings, including edits and
full diagnostics; see the [benchmark instructions](../cpp/README.md#editor-visible-benchmark).
Run performance measurements separately from builds and tests. For local sanitizer
runs on a busy or memory-constrained machine, use `meson test -C build/native-asan
--num-processes 1 --timeout-multiplier 2 --print-errorlogs`.

## BOM regression coverage

`tests/test_bom.py` covers grouping, metadata, quoting, reference persistence,
target isolation, and publication failure. The native CLI parity suite also checks
CSV bytes and cross-implementation BOM/KiCad state reuse. See the
[HOWTO](../HOWTO.md#6-export-the-bill-of-materials) for usage.
