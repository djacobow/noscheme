# Editor support

NoScheme provides Vim/Neovim and Emacs integrations, a local VS Code extension, and
an independent language server for diagnostics, navigation, references, rename,
hover, completion, and formatting. Follow
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
two-space indentation with spaces, word motions that include `#`, and `//`
comment formatting. Completion and whole-file formatting come from the language server.
To override indentation, put your preferences in `after/ftplugin/noscheme.vim`
under your Vim/Neovim configuration directory.

## Emacs (Eglot)

Use Emacs 29 or newer and its bundled [Eglot LSP client](https://www.gnu.org/software/emacs/manual/html_node/eglot/).
The integration in `editors/emacs` supplies `noscheme-mode` and starts Eglot when
opening `.nosch` files. It needs no additional Emacs packages. The mode provides
syntax highlighting, `//` and `/* ... */` comments, `#` in identifiers, two-space
indentation, and **Shift-TAB** to dedent. Whole-file formatting comes from the
server. Eglot uses Emacs's built-in completion, Eldoc, Xref, and Flymake interfaces.

### Try it without changing your Emacs configuration

From the NoScheme checkout:

```sh
uv sync --locked
emacs -Q -L "$PWD/editors/emacs" -l noscheme-eglot \
  "$PWD/examples/stm32h523/stm32h523.nosch"
```

`-Q` ignores your personal Emacs configuration, so this also serves as a clean
smoke test. Add `-nw` to run in a terminal. The mode line should show `NoScheme`
and `Eglot`; startup may take a moment while the server analyzes the project.
No circuit exports are written when you open or edit a design.

For someone new to Emacs: `C-` means hold **Ctrl**, `M-` usually means hold **Alt**
(or press **Esc**, release it, then press the following key). `M-x` opens a command
prompt: type the command's name and press **Enter**. Use **C-g** to cancel a command,
**C-x C-f** to open a file, **C-x C-s** to save, and **C-x C-c** to quit.

### Keep it enabled

Add the following to your Emacs init file (normally `~/.emacs.d/init.el`), adjusting
the checkout path. You can open that file with **C-x C-f** and create it if needed:

```elisp
(add-to-list 'load-path "/home/david/personal/noscheme/editors/emacs")
(require 'noscheme-eglot)
```

Restart Emacs, then open a `.nosch` file. The default command is the checkout's
`.venv/bin/noscheme-lsp`; if that executable does not exist, the integration looks
for `noscheme-lsp` on Emacs's `exec-path`. It does not require shell activation of
the virtual environment. For highlighting alone, require `noscheme-mode` instead.
To start LSP manually rather than on opening files, remove the automatic hook:

```elisp
(remove-hook 'noscheme-mode-hook #'eglot-ensure)
;; Use M-x eglot once in each project when wanted.
```

The nearest parent `noscheme.toml` defines the Emacs project for NoScheme buffers,
ahead of a surrounding Git root. Separate designs such as `examples/blink` and
`examples/stm32h523` therefore get separate server sessions. Without a matching
configuration, normal Emacs project discovery applies and the server can provide
syntax-only diagnostics. For shared libraries with ambiguous ownership, specify
`--project` in the command below.

### Commands and completion

| Action | Key or command |
| --- | --- |
| Complete a type, parameter, pin, or connection | **C-M-i** or `M-x completion-at-point` |
| View hover information | Pause on a symbol; `M-x eldoc-doc-buffer` opens the documentation buffer |
| Go to definition / return | **M-.** / **M-,** |
| Find references | **M-?** or `M-x xref-find-references` |
| Rename a supported symbol | `M-x eglot-rename` |
| Format the whole file | `M-x eglot-format-buffer` |
| Apply a code action | `M-x eglot-code-actions` |
| List diagnostics in this buffer | `M-x flymake-show-buffer-diagnostics` |
| List diagnostics across the project | `M-x flymake-show-project-diagnostics` |
| Jump to the next diagnostic | `M-x flymake-goto-next-error` |
| Restart the server after rebuilding it | `M-x eglot-reconnect` |

For example, enter `Capacitor0603<` on a board body line and invoke completion to
get `value: `. After `Capacitor0603<100n> cap(`, completion offers the component's
pins. Eglot supplies candidates to Emacs's completion interface; the stock setup
does **not** automatically open a completion menu merely because you type `<` or
`(`. A user's completion frontend may offer that automatically. Parameter
signature popups are not implemented by the server.

Diagnostics update for unsaved edits. Missing required connections are underlined
at the instance declaration; related information identifies the pin or port
whose contract is being violated. Other syntax errors can temporarily prevent
semantic completion or navigation; fix those first.

### Use the native server or an explicit project

After building the native server as described in [cpp/README.md](../cpp/README.md#build-and-check),
set this variable in your init file, before opening a design:

```elisp
(setq noscheme-eglot-server-command
      '("/home/david/personal/noscheme/.venv/bin/noscheme-lsp"
        "--implementation" "native"
        "--native-server" "/home/david/personal/noscheme/build/native/noscheme-native-lsp"))
```

The value is an argument list, not a shell command; paths containing spaces do
not need embedded quote characters. Append `"--project" "/path/to/design"` when
needed. For a Python server outside this checkout, use a one-element list with
the full path to its installed `noscheme-lsp` executable. After changing the
command, use `M-x eglot-shutdown`, then `M-x eglot` to start a new session.

### Troubleshooting and validation

- **No Eglot connection:** use `M-x eglot` to display the startup error. Confirm
  the configured executable exists and `uv sync --locked` has completed.
- **Server output or unexpected behavior:** use `M-x eglot-events-buffer`,
  `M-x eglot-stderr-buffer`, or `M-x view-echo-area-messages`.
- **No completion popup:** explicitly use **C-M-i**. **M-TAB** is another binding,
  but desktops often intercept it for window switching.
- **Configuration interference:** retry the `emacs -Q` command above. The
  [Eglot quick start](https://www.gnu.org/software/emacs/manual/html_node/eglot/Quick-Start.html)
  explains how Eglot integrates with Emacs's standard editing features.

The automated check launches `emacs -Q --batch` with the real Eglot client and
both server implementations, without reading or modifying your personal init
file. From the checkout, run:

```sh
.venv/bin/python -m pytest -q tests/test_emacs.py
# Select a different native build if desired:
NOSCHEME_NATIVE_LSP="$PWD/build/native/noscheme-native-lsp" \
  .venv/bin/python -m pytest -q tests/test_emacs.py
```

The Python case requires Emacs 29+; the native case additionally requires a built
server. Missing prerequisites are reported as skips. Coverage includes mode
syntax and indentation, project discovery, warning-free byte compilation,
automatic connection, Flymake diagnostics, Xref definitions, Eldoc hover,
rename, formatting, and completion in unsaved buffers. Test project paths contain
spaces. Validated locally with Emacs 30.2 and both language servers; CI installs
Emacs and runs these checks too.

## Language server

`noscheme-lsp` runs an independent LSP server over stdin/stdout using
[pygls](https://pygls.readthedocs.io/en/stable/servers/examples/publish-diagnostics.html).
After `uv sync --locked`, configure your editor's LSP client to run:

```sh
/home/david/personal/noscheme/.venv/bin/noscheme-lsp
```

The language server provides compiler/ERC diagnostics, go-to-definition,
find-references, rename, formatting, and resolved hover details, plus completion for component/block names, connection pins/ports,
imported `role.` / `net.` values, and footprint strings. It checks unsaved buffers, including
imported files, after a 200 ms typing delay. It does not write circuit artifacts,
netlists, or identity state.
Checks include project-local Python ERC extensions just as the CLI does.
Missing required pin or port connections are reported at the instance declaration,
with the interface definition linked as related information. Explicit binding
errors point to the binding itself.

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
and public bundle fields on both sides of connection bindings. Find-references uses the same resolved symbols. Rename supports selected symbols as described below.
A syntax error can temporarily prevent
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
Both servers advertise `<`, `(`, and `,` as automatic completion triggers:
`Capacitor0603<` offers parameter names such as `value: `, while
`Capacitor0603<100n> foo(` offers pins/ports and available connection targets.
Typing `,` requests choices for the next argument. Automatic display requires
an editor completion client; in Vim, manual completion remains **Ctrl-X Ctrl-O**.
These are completion suggestions; a separate signature-help popup is not yet
implemented.
Pin and alias completion also works inside unfinished connection lists such as
`[1..3, VD`. Navigation, references, and hover distinguish individual list
entries; a range represents all of its physical pins, as in an unbracketed range.
Qualified instance names such as `Device.R` and imported aliases are supported.
Requesting completion at the start of a board or block body line lists visible
components, blocks, bundles, and net types. `global `, bundle members, and ports
restrict choices to net types and bundles. `extend ` offers components, blocks,
and net types as bases. Qualified imports and builtin aliases are supported.

Both servers also offer local nets, public block ports, and explicit `global.*`
connections after a connection's colon. Parameter values, numeric metadata,
array indices/dimensions, and numeric ERC arguments offer parameters from the
enclosing definition, including inherited parameters. Imported `num` helpers
are available in these expressions. Connection targets do not include parameters
or another instance's private internals.

Metadata keys come from the schema for the current object, including registered
ERC fields. Enum values respect explicit imports and aliases (for example,
`r.digital_input` after `from noscheme import role as r`); boolean fields offer
`true` and `false`. Arbitrary keys inside `custom:` are not guessed. Strings and
comments do not receive these suggestions. Qualified type and rule completions
replace just the member after the namespace prefix in both implementations.


### Instance code actions

With the cursor on an `inst` declaration, use `:LspCodeAction` in Vim or
`:lua vim.lsp.buf.code_action()` in Neovim. In VS Code, press **Ctrl+.**
(**Cmd+.** on macOS) to open the code-action menu. Both servers offer:

- **Add required connections and parameters**: fill missing required interface
  bindings and parameters without defaults.
- **Add all connections and parameters**: also include optional pins and commented
  placeholders for parameters with defaults.
- **Add metadata template**: insert a commented metadata section to fill in.

These appear in the editor's code-action chooser, separate from completion.
Select an action to apply ordinary text edits; normal undo works. Replace the
`TODO_*` names with declared nets or parameter expressions. Optional parameters
stay commented so defaults continue to apply. There is no snippet Tab navigation.

Existing bindings, aliases, positional arguments, parameter values, and comments
are preserved. Pins marked `role.do_not_connect` are excluded. Arrays are supported;
instances with `elements:` overrides are currently excluded. A partially bound
bundle or pin array is left alone rather than filling its remaining members.
Actions require parseable source and a resolved component/block type, but work
when required connections or values are still missing. Restart a running language
server after installing the update.

Instance argument lists support pin/port and `parameter=` completion, and
bundle-field completion in positional or named connections, on one line or across
several. Their net and parameter references
also support navigation, hover, references, and rename.
Completion also works after `extend`, including an unfinished header such as
`extend Device.` with no name or body yet. Component
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
| Format the file | `<leader>f` |
| Diagnostic details | `<leader>e` |
| Next / previous diagnostic | `]d` / `[d` |
| Check attached clients | `:checkhealth vim.lsp` |

`<leader>` is your configured leader key (backslash by default). Completion
plugins may add their own popup keys.

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
- Run **Format Document** (Shift-Alt-F) and review the result.

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

For example, renaming `Sensor sensor()` to `probe` produces:

```nosch
Sensor probe:
  metadata:
    identity: "sensor"
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
Rename adds `identity` to the instance's `metadata:` section, or appends one after
the instance; its arguments, sections, and comments are left as written.

Physical pin IDs and aliases, internal instance names in reusable blocks,
module/file names, and the board entry point remain unsupported. Moving instances
between scopes and identity-preserving internal renames need a broader model;
no `local_identity` field is introduced. Only modules reachable from the selected
board are included; outside consumers of a reusable library require separate updates.

### Formatting

The server formats a whole file in the canonical layout described in
[LRM §2.5](LRM.md#25-canonical-layout), the same as `noscheme format`. The
editor's tab and indentation options do not apply. In Vim, run
`:LspDocumentFormat`; an optional mapping is:

```vim
autocmd FileType noscheme nmap <buffer> <leader>f <plug>(lsp-document-format)
```

Neovim maps `<leader>f` to `vim.lsp.buf.format()`, and VS Code uses
**Format Document**, so format-on-save works where the editor enables it. A file
with a syntax error is left unchanged; its diagnostic already explains why. A
comment the formatter cannot place is reported as an error and nothing changes.
Range formatting is not supported.

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
- Builtin `role`, `net`, and `unused` members: explanations of their electrical
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

### Selecting an LSP implementation

Existing editor commands continue to start the Python server. You can also use
`noscheme lsp`; both entry points accept the same options:

```sh
noscheme-lsp --implementation python
noscheme lsp --implementation python
```

The launcher also supports `--implementation native`, which runs
`noscheme-native-lsp` from PATH, or the executable supplied with
`--native-server /absolute/path/to/noscheme-native-lsp`. The `--project` option
is forwarded unchanged. Restart the editor's server after changing the command.

Meson now builds an experimental server at `build/native/noscheme-native-lsp`.
It provides syntax, project, semantic, and ERC diagnostics using full-document
updates, plus initial go-to-definition and hover support for project types,
lexical names, parameters, globals, public pin bindings, and nested bundle fields
through array indices/slices. Hover includes types, simple parameter defaults, and
common resolved metadata. Builtin role/net/unused descriptions and registered
ERC rule signatures are available. Initial completion covers imported types,
qualified names, enum members, and ERC rules; an unfinished member such as
`role.` can receive suggestions while retaining its syntax diagnostic.
Connection bindings suggest public pins/aliases and nested bundle fields,
including inherited interfaces and indexed/sliced bundles.
Direct component/instance footprint fields suggest the inherited default and
imported KiCad candidate hints, including empty values and unfinished quotes.
These are selection hints; verify the package dimensions.
Named connection targets suggest local nets, public ports, and explicit global
nets. ERC check calls suggest remaining parameter keywords, selectors, fields,
and missing-data policies, including imported plugin rules and unfinished calls.
Rule names are filtered by pin/net scope. Empty or unfinished connection lists
suggest named pins, plus positional targets for components with interchangeable
pins. Angle-bracket parameter lists suggest names followed by `: `. Already
supplied pins and parameters are excluded.
Connections in an instance body support empty rows, missing targets, and unfinished
pin/bundle-member names; an empty row also offers the `params`, `metadata`, and
`elements` section keywords. Grouped physical pin lists also offer names and aliases,
including across multiple lines, excluding pins already selected in that list or
other connection rows of the same instance. Quoted members and groups after instance-array
selectors are supported; index expressions themselves are left alone.
Native keyword completion also works in a fresh file without a project. It
suggests declarations and sections according to lexical context, inserting the
bare keyword; add the punctuation yourself. It skips comments, strings, and open
argument/index expressions.
Native Find References includes cross-file uses and optional declarations.
Grouped pins support definition and hover, and instance hover shows bounded
elaborated details. A local fallback keeps complete declarations navigable in
standalone and partially broken files, without guessing external imports.
Native whole-file formatting follows the same canonical rules as Python.
Prepare Rename and Rename support the same current scope, with identity
preservation for board-level instances and validation of proposed edits.
Native editor requests wait for
native analysis and registry loading, then can respond while ERC runs.
Project selection uses `--project`, the nearest ancestor `noscheme.toml`, or the
most specific source root among projects in the initialized workspace folders.
Ambiguous workspace matches require `--project`. Unopened dependency files are checked for content changes before snapshot reuse,
and file-watcher notifications invalidate analysis. The launcher passes its own
Python interpreter for ERC plugins.

Interactive requests bypass the native server's typing debounce. The built-in
ERC registry is reused for the server lifetime; restart after updating the
NoScheme installation. User plugin registries reload on each analysis, including
transitive imports. Rename returns versioned document edits when the editor
advertises support, protecting open buffers from stale edit application.

Native analysis runs in the background after a 200 ms typing delay. Superseded
results are discarded and active Python ERC checks are cancelled. Native compiler
phases finish before cancellation takes effect. Keep Python for normal editing
until manual native editor checks are complete. Release performance measurements
are recorded in the native README. See the [native build instructions](../cpp/README.md#experimental-native-lsp).

Selecting native without an available executable fails visibly and never falls
back to Python. The launcher preserves stdio and process signals.
