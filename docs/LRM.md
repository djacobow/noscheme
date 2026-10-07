# NoScheme Language Reference Manual

**Language version:** `0.1`

**Scope:** the implemented language and compiler in this repository.

Version `0.1` is experimental and has not been frozen. Syntax and semantics can
change while retaining this version number; pin a repository revision together
with project sources and converted library snapshots for exact reproducibility.
An explicit future freeze will establish a compatibility baseline. After that
freeze, breaking source-language changes require a new language version. The
compiler currently accepts only `language_version = "0.1"`; artifact/state schema
versions are separate and do not imply source-language compatibility.

NoScheme describes a circuit as explicit physical parts connected by named nets.
The compiler expands reusable blocks and arrays, checks connectivity and declared
electrical constraints, and exports a netlist for KiCad. Placement and routing
belong to the CAD tool.

This manual is the normative reference for the implemented source language and
compiler behavior. [DEVELOPMENT.md](DEVELOPMENT.md)
covers installation and commands; [EDITOR_SUPPORT.md](EDITOR_SUPPORT.md) covers
editor setup; [PROJECT.md](PROJECT.md) records design decisions;
[PLAN.md](PLAN.md) tracks implementation work. Features identified as unsupported
are not available merely because their syntax resembles another language.

## Contents

1. [Projects and modules](#1-projects-and-modules)
2. [Lexical rules](#2-lexical-rules)
3. [Declarations](#3-declarations)
4. [Parameters and expressions](#4-parameters-and-expressions)
5. [Instances and connections](#5-instances-and-connections)
6. [Bundles and arrays](#6-bundles-and-arrays)
7. [Specialization](#7-specialization)
8. [Metadata reference](#8-metadata-reference)
9. [Electrical roles and net types](#9-electrical-roles-and-net-types)
10. [Electrical checks](#10-electrical-checks)
11. [ERC choices and configurations](#11-erc-choices-and-configurations)
12. [Extending ERC](#12-extending-erc)
13. [CAD identities and export](#13-cad-identities-and-export)
14. [Commands and diagnostics](#14-commands-and-diagnostics)
15. [Complete example](#15-complete-example)
16. [Limits and unsupported features](#16-limits-and-unsupported-features)

Examples described as fragments belong inside the indicated enclosing construct.
A complete project requires a TOML configuration and a selected board definition.

## 1. Projects and modules

### 1.1 Project configuration

A project describes one board. Its `noscheme.toml` contains these five required
keys:

```toml
language_version = "0.1"
board = "main.Demo"
source_paths = ["src"]
library_paths = ["lib"]
output_dir = "build"
```

| Key | Meaning |
|---|---|
| `language_version` | The supported version string, currently `"0.1"` |
| `board` | Dotted module path followed by the board name |
| `source_paths` | Source search directories |
| `library_paths` | Additional module search directories; may be empty |
| `output_dir` | Directory for generated artifacts |
| `erc_modules` | Optional list of Python ERC extension files |
| `erc_rule_sets` | Optional list of automatic ERC rule sets; defaults to `["std.default"]`, or `[]` to disable automatic policies |

Paths resolve relative to the configuration file. Search directories must exist,
be distinct, and collectively provide at least one search root. Both path lists
participate in the same lookup: there is no first-match precedence. Unknown
configuration keys are errors.

### 1.2 Modules and imports

Source files use the `.nosch` suffix. The module `parts.passives` corresponds to
`parts/passives.nosch` under a configured search root. Multiple matches, missing
modules, import cycles, and symlink escapes from a search root are errors.
Only modules reachable from the selected board module are loaded by the compiler.

```nosch
from parts.passives import Resistor, Capacitor
from noscheme import role, nettype, unused, bias, num, std
```

Both module-qualified imports and local aliases are supported:

```nosch
import Device
from Device import R as KiCadResistor

component Resistor extends Device.R:
  metadata:
    footprint: "Resistor_SMD:R_0603_1608Metric"
```

`import Device` exposes definitions as `Device.R`, not bare `R`.
`from Device import R as KiCadResistor` binds only `KiCadResistor` locally.
Each name in a comma-separated `from` import may have its own `as` alias.
Qualified names work in inheritance, instance types, and bundle types.
For `import parts.passives`, references use `parts.passives.Resistor`;
this does not import sibling modules or a package initializer.
`import Device.R` would load a module at `Device/R.nosch`, not select
the component `R` from `Device`.

Wildcard imports, module aliases (`import Device as D`), and implicit
standard-library imports are not supported. Imported names can be re-exported
by importing them from another module. Their original definition identity is
preserved. Conflicting local/imported names and module namespace prefixes are
errors; distinct sibling module imports may share a namespace prefix.

`noscheme` is reserved for builtins:

| Import | Provides |
|---|---|
| `role` | Endpoint electrical roles |
| `nettype` | Scalar net types |
| `unused` | Connection policies |
| `bias` | Explicit pull-up/pull-down assertions |
| `num` | Numeric conversion and rounding helpers |
| `std` | Standard ERC rules and predicates for `checks` (section 12) |

Use qualified builtin values: `role.passive`, not bare `passive`. An attribute's
context does not supply an implicit namespace. Builtins also support aliases,
such as `from noscheme import role as electrical` with
`electrical.passive`, and module imports, such as `import noscheme` with
`noscheme.role.passive`. Module qualification exposes exported definitions;
it never grants access to a component or block's private internals.

## 2. Lexical rules

### 2.1 Layout and punctuation

Indentation defines blocks and must use spaces; indentation tabs are errors.
Two spaces per level is the canonical layout (section 2.5). Blank and comment-only lines
do not declare blocks. Newlines inside parentheses and brackets, including an
instance's argument list, are not layout.
Statements and mapping entries use newlines, not semicolons.

`:` separates names from types, bindings, or metadata values. `=` appears in
parameter defaults and named formatting arguments; it is not a connection
operator. Lists and helper calls use commas. Instance sections do not.

The structural words are `from`, `import`, `as`, `component`, `block`, `board`,
`bundle`, `nettype`, `extends`, `inst`, `net`, and `global`. Section names are `pins`,
`ports`, `params`, `defaults`, `connections`, `metadata`, and `elements`. These words are
recognized in their grammatical contexts. Metadata keys and builtin enum members
are not additional general-purpose statements.

### 2.2 Identifiers

An identifier matches:

```text
[A-Za-z_#][A-Za-z0-9_#]*
```

Names are case-sensitive. `reset#`, `#select`, and `_supply` are valid identifiers.
`#` is always available in an identifier and never starts a comment. Names of
nets, definitions, parameters, and instances follow this rule. CAD reference
validation is a separate backend rule.

`no_connect`, `global`, `true`, `false`, `number`, and `string` are reserved
declaration names. They cannot name definitions, nets, instances, parameters,
ports, bundle fields, or import bindings. Other keywords remain contextual:
for example, `net inst: nettype.passive` is allowed. Exact physical pin IDs may
use reserved spellings when quoted, such as `"global"`; metadata keys and literal
strings are not subject to this declaration-name restriction.

### 2.3 Comments and strings

```nosch
// End-of-line comment.
/* A block comment
   may span lines. */
```

Block comments do not nest; the first `*/` ends the comment. Comment delimiters
inside strings are ordinary string contents.

Strings use double quotes with JSON-style escapes: `\"`, `\\`, `\/`, `\b`, `\f`,
`\n`, `\r`, `\t`, and `\uXXXX`. Invalid escapes and unpaired Unicode surrogate
escapes are errors. Literal strings cannot span source lines. There are no
single-quoted or interpolated string literals.

`true` and `false` are boolean metadata literals. `no_connect` is a connection
target denoting intentional disconnection. Neither introduces a new public
parameter type.

### 2.4 Numbers and physical pin identifiers

Number literals are decimal integers, decimals with digits on both sides of the
point, or exponent forms: `12`, `12.5`, `12e3`, `1.25e-3`. A leading sign is a
unary operator. Hexadecimal, binary, underscore-separated, and `.5` literals are
not supported.

A number may end in an SI prefix, with no space: `4.7k`, `100n`, `10u`, `2.2M`.
The prefix may instead replace the decimal point, as in IEC 60062 codes: `4k7`
is `4.7k`, `1u5` is `1.5u`, and `R` marks a unity decimal point (`4R7` is 4.7,
`47R` is 47).

| Prefix | `a` | `f` | `p` | `n` | `u` | `m` | `R` | `k` | `M` | `G` | `T` | `P` |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Scale | 10⁻¹⁸ | 10⁻¹⁵ | 10⁻¹² | 10⁻⁹ | 10⁻⁶ | 10⁻³ | 1 | 10³ | 10⁶ | 10⁹ | 10¹² | 10¹⁵ |

Prefixes are case-sensitive (`m` is milli, `M` is mega), and there is no `E` or
`µ`. The value is computed exactly in decimal: `100n` is the double nearest
10⁻⁷, not `100 * 1e-9`. An integral result has the integer tag (`4.7k` is
4700); others are doubles. A literal has at most one decimal mark (`4.7k7` is an
error), cannot combine a prefix with an exponent, and cannot be followed by a
name character, so a unit such as `10kOhm` is a syntax error. Use a quoted pin key
for a pin named like a scaled number, such as `"10k"`.

In a physical pin key, integer spelling is preserved as text:

| Spelling | Physical identity |
|---|---|
| `1`, `"1"` | The same pin |
| `01`, `"01"` | The same pin, distinct from `1` |
| `A1`, `TAB` | Bare identifier pin names |
| `"EP-1"` | A quoted pin name containing punctuation |

Physical pin identifiers are not evaluated as numbers. Numeric expressions and
array indices use the numeric rules in section 4.

### 2.5 Canonical layout

`noscheme format` rewrites sources in one canonical layout; editors offer the same
through the language server. It changes only layout, never meaning:

- Two spaces per level. One space after `:` and `,` and around binary operators
  and parameter-default `=`; none around `=` in arguments, `..` in pin ranges, or
  `:` in slices.
- Lines are at most 100 characters where possible. A longer bracketed list (instance
  arguments, call arguments, or a list value) breaks one item per line with a
  trailing comma. A list written with a trailing comma stays broken, so the author
  can keep a short list vertical; a broken list without one is joined when it fits.
- One blank line before each top-level definition and the comments directly above
  it. Elsewhere at most one blank line is kept, and none at the start of a block.
- Spellings are kept: `4k7` stays `4k7`, strings keep their escapes, and pin keys
  keep their quotes. Order, the pin-entry shorthand, and the choice between
  instance arguments and sections are also the author's. The parentheses of an
  empty argument list are dropped.
- A trailing comment stays after its code, two spaces from it. A comment on its
  own line stays above the code that follows it, at that code's indentation, or at
  the indentation of the block it ends. A comment inside a bracketed list keeps
  the list broken so the comment stays with its item.

The formatter checks that its result parses to the same syntax tree with the same
comments. It reports, and leaves unchanged, a file with a syntax error or a comment
it cannot place without moving it, such as one inside an expression outside a
bracketed list or a block comment followed by code on its line.

## 3. Declarations

A module contains imports and definitions. Definitions are `component`, `block`,
`board`, `bundle`, or `nettype` (section 9.3); their bodies must contain
declarations, sections, or (for net types) net metadata fields.
Duplicate names, duplicate sections, and overlapping pin declarations are errors.

### 3.1 Physical components

A component describes one physical part and its pin interface:

```nosch
from noscheme import role, unused

component Resistor:
  params:
    value: resistance
  pins:
    1..2: [role.passive, unused.never]
  metadata:
    ref_prefix: "R"
    footprint: "Resistor_SMD:R_0603_1608Metric"
```

A pin entry may list builtin values instead of, or before, indented fields. Each
value's namespace names the field it sets: `role.x` sets `role`, `bias.x` sets
`bias`, and `unused.x` sets `unused`. Several values from one namespace form a
list, which only `role` accepts, as a definition's permitted roles:

```nosch
pins:
  1: [role.ground, role.power_input, unused.never]
  2: role.open_drain
    capacitance: 5p
```

This also applies in specializations and instance pin overlays, and follows
import aliases. Setting a field both ways, or using a value that is not a builtin
`role`, `bias`, or `unused` value, is an error. Ports and bundle fields are
excluded: their value position holds the net type.

Physical components do not contain internal instances or nets. A component may
omit its footprint while a design is being developed; physical instances then
receive an unspecified-footprint warning.

Pins may declare aliases. Component-body fragment:

```nosch
pins:
  1:
    aliases: [cathode, K]
    role: role.passive
  2:
    aliases: [anode, A]
    role: role.passive
```

Aliases and physical identifiers refer to the same endpoint. Every alias must
resolve to exactly one pin, including when it matches another physical pin's
identifier. Binding through an alias still exports the original physical pin ID.

### 3.2 Reusable blocks

A block exposes ports and parameters and contains an internal circuit. Definition
fragment, assuming `Resistor` and the builtin namespaces have been imported:

```nosch
block SeriesResistor:
  params:
    resistance: resistance = 1k
  ports:
    input: nettype.passive
    output: nettype.passive
  inst resistor: Resistor
    connections:
      1: input
      2: output
    params:
      value: resistance
```

Ports declare internal connections; do not separately declare a net of the same
name. Internal parts and nets are private. A caller may configure only the public
parameters, ports, choices, and supported instance metadata. It cannot access
`resistor.1` through the block or modify the internal resistor directly.

Each expanded port must lead internally to a physical pin, an explicitly modeled
external endpoint, or a placeholder. Recursive block instantiation is an error.

A block cannot directly connect two of its own ports: there is no internal
net-alias or net-merging statement. A pure-wiring bundle adapter must therefore
be expressed through field bindings at its caller. Placeholders permit incomplete
implementations but do not create a connection between otherwise separate ports.

### 3.3 Boards and globals

A board is the selected top-level circuit. It may declare local nets, instances,
metadata, and global nets. Only a board may declare globals. Boards do not declare
parameters or public ports in the implemented syntax; use a parameterized block
inside the board when those interfaces are needed.

Board-body fragment:

```nosch
global net gnd: nettype.ground
global net v3v3: nettype.power
net reset#: nettype.digital
```

Reference global nets explicitly as `global.gnd` and `global.v3v3`. A reference
never creates a net. An instantiated block referring to a missing global is an
error. Authors may expose a supply port instead of referring to a global; there
is no separate global-dependency declaration.

### 3.4 Order and scope

Within a definition, declarations and connections are order-independent. Names
resolve lexically. All nets must be declared. Instance parameter expressions are
evaluated in the caller's scope; inherited expressions retain their original
module scope. Internal definitions cannot be monkey-patched through a path.

## 4. Parameters and expressions

### 4.1 Parameter declarations and binding

Public parameter types are `number`, `string`, and the quantities below.
Definition-body fragment:

```nosch
params:
  count: number = 2
  value: resistance = 4.7k
  label: string
```

| Quantity | Unit | Displayed as |
|---|---|---|
| `voltage` | volt | `3.3V` |
| `current` | ampere | `100mA` |
| `resistance` | ohm | `4.7kOhm` |
| `capacitance` | farad | `100nF` |
| `inductance` | henry | `2.2uH` |
| `frequency` | hertz | `8MHz` |
| `time` | second | `10us` |
| `power` | watt | `250mW` |

A quantity parameter holds a number in its base SI unit. It names the unit and
how the value is displayed; arithmetic is ordinary numeric arithmetic, and there is
no dimensional checking. A quantity parameter also accepts a string, such as
`"10k 1%"` or `"DNP"`, which is displayed verbatim and has no numeric meaning; using
it where a number is required is an error.

A parameter without a default is required at instantiation. Instance arguments
are named entries under `params:`. Unknown arguments and wrong types are errors.
Defaults may depend on other parameters; dependency cycles are errors. An
explicit argument replaces its default, so the replaced default is not evaluated.

Parameters may determine dimensions, indices, physical part values, and numeric
ERC quantities, and may be forwarded to child parameters. They do not select topology, imported types,
footprints, electrical roles, or arbitrary metadata. ERC choices are a separate
mechanism, described in section 11.

A parameter named `value` supplies the conventional displayed part value.
`metadata.value` can explicitly provide a value expression, and an instance's
`metadata.value` overrides both. A numeric displayed value has a unit only when it
is a quantity parameter: the component's `value` parameter, or a bare parameter
name in `metadata.value` or an instance override, looked up where the expression is
written (the caller, for an override). It then renders with an engineering prefix,
rounded to six significant digits (ties to even) without exponent notation or
trailing zeros: `4700` as `4.7kOhm`, `1e-7` as `100nF`. Computed and literal numbers,
such as `value / 1k` or `4.7k`, have no quantity and render plainly (`4.7`,
`4700`). Strings are display values, not electrical quantities.

### 4.2 Operators and numeric tags

Numbers carry either an integer tag or a double tag. Integers have arbitrary
precision within resource limits; doubles are finite IEEE-754 binary64 values.

Precedence, highest first:

| Level | Operators/forms | Association |
|---|---|---|
| 1 | Grouping, member access, indexing, supported calls | Postfix |
| 2 | Unary `+`, `-` | Unary |
| 3 | `*`, `/` | Left |
| 4 | `+`, `-` | Left |
| 5 | `<<`, `>>` | Left |

`+`, `-`, and `*` produce an integer for integer operands and a double if either
operand is a double. `/` always produces a double. Division by zero, overflow to
nonfinite values, and nonfinite results are errors. These operators are numeric;
`+` does not concatenate strings.

Shifts require integer-tagged operands and a nonnegative count. Right shift
preserves the sign. In integer contexts, a double is truncated toward zero with
a warning, even if its value is integral. Resulting indices and dimensions must
still satisfy their bounds.

### 4.3 Numeric helpers

Import `num`. Each helper takes one positional numeric argument and returns an
integer:

| Helper | Result |
|---|---|
| `num.int(x)` | Truncate toward zero without an implicit-conversion warning |
| `num.ceil(x)` | Round toward positive infinity |
| `num.floor(x)` | Round toward negative infinity |
| `num.round(x)` | Nearest integer; ties away from zero |

For example, `num.round(-2.5)` is `-3`. Explicit conversion does not bypass
bounds or resource checks.

### 4.4 String formatting

Supported string expressions are literals, parameter references, and formatting.
Formatting is inspired by Python, but does not evaluate Python. Expression
examples:

```text
"{0}".format(value)
"{resistance:.1f}kOhm".format(resistance=r)
"{{{0}}}".format(value)
```

Fields must use explicit numeric indices or named arguments. Positional arguments
precede named arguments. Names are not captured implicitly from the surrounding
scope. `{{` and `}}` insert literal braces. Automatic `{}` fields, attribute/index
traversal, conversion flags such as `!r`, and nested format fields are errors.

Numeric format specifications accept optional width, optional `.precision`, and a
required `d`, `f`, `e`, or `g` suffix. `d` requires an integer and forbids precision.
Formatting width, precision, and output size are bounded.
Without a format specification, integers render exactly and doubles use their
shortest round-trip representation, with a trailing `.0` removed.

Quantity values format themselves (section 4.1). ERC never
derives a quantity from a displayed value.

## 5. Instances and connections

### 5.1 Instance form

Definition-body fragment:

```nosch
inst series: Resistor
  connections:
    1: source
    2: destination
  params:
    value: 33R
  metadata:
    identity: "series-resistor"
    ref: "R7"
```

An instance line names the instance and its type. It may carry an argument list in
parentheses (below), and it may be followed by indented `connections`, `params`,
`metadata`, and `elements` sections. Either, both, or neither may be present:
`inst holder: Holder` and `inst holder: Holder()` are the same instance. Omit an
empty section rather than writing its header without entries; omitting arguments
waives nothing, so required parameters and connection policies still apply.
Connections bind by name, except for the explicit interchangeable-pin group
described below. Parameters always bind by name.

#### Instance arguments and interchangeable pins

A component may declare one group of electrically interchangeable physical pins:

```nosch
component Resistor:
  params:
    value: resistance
  pins:
    1..2: role.passive
  metadata:
    interchangeable: [1, 2]
```

The list accepts physical pin IDs or aliases, including bare integer spellings.
It must resolve to at least two distinct existing pins. Their electrical metadata
must match, including roles, unused policies, bias, numeric fields, and checks;
aliases and `custom` data may differ. This is an author assertion about the part's
symmetry, not electrical inference. Only one group is supported. An empty list
clears an inherited group. Specializations that give the pins different electrical
metadata, such as putting pull-up bias on only one terminal, must clear it with
`interchangeable: []` and use named connections.

```nosch
inst series: Resistor(source, destination, value=4.7k)
inst decouple[4]: Capacitor(global.v3v3, global.gnd, value=100n)
inst esd: Protection(data, no_connect, gnd: global.gnd)
```

These fragments assume the referenced types and nets exist. `Capacitor` declares
two interchangeable pins; `Protection` declares two interchangeable signal pins
and a separate pin aliased `gnd`.

An argument list has three kinds of argument, separated by commas:

- A positional net reference binds the next pin in the declared interchangeable
  group. If any positional connections are supplied, they must cover the whole
  group; write `no_connect` explicitly for an unused member. The target must be
  a component with a nonempty group.
- `pin: net` connects a single pin or public port by name, alias, or quoted ID.
  Named connections also work without an interchangeable group. A pin cannot be
  bound twice, including through a positional argument and an alias.
- `parameter=expression` supplies a named parameter in the caller's scope.

Newlines inside the parentheses are not layout, so arguments may be written on
one line or across several, and a trailing comma is allowed. Positional
references may include globals, bundle fields, and array indices/slices, with the
same broadcast rules as section connections. Endpoint lists and ranges need a
`connections:` section.

Arguments and sections describe one instance, and neither overrides the other.
Named connections join the instance's connections and `name=value` arguments its
parameters, so binding a pin or parameter twice, in either place or through an
alias or a positional net, is an error. An `elements:` entry overrides argument
parameters like any common parameter. Arguments with a `metadata:` or `elements:`
section suit a part that needs one setting a line cannot hold:

```nosch
inst high: Resistor(bus.high, split, value=60.4R)
  metadata:
    custom:
      dnp: true
```

Prefer keeping all of a part's connections in one place, either its arguments or
its `connections:` section, so its wiring reads at a glance.

Positional nets map deterministically to the listed physical pins, in list order.
Reordering the arguments or the group changes pad assignments in the exported
netlist even though the declared electrical behavior is equivalent. There is no
automatic PCB pin swapping or routing optimization.

#### Explicit array element overrides

An array instance may contain an `elements:` section. Each entry selects exactly
one element, with one nonnegative integer literal per dimension:

```nosch
inst pulls[2]: Pullup
  connections:
    supply: supply
    signal: bus
  params:
    value: 4.7k
  elements:
    [0]:
      params:
        value: 2.2k
      metadata:
        custom:
          placement: "Beside the controller"
```

An entry contains `params:` and/or `metadata:`. Element parameters replace common
arguments with the same name; metadata merges recursively, including annotations
on the same canonical physical pin. Both evaluate in the caller's scope. Component
defaults apply after these arguments are chosen. An element can supply a required
parameter without a common argument if every element supplies it.

`[1][2]:` selects one element of a two-dimensional array. Slices, partial indices,
index variables, duplicate selectors, out-of-range indices, nested `elements`,
and element-local connections are not supported. Use indexed connections in the
common `connections:` section. Per-element `ref` and `identity` are disallowed;
existing common identity rules, including array index suffixes, remain unchanged.
Changing an element's value does not change its identity. Reordering or inserting
array positions still can change physical identities.

`evaluate` reports the common declaration template when any elements use it, plus
explicit element variants. `compile` expands every element and checks the resulting
connection shapes; element parameters do not relax shape compatibility.

### 5.2 Endpoint binding

The left side names an instance pin, alias, or public port. It may select public
bundle fields and array elements. The right side names a declared caller net,
a caller port, a global, or `no_connect`.

`anode: anode` is valid: the left name belongs to the instance interface and the
right name belongs to the caller. A pin may not bind twice, even to the same net.
Whole-port, field, range, and indexed bindings may not overlap.

Several distinct ports may bind to one caller net. Their attached endpoints then
share one flattened net; this is not itself an error. Independent declared nets
cannot be merged through a pin or an already connected internal net. There are
no standalone connection, net-alias, or net-merging statements.

The outermost bound caller net supplies the flattened source name. Internal names
and source locations remain available for diagnostics.

### 5.3 Unused and forbidden connections

| Pin/port policy | Caller obligation |
|---|---|
| `unused.always` | Omission or intentional disconnection is allowed |
| `unused.explicit` | Connect or explicitly bind `no_connect`; this is the default |
| `unused.never` | Make a real connection; `no_connect` and omission are errors |

A net name alone does not establish a useful physical connection. A connected
physical pin subject to connection requirements needs another physical or modeled
external endpoint. Ordinary forwarding ports and placeholders do not count as
additional physical endpoints.

For an intentionally lone endpoint, such as a test pad or antenna feed on its
own net, declare `unused: unused.always` on the pin definition or a metadata
specialization. This permits the lone connection; it does not waive driver or
other electrical checks on that net. A net waiver cannot replace this pin policy.

`role.do_not_connect` forbids assigning any net, including an otherwise empty net.
Omission and `no_connect` are allowed. Combining it with `unused.never` is a
conflicting-constraints error. Instance metadata and waivers cannot override
mandatory connection policies; a metadata specialization may correct a definition.

A required child port also needs an endpoint outside its block instance.

## 6. Bundles and arrays

### 6.1 Bundle declarations

```nosch
from noscheme import nettype

bundle I2C:
  scl: nettype.open_drain
  sda: nettype.open_drain
```

A bundle is a named net type with named fields. A port or net can use `I2C` as
its type. A whole-bundle binding requires the same named bundle type and connects
fields by name. Merely having identical field structures does not make two bundle
types interchangeable.

Connection-section fragment:

```nosch
connections:
  bus.scl: control.scl
  bus.sda: control.sda
```

Field-level bindings can connect different bundle types explicitly. Crossed
interfaces use ordinary bindings such as `uart.rx: link.tx`. No protocol-specific
rewiring is inferred. Nested bundles are supported; recursive bundle definitions
are errors.

### 6.2 Dimensions and selectors

Array dimensions follow the type for nets and ports, and the name for instances.
Definition-body fragment:

```nosch
net signals: nettype.digital[4]
net matrix: nettype.passive[2][3]
inst receivers[4]: Receiver
  connections:
    input: signals
```

Dimensions must be positive integers. Arrays are zero-based. `matrix[1][2]`
selects one scalar net. Bundle fields may be arrays; field access and indexing
compose left to right, for example `links[1].data[2]`.

Instance dimensions precede port dimensions. An instance array of shape `[3]`
whose port has shape `[4]` exposes an effective connection shape `[3][4]`.

| Binding | Result |
|---|---|
| Equal array shapes | Connect corresponding elements |
| Scalar net to endpoint array | Share that net across all selected endpoints |
| Scalar bundle to same-type bundle port array | Share each field's net |
| Different array shapes | Error, even with equal element counts |
| Array of distinct nets to one physical pin | Error; would merge nets |
| `[1]` array to scalar | Error; a one-element array is still an array |
| `[4]` nets to `[3][4]` endpoints | Error; partial broadcasting is not supported |

There is no `each` keyword. Shared connections undergo normal ERC.

### 6.3 Instance selection, ranges, and slices

Leading selectors choose elements of an instance array. Instance fragment:

```nosch
inst indicators[3]: Indicator
  connections:
    [0].drive: status
    [1].drive: activity
    [2].drive: fault
```

A bare `drive` selects the port across the whole instance array. `[1].data[2]`
selects port element 2 on instance element 1. Indexed bindings do not override
broader bindings; overlap is an error.

Physical pin ranges use inclusive bounds. Array slices use an exclusive stop.
Connection-section fragment:

```nosch
connections:
  11..20: gpio[0:10]
```

The ten pins match the ten net elements in ascending order. This explicit range
ordering is distinct from positional instance arguments. Physical ranges require
ordinary integer labels; they do not support alphanumeric or zero-padded IDs.
Ranges also apply to pin definitions and instance pin metadata. Slices require
explicit start and stop bounds; negative indices and out-of-range bounds are
errors. Empty selections are not useful connection endpoints and are rejected.

Bracketed pin lists select noncontiguous physical pins. A list may contain exact
pin identifiers, aliases, inclusive ranges, or a mixture:

```nosch
connections:
  [11, 28, 50, 75, 100]: supply
  [7..9, 23..24, 35]: no_connect
  [VDD, 2..3, "07"]: supply
```

`[m..n]` has the same inclusive numeric bounds as `m..n`; `[1]` and `[1,]` are
valid singleton lists. Lists can span lines and have a trailing comma. Members
are literal pin identifiers, not parameter expressions. Ranges cannot use
alphanumeric or zero-padded bounds. List order, with each range expanded in
ascending order, determines the selection's array order. Existing connection
shape rules apply; a scalar net broadcasts to the whole selection.

The same keys work in a component's `pins:` declarations or specialization and
under an instance's `metadata.pins:`. Base declarations define physical IDs;
specializations, bindings, and instance annotations may also use existing aliases.
For example, a specialization may set shared supply metadata with:

```nosch
pins:
  [11, 28, 50, 75, 100]:
    role: role.power_input
    unused: unused.never
```

Pin lists do not apply to block ports, bundle declarations, parameters, or
ordinary metadata keys. Empty lists, duplicate entries, and overlapping ranges
are errors. Aliases that select the same pin twice are also errors. Overlap with
another binding or annotation remains an error. To select pins on a specific
instance-array element, write `[0].[1, 3]`; `[0].1` retains its existing meaning.

## 7. Specialization

Components and blocks use `extends` for single inheritance; net types always
extend a builtin net type or another net type (section 9.3). Boards and bundles
cannot specialize definitions. Definition fragment:

```nosch
component Pullup extends Resistor:
  defaults:
    value: 4.7k
  metadata:
    custom:
      purpose: "pull-up"
```

Specializations may override metadata and defaults for existing parameters.
They may not add/remove pins or parameters, change parameter types, alter an
internal circuit, or expose private internals. Existing parameterized dimensions
can still vary through parameter values. Instance arguments override defaults.

Metadata mappings merge recursively by key. Scalar values and lists replace
inherited values; lists such as `aliases` do not concatenate. A specialization
can replace a default but cannot remove it. The merged result is validated,
including aliases and electrical constraints. Inheritance cycles are errors.

At definition time, metadata can correct restrictive upstream roles or unused-pin
policies. Instances must obey the resulting definition.

## 8. Metadata reference

Unknown fields outside `custom` are errors. Recognized fields have explicit scopes
and types. Most metadata is literal; `value` and numeric ERC quantities support
restricted parameter expressions.
Numeric electrical metadata is in base SI units, and fields declare their quantity
(section 4.1); field names never carry a unit or scale. Write the scale in the
literal instead: `capacitance: 10p`, `max_load_current: 100m`.

### 8.1 Core fields

<!-- generated:core-fields -->
| Scope | Fields |
|---|---|
| `component` | `current_loads`, `custom`, `footprint`, `interchangeable`, `ref_prefix`, `value` |
| `current_load` | `max_load_current`, `pins` |
| `block` | `custom`, `erc_choices` |
| `board` | `configurations`, `custom`, `erc_choices` |
| `bundle` | `custom` |
| `pin` | `aliases`, `checks`, `custom`, `max_load_current`, `max_source_current`, `role`, `tristate_capable`, `unused` |
| `aggregate_load` | `max_load_current` |
| `port` | `aggregate_load`, `checks`, `custom`, `placeholder`, `role`, `unused`, `waivers` |
| `net` | `checks`, `custom`, `net_class`, `placeholder`, `waivers` |
| `instance` | `custom`, `erc_choices`, `footprint`, `identity`, `ref`, `value` |
| `instance_pin` | `checks`, `custom`, `external_role`, `role`, `tristate_capable` |
<!-- /generated:core-fields -->

The table uses internal scope names: `pin` means a physical pin declaration,
`port` includes bundle fields, and `instance_pin` means `metadata.pins` entries.
The `current_load` and `aggregate_load` scopes describe nested entries.
`pins` is a structural instance section handled separately from these fields.
Physical instances accept `footprint`, `ref`, `identity`, `value`, and `custom`;
block instances accept `identity`, `erc_choices`, and `custom`.

`footprint`, `ref_prefix`, `ref`, `identity`, and `net_class` are strings.
`tristate_capable` and `placeholder` are booleans. Current amounts are finite,
nonnegative numeric expressions. `aliases` is a list of names, strings, or integer
pin spellings (so `01` retains its leading zero). `role` on a
definition can be one role or a list of permitted roles.

Definitions declare `pins` directly. Instances annotate existing pins under
`metadata.pins`; they cannot add pins or change aliases or unused policies. An
instance's `role` is the role the pin plays in that circuit, directly or through a
choice mapping:

- On a component pin that declares no role or only `passive`, such as a
  connector or test-point contact, it describes the equipment attached there.
  Reports and ERC record it as the pin's `external_role`.
- On a pin that declares other roles, and on a block port, whose role is its
  public contract, it selects one of the declared roles.

`external_role` remains only for block ports, where it declares an explicitly
modeled endpoint outside the block. Writing it on a component pin is an error that
names `role`.

`custom` accepts nested mappings, lists, strings, finite numbers, and booleans.
It has no built-in electrical meaning. For example, `custom.dnp` is a user
convention and does not automatically omit a part or change ERC.

### 8.2 Built-in electrical extension fields

These fields use the same registry available to project ERC extensions:

<!-- generated:extension-fields -->
| Scope | Field | Type |
|---|---|---|
| `instance_pin` | `bias` | `bias.none`, `bias.pull_down`, `bias.pull_up` |
| `instance_pin` | `capacitance` | `capacitance` |
| `instance_pin` | `max_voltage` | `voltage` |
| `instance_pin` | `min_voltage` | `voltage` |
| `instance_pin` | `pull_resistance` | `resistance` |
| `net` | `max_voltage` | `voltage` |
| `net` | `min_voltage` | `voltage` |
| `net` | `wiring_capacitance` | `capacitance` |
| `pin` | `bias` | `bias.none`, `bias.pull_down`, `bias.pull_up` |
| `pin` | `capacitance` | `capacitance` |
| `pin` | `max_voltage` | `voltage` |
| `pin` | `min_voltage` | `voltage` |
| `pin` | `pull_resistance` | `resistance` |
<!-- /generated:extension-fields -->

`number` accepts any finite signed number. `positive` and `nonnegative` add the
stated bounds. A quantity kind holds a value in its base SI unit; `voltage` is
signed, `resistance` is positive, and the other quantities are nonnegative.
Numeric fields accept the restricted expressions in section 4. Definition fields
evaluate using the component/block's bound parameters; instance fields use the
caller. Inherited expressions retain their lexical module. Nested numeric fields
in current-load groups and project extension schemas follow the same rules.
Values are checked after evaluation and before ERC. Unrecognized names are source
errors even in unused definitions; overwritten expressions are not evaluated.

Use one numeric parameter for a displayed value and a physical quantity:

```nosch
component Pullup:
  params:
    value: resistance = 4.7k
  pins:
    1:
      role: role.passive
    2:
      role: role.passive
      bias: bias.pull_up
      pull_resistance: value
```

Import `role` and `bias` from `noscheme` for this fragment. The part displays
`4.7kOhm`, and ERC sees 4700 ohms. Quantities are
never inferred from display strings. Independent instance metadata overrides can
still intentionally change the model; shared parameters eliminate duplicated
constants in the ordinary case. See the complete
[parameterized ERC example](../examples/parameterized_erc/README.md).

## 9. Electrical roles and net types

### 9.1 Roles

Import `role` and use one of:

<!-- generated:roles -->
| Role | Meaning |
|---|---|
| `passive` | Passive contact; imposes no electrical domain and supplies no active driver. |
| `digital_input` | Receives a digital signal. An explicitly declared input port may be tied to a power or ground rail. |
| `digital_output` | Actively drives a digital signal; participates in multiple-driver checks. |
| `digital_bidirectional` | Can receive or drive digital signals. Single-driver nets need an operating assumption to resolve driver ambiguity. |
| `analog_input` | Receives an analog signal. |
| `analog_output` | Actively drives an analog signal; participates in multiple-driver checks. |
| `analog_bidirectional` | Can receive or drive analog signals. |
| `power_input` | Consumes power. Connecting to a ground net requires an explicit incompatible_role waiver. Optional max_load_current metadata contributes to current-budget checks. |
| `power_output` | Supplies power. Optional max_source_current metadata declares its current capacity. |
| `ground` | Ground connection; connecting a power output to ground is an ERC error. |
| `open_drain` | Can pull a signal low or release it. Multiple open-drain drivers may share a net; a pull-up provides the high level. |
| `open_source` | Can pull a signal high or release it. Multiple open-source drivers may share a net; a pull-down provides the low level. |
| `unchecked` | Electrical behavior is unspecified; ERC cannot treat this endpoint as passive or prove its driver behavior. |
| `do_not_connect` | Must not be assigned to any net. Conflicts with unused.never; this connection rule cannot be waived. |
<!-- /generated:roles -->

An absent, unchecked, or unselected role is not silently treated as passive.
These roles do not assert voltage tolerance, logic thresholds, timing, or a
particular operating state unless explicitly modeled by other metadata.

### 9.2 Scalar net types

All checked scalar types permit passive attachments. Other permitted roles are:

| `nettype` member | Other permitted roles |
|---|---|
| `passive` | Ordinary digital, analog, power, and ground roles |
| `digital` | Digital input/output/bidirectional, open-drain, open-source |
| `bidirectional` | Same role compatibility as `digital`, without its required active driver |
| `tristate` | Digital inputs; digital output-capable roles explicitly marked `tristate_capable: true` |
| `open_drain` | Digital inputs and open-drain endpoints |
| `open_source` | Digital inputs and open-source endpoints |
| `analog` | Analog input/output/bidirectional |
| `power` | Power input/output, digital input, analog input |
| `ground` | Ground, digital input, analog input |
| `unchecked` | No domain restriction from this declaration |

`do_not_connect` always retains its disconnection rule. Every checked declaration
on a flattened net contributes constraints; a caller does not erase an inner
port's constraints. An explicit digital-input port may connect to power/ground
as a fixed logic level while retaining its internal input contract.

An outermost `unchecked` declaration skips the standard driver, current-budget,
and voltage-limit policies. With the default rule set it produces an
`unchecked_net` warning. It does not
suppress pin connection policies, inner compatibility constraints, or
grouped/aggregate-load validity checks. Explicit `checks` applications, including
those an inner port receives from its net type, and project rule-set callbacks
still run; `unchecked` is not a general callback bypass.

### 9.3 Net type definitions

A `nettype` definition names reusable net metadata, typically checks, on top of a
builtin electrical type or another net type:

```nosch
from noscheme import nettype, bias, std

nettype I2CLine extends nettype.open_drain:
  wiring_capacitance: 5p
  checks:
    pull: std.parallel(pull_resistance, where=bias.pull_up, min=1.5k, max=10k)
    load: std.sum(capacitance, net=wiring_capacitance, max=400p)

nettype FastI2CLine extends I2CLine:
  checks:
    load: std.sum(max=200p)

bundle I2C:
  scl: I2CLine
  sda: I2CLine
```

A net type is used wherever a net type is expected: nets, ports, and bundle
fields, with or without array dimensions. Its body contains the fields a net
declaration accepts (section 8), with no sections or parameters. `extends` is
required, and every chain ends at a builtin `nettype` value, which determines
role compatibility and the standard policies (section 9.2). Metadata merges like
specialization (section 7): a derived type, and then each declaration using the
type, merges its fields over the inherited ones. A declaration can therefore
override one argument of an inherited check, such as `pull: std.parallel(min=2200)`.
Calling a different rule under an inherited name is an error. Numeric fields are
evaluated without parameters.

Each declaration of the type contributes the type's checks to its flattened net.
When the same application, with the same name, rule, arguments, and source, reaches
one net through several declarations, such as a board net and the block port it
connects to, it runs once on the outermost of them, provided its rule is
registered `net_only` (section 12.1). The generic `std` rules are; a rule that may
inspect its owning declaration is not, and runs once per declaration. A declaration that overrides
an argument contributes a separate application. Reports record a declaration's
type as `nettype: "module.Name"` beside its builtin `type`.

### 9.4 Public port roles

A port's `role` promises behavior implemented inside the block. It adds no driver
or load. Internal physical or modeled endpoints must implement the promise;
caller circuitry cannot supply the evidence for an internal output.

Input contracts admit inputs and passive contacts. Output contracts admit outputs,
feedback inputs, and passive contacts in the same domain. Bidirectional contracts
admit the corresponding directional roles. Open-drain/open-source contracts admit
their respective drivers, digital inputs, and passive contacts. Ground contracts
admit ground, passive, digital-input, and analog-input roles. Passive/unchecked contracts add no domain
restriction.

A directional contract requires a corresponding implementing endpoint. A
bidirectional contract may be implemented by a bidirectional endpoint or separate
input and output endpoints. Bias through passive components does not implement
an output contract.

### 9.5 Tri-state buses

On `nettype.tristate`, every digital output-capable endpoint must declare
`tristate_capable: true`, including bidirectional and open-drain/open-source roles.
Digital inputs and passive attachments need no flag. Several capable drivers may
share the net. Wrong-domain roles remain errors even with the flag.

This checks declared capability, not output-enable timing or bus arbitration.
An all-input tri-state net does not receive a missing-active-driver error from
this type. If another declaration on the flattened net is `nettype.digital`,
that declaration retains its ordinary driver checks. Generic bidirectional roles
do not imply tri-state capability.

## 10. Electrical checks

### 10.1 Checking model

ERC operates on flattened nets and counts physical endpoints once across
hierarchy. Ordinary ports do not duplicate endpoints. A connector contact's
instance role, its attached equipment, replaces the contact's role for
driver/load checking. An explicitly modeled
external endpoint on a block port counts once but creates no physical CAD pin.

Digital nets require a suitable driver or explicitly declared compatible bias;
power nets require a power source. Multiple known active outputs, conflicting
power/ground connections, and incompatible open-driver combinations are errors.
Unknown roles produce incomplete-information warnings where they prevent a
conclusion. Known conflicts remain errors despite other unknown endpoints.

ERC does not propagate behavior through resistors, regulators, or other parts.
A schematic-looking chain of passive components is not an inferred driver model.

### 10.2 Placeholders

`placeholder: true` on a net or port allows an unfinished block's structural port
reachability check to pass. Placeholder presence causes no warning by itself.
It creates no footprint, driver, load, or physical connection, and does not waive
other checks. A placeholder alone cannot satisfy a required physical pin.

### 10.3 Current budgets

Pin definitions may supply `max_source_current` and `max_load_current`, in amperes.
The checker compares known loads against one annotated source capacity. Missing
loads or capacity produce incomplete-budget warnings when the net has budget
annotations; known overloads still produce errors. Multiple source capacities
are not added. Displayed values are not parsed and regulator output consumption
is not propagated to its input.

For a component with several supply pins, component metadata may group them:

```nosch
metadata:
  current_loads:
    core:
      pins: [vdd, "2", "3"]
      max_load_current: 100m
```

Members are aliases, quoted physical pin IDs, or bare integer pin spellings. Groups must be nonempty, resolve
without duplicate pads, not overlap other groups, and contain pins permitting
`power_input`. A member cannot also have an individual load annotation. All
members must connect to the same flattened net and have effective `power_input`
roles. A valid group counts once per physical component instance. Invalid groups
report errors without hiding missing individual demand information.

A block supply port can declare an aggregate; block-body fragment:

```nosch
ports:
  supply: nettype.power
    role: role.power_input
    aggregate_load:
      max_load_current: 100m
```

The parent counts this amount once instead of descendant loads on that net.
Internal loads are still checked against the aggregate. Nested boundaries are
processed from inside outward. Missing internal demands warn; known internal
overloads error. The parent still counts the declared amount after an internal
overload. Arrays apply the annotation independently to scalar port elements.

An aggregate requires an explicit effective power-input port. Overlapping
same-block aggregate boundaries, internal same-net power sources, and
external-endpoint aggregates are invalid. Invalid boundaries
leave internal loads visible to the parent. Grouped and aggregate constraint
checks cannot be waived and still run on unchecked nets.

### 10.4 Pull resistance and capacitance

`bias` is an assertion about a signal-side endpoint. It does not verify a
resistor's other terminal, rail, displayed resistance, or supply voltage by itself.
Use `std.bias_reference` for the optional terminal check described below.
Open-drain default-level checks require pull-up bias; open-source default-level
checks require pull-down bias. Bias cannot power a rail or satisfy an output-port
contract.

Nets and public ports opt into budgets with named checks (section 12.2). For
example, with `bias` and `std` imported:

```nosch
net bus: nettype.open_drain
  wiring_capacitance: 2p
  checks:
    pull_strength: std.parallel(pull_resistance, where=bias.pull_up, min=1.5k, max=10k)
    capacitance: std.sum(capacitance, net=wiring_capacitance, max=400p)
```

Each physical endpoint counts once. Ordinary public ports contribute assertions,
not extra physical loads. Explicit external endpoints do contribute. An inner
port's assertions and its caller's assertions both run against the flattened net.
`net=` adds the named outermost-net quantities once per application.

Matching pulls combine as `1/R = sum(1/R_i)`. `std.sum` adds capacitance or other
numeric attributes. Numeric limits accept parameter expressions. Missing values
and unknown selections remain unknown; use explicit zero for a known zero
capacitance and `bias.none` for an endpoint known to have no pull. Known overloads
and known parallel resistance below a minimum remain errors even with missing
contributors. Incomplete reductions also warn by default. An empty parallel
reduction represents an open circuit: it passes a finite minimum and fails a
finite maximum. Empty sums are zero.

Nets without an application do not receive these budget diagnostics. Tolerance,
mixed reference rails, and protocol-derived limits are not automatically modeled.

#### Pull reference terminals

A physical signal-side pin can check its pull's other terminal with the pin rule
`std.bias_reference(pin)`, naming another pin ID or alias on the same component:

```nosch
pins:
  2:
    role: role.passive
    bias: bias.pull_up
    checks:
      reference: std.bias_reference("1")
```

With `bias.pull_up`, the reference terminal must reach a net declared
`nettype.power`; with `bias.pull_down`, it must reach `nettype.ground`. Missing
pins, disconnected references, conflicting rail types, and a reference tied to the
signal net itself produce `erc.bias_reference`. The check also runs on a
disconnected signal pin. It traces no paths through other components and infers
no voltages. To waive it for one pin, name the application with a `pins` target
on the signal net (section 10.5); disconnected pins have no net waiver.

#### Explicit voltage ranges and pin limits

A net declares its operating range with `min_voltage` and `max_voltage`
together. A physical pin or instance pin uses the same fields, alone or together,
for its absolute limits, in volts. These fields accept finite signed numeric expressions, in volts. Bounds
are inclusive; minimum values cannot exceed maximum values.

`erc.voltage_limits` compares each annotated pin against the declared net range.
Exceeding either pin limit is an error. An annotated pin without a complete net
range produces a warning; an incomplete or reversed net range is an error.
Unannotated pins produce no missing-limit warnings. When connected declarations
specify different ranges, ERC reports a conflict rather than choosing one; it
also checks the pin against each declared range.

This checks only explicit quantities. Ground is not implicitly zero volts, signal
levels are not inferred from pull resistors, and no supply-domain, logic-threshold,
transient, clamp-current, or operating-state model is assumed. Use explicit ranges
for signal nets too when appropriate. The default rule set applies
`std.voltage_limits` to every net except an outermost unchecked one; nets and pins
without these fields produce no diagnostics. Net and targeted pin waivers are
supported.

### 10.5 Waivers and check names

Net-declaration fragment:

```nosch
net signal: nettype.digital
  waivers:
    missing_driver: "Driver is across the explicitly modeled series resistor."
```

A waiver names an application and gives a nonempty literal reason. It applies to
every application of that name on the flattened net: those of the outermost
declaration, those an inner port or its net type contributes, and pin checks on
the net's physical pins. Add a `pins` target (below) to restrict it. A waiver
that matches no waivable application on its net produces `erc.waiver_target`;
naming a non-waivable standard check is a source error, as is a quoted report
identity such as `"net:receiver.bus:capacitance"`. Only the outermost declaration
of the flattened net may waive its checks. Inner-port
waivers produce `waiver_scope` and do not suppress diagnostics. Private-net
waivers apply independently to each block instance. Reports retain reasons,
locations, and waived diagnostics, including waivers with no matching diagnostic.

To waive a check only for specific physical pins, use a mapping with `reason`
and `pins` instead of the string shorthand:

```nosch
net gnd: nettype.ground
  waivers:
    incompatible_role:
      reason: "Ground VDD_AUX to disable the unused power domain."
      pins: ["chip.12"]
```

Pin paths are literal strings naming an instance path followed by the exact
physical pin ID, not an alias. They are relative to the net declaration's block
instance (or the board), and may include explicit array indices such as
`"devices[0].12"`. There are no wildcards. This is a diagnostic target, not
permission to connect to or modify a block's private internals.

Every target must be a physical endpoint on that flattened net. Missing or
disconnected targets produce the non-waivable `waiver_target` error and disable
the entire waiver. A targeted waiver suppresses only diagnostics whose
`instance_path` exactly names one of its pins; net-wide and port-contract
diagnostics remain visible. Inner declarations still cannot waive outer nets.
JSON and text reports include the resolved targets, reason, and waived diagnostics.

A `role.power_input` on ground now reports `incompatible_role`. Keep that role
when deliberately grounding a supply pin and use a targeted waiver as above.
KiCad `power_in` conversion emits `[role.ground, role.power_input]`, because its
electrical type does not distinguish supply from return. Select the intended
role explicitly in a specialization or instance. ERC does not choose a role to
fit the net; unselected alternatives warn when compatibility is uncertain.
Digital and analog input pins may still be tied directly to supply or ground.

The default standard-library diagnostics retain these identifiers:

```text
do_not_connect             required_connection          unused_pin
unconnected_net            missing_driver               multiple_drivers
power_ground_conflict      incomplete_roles             unchecked_net
aggregate_load_boundary    aggregate_current_overload   incomplete_aggregate_load
current_load_group         current_overload             incomplete_budget
waiver_scope               waiver_target                incompatible_role            tristate_capability
port_role                  incomplete_port_role         required_port_connection
unused_port               bias_reference               voltage_limits
```

Standard rules have qualified registry names such as `std.multiple_drivers` and
`std.voltage_limits`; their default application names omit `std.`. Generic
checks report their rule, such as `erc.std.sum`, `erc.std.parallel`,
`erc.std.count`, `erc.std.any`, or `erc.std.all`. Every result carries a separate application identity, so two
applications of the same rule can be diagnosed and waived independently.
`waiver_scope` and `waiver_target` are engine diagnostics.

`do_not_connect`, `required_connection`, `unused_pin`, `required_port_connection`,
`unused_port`, `waiver_scope`, `waiver_target`, `current_load_group`, `aggregate_load_boundary`,
`aggregate_current_overload`, and `incomplete_aggregate_load` cannot be waived.
Other implemented net checks support scoped waivers. Project rules may disable
waivers by registering with `waivable=False`.

## 11. ERC choices and configurations

Choices describe electrical assumptions on one unchanged physical circuit.
They do not alter parts, values, connectivity, footprints, or identities.

A board or block declares public choices in metadata:

```nosch
metadata:
  erc_choices:
    mode: ["source", "load", "absent"]
```

An internal instance can select a role using a local choice. Instance fragment:

```nosch
metadata:
  pins:
    1:
      role:
        choice: "mode"
        cases:
          source: role.power_output
          load: role.power_input
          absent: role.passive
```

On a pin that declares active roles, every case must be one of them (section 8.1).
Every alternative must have exactly one case. Alternatives are distinct,
nonempty literal strings. A role list without a selection remains uncertain;
the compiler does not choose a role to make the circuit pass.

Parents bind child choices under instance `metadata.erc_choices`. The value is a
literal alternative or a mapping from a parent's lexical choice:

```nosch
metadata:
  erc_choices:
    mode:
      choice: "power_source"
      cases:
        usb: "source"
        header: "absent"
```

Choices are forwarded explicitly through each block interface. They are not
addressable by private instance paths or available in parameter expressions.
Array elements inherit common choice bindings; `elements:` metadata may override
bindings for an individual block element. Each retains a distinct expanded scope.

Board metadata defines named configurations:

```nosch
metadata:
  erc_choices:
    power_source: ["usb", "header"]
  configurations:
    usb_powered:
      power_source: "usb"
    header_powered:
      power_source: "header"
```

All named configurations run by default. Repeat `--erc-configuration NAME` to
select a subset. Without named configurations, one unconfigured check runs.
Choices have no implicit defaults; omitted selections warn where they prevent
checking. Invalid choices, mappings, and role overrides are blocking source
errors. Configuration names do not waive conflicts.

See [examples/power_modes](../examples/power_modes) for a complete project.

## 12. Extending ERC

### 12.1 Registry and project configuration

Built-in and project rules use the same Python API and immutable circuit views.
Declare extension files relative to `noscheme.toml`:

```toml
erc_modules = ["checks.py"]
erc_rule_sets = ["std.default"]
```

`erc_rule_sets` defaults to `["std.default"]`. Use `[]` to disable automatic
electrical policies, or name project rule sets to replace or augment them.
Explicit source applications always run, including on unchecked nets. Disabling
defaults does not disable parsing, resolution, connection-shape validation, or
other structural compiler checks. The standard rule set preserves the documented
role, nettype, current, and voltage policies; pull-reference checks are explicit
`std.bias_reference` applications.

Each module defines `register(registry)`. Registration names are qualified strings
such as `myboard.pin_count`. Duplicates in a registry category are errors.
Everything before the last dot is the name's namespace. Source files import a
project namespace like a module, either `import myboard` (then
`myboard.pin_count(...)`) or `from myboard import pin_count`, and its rules and
predicates are its members. A namespace that also names a source module is an
ambiguous import error. The standard namespace is the builtin `std`, imported
with `from noscheme import std`.

| API | Purpose |
|---|---|
| `fields(scope, schema)` | Add descriptive metadata fields |
| `net_rule(name, callback, parameters={}, defaults={}, waivable=True, net_only=False)` | Register a net assertion; `net_only=True` promises results depend only on the flattened net and the arguments, never `ctx.owner` |
| `pin_rule(name, callback, parameters={}, defaults={}, waivable=True)` | Register a physical-pin assertion |
| `predicate(name, callback)` | Register `(pin, ctx) -> True / False / None` |
| `reducer(name, callback)` | Register a function from a tuple of intervals to an interval, and a net rule of the same name that applies it (section 12.4) |
| `rule_set(name, callback)` | Register `(ctx) -> iterable[Application]` |

The table's rule options are keyword arguments. Field schemas accept `number`,
`positive`, `nonnegative`, a quantity kind such as `capacitance` (section 4.1),
`string`, `bool`, `literal`, `attributes` (an arbitrary literal mapping), nested
dictionaries, or `(namespace, choices)` enums. A field name cannot end in a unit,
scaled or not, such as `_uA`, `_V`, or `_kOhm`; declare a quantity kind and use
base SI values. Enum
values can be literal strings; built-in namespaces also accept explicitly
imported enum values. Numeric values are finite and can use parameter expressions.

Rule parameters accept the same scalar kinds, but not mappings, plus three kinds
that name source entities rather than values:

| Kind | Source argument | Callback receives |
|---|---|---|
| `field` | A bare endpoint metadata field name, such as `capacitance` | The name as a string |
| `net_fields` | A net field name or a list of them | A tuple of names |
| `selector` | A predicate, `role` or `bias` value, boolean field name, or a list of them | A tuple of terms for `noscheme.erc_primitives.selected` |

Field names are checked against core and registered fields; they are never
evaluated as parameter expressions. `missing` is reserved for the missing-data
policy and cannot name a rule parameter. Parameters without defaults are required
after specialization and overlays merge. Unknown arguments and invalid evaluated
values are blocking errors.

`fields` accepts existing metadata scopes, including `pin`, `instance_pin`, `net`,
and `port`. It cannot replace core fields or previously registered fields.
Attributes do not cause the engine to invent check applications; a rule set may
select applications based on their presence.

### 12.2 Source applications and defaults

Use `checks` in pin, net, or public-port metadata, or in an instance's pin
metadata overlay. Pins accept pin rules; nets and public ports accept net rules.
Each entry names one application and calls an imported rule:

```nosch
from noscheme import nettype, std

board B:
  net signal: nettype.digital
    checks:
      driver_present: std.any(std.active_digital_driver)
      no_contention: std.count(std.active_digital_driver, max=1)
```

Positional arguments fill the rule's parameters in registration order; the rest
are named. A call may span lines inside its parentheses. The reserved keyword
`missing="warning"`, `"error"`, or `"ignore"` sets the missing-data policy. A
rule must be imported, there are no implicit namespaces, and a pin rule cannot
be applied to a net or the reverse.

These illustrate generic presence and count assertions. Full standard drive
policy also accounts for selectable roles, open-drain/source bias, tri-state
capability, and public-boundary contracts; replacing it requires choosing those
policies explicitly.

A specialization or instance overlay merges same-name applications by argument.
Call the same rule and pass only the arguments that change, for example
`pull_strength: std.parallel(min=2200)`. Calling a different rule under an
inherited name is an error; use a new application name.
Applications from multiple selected rule sets must have distinct names on an
object, as must automatic and explicit applications. There is no implicit removal
or replacement of a default application.

Rule sets receive a context once per flattened net and once per physical pin,
including disconnected pins, for each configuration. Return
`Application("name", "qualified.checker", params={}, missing="warning")` from
`noscheme.erc_registry`. Net contexts have `ctx.pin is None`; pin contexts have a
pin and a connected net or `None`. Explicit applications on inner declarations
run separately; all see the same flattened endpoints.

### 12.3 Shared contexts and callbacks

A rule callback is called as `callback(ctx, **params)` and yields diagnostics.
For example, `checks.py` can contain:

```python
def register(registry):
    registry.net_rule("demo.pin_count", check,
                      parameters={"max_pins": "nonnegative"})

def check(ctx, *, max_pins):
    if len(ctx.pins) > max_pins:
        yield ctx.error("too many attached endpoints")
```

After `import demo`, a net applies it with `pin_count: demo.pin_count(max_pins=3)`.

Contexts expose:

- `circuit`: a shared mapping with `nets`, `pins`, and `components` lookup
  attributes. Dictionary access exposes the JSON circuit representation,
  including `erc_configuration` and `erc_effective_choices`.
- `net`, `pin`, `owner`: the connected net, physical pin (pin rules only), and
  declaration or pin that owns the application. `net.declarations` retains every
  public boundary. `pin.component` gives its physical component.
- `pins`, `other_pins`: physical and explicitly modeled external endpoints on
  the connected net, with the current physical pin excluded from `other_pins`.
- Records have `label`, unwrapped `attributes`, and mapping access to source
  spans, bindings, raw metadata, and other circuit fields.
- `application`, `checker`, `span`, and `missing`: application identity,
  registered rule name, application source location, and missing-data policy.
- `predicates`, `reducers`: registered functions, available to project callbacks
  as well as built-ins.
- `shared(key, factory)`: cache immutable derived facts within one configuration;
  use a project-qualified key to avoid collisions.

The circuit graph, attributes, and parameter mappings are recursively read-only.
Built-in and project callbacks share the same views; there is no whole-circuit
copy per rule. Mutation attempts fail. Rules should be deterministic and free of
side effects. Python extensions still execute in the compiler/editor process;
read-only views are not a Python sandbox.

Unchanged immutable data can be shared between configurations. Selected roles
and configuration-local derived facts remain separate. Default parameter setup
may be reused within a configuration; callbacks must not depend on receiving
fresh parameter objects. A new ERC run observes current circuit and registry data.

`ctx.error(message, related=())` and `ctx.unknown(message, related=())` attach the
application's location and identity. Unknown results become warnings by default;
`missing` changes only those results, never definite violations. For additional
locations or standard diagnostic names, use
`ctx.diagnostic(code, message, record=..., severity=..., related=...)`.
Import/registration errors, callback failures, and malformed results block
publication. Ordinary ERC errors retain the existing check/compile/export behavior.
Text and JSON reports preserve application identities, related locations, and
configuration names, including for waived diagnostics.

### 12.4 Generic predicates and reductions

### Selectors

A selector chooses endpoints. Each term is one of:

- a registered predicate, such as `std.active_digital_driver`;
- a `role` value, which tests the endpoint's effective role, including the
  equipment attached to a connector contact;
- a `bias` value, which compares the endpoint's `bias` field;
- the name of a boolean field, such as `tristate_capable`, which must be `true`.

A list such as `[bias.pull_up, std.digital_compatible]` requires every term.
Selection is three-valued: missing attributes, unselected role alternatives, and
predicates returning `None` make it unknown. An empty list selects every endpoint.

### Rules

Every registered reducer is also a net rule with parameters
`(field, where=(), net=(), min, max)`. It reduces `field` over the endpoints
selected by `where`, adds the outermost-net quantities named by `net`, and asserts
`min` and/or `max`. For example, `std.sum(capacitance, max=400p)` and
`std.parallel(pull_resistance, where=bias.pull_up, min=1.5k)`.

`std.count(what, min, max)` requires at least one bound and counts endpoints
matching `what`. `std.any(what)` asserts at least one match. `std.all(what,
where=())` asserts that every endpoint selected by `where` matches `what`. Empty
count is zero, empty any is false, and empty all is true. Available predicates
include `std.active_digital_driver`, `std.power_source`, `std.digital_compatible`,
`std.open_drain_compatible`, `std.open_source_compatible`, and
`std.tristate_capable`. Active drive and the capability to tri-state are separate
facts; runtime enable states are not inferred.

Reducers receive `Interval(lower, upper, optional=False, unknown=False)` values
from `noscheme.erc_primitives`. Bounds describe possible contributions;
`optional=True` means selection is uncertain. Unknown nonnegative quantities
range from zero to infinity; general signed quantities are unbounded.
`std.sum` and `std.parallel` propagate intervals and completeness. A custom
reducer must return an ordered interval and retain uncertainty honestly. Infinities
represent internal bounds/open circuits and are never serialized as JSON numbers.
Each incomplete reduction warns by default, even when known contributions already
prove an error. Explicit zero is complete. Assertions can report a definite
violation and missing-data warning together.

See [the extension example](../examples/erc_extensions/README.md) and
[parameterized ERC example](../examples/parameterized_erc/README.md).

## 13. CAD identities and export

### 13.1 Library conversion and footprints

Conversion expands each symbol's `ki_fp_filters` against the explicit
`--footprint-root` directories. It saves matches with exactly the same unique
numbered pads as the symbol's physical pins in
`metadata.custom.kicad_footprint_candidates`, and snapshots those footprints
alongside defaults and explicit extra footprints. Duplicate pads are allowed;
unnumbered mechanical pads are ignored. Malformed or ambiguous alternatives are
omitted and reported; invalid explicitly selected footprints retain the usual
conversion errors.

Filters follow the [KiCad library conventions](https://klc.kicad.org/symbol/s5/s5.2/):
`*` matches any number of characters, `?` zero or one, and a colon includes
the library name in the match. Matching is case-insensitive. Without filters or
footprint roots, no alternatives are inferred. These custom metadata lists are
inherited and may be replaced by specializations. They provide editor hints,
not language restrictions or guarantees of mechanical compatibility.


The compiler consumes NoScheme definitions; it does not import KiCad symbols
implicitly. `convert-library` writes explicit components and footprint snapshots
into a new directory, with sources in the canonical layout. It preserves physical
pin text, follows symbol inheritance, folds duplicate representations, and keeps
unambiguous aliases. A two-terminal
symbol whose reference prefix is `R`, `C`, or `L` gets a required `value`
parameter of type `resistance`, `capacitance`, or `inductance`, since its KiCad
`Value` is only a placeholder; other symbols keep `value: string` with KiCad's
`Value` as the default. Ambiguous aliases
and unsupported electrical types are reported rather than guessed.

The converter also declares interchangeable pins for two-terminal symbols whose
pins are all passive and whose reference prefix is `R`, `C`, `L`, or `FB`, unless
the symbol's name, description, or keywords mark it as polarized. It also recognizes
two-terminal diode symbols with passive pins named `A1` and `A2`. Ordinary diodes,
polarized capacitors, and devices with more than two pins are excluded. This is a
library-data heuristic: review the generated group for specialized parts, and
clear it in a specialization when the terminals have different uses.

KiCad `no_connect` maps to `role.do_not_connect`. An explicit KiCad `tri_state`
output maps to `role.digital_output` plus `tristate_capable: true` when its other
representations do not contradict that capability. A generic bidirectional pin
does not acquire the flag automatically.

Export validates against supplied `footprints.json` records, not live CAD
libraries. All numbered footprint pads need corresponding component pin IDs;
repeated pad numbers retain one electrical identity. Mechanical pads with names
must be modeled explicitly. Unspecified footprints warn and are omitted from
the netlist while remaining in reports/state. Unknown assigned footprints and
pin/pad mismatches block export.

### 13.2 Identities and references

The default identity is the instance's hierarchical path. `metadata.identity`
provides an explicit stable string. On arrays it gains element-index suffixes;
on blocks it anchors descendant identities across block renames. Duplicate
physical identities are errors.

LSP rename of a board-level instance inserts its old name as explicit `identity`
when needed. This preserves array-element and descendant identities using the
same rules as handwritten metadata. Existing explicit identities are retained.
This does not extend to moving instances or renaming internals of reusable blocks;
see [Editor support](EDITOR_SUPPORT.md) for the editor workflow.

`metadata.ref` requests an explicit physical reference. Otherwise the exporter
allocates references using the component prefix (default `"U"`) and persistent reservations.
KiCad reference names must match `[A-Za-z_#][A-Za-z0-9_#]*`, and case-insensitive
reference collisions are rejected.

References beginning with `#` are permitted but produce a non-blocking
`reference.leading-hash` warning, since downstream BOM or placement tools may
treat them as virtual symbols and omit them. This includes references allocated
from a leading-`#` prefix and reused references from state. Export warnings appear
on CLI stderr and in the `warnings` list in `export.json`, separately from ERC
counts. A `#` elsewhere in a reference does not trigger this warning.

`noscheme.state.json`, beside the configuration, stores identity UUIDs, reference
assignments, reference reservations, and the previous exported design. Commit
this file to version control. Export reads and rewrites it; ordinary compilation
and editor analysis do not. Deleted reference assignments remain reserved.
There is no reservation-cleanup command yet.

The exporter stages output and atomically replaces state last. Ordinary
replacement failures roll back earlier replacements; abrupt process/machine
failure is not an all-files transaction.

### 13.3 Names and net classes

The KiCad adapter converts source names and writes a reversible mapping. This
does not synchronize CAD circuit edits back into source. Source names and source
locations remain in the circuit representation.

`net_class: "Name"` assigns membership. KiCad owns class settings such as width
and clearance. `update-kicad-classes` writes a new project copy preserving those
settings and rejects undefined classes. Language-level differential-pair metadata
is not implemented; it must not be inferred to work from source-name suffixes.

## 14. Commands and diagnostics

The installed tools are `noscheme` and `noscheme-lsp`. With the development
environment, prefix commands with `uv run --locked`.

| Command | Result |
|---|---|
| `noscheme parse FILE` | Parse source and emit typed AST JSON |
| `noscheme format [PATH...]` | Rewrite sources in the canonical layout |
| `noscheme load --project PATH` | Load configuration and imports |
| `noscheme resolve --project PATH` | Resolve definitions and public interfaces |
| `noscheme lint --project PATH` | Opt-in unused-parameter warnings; no artifacts |
| `noscheme evaluate --project PATH` | Evaluate parameterized declarations |
| `noscheme check --project PATH` | Check structure and ERC |
| `noscheme compile --project PATH` | Write circuit and ERC reports |
| `noscheme export-kicad --project PATH --footprints FILE` | Validate footprints, export netlist, and update state |
| `noscheme convert-library FILE --output NEW_DIRECTORY` | Convert a KiCad symbol library |
| `noscheme update-kicad-classes INPUT --memberships FILE --output NEW_FILE` | Update class membership in a project copy |
| `noscheme-lsp [--project PATH]` | Run the language server over stdin/stdout |

`--project` accepts a directory or TOML file and defaults to the current directory
for compiler commands. Diagnostics support `--diagnostics text` and `json`.
`parse` also accepts `--output FILE`. Conversion accepts repeatable `--symbol`,
`--footprint-root`, and `--extra-footprint` options; omit `--symbol` to convert
all symbols. The conversion output directory must not already exist.

`check`, `compile`, and `export-kicad` accept repeated `--erc-configuration NAME`.

`format` accepts files and directories, defaulting to the current directory. It
skips hidden directories and each project's `output_dir`. `--check` writes nothing
and fails if a file would change; `--diff` prints the changes instead of writing
them. Files it cannot format are reported, and the command then fails.
`load`, `resolve`, and `evaluate` are inspection stages, not electrical validation.

Conversion maps KiCad `open_collector` to `role.open_drain` and `open_emitter`
to `role.open_source`. `free` and `unspecified` remain unmapped, retain their
original type in custom metadata, and produce conversion issues. They do not
silently become unchecked endpoints.

`noscheme lint --project PATH` reports `lint.unused-parameter` warnings in text,
or JSON with `--diagnostics json`. It runs only when explicitly requested, returns
zero for warnings, and writes no artifacts. Structural errors still fail. It
checks lexical references across loaded definitions, including inherited uses,
default expressions, numeric metadata, array dimensions, and element overrides.
The conventional component `value` parameter is implicitly used unless replaced
by `metadata.value`. Supplying a named argument alone is not a use inside the
component. This is a source lint, not whole-program dead-code analysis; intentional
public compatibility placeholders can remain unused.

### 14.1 Failures and publication

Diagnostics carry a stable code, severity, message, filename, line/column, and
related source locations. Expanded instance paths and configuration labels are
retained where applicable. Text and JSON reports describe the same checks.

Once interfaces resolve successfully, body checking reports independent errors
across definitions and instances, including separate arguments and connection
statements. It stops a dependent check when its prerequisite is invalid: an
unknown component type, for example, prevents checking that instance's pins.
Namespace/type setup failures stop the affected body. Identical errors in shared
inherited bodies are reported once. Structural errors still block subsequent
evaluation, elaboration, and export. Grammar parsing, interface construction,
and elaboration remain fail-fast. Once a file parses, AST structural validation
can report multiple duplicate sections, fields, or invalid declaration contexts.
This is not general syntax-error recovery.

- `check` returns 1 for electrical errors.
- `compile` and `export-kicad` may publish with electrical errors and return 0.
  Inspect their reports; successful export does not mean the circuit passed ERC.
- Structural/source errors return 1 and block publication.
- Configuration, command, and I/O errors use exit status 2.
- Warnings alone do not block publication. Failed compilation preserves previous
  successful circuit/state artifacts. Editor analysis writes neither.

### 14.2 Output files

Compilation produces `circuit.json`, `erc.txt`, and `erc.json`. KiCad export
produces ERC reports, `board.net`, `names.json`, `netclasses.json`, `nets.txt`, `nets.json`,
`changes.txt`, and `export.json`, plus persistent `noscheme.state.json` outside the
output directory. Change reports compare against the last successful export.
`changes.txt` lists before/after values, footprints, references, paths, and each
changed pin connection. `export.json.probable_renames` lists unique pairs of
removed/added identities with exactly matching footprints, values, and pin nets.
These are advisory hints, not proof of a rename. They never transfer UUIDs,
references, or placement; added and removed identities remain distinct. To retain
identity across a planned source rename, set explicit `identity` metadata before
exporting the renamed design (section 13).

Every exported physical component has two netlist properties, also exported as
fields so KiCad can copy them into footprint properties:

- `noscheme_path`: its current full source instance path, relative to the board,
  including nested block names and array indices, such as `indicators[0].resistor`.
- `noscheme_identity`: its stable identity used to allocate UUIDs and references.
  Explicit identity metadata can keep this different from the current source
  path after a rename.

Connectivity remains flattened. These properties describe the source hierarchy;
they do not create KiCad sheets or alter footprint placement. After updating
the PCB from the netlist, select a footprint and press `E` to inspect its fields
in Footprint Properties. The user verified both fields after importing the
STM32 example into KiCad.

Explicit `no_connect` bindings and unconnected `role.do_not_connect` pins are
exported on individual synthetic nets with node `pintype: no_connect`. This
lets KiCad distinguish intentionally unused pads from pins missing in the
netlist, including footprints with duplicate mounting-pad numbers. Names use
the stable component UUID and pin number, with a suffix if needed to avoid a
real net name. They exist only in `board.net`; they do not add source nets,
ERC connections, or entries to `names.json` and the source-net reports.
Other omitted pins retain their existing diagnostics and are not automatically
marked no-connect.

KiCad `libparts` retains the first declared pin alias (or physical pad ID when
there is no alias). A single known role maps to the corresponding KiCad pin type;
analog and digital inputs/outputs share KiCad's input/output types, ground maps
to `power_in`, and explicitly tri-state-capable outputs map to `tri_state`.
Unknown, unresolved, or differing instance types in a shared component entry
become `unspecified`. NoScheme ERC remains authoritative. The field format follows
[KiCad's documented libparts format](https://github.com/KiCad/kicad-doc/blob/master/src/eeschema/eeschema_creating_customized_netlists_and_bom_files.adoc);
PCB Editor display behavior still requires manual validation.

The language server supports diagnostics, go-to-definition, references, rename,
formatted hover, and contextual completion. It uses unsaved buffers and a short typing debounce.
See [EDITOR_SUPPORT.md](EDITOR_SUPPORT.md#language-server) for client setup and current limits.

## 15. Complete example

This two-part test board connects a resistor between an external digital signal
and ground. `main.nosch`:

```nosch
from noscheme import role, nettype, unused

component Resistor:
  params:
    value: resistance
  pins:
    1..2: [role.passive, unused.never]
  metadata:
    ref_prefix: "R"
    footprint: "Resistor_SMD:R_0603_1608Metric"

component Header:
  pins:
    1..2: [role.passive, unused.never]
  metadata:
    ref_prefix: "J"
    footprint: "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical_SMD_Pin1Left"

board Demo:
  global net gnd: nettype.ground
  net signal: nettype.digital
  inst connector: Header
    connections:
      1: signal
      2: global.gnd
    metadata:
      pins:
        1: role.digital_output
        2: role.ground
  inst load: Resistor
    connections:
      1: signal
      2: global.gnd
    params:
      value: 10k
```

Place this `noscheme.toml` beside it:

```toml
language_version = "0.1"
board = "main.Demo"
source_paths = ["."]
library_paths = []
output_dir = "build"
```

Run `noscheme check --project .` and `noscheme compile --project .`. Export also
requires explicit footprint records. Larger runnable examples are
[blink](../examples/blink), [power_modes](../examples/power_modes),
[tristate](../examples/tristate), [erc_extensions](../examples/erc_extensions), the
[STM32 reference board](../examples/stm32h523), and the
[Rulos six-line matrix](../examples/rulos_6matrix/README.md).
The [parameterized ERC example](../examples/parameterized_erc/README.md) demonstrates
shared value/quantity parameters, and [WREN](../examples/wren/README.md) provides
a 1,507-component compilation/LSP benchmark with explicitly unchecked net policies.

## 16. Limits and unsupported features

The implementation has configurable Python-API resource limits to reject runaway
expansion and oversized expressions. Defaults include 65,536 integer bits, 65,536
string characters, formatting width 4,096 and precision 100, expression depth
128, individual array dimensions up to 100,000, 10,000 expanded instances, and
100,000 expanded leaves. Hierarchy and bundle type expansion depths default to
128. These are implementation safeguards, not promises of unrestricted execution.

Not implemented:

- Macros, general Python expressions, loops, conditionals, and automatic part generation.
- Multiple inheritance or structural specialization.
- Implicit nets, positional parameters, multiple interchangeable-pin groups,
  private-instance access, or net merging.
- Dimensional analysis of arithmetic (quantities name units but do not check them)
  or automatic electrical inference from part-value strings.
- Tri-state output-enable simulation or arbitration proofs.
- Resistance/capacitance tolerance models and automatic protocol-limit derivation.
- Language-level differential-pair declarations.
- Range formatting, incremental compiler analysis, or identity-preserving
  moves and internal instance renames. LSP rename preserves CAD identities for
  board-level component/block instances using existing `identity` metadata;
  public bundle-member navigation is also implemented. See
  [Editor support](EDITOR_SUPPORT.md) for supported cases and limitations.
- CAD-to-source circuit synchronization or automatic placement/routing.

The parser grammar is maintained in
[src/noscheme/noscheme.lark](../src/noscheme/noscheme.lark); metadata schemas are in
[src/noscheme/metadata.py](../src/noscheme/metadata.py) and built-in electrical schemas
in [src/noscheme/erc_electrical.py](../src/noscheme/erc_electrical.py). Parsing alone
does not establish semantic validity: resolution, elaboration, ERC, and export
validation apply the additional rules described in this manual.
