" NoScheme file detection for Vim and Neovim.
augroup noscheme_filetype
  autocmd!
  autocmd BufRead,BufNewFile *.nosch setfiletype noscheme
augroup END
