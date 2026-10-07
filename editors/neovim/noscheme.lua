-- Load with dofile('/path/to/noscheme/editors/neovim/noscheme.lua').
-- Requires Neovim 0.11+ and `uv sync --locked` in the checkout.
local source = debug.getinfo(1, 'S').source:sub(2)
local checkout = vim.fn.fnamemodify(source, ':p:h:h:h')
vim.opt.runtimepath:append(checkout .. '/editors/vim')
vim.filetype.add({ extension = { nosch = 'noscheme' } })

vim.lsp.config('noscheme', {
  cmd = { checkout .. '/.venv/bin/noscheme-lsp' },
  filetypes = { 'noscheme' },
  root_markers = { 'noscheme.toml', '.git' },
  on_attach = function(_, bufnr)
    vim.bo[bufnr].omnifunc = 'v:lua.vim.lsp.omnifunc'
    local function map(key, action, desc)
      vim.keymap.set('n', key, action, { buffer = bufnr, desc = desc })
    end
    map('K', vim.lsp.buf.hover, 'NoScheme hover')
    map('gd', vim.lsp.buf.definition, 'NoScheme definition')
    map('gD', vim.lsp.buf.definition, 'NoScheme definition')
    map('gr', vim.lsp.buf.references, 'NoScheme references')
    map('<leader>rn', vim.lsp.buf.rename, 'NoScheme rename')
    map('<leader>f', vim.lsp.buf.format, 'NoScheme format file')
    map('<leader>e', vim.diagnostic.open_float, 'Show diagnostic')
  end,
})
vim.lsp.enable('noscheme')
