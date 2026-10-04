# Editor support

NoScheme provides Vim/Neovim syntax highlighting and an independent language
server for diagnostics, navigation, hover, and completion. Follow
[Development setup](DEVELOPMENT.md#environment-and-checks) to install the tools.

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
imported `role.` / `nettype.` values, and footprint strings. It checks unsaved buffers, including
imported files, after a 200 ms typing delay. It does not write circuit artifacts,
netlists, or identity state.
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
**Ctrl-X Ctrl-O** in Insert mode. Add this to your vimrc to keep that setting:

```vim
autocmd FileType noscheme setlocal omnifunc=lsp#complete
```

Completion supports incomplete instance/connection statements, inherited aliases,
and numeric pins; accepting a suggestion replaces the current token.
Qualified instance names such as `Device.R` and imported aliases are supported.
Completion also works after `extends`, including an unfinished header such as
`component Resistor extends Device.` with no colon or body yet. Component
headers suggest components; block headers suggest blocks. The definition being
edited is excluded. Both local aliases and qualified names are available.
Definition navigation and hover follow both forms to the original declaration;
pin and footprint completion use that declaration's inherited interface and hints.
It does not insert imports or infer private interface members. For explicit project
selection, append `'--project', '/absolute/path/to/project'` to the command list.
This follows the [vim-lsp registration interface](https://github.com/prabirshrestha/vim-lsp).
Other LSP clients, including Neovim's, use the same server command and `noscheme`
filetype. No editor configuration is modified by installing NoScheme.

### Footprint completion

Inside a component or instance's `metadata.footprint` string, use
`Ctrl-X Ctrl-O` to select a footprint. This works with an unfinished opening
quote and replaces the whole string. The component's default is listed first,
followed by saved candidates matching the typed prefix. Specializations inherit
these hints; imported files and unsaved buffers are supported.

The converter records hints under `metadata.custom.kicad_footprint_candidates`
when given `--footprint-root`. The LSP reads this saved list and does not require
KiCad libraries. Existing converted libraries need reconversion to acquire
alternatives; a component's default can still be suggested without a list.
The STM32 example includes the MCU's saved hint. Its hand-written resistor and
capacitor specializations are in `examples/stm32h523/stm32h523_components.nosch`; edit
their footprint strings there to try the inherited alternatives.

Hints are advisory. You can enter any footprint string, and export still checks
the selected footprint's pads. Matching pins does not establish mechanical
compatibility; check the package dimensions.
