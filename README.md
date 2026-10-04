# NoScheme

A text-based PCB design language and compiler targeting KiCad. Designs explicitly
instantiate parts and connect named nets, with reusable blocks, arrays, metadata,
and electrical checks.

This is an early implementation. The LED and 58-part STM32 examples compile
and export with explicit footprints. ERC reports list checks
that remain unimplemented. The STM32 netlist has been imported successfully into
KiCad; placement and routing are deferred.

## Vim and Neovim

The editor runtime is in `editors/vim`. If you use **vim-plug**, add this
inside your existing `plug#begin()` / `plug#end()` block:

```vim
Plug '/home/david/personal/noscheme/editors/vim'
```

This is a local plugin, so no download or `:PlugInstall` is needed. Adjust the
path if your checkout is elsewhere. Remove any earlier NoScheme `runtimepath^=`
line: prepending before a no-argument `plug#begin()` changes vim-plug's default
plugin directory and can make installed plugins disappear.

Without a plugin manager, add these lines to `~/.vimrc`, before existing
`filetype` or `syntax` setup. Append the path to preserve the first runtime entry:

```vim
set runtimepath+=/home/david/personal/noscheme/editors/vim
filetype plugin on
syntax enable
```

Restart Vim and open a `.nosch` file. Existing files and new buffers are detected
as `noscheme`; `:set filetype?` should confirm this. Colors follow your current
colorscheme. Keeping this runtime path pointed at the checkout picks up future
highlighting updates when Vim restarts.

The same lines work in Neovim's `~/.config/nvim/init.vim`. For `init.lua`, use:

```lua
vim.opt.runtimepath:append("/home/david/personal/noscheme/editors/vim")
vim.cmd("filetype plugin on")
vim.cmd("syntax enable")
```

Highlighting covers declarations, sections, metadata keys, qualified enums and
helpers, numbers, strings, and both comment forms. `#` is part of an identifier,
not a comment marker, and block comments do not nest. The filetype plugin sets
four-space indentation with spaces, word motions that include `#`, and `//`
comment formatting. It does not provide completion or automatic formatting.
To override indentation, put your preferences in `after/ftplugin/noscheme.vim`
under your Vim/Neovim configuration directory.

## Language server

`noscheme-lsp` runs an independent LSP server over stdin/stdout using
[pygls](https://pygls.readthedocs.io/en/stable/servers/examples/publish-diagnostics.html).
After `uv sync --locked`, configure your editor's LSP client to run:

```sh
/home/david/personal/noscheme/.venv/bin/noscheme-lsp
```

The first version provides compiler/ERC diagnostics, go-to-definition, and basic
symbol hover, plus completion for component/block names, connection pins/ports,
and imported `role.` / `nettype.` values. It checks unsaved buffers, including imported files, after a 200 ms
typing delay. It does not write circuit artifacts, netlists, or identity state.
Checks include project-local Python ERC extensions just as the CLI does.

Project selection first looks for `noscheme.toml` in parent directories, then
checks configurations within the editor workspace whose source/library paths
contain the file. Ambiguous ownership requires an explicit selection. For the
STM32 example, a reliable command is:

```sh
/home/david/personal/noscheme/.venv/bin/noscheme-lsp --project /home/david/personal/noscheme/examples/stm32h523
```

Without a project, files receive syntax diagnostics only. Files outside the
board's reachable import graph are also only syntax-checked. Navigation covers
named definitions/imports, lexical nets/parameters, and explicit pin/port aliases
in connection bindings. Bundle member navigation, rename, references,
and formatting are not implemented. A syntax error can temporarily prevent
semantic navigation until the code parses again. Analysis currently performs
serialized full checks; incremental compilation and background cancellation are
future improvements.

### Vim client setup

Keep the existing NoScheme highlighting plugin. With vim-plug, add the LSP client
inside your existing plugin block, then run `:PlugInstall`:

```vim
Plug 'prabirshrestha/vim-lsp'
```

After `plug#end()`, register the server (adjust the checkout path):

```vim
augroup noscheme_lsp
  autocmd!
  autocmd User lsp_setup call lsp#register_server({
        \ 'name': 'noscheme',
        \ 'cmd': {info -> ['/home/david/personal/noscheme/.venv/bin/noscheme-lsp']},
        \ 'allowlist': ['noscheme'],
        \ })
augroup END
```

Use `:LspDefinition`, `:LspHover`, and `:LspNextDiagnostic`. For completion,
run `:setlocal omnifunc=lsp#complete` in the NoScheme buffer, then press
**Ctrl-X Ctrl-O** in Insert mode. Add `autocmd FileType noscheme setlocal
omnifunc=lsp#complete` as one line in your vimrc to keep that setting.
Completion supports incomplete instance/connection statements, inherited aliases,
and numeric pins; accepting a suggestion replaces the current token.
It does not insert imports or infer private interface members. For explicit project
selection, append `'--project', '/absolute/path/to/project'` to the command list.
This follows the [vim-lsp registration interface](https://github.com/prabirshrestha/vim-lsp).
Other LSP clients, including Neovim's, use the same server command and `noscheme`
filetype. No editor configuration is modified by installing NoScheme.

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

Optional `erc_modules = ["checks.py"]` in `noscheme.toml` enables Python ERC
extensions. Pull-resistance and capacitance budgets use the same registry and
run only on nets with declared limits. Try the included example:

```sh
uv run --locked noscheme check --project examples/erc_extensions
```

See [PROJECT.md](PROJECT.md) for the language and design decisions,
[PLAN.md](PLAN.md) for implementation status and KiCad export commands, and
[experiments/README.md](experiments/README.md) for the manual KiCad checks.

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
and LEDs. See PROJECT.md for the pin map and remaining hardware assumptions.

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

### Tri-state buses

Use `nettype.tristate` for a shared bus. Digital inputs and passive attachments
are allowed; each output-capable pin needs `tristate_capable: true` in its
pin definition or instance-pin metadata. ERC checks capability, while the designer
remains responsible for bus arbitration. Run the electrical-only example with:

```sh
uv run --locked noscheme check --project examples/tristate
```

The example intentionally has no footprints; those warnings are expected.
