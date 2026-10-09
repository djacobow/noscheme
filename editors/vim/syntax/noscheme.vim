" Vim syntax file for the NoScheme PCB language (.nosch).
if exists('b:current_syntax')
  finish
endif

syntax case match
syntax iskeyword 65-90,97-122,48-57,_,35

syntax keyword noschemeDeclaration component block board bundle nextgroup=noschemeDefinition skipwhite
" nettype declares a net type only before a name; nettype.member is a builtin value.
syntax match noschemeDeclaration /\<nettype\ze\s\+[A-Za-z_#]/ nextgroup=noschemeDefinition skipwhite
syntax match noschemeDefinition /[A-Za-z_#][A-Za-z0-9_#]*/ contained
syntax keyword noschemeKeyword inst net global extends
syntax keyword noschemeImport from import as
syntax keyword noschemeType number string
syntax keyword noschemeConstant true false no_connect
syntax match noschemeField /^\s*\zs[A-Za-z_#][A-Za-z0-9_#]*\ze\s*:/
syntax match noschemeSection /^\s*\zs\%(pins\|ports\|params\|defaults\|metadata\|elements\|checks\)\ze\s*:/
syntax match noschemeBuiltin /\<\%(role\|nettype\|unused\|bias\|std\)\.[A-Za-z_#][A-Za-z0-9_#]*\>/
syntax match noschemeFunction /\<num\.[A-Za-z_#][A-Za-z0-9_#]*\>/
syntax match noschemeFunction /\.\zsformat\ze\s*(/
syntax match noschemeNumber /\<[0-9]\+\%([.][0-9]\+\)\?\%([eE][+-]\?[0-9]\+\)\?\>/
" SI-prefixed literals: 4.7k, 4k7, 100n, 4R7.
syntax match noschemeNumber /\<[0-9]\+\%([.][0-9]\+\)\?[afpnumkMGTPR]\%([0-9]\+\)\?\>/
syntax match noschemeOperator /<<\|>>\|+\/-\|\%u00b1\|[=+*\/%-]/
syntax match noschemeDelimiter /[][():,.]/

syntax region noschemeString start=/"/ skip=/\\./ end=/"/ oneline contains=noschemeEscape
syntax match noschemeEscape /\\\%(["\\\/bfnrt]\|u[0-9A-Fa-f]\{4}\)/ contained
syntax keyword noschemeTodo TODO FIXME XXX NOTE contained
syntax region noschemeLineComment start=+//+ end=+$+ contains=noschemeTodo,@Spell
" Block comments deliberately do not nest.
syntax region noschemeBlockComment start=+/\*+ end=+\*/+ keepend contains=noschemeTodo,@Spell
syntax sync fromstart

highlight default link noschemeDeclaration Keyword
highlight default link noschemeDefinition Type
highlight default link noschemeKeyword Keyword
highlight default link noschemeImport Include
highlight default link noschemeType Type
highlight default link noschemeConstant Constant
highlight default link noschemeField Identifier
highlight default link noschemeSection Keyword
highlight default link noschemeBuiltin Constant
highlight default link noschemeFunction Function
highlight default link noschemeNumber Number
highlight default link noschemeOperator Operator
highlight default link noschemeDelimiter Delimiter
highlight default link noschemeString String
highlight default link noschemeEscape SpecialChar
highlight default link noschemeTodo Todo
highlight default link noschemeLineComment Comment
highlight default link noschemeBlockComment Comment

let b:current_syntax = 'noscheme'
