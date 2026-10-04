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
uv run --locked noscheme compile --project examples/blink
```

Compilation writes `circuit.json`, `erc.txt`, and `erc.json` under the project's
configured output directory. Electrical errors allow compilation/export;
`noscheme check` returns a failing status when it finds electrical errors.
Structural errors block compilation.

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

See [PROJECT.md](PROJECT.md) for the language and design decisions,
[PLAN.md](PLAN.md) for implementation status and KiCad export commands, and
[experiments/README.md](experiments/README.md) for the manual KiCad checks.

## Tri-state example

Use `nettype.tristate` for a shared bus. Digital inputs and passive attachments
are allowed; each output-capable pin needs `tristate_capable: true` in its
pin definition or instance-pin metadata. ERC checks capability, while the designer
remains responsible for bus arbitration. Run the electrical-only example with:

```sh
uv run --locked noscheme check --project examples/tristate
```

The example intentionally has no footprints; those warnings are expected.

## STM32 proof-of-concept board

```sh
uv run --locked noscheme check --project examples/stm32h523
uv run --locked noscheme export-kicad --project examples/stm32h523 --footprints examples/stm32h523/footprints.json
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

## CI

GitHub Actions runs on pushes, pull requests, and manual dispatch:

- Python 3.11 and 3.14: locked dependencies, Ruff formatting/lint, all tests,
  distribution builds, and installed-wheel parser/compiler smoke checks.
- Python 3.12 with Ubuntu's KiCad library data: all tests, including conversion
  and saved-board fixtures that otherwise skip when KiCad data is unavailable.

CI does not replace PCBNew's manual import/update checks. The KiCad data job uses
Ubuntu's packaged libraries; it is not a KiCad 10 GUI validation job.
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
