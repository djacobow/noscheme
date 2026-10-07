# NoScheme for VS Code

Connects `.nosch` files to the NoScheme language server for diagnostics,
completion, hover, definition, references, rename, and formatting.

Run `uv sync --locked` in the NoScheme checkout, then open that checkout in
VS Code. The extension finds `.venv/bin/noscheme-lsp` in the workspace.
For other workspaces, set `noscheme.serverPath` to the absolute executable path.
Optional `noscheme.serverArgs` can select a project with
`["--project", "/absolute/project/directory"]`.
Reload the window after changing server settings. Trusted workspaces are
required because project-local ERC plugins execute Python code.

## Build and install locally

From this directory:

```sh
npm ci
mkdir -p ../../build
npm run package
code --install-extension ../../build/noscheme-0.1.0.vsix --force
```

Run **Developer: Reload Window**, then open a `.nosch` file.
Use F12 for definition, Shift-F12 for references, F2 for rename, Ctrl-Space
for completion, Shift-Alt-F to format, and mouse hover for documentation. Diagnostics appear in
Problems (Ctrl-Shift-M). The NoScheme output channel contains server messages.
