# Editor support

NoScheme provides Vim/Neovim syntax highlighting, a local VS Code extension, and
an independent language server for diagnostics, navigation, references, rename,
hover, and completion. Follow
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

The language server provides compiler/ERC diagnostics, go-to-definition,
find-references, rename, and resolved hover details, plus completion for component/block names, connection pins/ports,
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
named definitions/imports, lexical nets/parameters, explicit pin/port aliases,
and public bundle fields on both sides of connection bindings. Find-references uses the same resolved symbols. Rename supports selected symbols as described below;
formatting is not implemented. A syntax error can temporarily prevent
semantic navigation until the code parses again.

The server uses two persistent Python subprocesses: one for interactive semantic
requests, the other for full diagnostics, including ERC. Slow ERC checks do not
block completion, navigation, or handling new editor messages. Each project is
checked once per snapshot even when several of its files are open. Edits are
coalesced after a 200 ms debounce; an already-running check finishes, but its
results are discarded if newer edits arrived. Request cancellation terminates
that request's worker, which restarts on demand.

Workers reuse ASTs by filename and exact source text, including parse failures.
Only changed files need parsing again, but each changed file is still parsed in
full. Resolved projects and semantic indexes are reused within an unchanged
snapshot. Workers check disk content, configuration, module availability, and
configured ERC module files before reuse. These caches are in memory only.
Changes to Python helpers imported by ERC modules require restarting the server.
The debounce is not a response-time guarantee; cold startup and large changed
files still take time. See [Development](DEVELOPMENT.md#reference-tables-coverage-and-performance)
for measurements and the benchmark command.

The resolver can report several independent mistakes in one update, such as
misspelled nets in separate bindings and an unknown component in another
instance. Checks that depend on a failed result stop to avoid cascading errors.
Grammar parsing, interface construction, and elaboration still stop at their
first error. Structural validation after a successful parse can report multiple
independent errors.

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
  autocmd FileType noscheme nmap <buffer> K <plug>(lsp-hover)
augroup END
```

Use `:LspDefinition`, `:LspHover`, and `:LspNextDiagnostic`. For completion,
run `:setlocal omnifunc=lsp#complete` in the NoScheme buffer, then press
**Ctrl-X Ctrl-O** in Insert mode. Add this to your vimrc to keep that setting:

```vim
autocmd FileType noscheme setlocal omnifunc=lsp#complete
```

The `FileType` hover mapping above also applies to files opened through
`:LspDefinition`. A standalone `nmap <buffer> K ...` in your vimrc only maps
the startup buffer; in other buffers, Vim's default `K` opens a system manual
page and may report "No manual entry". After updating your vimrc, restart Vim
or run `:nmap <buffer> K <plug>(lsp-hover)` in an already-open buffer.

Completion supports incomplete instance/connection statements, inherited aliases,
and numeric pins; accepting a suggestion replaces the current token.
Pin and alias completion also works inside unfinished connection lists such as
`[1..3, VD`. Navigation, references, and hover distinguish individual list
entries; a range represents all of its physical pins, as in an unbracketed range.
Qualified instance names such as `Device.R` and imported aliases are supported.

Compact instances support pin/port and `parameter=` completion, and bundle-field
completion in positional or named connections. Their net and parameter references
also support navigation, hover, references, and rename.
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

### Neovim client setup

Use Neovim 0.11 or newer. The setup was tested with the official stable
[Neovim 0.12.5 release](https://github.com/neovim/neovim/releases/tag/v0.12.5)
and its built-in LSP client. Official Linux release archives can be unpacked
under your home directory without replacing the distribution's package.
Check `nvim --version` after installation; shell aliases can select an older
binary even when your PATH includes the new one.

After `uv sync --locked`, add this to `~/.config/nvim/init.lua`, adjusting the
checkout path:

```lua
dofile('/home/david/personal/noscheme/editors/neovim/noscheme.lua')
```

This loads the repository's highlighting and registers the server using
`vim.lsp.config` / `vim.lsp.enable`. No LSP installer or additional plugin is
required. The nearest `noscheme.toml` identifies the project, with `.git` as
a workspace fallback. Existing completion plugins can use the server too.

Open `examples/stm32h523/peripherals.nosch`. Use:

| Action | Keys / command |
| --- | --- |
| Completion in Insert mode | Ctrl-X Ctrl-O |
| Hover | `K` |
| Definition | `gd` or `gD` |
| References | `gr` |
| Rename | `<leader>rn` |
| Diagnostic details | `<leader>e` |
| Next / previous diagnostic | `]d` / `[d` |
| Check attached clients | `:checkhealth vim.lsp` |

`<leader>` is your configured leader key (backslash by default). Completion
plugins may add their own popup keys. NoScheme does not provide formatting.

#### Manual smoke checklist

- Confirm `:set filetype?` reports `noscheme` and the client attaches.
- Hover over `SWD`, then follow its definition to `interfaces.nosch`; check
  hover and references in that opened buffer too.
- Complete a bundle field after `debug.` and a footprint inside an unfinished
  quoted string. Accept a suggestion and check the inserted text.
- Introduce an error without saving; check its diagnostic, then undo it and
  check that the diagnostic clears.
- In a disposable copy of the example, rename a board-level instance and
  inspect the edits and inserted identity metadata before saving. Re-export
  and confirm that its physical identities remain stable.

A headless check with Neovim 0.12.5 exercised attachment, cross-file definition,
hover, references, unfinished footprint-string completion using inherited KiCad
candidates, and diagnostics from unsaved edits. The user also confirmed
interactive completion, cross-file navigation and hover, and board-instance
rename retaining the original `metadata.identity`.

### VS Code client setup

The local extension in [editors/vscode](../editors/vscode/README.md) provides
highlighting, `.nosch` file detection, and a client for the same language server.
Build and install it following that directory's README, then run
**Developer: Reload Window** from the Command Palette (Ctrl-Shift-P).

Open the NoScheme checkout as a folder after running `uv sync --locked`.
The extension finds `.venv/bin/noscheme-lsp` at the workspace root. If you open
an example directory on its own, set `noscheme.serverPath` in User Settings to
the absolute server executable path. `noscheme.serverArgs` optionally accepts
`["--project", "/absolute/project/path"]`. Reload after changing these settings.
Workspace Trust is required because project-local ERC plugins run Python code.

- Open `examples/stm32h523/components.nosch`; the language mode should be
  **NoScheme**.
- Hover over `Resistor`, use F12 for definition, and Shift-F12 for references.
- Use Ctrl-Space inside a footprint string for completion, including an
  unfinished quote. Inspect the text after accepting a suggestion.
- Introduce an unsaved error and check Problems (Ctrl-Shift-M); undo it and
  confirm the diagnostic clears.
- Use F2 on a board-level instance to rename it. Review the workspace edits
  and retained `metadata.identity` before saving.

Server messages appear in **View → Output → NoScheme**. If no server attaches,
check the executable path, language mode, and workspace trust. In Restricted
Mode the extension is disabled, including its language association: `.nosch`
files may appear as Plain Text. For a project you trust, use **Manage Workspace
Trust**, then reload the window.

With VS Code 1.140.0 and the local NoScheme extension 0.1.0, the user confirmed
highlighting, hover, definition navigation, completion, board-instance rename
retaining `metadata.identity`, and unsaved diagnostics appearing and clearing
after undo. The user also confirmed references and a post-rename export
comparison preserving UUIDs and reference designators.

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
capacitor specializations are in `examples/stm32h523/components.nosch`; edit
their footprint strings there to try the inherited alternatives.

Hints are advisory. You can enter any footprint string, and export still checks
the selected footprint's pads. Matching pins does not establish mechanical
compatibility; check the package dimensions.


### Bundle fields

In a connection, completion after `debug.` suggests the fields of its declared
bundle. Nested bundles, array indexing/slices, explicit `global.` nets, and
inherited public ports are supported. This works on either side of the colon,
including incomplete lines, using current unsaved source and imported files.

For the STM32 example, open
`examples/stm32h523/peripherals.nosch` and try completion after
`debug.` in the `CortexDebug` block. It offers `swclk`, `swdio`, `swo`,
and `reset#`. `:LspDefinition` on one of those field names jumps to its
declaration in `interfaces.nosch`; hover identifies the bundle field.
Private instance internals and scalar-net members are not suggested. Index
bounds and connection shapes remain compiler checks.


### Find references

Place the cursor on a definition or use and run `:LspReferences`. An optional
Vim mapping is:

```vim
autocmd FileType noscheme nmap <buffer> gr <plug>(lsp-references)
```

Results cover the selected board's reachable module graph, including unopened
dependencies and current unsaved buffers. Supported targets include component,
block, bundle, and net type definitions, local and global nets, parameters, public ports,
physical pins/aliases, and bundle fields. Imported aliases are grouped with
their original definition; unrelated symbols with the same spelling stay
separate. Import sites count as references. The client's `includeDeclaration`
option controls inclusion of declarations.

A physical pin range groups the pins declared at that location; querying it
returns the combined references without duplicate locations. Results are
source references, so an instance array does not repeat the same location for
each elaborated part. Comments and literal strings are not searched. Syntax or
resolution errors may limit results; old buffer locations are never reused.
This operation does not edit source. Rename is available separately below.


### Rename

Place the cursor on a declaration or reference and run `:LspRename`. Vim asks
for the new name and applies the returned edits across buffers. Save the affected
buffers when satisfied. An optional mapping is:

```vim
autocmd FileType noscheme nmap <buffer> <leader>rn <plug>(lsp-rename)
```

Supported targets are nets, parameters, component/block/bundle/net type names, public
block ports, and bundle fields. Qualified references keep their module prefix.
Place the cursor on the final name in `Device.R`, not on `Device`.

Explicit import aliases own their spelling. For
`from Device import R as KiCadResistor`:

- Renaming `R` changes the original definition, import source names, and uses
  that have no explicit alias. `KiCadResistor` stays unchanged.
- Renaming `KiCadResistor` changes the alias binding and its uses, including
  unaliased re-exports, while leaving the original `R` unchanged.

Before offering edits, the server parses and resolves the proposed project in
memory. Name collisions and broken bindings reject the rename. Rename requires
a project that already parses and resolves; electrical ERC errors do not block
it. Open files use their current unsaved contents. Clients supporting versioned
workspace edits receive buffer versions; the server itself never writes files
or regenerates circuit artifacts.

Board-level component and block instances can also be renamed. The server adds
`metadata.identity` with the old instance name unless an explicit identity already
exists. Arrays keep their index suffixes; renaming a board-level block preserves
its descendants' identities, including explicitly identified children. Repeated
renames retain the first preserved identity.

For example, renaming `inst sensor: Sensor()` to `probe` produces:

```nosch
inst probe: Sensor(
    metadata:
        identity: "sensor"
)
```

The server elaborates the original and proposed board in memory and checks that
physical identities, part properties, and physical net membership are unchanged.
This operation therefore requires successful elaboration as well as resolution;
electrical ERC errors still do not block it. No export state is needed or modified.
After accepting the edit, compile/export and reimport normally. Footprint UUIDs
and reference designators remain stable; source paths and private net names may
change with the instance spelling.

Targeted waiver pin paths on the containing board are updated too. Comments, custom text, reasons, and existing identity
strings stay unchanged. The identity insertion preserves comments and indentation.
For a compact instance, rename expands its arguments into `connections:` and
`params:` sections so it can add `metadata.identity`. Positional connections become
explicit physical pin bindings; their pad assignments stay unchanged.

Physical pin IDs and aliases, internal instance names in reusable blocks,
module/file names, and the board entry point remain unsupported. Moving instances
between scopes and identity-preserving internal renames need a broader model;
no `local_identity` field is introduced. Only modules reachable from the selected
board are included; outside consumers of a reusable library require separate updates.


### Hover details

Use `:LspHover` on a declaration or use. Hover includes:

- Net types: base type, builtin electrical type, and merged metadata, including checks.
- Components, blocks, and bundles: base definition, parameter defaults or required
  values, public interface, and merged metadata. Core metadata shows its source
  module and line, including inherited footprint selections.
- Instance names: elaborated identities and, for physical parts, actual values,
  footprints, and parameters after instance overrides. Arrays and reusable blocks
  may produce several values; the display lists a bounded set of instances.
- Pins and ports: types, aliases, roles, unused-pin policy, and electrical
  metadata. Physical pin connection bindings also show effective roles and nets.
- Nets and bundle fields: declared types, field metadata, and net endpoint counts.
- Builtin `role`, `nettype`, and `unused` members: explanations of their electrical
  meaning and ERC behavior. This also works with import aliases and qualified
  names such as `noscheme.role.open_drain`.

Hover uses a bold heading and bullet lists when the client prefers Markdown
(including vim-lsp), with spacing between elaborated instances. Clients that
prefer plain text receive plain text with a separated heading. Source text is
escaped so values and names cannot accidentally become Markdown formatting.

Defaults are labeled separately from actual instance values. If elaboration
fails, instance hover explicitly falls back to definition defaults. Large custom
metadata and footprint candidate lists are summarized. Current unsaved buffers
are used throughout; hover does not write files or export artifacts.

For example, hover over `resistor` in the `Indicator` block in
`examples/stm32h523/components.nosch` to see the three elaborated
LED resistors, their 1kOhm values, and their selected 0603 footprints.

## ERC application completion

Inside `checks`, completion suggests imported rules of the right scope (pin or
net) as you type a call, such as `std.co` → `std.count`. Inside the parentheses
it offers the rule's remaining parameters as `name=` keywords, then values for
the parameter being typed: predicates and boolean fields for selectors, endpoint
or net field names for field parameters, and the `missing` policies. Calls may
span lines. Hovering a rule shows its parameters and summary. Field-name
arguments are not linked to same-named parameters, so renaming a parameter leaves
them unchanged. The registry includes modules from the project's `erc_modules`
configuration.
Application-specific ERC diagnostics use the same source locations and related
locations as command-line reports.
