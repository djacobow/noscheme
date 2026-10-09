grammar NoScheme;
tokens { INDENT, DEDENT }
module: NL* (importDecl | definition)* EOF;
importDecl: FROM path IMPORT importedName (COMMA importedName)* NL | IMPORT path NL;
importedName: name (AS name)?;
definition: kind name (EXTENDS path)? COLON NL INDENT definitionItem+ DEDENT;
kind: COMPONENT | BLOCK | BOARD | BUNDLE | NETTYPE;
definitionItem: instance | netDecl | section | field;
section: sectionName COLON NL INDENT field+ DEDENT;
sectionName: PARAMS | DEFAULTS | PINS | PORTS | METADATA;
field: (fieldKey | aliasedKey) COLON (fieldValue (EQUAL expr)?)? NL (INDENT field+ DEDENT)?;
// Ranges and tolerances are field values only: `voltage: 4.75..5.25`, `voltage: 5 ± 5%`.
fieldValue: expr (RANGE expr | TOLERANCE expr PERCENT?)?;
// A pin declaration may name its aliases: `1 (cathode, K): role.passive`.
aliasedKey: pinKey LPAREN pinKey (COMMA pinKey)* COMMA? RPAREN;
fieldKey: keyItem | LBRACK keyItem (COMMA keyItem)* COMMA? RBRACK;
keyItem: pinKey (RANGE pinKey)?;
pinKey: name | INT | STRING;
netDecl: GLOBAL? NET name (COMMA name)* COLON expr NL (INDENT field+ DEDENT)?;
// The indented body holds connections, then params, metadata, and elements.
instance: INST name index* COLON path (LPAREN inlineArguments? RPAREN)? NL (INDENT (binding+ instSection* | instSection+) DEDENT)?;
inlineArguments: inlineArgument (COMMA inlineArgument)* COMMA?;
// MUL is `*`: connect remaining pins and ports to same-name nets.
inlineArgument: pinKey COLON target | name EQUAL expr | MUL | target;
instSection: arguments | instanceMetadata | elements;
binding: endpoint COLON target NL;
// Section words start sections here; quote a pin with such a name.
endpoint: (index+ DOT fieldKey | bindingKey) endpointSuffix*;
bindingKey: bindingPin (RANGE bindingPin)? | LBRACK keyItem (COMMA keyItem)* COMMA? RBRACK;
bindingPin: bindingName | INT | STRING;
// flat(net) binds an array net's elements in row-major order.
target: reference | name LPAREN reference RPAREN;
endpointSuffix: DOT name | index;
arguments: PARAMS COLON NL INDENT argument+ DEDENT;
argument: name COLON expr NL;
instanceMetadata: METADATA COLON NL INDENT field+ DEDENT;
elements: ELEMENTS COLON NL INDENT elementOverride+ DEDENT;
elementOverride: index+ COLON NL INDENT (arguments | instanceMetadata)+ DEDENT;
expr: shift;
shift: sum (SHIFT sum)*;
sum: product (ADD product)*;
product: unary (MUL unary)*;
unary: ADD unary | postfix;
postfix: atom suffix*;
atom: SCALED | NUMBER | INT | STRING | name | LPAREN expr RPAREN | LBRACK (expr (COMMA expr)* COMMA?)? RBRACK;
suffix: DOT name | index | LPAREN (callArgument (COMMA callArgument)* COMMA?)? RPAREN;
callArgument: name EQUAL expr | expr;
reference: name (DOT name | index)*;
index: LBRACK expr (COLON expr)? RBRACK;
path: name (DOT name)*;
// Lark's contextual lexer permits structural words as names outside their keyword context.
name: bindingName | PARAMS | METADATA | ELEMENTS;
bindingName: NAME | FROM | IMPORT | AS | COMPONENT | BLOCK | BOARD | BUNDLE | NETTYPE | EXTENDS | INST | NET | GLOBAL | DEFAULTS | PINS | PORTS;
FROM:'from'; IMPORT:'import'; AS:'as'; COMPONENT:'component'; BLOCK:'block'; BOARD:'board'; BUNDLE:'bundle'; NETTYPE:'nettype'; EXTENDS:'extends'; INST:'inst'; NET:'net'; GLOBAL:'global'; PARAMS:'params'; DEFAULTS:'defaults'; PINS:'pins'; PORTS:'ports'; METADATA:'metadata'; ELEMENTS:'elements';
SHIFT: '<<' | '>>'; TOLERANCE: '\u00B1' | '+/-'; PERCENT: '%'; ADD: '+' | '-'; MUL: '*' | '/';
RANGE:'..'; DOT:'.'; COMMA:','; COLON:':'; EQUAL:'=';
LPAREN:'('; RPAREN:')'; LBRACK:'['; RBRACK:']';
SCALED: [0-9]+ ('.' [0-9]+)? [afpnumkMGTPR] [0-9]*;
NUMBER: [0-9]+ '.' [0-9]+ ([eE] [+-]? [0-9]+)? | [0-9]+ [eE] [+-]? [0-9]+;
INT: [0-9]+;
NAME: [A-Za-z_#] [A-Za-z0-9_#]*;
STRING: '"' ('\\' ~[\r\n] | ~["\\\r\n])* '"';
BLOCK_COMMENT: '/*' .*? '*/' -> channel(HIDDEN);
LINE_COMMENT: '//' ~[\r\n]* -> channel(HIDDEN);
NL: ('\r'? '\n' [ \t]*)+;
WS: [ \t\f]+ -> skip;
INVALID: .;
