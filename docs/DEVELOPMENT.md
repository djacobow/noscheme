# Development

Run the commands in this document from the repository root. For syntax and
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
abort the benchmark. Unchecked-net warnings are expected for this fixture.
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

## Electrical checking

Named ERC configurations check alternative role assumptions on one unchanged
board. All configurations run by default; select a subset when needed:

```sh
uv run --locked noscheme check --project examples/power_modes
uv run --locked noscheme check --project examples/power_modes --erc-configuration usb_powered
```

Optional `erc_modules = ["checks.py"]` in `noscheme.toml` enables Python ERC
extensions. Pull-resistance and capacitance budgets use the same registry and
run only on nets with declared limits. Try the included example:

```sh
uv run --locked noscheme check --project examples/erc_extensions
```

See [LRM.md](LRM.md) for language behavior, [PROJECT.md](PROJECT.md) for design
rationale, [PLAN.md](PLAN.md) for implementation status, and
[experiments/README.md](experiments/README.md) for the manual KiCad checks.
The retained harness and local trial outputs live under `tools/experiments/`.

## Tri-state example

Use `nettype.tristate` for a shared bus. Digital inputs and passive attachments
are allowed; each output-capable pin needs `tristate_capable: true` in its
pin definition or instance-pin metadata. ERC checks capability, while the designer
remains responsible for bus arbitration. Run the electrical-only example with:

```sh
uv run --locked noscheme check --project examples/tristate
```

The example intentionally has no footprints; those warnings are expected.

## Rulos six-line matrix

The [matrix example](../examples/rulos_6matrix/README.md) transcribes Jeremy
Elson's Rev B schematic using nested arrays. Its README includes attribution,
source verification, package substitutions, and KiCad library snapshots.

```sh
uv run --locked noscheme check --project examples/rulos_6matrix
uv run --locked noscheme export-kicad --project examples/rulos_6matrix --footprints examples/rulos_6matrix/kicad_lib/footprints.json
```

The export contains 93 parts and 106 nets, all using standard KiCad footprints.
All six power configurations pass ERC with the documented demo current, voltage,
pull-up, and capacitance assumptions. Physical validation remains outstanding.

## STM32 proof-of-concept board

```sh
uv run --locked noscheme check --project examples/stm32h523
uv run --locked noscheme export-kicad --project examples/stm32h523 --footprints examples/stm32h523/kicad_lib/footprints.json
```

Open a new PCB in KiCad PCB Editor and use **File → Import → Netlist…** to load
`examples/stm32h523/build/board.net`. Use **Load and Test Netlist**, inspect the
report, then **Update PCB**. Expect 58 footprints ready to place. Save the board
outside the generated `build` directory, for example as
`examples/stm32h523/stm32h523.kicad_pcb`.

Subsequent exports reuse `examples/stm32h523/noscheme.state.json`; retain that file
with the project to preserve part identity. The checked-in footprint snapshot
covers the selected KiCad library footprints. The board includes decoupling,
crystal/load capacitors, USB-C, microSD, SWD, two UARTs, expansion headers, buttons,
and LEDs. See [PROJECT.md](PROJECT.md#reference-board) for the pin map and
remaining hardware assumptions.

### Example source modules

The entry point remains `stm32h523.STM32H523Reference`. The source files live
under `examples/stm32h523/`:

| File | Contents |
|---|---|
| [stm32h523.nosch](../examples/stm32h523/stm32h523.nosch) | Board nets, power configurations, and subsystem/component instances |
| [mcu.nosch](../examples/stm32h523/mcu.nosch) | MCU specialization and subsystem, including decoupling and crystal |
| [components.nosch](../examples/stm32h523/components.nosch) | Passives, LEDs, buttons, indicator block, and small connectors |
| [interfaces.nosch](../examples/stm32h523/interfaces.nosch) | Shared I2C, SPI, USB, and SWD bundles |
| [peripherals.nosch](../examples/stm32h523/peripherals.nosch) | USB-C, regulator, microSD, and debug subsystems |
| [kicad_lib/mcu/MCU_ST_STM32H5.nosch](../examples/stm32h523/kicad_lib/mcu/MCU_ST_STM32H5.nosch) | Generated KiCad MCU definition |
| [kicad_lib/device/Device.nosch](../examples/stm32h523/kicad_lib/device/Device.nosch) | Generated KiCad resistor and capacitor definitions |

The board demonstrates qualified references such as
`mcu.MCU_H523` and `peripherals.UsbC`. Shared components
and bundles use named imports. A shared interface module keeps bundle identity
consistent across the board and subsystems without circular imports.
All source files use the same project configuration in `examples/stm32h523/`.

Generated library files and their regeneration instructions are documented in
[kicad_lib/README.md](../examples/stm32h523/kicad_lib/README.md).

### Converted passive library

The shared components module uses `import Device` with the checked-in
`examples/stm32h523/kicad_lib/device/Device.nosch`, generated from KiCad's `Device:R` and
`Device:C`. `Resistor extends Device.R` and `Capacitor extends Device.C`
specialize the unused-pin rules. Their `0603` subclasses select the board's
default footprints; `Capacitor0603` also supplies the default value `"100nF"`.
Pin roles, reference prefixes, value parameters, and footprint hints come from
KiCad. The raw imported value defaults are `"R"` and `"C"`; this board supplies
actual values through instances and specializations.

To reproduce the extraction, choose a fresh output directory:

```sh
uv run --locked noscheme convert-library /usr/share/kicad/symbols/Device.kicad_sym --symbol R --symbol C --footprint-root /usr/share/kicad/footprints --output /tmp/noscheme-device
```

The generated directory contains the module, conversion report, and footprint
snapshot. The board's `kicad_lib/footprints.json` includes those candidate records so
alternative selections can be exported. KiCad's passive filters are broad,
including through-hole parts; review the package before choosing.

In Vim, open `examples/stm32h523/components.nosch` and edit the footprint under
`component Resistor0603 extends Resistor` to
`footprint: "Resistor_SMD:` and press **Ctrl-X Ctrl-O**. Alternatives such as
0805 and hand-soldering variants should appear. This also works on instance
footprint overrides. For capacitors, edit `Capacitor0603` and use the prefix
`footprint: "Capacitor_SMD:`.

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
