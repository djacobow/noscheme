grammar NoScheme;
tokens { INDENT, DEDENT }
module: NL* (importDecl | definition | extension)* EOF;
importDecl: FROM path IMPORT importedName (COMMA importedName)* NL | IMPORT path NL;
importedName: name (AS name)?;
definition: kind name COLON NL INDENT definitionItem+ DEDENT;
kind: COMPONENT | BLOCK | BOARD | BUNDLE;
// `extend Base<params> Name:` specializes a component, block, or net type, and
// is of the base's kind; `<...>` gives parameter defaults. The body is optional.
extension: EXTEND path typeArguments? name (NL | COLON NL INDENT definitionItem+ DEDENT);
definitionItem: declaration | section | field;
section: sectionName COLON NL INDENT (field | typedField)+ DEDENT;
sectionName: PARAMS | PINS | PORTS | METADATA;
field: (fieldKey | aliasedKey) COLON (fieldValue (EQUAL expr)?)? NL (INDENT field+ DEDENT)?;
// Ranges and tolerances are field values only: `voltage: 4.75..5.25`, `voltage: 5 ± 5%`.
fieldValue: expr (RANGE expr | TOLERANCE expr PERCENT?)?;
// A pin declaration may name its aliases: `1 (cathode, K): role.passive`.
aliasedKey: pinKey LPAREN pinKey (COMMA pinKey)* COMMA? RPAREN;
fieldKey: keyItem | LBRACK keyItem (COMMA keyItem)* COMMA? RBRACK;
keyItem: pinKey (RANGE pinKey)?;
pinKey: name | INT | STRING;
// Declarations put the type first, then names with their array dimensions; a ':'
// ends the line when an indented body follows. In params and ports:
// `number gain = 2`, `net.power supply:`.
typedField: path declarator (COMMA declarator)* (EQUAL expr)? (NL | COLON NL INDENT field+ DEDENT);
declarator: name index*;
// In a block, board, or bundle: a net or an instance, told apart once the type
// resolves. `<...>` holds parameters, `(...)` connections; the body holds net
// fields, or connections then params, metadata, and elements sections.
declaration: GLOBAL? path typeArguments? declarator (COMMA declarator)* argumentList? (NL | COLON NL INDENT (bodyEntry+ instSection* | instSection+) DEDENT);
// Shifts in parameter values need parentheses: `<n: (a >> 1)>`.
typeArguments: LANGLE (typeArgument (COMMA typeArgument)* COMMA?)? RANGLE;
typeArgument: name COLON sum | sum;
argumentList: LPAREN inlineArguments? RPAREN;
inlineArguments: inlineArgument (COMMA inlineArgument)* COMMA?;
// MUL is `*`: connect remaining pins and ports to same-name nets.
inlineArgument: pinKey COLON target | MUL | target;
instSection: arguments | instanceMetadata | elements;
// A connection, or a net field; which one depends on the declaration's type.
bodyEntry: endpoint COLON fieldValue? NL (INDENT field+ DEDENT)?;
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
bindingName: NAME | FROM | IMPORT | AS | COMPONENT | BLOCK | BOARD | BUNDLE | EXTEND | GLOBAL | PINS | PORTS;
FROM:'from'; IMPORT:'import'; AS:'as'; COMPONENT:'component'; BLOCK:'block'; BOARD:'board'; BUNDLE:'bundle'; EXTEND:'extend'; GLOBAL:'global'; PARAMS:'params'; PINS:'pins'; PORTS:'ports'; METADATA:'metadata'; ELEMENTS:'elements';
SHIFT: '<<' | '>>'; TOLERANCE: '\u00B1' | '+/-'; PERCENT: '%'; ADD: '+' | '-'; MUL: '*' | '/';
RANGE:'..'; DOT:'.'; COMMA:','; COLON:':'; EQUAL:'=';
LPAREN:'('; RPAREN:')'; LBRACK:'['; RBRACK:']'; LANGLE:'<'; RANGLE:'>';
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
