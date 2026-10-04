# NoScheme

A text-based PCB design language and compiler targeting KiCad. Designs explicitly
instantiate parts and connect named nets, with reusable blocks, arrays, metadata,
and electrical checks.

This is an early implementation. The small LED example compiles and exports;
the STM32 reference board still contains placeholders. ERC reports list checks
that remain unimplemented. Production-generated netlists still need GUI validation.

## Development

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

See [PROJECT.md](PROJECT.md) for the language and design decisions,
[PLAN.md](PLAN.md) for implementation status and KiCad export commands, and
[experiments/README.md](experiments/README.md) for the manual KiCad checks.

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
