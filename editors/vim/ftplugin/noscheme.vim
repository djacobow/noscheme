" NoScheme uses space indentation and permits # in identifiers.
if exists('b:did_ftplugin')
  finish
endif
let b:did_ftplugin = 1

setlocal expandtab shiftwidth=4 softtabstop=4
setlocal iskeyword=65-90,97-122,48-57,_,35
setlocal comments=s1:/*,mb:*,ex:*/,://
setlocal commentstring=//\ %s
let b:undo_ftplugin = 'setlocal expandtab< shiftwidth< softtabstop< iskeyword< comments< commentstring<'
