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
its semantics. The [usage guide](../USAGE.md) covers installation, circuit
development, and the [command reference](../USAGE.md#9-command-reference), including
implementation-specific diagnostics and artifacts.
[DEVELOPMENT.md](DEVELOPMENT.md) covers compiler development;
[EDITOR_SUPPORT.md](EDITOR_SUPPORT.md) covers editor setup;
[PROJECT.md](PROJECT.md) records design decisions; [PLAN.md](PLAN.md) retains
implementation history.

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
14. [Complete example](#14-complete-example)
15. [Limits and unsupported features](#15-limits-and-unsupported-features)

Examples described as fragments belong inside the indicated enclosing construct.
A complete project requires a `.toml` configuration and one or more root blocks.

## 1. Projects and modules

### 1.1 Project configuration

A project can describe several boards or variants. Each named target selects a
root block. A typical `noscheme.toml` is:

```toml
language_version = "0.1"
source_paths = ["src"]
library_paths = ["lib"]
output_dir = "build"
default_target = "demo"

[targets.demo]
root = "main.Demo"
```

| Key | Meaning |
|---|---|
| `language_version` | The supported version string, currently `"0.1"` |
| `targets` | Nonempty table of named targets; each requires a qualified `root` block |
| `default_target` | Target used when `--target` is omitted; optional for a sole target |
| `source_paths` | Source search directories |
| `library_paths` | Additional module search directories; may be empty |
| `output_dir` | Base directory for generated artifacts; each target defaults to `<output_dir>/<target>` |
| `erc_modules` | Optional list of Python ERC extension files |
| `erc_rule_sets` | Optional list of automatic ERC rule sets; defaults to `["std.default"]`, or `[]` to disable automatic policies |

Paths resolve relative to the configuration file. Search directories must exist,
be distinct, and collectively provide at least one search root. Both path lists
participate in the same lookup: there is no first-match precedence. Unknown
configuration keys are errors.

Each target accepts `root` and an optional `output_dir`. An explicit target output
path is relative to the TOML file, not the base output directory. Target names
start with an ASCII letter or underscore and contain letters, digits, underscores,
or hyphens. Select one with `--target NAME` on `load`, `resolve`, `lint`, `evaluate`,
`check`, `compile`, `export-bom`, or `export-kicad`. With multiple targets, an omitted selection
requires `default_target`; editors use that default too. Only the selected root's
module graph is loaded.

### 1.2 Modules and imports

Source files use the `.nosch` suffix. A module called `parts.passives` corresponds to
`parts/passives.nosch` under a configured search root. Multiple matches, missing
modules, import cycles, and symlink escapes from a search root are errors.
Only modules reachable from the selected root's module are loaded by the compiler.

```nosch
from parts.passives import Resistor, Capacitor
from noscheme import role, net, use, pull, num, std, drive
```

Both module-qualified imports and local aliases are supported:

```nosch
import Device
from Device import R as KiCadResistor
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
| `role` | Endpoint classifications such as input, passive, and power |
| `drive` | Active directional digital drive (`low` or `high`) |
| `net` | Scalar net types |
| `use` | Connection policies |
| `pull` | Explicit pull-up/pull-down assertions |
| `num` | Numeric conversion and rounding helpers |
| `std` | Standard ERC rules and predicates for `checks` (section 12) |

Use qualified builtin values: `role.passive`, not bare `passive`. An attribute's
context does not supply an implicit namespace. Builtins also support aliases,
such as `from noscheme import role as electrical` with
`electrical.passive`, and module imports, such as `import noscheme` with
`noscheme.role.passive`. Module qualification exposes exported definitions;
it never grants access to a component or block's private internals.

## 2. Lexical rules

The parser grammars are [Python/Lark](../src/noscheme/noscheme.lark) and
[C++/ANTLR](../cpp/grammar/NoScheme.g4). Source files use UTF-8 encoding.

### 2.1 Layout and punctuation

Indentation defines blocks and must use spaces; indentation tabs are errors.
Two spaces per level is the canonical layout (section 2.5). Blank and comment-only lines
do not declare blocks. Newlines inside parentheses, brackets, and parameter angle brackets, including an
instance's argument list, are not layout.
Statements and mapping entries use newlines, not semicolons.

Declarations put the type before the name (`number count`, `net.digital signal`,
`Resistor r`). `:` introduces an indented body and separates binding names or
metadata keys from their values. `=` appears in
parameter defaults and named formatting arguments; it is not a connection
operator. Lists and helper calls use commas. Instance sections do not.

The structural words are `from`, `import`, `as`, `component`, `block`,
`bundle`, `extend`, and `global`. Section names are `pins`,
`ports`, `params`, `metadata`, and `elements`. These words are
recognized in their grammatical contexts. In an instance body, `params`, `metadata`,
and `elements` start sections, so a pin with one of those names is quoted in a
connection: `"params": net`. Metadata keys and builtin enum members are not
additional general-purpose statements.

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
for example, `net.passive inst` is allowed. Exact physical pin IDs may
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

Number literals include decimal integers (`12`), hexadecimal integers (`0xFF`),
binary integers (`0b1010`), decimal fractions (`12.5`, `.5`), and exponent forms
(`12e3`, `1.25e-3`, `.5e2`). Base prefixes may also use uppercase `X` or `B`.
A leading sign is a unary operator. Octal syntax is not supported; a leading zero
in a decimal literal does not change its base.

A single underscore may separate adjacent digits: `1_000`, `0xFF_FF`,
`0b1010_0011`, `1_2.3_4`, and `1e1_0`. Underscores cannot lead or trail a digit
sequence, repeat, or touch a base prefix, decimal point, exponent marker, sign,
or SI prefix. They do not affect the value.

A number may end in an SI prefix, with no space: `4.7k`, `100n`, `10u`, `2.2M`.
The prefix may instead replace the decimal point, as in IEC 60062 codes: `4k7`
is `4.7k`, `1u5` is `1.5u`, and `R` marks a unity decimal point (`4R7` is 4.7,
`47R` is 47).

| Prefix | `a` | `f` | `p` | `n` | `u` | `m` | `R` | `k` | `M` | `G` | `T` | `P` |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Scale | 10⁻¹⁸ | 10⁻¹⁵ | 10⁻¹² | 10⁻⁹ | 10⁻⁶ | 10⁻³ | 1 | 10³ | 10⁶ | 10⁹ | 10¹² | 10¹⁵ |

Prefixes are case-sensitive (`m` is milli, `M` is mega). `µ` is an alias for `u`,
including embedded forms such as `1µ5`; displays use the ASCII spelling `u`.
There is no `E` prefix. SI prefixes apply to decimal literals only, including
leading-dot forms such as `.5µ`. The value is computed exactly in decimal: `100n` is the double nearest
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

Physical pin identifiers are not evaluated as numbers. An unquoted numeric pin
key is plain decimal digits: base prefixes and digit separators, as in `0x1` or
`1_0`, are errors there, so quote such a pin name. Numeric expressions and array
indices use the numeric rules in section 4.

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
- Spellings are kept: `4k7` stays `4k7`, strings keep their escapes, pin keys
  keep their quotes, and a tolerance keeps `±` or `+/-`. Order, the pin-entry
  shorthand, and the choice between instance arguments and body lines are also
  the author's. The parentheses of an empty argument list are dropped.
- `net a, b: T` and `1 (a, b):` take one space after each comma; a tolerance is
  written `5 ± 5%` and a range `4.75..5.25`.
- A trailing comment stays after its code, two spaces from it. A comment on its
  own line stays above the code that follows it, at that code's indentation, or at
  the indentation of the block it ends. A comment inside a bracketed list keeps
  the list broken so the comment stays with its item.

The formatter checks that its result parses to the same syntax tree with the same
comments. It reports, and leaves unchanged, a file with a syntax error or a comment
it cannot place without moving it, such as one inside an expression outside a
bracketed list or a block comment followed by code on its line.

## 3. Declarations

A module contains imports, definitions, and extensions. Definitions start with
`component`, `block`, or `bundle` and have a nonempty indented body.
`extend Base<parameters> Name` specializes a component, block, or net type
(section 9.3); it takes the kind of its base. Its body is optional. Nets and
instances both use `Type name`; the resolved type determines which they declare.
Duplicate names, duplicate sections, and overlapping pin declarations are errors.

### 3.1 Physical components

A component describes one physical part and its pin interface:

```nosch
from noscheme import role, use, drive

component Resistor:
  params:
    resistance value
  pins:
    1..2: [role.passive, use.required]
  metadata:
    ref_prefix: "R"
    footprint: "Resistor_SMD:R_0603_1608Metric"
```

A pin entry may list builtin values instead of, or before, indented fields. Each
value's namespace names the field it sets: `role.x` sets `role`, `pull.x` sets
`pull`, `drive.x` sets `drive`, and `use.x` sets `use`. Several values from one namespace form a
list, which only `role` accepts, as a definition's permitted roles:

```nosch
pins:
  1: [role.ground, role.power_input, use.required]
  2: drive.low
    capacitance: 5p
```

This also applies in [specializations](#7-specialization) and instance pin
overlays, and follows
import aliases. Setting a field both ways, or using a value that is not a builtin
`role`, `drive`, `pull`, or `use` value, is an error. Ports and bundle fields are
excluded: their value position holds the net type.

Physical components are the lowest-level objects in NoScheme and do not contain
internal instances or nets. A component may
omit its footprint while a design is being developed; physical instances then
receive an unspecified-footprint warning.

Pins may declare aliases in parentheses after the pin key. Component-body
fragment:

```nosch
pins:
  1 (cathode, K): role.passive
  2 (anode, A): role.passive
```

Aliases and physical identifiers refer to the same endpoint. Every alias must
resolve to exactly one pin, including when it matches another physical pin's
identifier. Binding through an alias still exports the original physical pin ID.
An alias is a name, an integer spelling, or a quoted string such as `"EP-1"`. Only
a single pin key in a component's `pins` section, or its specialization, can take
aliases; a specialization's aliases replace the inherited ones.

### 3.2 Reusable blocks

A block exposes ports and parameters and contains an internal circuit. This
example assumes that `Resistor` and the builtin namespaces have been imported:

```nosch
block SeriesResistor:
  params:
    resistance resistance = 1k
  ports:
    net.passive input
    net.passive output
  Resistor<value: resistance> resistor:
    1: input
    2: output
```

Ports connect a block's internal circuit to the circuit that encloses each
instance of that block. Each port name is available as a connection inside the
block; do not separately declare a net of the same name. Internal parts and nets
are private. A caller may configure only the public
parameters, ports, choices, and supported instance metadata. It cannot access
`resistor.1` through the block or modify the internal resistor directly.

Each expanded port must lead internally to a physical pin, an explicitly modeled
external endpoint, or a placeholder. Recursive block instantiation is an error.

A block cannot directly connect two of its own ports: there is no internal
net-alias or net-merging statement. A pure-wiring bundle adapter must therefore
be expressed through field bindings at its caller. Placeholders permit incomplete
implementations but do not create a connection between otherwise separate ports.

### 3.3 Boards and globals

A board is the circuit produced by a project target. Its root is an ordinary block,
possibly specialized with `extend`. A root must have no public ports. Its parameters
use their defaults, including values bound by `extend`; a remaining required
parameter is an error. TOML and command-line parameter overrides are not supported.

Blocks may declare global nets and named ERC configurations, but a block containing
these declarations may only be expanded as a root. Instantiating one inside another
block reports an error at the instance. Inherited globals and configurations obey
the same rule. Ordinary blocks can serve as roots or be reused as instances.

Root-block body fragment:

```nosch
global net.ground gnd
global net.power v3v3
net.digital reset#, cs#
```

One declaration may name several nets. They share its type and indented fields,
so `I2CLine scl, sda:` with `wiring_capacitance: 50p` gives each net that
capacitance.

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
  number count = 2
  resistance value = 4.7k
  string label
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
no dimensional checking (for now). A quantity parameter also accepts a string, such as
`"10k 1%"` or `"DNP"`, which is displayed verbatim and has no numeric meaning; using
it where a number is required is an error.

A parameter without a default is required at instantiation. Supply named values
between the type and instance name, as `Resistor<value: 4.7k> r`, or under the
instance's `params:` section as `value: 4.7k`. Unknown arguments and wrong types
are errors.

A single unnamed value, `Resistor<4.7k> r`, selects the only parameter without a
default, even when other parameters have defaults. For example, with parameters
`resistance value` and `string label = ""`, it sets `value`. If every parameter has
a default, an unnamed value is allowed only when there is exactly one parameter.
Two required parameters, or two defaulted parameters with no required parameter,
make the shorthand ambiguous. An inherited unnamed parameter keeps its identity,
even after a specialization gives it a default. An ambiguous or absent target is
an error; an unnamed value must precede named entries, and the same parameter
cannot be set twice. Shifts in such lists require parentheses, for example
`Part<count: (1 << 3)> p`.
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
converted to binary64 and rounded to six significant digits (ties to even) without exponent notation or
trailing zeros: `4700` as `4.7kOhm`, `1e-7` as `100nF`. Computed and literal numbers,
such as `value / 1k` or `4.7k`, have no quantity and render plainly (`4.7`,
`4700`). Strings are display values, not electrical quantities.

### 4.2 Operators and numeric tags

Numbers carry either an integer tag or a double tag. Integers are signed 64-bit
values, from -9223372036854775808 through 9223372036854775807. Doubles are finite
IEEE-754 binary64 values. Integer literals, arithmetic, and conversions outside
that range fail with `number.limit`; they never wrap. The minimum negative
integer can be written directly as `-9223372036854775808`.

Precedence, highest first:

| Level | Operators/forms | Association |
|---|---|---|
| 1 | Grouping, member access, indexing, supported calls | Postfix |
| 2 | Unary `+`, `-` | Unary |
| 3 | `*`, `/` | Left |
| 4 | `+`, `-` | Left |
| 5 | `<<`, `>>` | Left |

`+`, `-`, and `*` produce an integer for integer operands and a double if either
operand is a double. `/` converts both operands to doubles and produces a double. Division by zero, overflow to
nonfinite values, and nonfinite results are errors. These operators are numeric;
`+` does not concatenate strings.

Shifts require integer-tagged operands and a count from 0 through 63. Left-shift
overflow is an error, including for negative operands. Right shift
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
Resistor<value: 33R> series:
  1: source
  2: destination
  metadata:
    identity: "series-resistor"
    ref: "R7"
```

An instance line starts with the type, optional `<parameters>`, and the instance
name with any array dimensions. It may carry a connection list in parentheses
(below). A trailing `:` introduces an indented body: its connections,
one `pin: net` per line, then `params`, `metadata`, and `elements` sections, in any
order among themselves. Connections come first; a connection after a section is a
syntax error. Either the argument list, the body, both, or neither may be present:
`Holder holder` and `Holder holder()` are the same instance. Omit an
empty section rather than writing its header without entries; omitting arguments
waives nothing, so required parameters and connection policies still apply.
Connections bind by name, except for the explicit interchangeable-pin group and
same-name connections described below. Parameters bind separately through `<...>` or `params:` (section 4.1).

#### Instance arguments and interchangeable pins

A component may declare one group of electrically interchangeable physical pins:

```nosch
component Resistor:
  params:
    resistance value
  pins:
    1..2: role.passive
  metadata:
    interchangeable: [1, 2]
```

The list accepts physical pin IDs or aliases, including bare integer spellings.
It must resolve to at least two distinct existing pins. Their electrical metadata
must match, including roles, connection policies, pull, numeric fields, and checks;
aliases and `custom` data may differ. This is an author assertion about the part's
symmetry, not electrical inference. Only one group is supported. An empty list
clears an inherited group. Specializations that give the pins different electrical
metadata, such as putting pull-up pull on only one terminal, must clear it with
`interchangeable: []` and use named connections.

```nosch
Resistor<value: 4.7k> series(source, destination)
Capacitor<value: 100n> decouple[4](global.v3v3, global.gnd)
Protection esd(data, no_connect, gnd: global.gnd)
```

These fragments assume the referenced types and nets exist. `Capacitor` declares
two interchangeable pins; `Protection` declares two interchangeable signal pins
and a separate pin aliased `gnd`.

A connection list has three kinds of argument, separated by commas:

- A positional net reference binds the next pin in the declared interchangeable
  group. If any positional connections are supplied, they must cover the whole
  group; write `no_connect` explicitly for an unused member. The target must be
  a component with a nonempty group.
- `pin: net` connects a single pin or public port by name, alias, or quoted ID.
  Named connections also work without an interchangeable group. A pin cannot be
  bound twice, including through a positional argument and an alias.
- `*` connects each remaining pin or port to the caller's net or port of the same
  name (below).

Newlines inside the parentheses are not layout, so arguments may be written on
one line or across several, and a trailing comma is allowed. Positional
references may include globals, bundle fields, and array indices/slices, with the
same broadcast rules as body connections. Endpoint lists and ranges are written in
the body.

Arguments and sections describe one instance, and neither overrides the other.
Named connections join the instance's connections and `<name: value>` entries its
parameters, so binding a pin or parameter twice, in either place or through an
alias or a positional net, is an error. An `elements:` entry overrides argument
parameters like any common parameter. Arguments with a `metadata:` or `elements:`
section suit a part that needs one setting a line cannot hold:

```nosch
Resistor<value: 60.4R> high(bus.high, split):
  metadata:
    custom:
      dnp: true
```

Prefer keeping all of a part's connections in one place, either its arguments or
its body, so its wiring reads at a glance.

#### Same-name connections

`*` in the argument list connects every pin or port that no other argument or body
line binds to the caller's net or port with the same name. Blocks often pass
supplies and buses straight through:

```nosch
block OutputBank:
  ports:
    net.power supply
    net.ground ground
    net.digital input[4][2]
    net.digital output[4][2]
  net.digital translated[4][2]
  OutputPair pairs[4](*, input: translated)
```

Here `supply`, `ground`, and `output` bind to the bank's ports of those names and
`input` to `translated`. A pin matches through its physical ID or any alias, so
`Led(*)` connects nets named `anode` and `cathode`. Candidates are the enclosing
definition's local nets and, in a block, its ports; globals are never matched, so
write `gnd: global.gnd`. A pin or port with no same-name net is left to its
connection policy, which by default requires a connection or `no_connect`.
`*` is an error when it matches nothing, or when one pin matches through two of
its names. Shapes follow the usual rules: a scalar net broadcasts across an
instance array, and an array net must match the instance and port dimensions.
Renaming a net or port can silently change what `*` connects; the connection
policy reports a pin that loses its connection.

Positional nets map deterministically to the listed physical pins, in list order.
Reordering the arguments or the group changes pad assignments in the exported
netlist even though the declared electrical behavior is equivalent. There is no
automatic PCB pin swapping or routing optimization.

#### Explicit array element overrides

An array instance may contain an `elements:` section. Each entry selects exactly
one element, with one nonnegative integer literal per dimension:

```nosch
Pullup<value: 4.7k> pulls[2]:
  supply: supply
  signal: bus
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
instance body. Per-element `ref` and `identity` are disallowed;
existing common identity rules, including array index suffixes, remain unchanged.
Changing an element's value does not change its identity. Reordering or inserting
array positions still can change physical identities.

`evaluate` reports the common declaration template when any elements use it, plus
explicit element variants. `compile` expands every element and checks the resulting
connection shapes; element parameters do not relax shape compatibility.

### 5.2 Endpoint binding

The left side names an instance pin, alias, or public port. It may select public
bundle fields and array elements. The right side names a declared caller net,
a caller port, a global, or `no_connect`, or `flat(net)` (section 6.3).

`anode: anode` is valid: the left name belongs to the instance interface and the
right name belongs to the caller. A pin may not bind twice, even to the same net.
Whole-port, field, range, and indexed bindings may not overlap.

Several distinct ports may bind to one caller net. Their attached endpoints then
share one flattened net; this is not itself an error. Independent declared nets
cannot be merged through a pin or an already connected internal net. There are
no standalone connection, net-alias, or net-merging statements.

The outermost bound caller net supplies the flattened source name. Internal names
and source locations remain available for diagnostics.

### 5.3 Connection policies

| Pin/port policy | Caller obligation |
|---|---|
| `use.optional` | Omission or intentional disconnection is allowed |
| `use.explicit` | Connect or explicitly bind `no_connect`; this is the default |
| `use.required` | Make a real connection; `no_connect` and omission are errors |
| `use.disallowed` | No net may be assigned; omission and `no_connect` are allowed |

A net name alone does not establish a useful physical connection. A connected
physical pin subject to connection requirements needs another physical or modeled
external endpoint. Ordinary forwarding ports and placeholders do not count as
additional physical endpoints.

For an intentionally lone endpoint, such as a test pad or antenna feed on its
own net, declare `use: use.optional` on the pin definition or a metadata
specialization. This permits the lone connection; it does not waive driver or
other electrical checks on that net. A net waiver cannot replace this pin policy.

`use.disallowed` forbids assigning any net, including an otherwise empty net.
Omission and `no_connect` are allowed. A pin or port has exactly one connection
policy, specified with `use: use.disallowed` or pin shorthand. Electrical roles
remain separate. Instance metadata and waivers cannot override
mandatory connection policies; a metadata specialization may correct a definition.

A required child port also needs an endpoint outside its block instance.

## 6. Bundles and arrays

### 6.1 Bundle declarations

```nosch
from noscheme import net, use, drive

bundle I2C:
  net.idle_high scl
  net.idle_high sda
```

A bundle is a named net type with named fields. A port or net can use `I2C` as
its type. A whole-bundle binding requires the same named bundle type and connects
fields by name. Merely having identical field structures does not make two bundle
types interchangeable.

Instance-body fragment:

```nosch
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
net.digital signals[4]
net.passive matrix[2][3]
Receiver receivers[4]:
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
| `flat(net)` to a pin list or range | Connect the net's elements in row-major order |

There is no `each` keyword. Shared connections undergo normal ERC.

### 6.3 Instance selection, ranges, and slices

Leading selectors choose elements of an instance array. Instance fragment:

```nosch
Indicator indicators[3]:
  [0].drive: status
  [1].drive: activity
  [2].drive: fault
```

A bare `drive` selects the port across the whole instance array. `[1].data[2]`
selects port element 2 on instance element 1. Indexed bindings do not override
broader bindings; overlap is an error.

Physical pin ranges use inclusive, nonnegative bounds that fit unsigned 64 bits.
Individual pin identifiers remain text and are not subject to that numeric bound.
Array slices use an exclusive stop.
Instance-body fragment:

```nosch
11..20: gpio[0:10]
30..21: lanes
```

The ten pins `11..20` match the ten net elements in ascending order. A range whose
first bound is larger counts down: pin 30 takes `lanes[0]` and pin 21 `lanes[9]`.
This explicit range ordering is distinct from positional instance arguments.
Physical ranges require ordinary integer labels; they do not support alphanumeric
or zero-padded IDs.
Ranges also apply to pin definitions and instance pin metadata. Slices require
explicit start and stop bounds; negative indices and out-of-range bounds are
errors. Empty selections are not useful connection endpoints and are rejected.

Bracketed pin lists select noncontiguous physical pins. A list may contain exact
pin identifiers, aliases, inclusive ranges, or a mixture:

```nosch
[11, 28, 50, 75, 100]: supply
[7..9, 23..24, 35]: no_connect
[VDD, 2..3, "07"]: supply
```

`[m..n]` has the same inclusive numeric bounds as `m..n`; `[1]` and `[1,]` are
valid singleton lists. Lists can span lines and have a trailing comma. Members
are literal pin identifiers, not parameter expressions. Ranges cannot use
alphanumeric or zero-padded bounds. List order, with each range expanded in its
written direction, determines the selection's array order. Existing connection
shape rules apply; a scalar net broadcasts to the whole selection.

A selection of pins is one-dimensional, so it cannot bind a multi-dimensional net
directly. `flat(net)` lists an array net's scalar elements in row-major order,
the last index varying fastest:

```nosch
// input is net.digital[4][2]: pins 4, 3 take input[0][0..1], and so on.
[4, 3, 5..10]: flat(input)
21..14: flat(translated)
```

The flattened net and the pin selection must then have the same length.
`flat()` takes an array net or array bundle field; a scalar or `no_connect` is an
error. Bundles inside the array stay whole elements.

The same keys work in a component's `pins:` declarations or specialization and
under an instance's `metadata.pins:`. Base declarations define physical IDs;
specializations, bindings, and instance annotations may also use existing aliases.
For example, a specialization may set shared supply metadata with:

```nosch
pins:
  [11, 28, 50, 75, 100]:
    role: role.power_input
    use: use.required
```

Pin lists do not apply to block ports, bundle declarations, parameters, or
ordinary metadata keys. Empty lists, duplicate entries, and overlapping ranges
are errors. Aliases that select the same pin twice are also errors. Overlap with
another binding or annotation remains an error. To select pins on a specific
instance-array element, write `[0].[1, 3]`; `[0].1` retains its existing meaning.

## 7. Specialization

Use `extend Base<parameters> Name` for single inheritance. The base must be a
component, block, or net type (section 9.3); bundles cannot be bases.
Parameter values in `<...>` replace inherited defaults. A trailing `:` is required
only when an indented body follows. Definition fragment:

```nosch
extend Resistor<value: 4.7k> Pullup:
  metadata:
    custom:
      purpose: "pull-up"
```

Specializations may override metadata and defaults for existing parameters.
They may not add/remove pins or parameters, change parameter types, alter an
internal circuit, or expose private internals. In particular, a block
specialization cannot add instances or nets. Existing parameterized dimensions
can still vary through parameter values. Instance arguments override defaults.

Metadata mappings merge recursively by key. Scalar values and lists replace
inherited values; lists such as a pin's aliases do not concatenate. A specialization
can replace a default but cannot remove it. The merged result is validated,
including aliases and electrical constraints. Inheritance cycles are errors.

At definition time, metadata can correct restrictive upstream roles or connection
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
| `component` | `bom`, `current_loads`, `custom`, `footprint`, `interchangeable`, `ref_prefix`, `value` |
| `current_load` | `max_load_current`, `pins` |
| `block` | `configurations`, `custom`, `erc_choices` |
| `bundle` | `custom` |
| `pin` | `checks`, `custom`, `max_load_current`, `max_source_current`, `role`, `tristate_capable`, `use` |
| `aggregate_load` | `max_load_current` |
| `port` | `aggregate_load`, `checks`, `custom`, `placeholder`, `role`, `use`, `waivers` |
| `net` | `checks`, `custom`, `net_class`, `placeholder`, `waivers` |
| `instance` | `bom`, `custom`, `erc_choices`, `footprint`, `identity`, `ref`, `value` |
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
nonnegative numeric expressions. A pin's aliases are written in its key (section
3.1), not as a field; names, strings, and integer spellings are allowed (so `01`
retains its leading zero). `role` on a definition can be one role or a list of
permitted roles.

Definitions declare `pins` directly. Instances annotate existing pins under
`metadata.pins`; they cannot add pins or change aliases or connection policies. An
instance's `role` is the role the pin plays in that circuit, directly or through a
choice mapping:

- On a component pin that declares no role or only `passive`, such as a
  connector or test-point contact, it describes the equipment attached there.
  Reports and ERC record it as the pin's `external_role`.
- On a pin that declares other roles, and on a block port, whose role is its
  public contract, it selects one of the declared roles.

`external_role` remains only for block ports, where it declares an explicitly
modeled endpoint outside the block. Writing it on a component pin is an error that
names `role`. Likewise, use `drive` on a component pin and `external_drive`
on a block port to describe attached directional drive. A modeled external
endpoint counts on the caller side of that port; the port's own `drive` remains
a promise that must be implemented inside the block.

`custom` accepts nested mappings, lists, strings, finite numbers, and booleans.
It has no built-in electrical meaning. For example, `custom.dnp` is a user
convention and does not automatically omit a part or change ERC. BOM fields belong
under the validated `bom` mapping (section 13.4), not under `custom`.

### 8.2 Built-in electrical extension fields

These fields use the same registry available to project ERC extensions:

<!-- generated:extension-fields -->
| Scope | Field | Type |
|---|---|---|
| `instance_pin` | `capacitance` | `capacitance` |
| `instance_pin` | `drive` | `drive.high`, `drive.low` |
| `instance_pin` | `external_drive` | `drive.high`, `drive.low` |
| `instance_pin` | `max_voltage` | `voltage` |
| `instance_pin` | `min_voltage` | `voltage` |
| `instance_pin` | `pull` | `pull.down`, `pull.none`, `pull.up` |
| `instance_pin` | `pull_resistance` | `resistance` |
| `net` | `max_voltage` | `voltage` |
| `net` | `min_voltage` | `voltage` |
| `net` | `wiring_capacitance` | `capacitance` |
| `pin` | `capacitance` | `capacitance` |
| `pin` | `drive` | `drive.high`, `drive.low` |
| `pin` | `max_voltage` | `voltage` |
| `pin` | `min_voltage` | `voltage` |
| `pin` | `pull` | `pull.down`, `pull.none`, `pull.up` |
| `pin` | `pull_resistance` | `resistance` |
| `port` | `drive` | `drive.high`, `drive.low` |
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
    resistance value = 4.7k
  pins:
    1:
      role: role.passive
    2:
      role: role.passive
      pull: pull.up
      pull_resistance: value
```

Import `role` and `pull` from `noscheme` for this fragment. The part displays
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
| `unchecked` | Electrical behavior is unspecified; ERC cannot treat this endpoint as passive or prove its driver behavior. |
<!-- /generated:roles -->

An absent, unchecked, or unselected role is not silently treated as passive.
An explicit `drive` supplies directional digital behavior even without a `role`.
These roles do not assert voltage tolerance, logic thresholds, timing, or a
particular operating state unless explicitly modeled by other metadata.

A pin's active drive and passive pull are independent:

```nosch
from noscheme import drive, pull

component ResetPin:
  pins:
    1: [drive.low, pull.up]
```

`drive.low` means actively pull low or release; `drive.high` means actively pull
high or release. Neither name specifies transistor topology. The KiCad importer retains its
conventional mapping of `open_collector` to `drive.low` and `open_emitter` to
`drive.high`, but reports `assumed_drive_direction` for review: those imported
names alone do not prove a particular device's polarity. Verify the datasheet
and specialize the imported declaration if needed. A pin can provide a
passive pull while its active driver is released, as shown above. `pull.none`
means this pin provides no passive pull; it does not forbid pulls elsewhere on
the net. An omitted `pull` is unknown.

`drive` may stand alone or refine a digital classification. On passive connector
pins an instance annotation describes attached equipment, just like an instance
`role`; the elaborated attribute is `external_drive`. A declared fixed drive
cannot be reversed by an instance. Analog, power, and ground classifications
cannot carry directional digital drive. Ordinary `role.digital_output` retains
its existing push-pull behavior when no `drive` is specified.

### 9.2 Scalar net types

All checked scalar types permit passive attachments. Other permitted roles are:

| `net` member | Other permitted roles |
|---|---|
| `passive` | Ordinary digital, analog, power, and ground roles |
| `digital` | Digital input/output/bidirectional, `drive.low`, `drive.high` |
| `bidirectional` | Same role compatibility as `digital`, without its required active driver |
| `tristate` | Digital inputs; digital output-capable roles explicitly marked `tristate_capable: true` |
| `idle_high` | Digital inputs and `drive.low` endpoints; requires a `drive.low` and a `pull.up` contributor |
| `idle_low` | Digital inputs and `drive.high` endpoints; requires a `drive.high` and a `pull.down` contributor |
| `analog` | Analog input/output/bidirectional |
| `power` | Power input/output, digital input, analog input |
| `ground` | Ground, digital input, analog input |
| `unchecked` | No domain restriction from this declaration |

The `idle_high` and `idle_low` requirements run by default, even without an
explicit `checks` section. One pin may satisfy both the drive and pull
requirements; multiple matching contributors are permitted. Missing active drive
is `erc.missing_driver`; missing passive pull is `erc.missing_pull`. An opposing
passive pull produces `erc.opposing_pull`: these net types do not model resistor
dividers. Opposing directional drive and push-pull outputs are incompatible.
Resistance and capacitance budgets remain separate opt-in checks.

`use.disallowed` always retains its disconnection rule. Every checked declaration
on a flattened net contributes constraints; a caller does not erase an inner
port's constraints. An explicit digital-input port may connect to power/ground
as a fixed logic level while retaining its internal input contract.

An outermost `unchecked` declaration skips the standard driver, current-budget,
and voltage-limit policies without a warning. The ERC report records
`unchecked_nets`: the number of flattened nets with an outermost unchecked
declaration, counted once across configurations. The text report includes this
count; CLI and export summaries expose it as `erc_unchecked_nets`. It does not
suppress pin connection policies, inner compatibility constraints, or
grouped/aggregate-load validity checks. Explicit `checks` applications, including
those an inner port receives from its net type, and project rule-set callbacks
still run; `unchecked` is not a general callback bypass. The `unchecked_net`
standard rule name remains available as a compatibility no-op.

### 9.3 Net type definitions

An extension of a `net` builtin names reusable net metadata, typically checks, on top of a
builtin electrical type or another net type:

```nosch
from noscheme import net, pull, std, use, drive

extend net.idle_high I2CLine:
  wiring_capacitance: 5p
  checks:
    pull: std.parallel(pull_resistance, where=pull.up, min=1.5k, max=10k)
    load: std.sum(capacitance, net=wiring_capacitance, max=400p)

extend I2CLine FastI2CLine:
  checks:
    load: std.sum(max=200p)

bundle I2C:
  I2CLine scl
  I2CLine sda
```

A net type is used wherever a net type is expected: nets, ports, and bundle
fields, with or without array dimensions. Its body contains the fields a net
declaration accepts (section 8), with no sections or parameters. Every chain
ends at a builtin `net` value, which determines
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

A port's `role` or `drive` promises behavior implemented inside the block. It adds no driver
or load. Internal physical or modeled endpoints must implement the promise;
caller circuitry cannot supply the evidence for an internal output.

Input contracts admit inputs and passive contacts. Output contracts admit outputs,
feedback inputs, and passive contacts in the same domain. Bidirectional contracts
admit the corresponding directional roles. `drive.low`/`drive.high` contracts admit
their respective drivers, digital inputs, and passive contacts. Ground contracts
admit ground, passive, digital-input, and analog-input roles. Passive/unchecked contracts add no domain
restriction.

A directional contract requires a corresponding implementing endpoint. A
bidirectional contract may be implemented by a bidirectional endpoint or separate
input and output endpoints. A passive pull does not implement
an output contract.

### 9.5 Tri-state buses

On `net.tristate`, every digital output-capable endpoint must declare
`tristate_capable: true`, including bidirectional roles and `drive.low`/`drive.high`.
Digital inputs and passive attachments need no flag. Several capable drivers may
share the net. Wrong-domain roles remain errors even with the flag.

This checks declared capability, not output-enable timing or bus arbitration.
An all-input tri-state net does not receive a missing-active-driver error from
this type. If another declaration on the flattened net is `net.digital`,
that declaration retains its ordinary driver checks. Generic bidirectional roles
do not imply tri-state capability.

## 10. Electrical checks

### 10.1 Checking model

ERC operates on flattened nets and counts physical endpoints once across
hierarchy. Ordinary ports do not duplicate endpoints. A connector contact's
instance role, its attached equipment, replaces the contact's role for
driver/load checking. An explicitly modeled
external endpoint on a block port counts once but creates no physical CAD pin.

Digital nets require a suitable driver or explicitly declared compatible pull;
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
  net.power supply:
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

`pull` is an assertion about a signal-side endpoint. It does not verify a
resistor's other terminal, rail, displayed resistance, or supply voltage by itself.
Use `std.pull_reference` for the optional terminal check described below.
`net.idle_high` requires `drive.low` and `pull.up`; `net.idle_low` requires
`drive.high` and `pull.down`. A passive pull cannot power a rail or satisfy an
output-port contract. Merely connecting a resistor to a rail does not infer a pull.

Nets and public ports opt into budgets with named checks (section 12.2). For
example, with `pull` and `std` imported:

```nosch
net.idle_high bus:
  wiring_capacitance: 2p
  checks:
    pull_strength: std.parallel(pull_resistance, where=pull.up, min=1.5k, max=10k)
    capacitance: std.sum(capacitance, net=wiring_capacitance, max=400p)
```

Each physical endpoint counts once. Ordinary public ports contribute assertions,
not extra physical loads. Explicit external endpoints do contribute. An inner
port's assertions and its caller's assertions both run against the flattened net.
`net=` adds the named outermost-net quantities once per application.

Matching pulls combine as `1/R = sum(1/R_i)`. `std.sum` adds capacitance or other
numeric attributes. Numeric limits accept parameter expressions. Missing values
and unknown selections remain unknown; use explicit zero for a known zero
capacitance and `pull.none` for an endpoint known to have no pull. Known overloads
and known parallel resistance below a minimum remain errors even with missing
contributors. Incomplete reductions also warn by default. An empty parallel
reduction represents an open circuit: it passes a finite minimum and fails a
finite maximum. Empty sums are zero.

Nets without an application do not receive these budget diagnostics. Tolerance,
mixed reference rails, and protocol-derived limits are not automatically modeled.

#### Pull reference terminals

A physical signal-side pin can check its pull's other terminal with the pin rule
`std.pull_reference(pin)`, naming another pin ID or alias on the same component:

```nosch
pins:
  2:
    role: role.passive
    pull: pull.up
    checks:
      reference: std.pull_reference("1")
```

With `pull.up`, the reference terminal must reach a net declared
`net.power`; with `pull.down`, it must reach `net.ground`. Missing
pins, disconnected references, conflicting rail types, and a reference tied to the
signal net itself produce `erc.pull_reference`. The check also runs on a
disconnected signal pin. It traces no paths through other components and infers
no voltages. To waive it for one pin, name the application with a `pins` target
on the signal net (section 10.5); disconnected pins have no net waiver.

#### Explicit voltage ranges and pin limits

A net declares its operating range with `min_voltage` and `max_voltage`
together. A physical pin or instance pin uses the same fields, alone or together,
for its absolute limits, in volts. These fields accept finite signed numeric expressions, in volts. Bounds
are inclusive; minimum values cannot exceed maximum values.

`voltage` sets both bounds in one field, as a range or a tolerance:

```nosch
net.power v5:
  voltage: 5 ± 5%        // 4.75 to 5.25
net.analog vref:
  voltage: 2.5 +/- 0.01  // 2.49 to 2.51
net.ground gnd:
  voltage: 0..0
```

`low..high` gives the bounds directly. `center ± deviation` is `center - deviation`
to `center + deviation`; with `%` the deviation is a percentage of the center,
computed as `center * (100 ∓ deviation) / 100`, so a negative center needs a range.
`+/-` is the ASCII spelling of `±`. Each part is an ordinary numeric expression,
such as `0..supply * 2`. `voltage` cannot be combined with `min_voltage` or
`max_voltage` on the same declaration, and a plain number is an error: it does not
say which bound it sets. Ranges and tolerances are written only as a `voltage`
value.

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
net.digital signal:
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
net.ground gnd:
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
unused_port               pull_reference               voltage_limits
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

A block declares public choices in metadata:

```nosch
metadata:
  erc_choices:
    mode: ["source", "load", "absent"]
```

An internal instance can select a role using a local choice. Instance fragment:

```nosch
metadata:
  pins:
    1: choice(mode, source=role.power_output, load=role.power_input, absent=role.passive)
```

`choice(name, alternative=value, ...)` names a local choice, then gives the value
for each of its alternatives. It may also be written as a pin's `role:` field. On a
pin that declares active roles, every case must be one of them (section 8.1).
Every alternative must have exactly one case. Alternatives are distinct,
nonempty literal strings; one used in `choice(...)` must also be a name. A role
list without a selection remains uncertain; the compiler does not choose a role to
make the circuit pass.

Parents bind child choices under instance `metadata.erc_choices`. The value is a
literal alternative or a `choice(...)` of a parent's lexical choice:

```nosch
metadata:
  erc_choices:
    mode: choice(power_source, usb="source", header="absent")
```

Choices are forwarded explicitly through each block interface. They are not
addressable by private instance paths or available in parameter expressions.
Array elements inherit common choice bindings; `elements:` metadata may override
bindings for an individual block element. Each retains a distinct expanded scope.

ERC checks every combination of the board's choices. Each combination is a
configuration named by its selections in choice-name order, such as
`supply_12v=port0,supply_5v=jack`. A board whose choices form more than 64
combinations must name its configurations instead.

Board metadata may name configurations, which then replace the combinations. Name
them to give them readable names or to leave out combinations that cannot occur:

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

All configurations run by default. Repeat `--erc-configuration NAME` to select a
subset. A board without choices has one unconfigured check. A named configuration
may leave a choice unselected; choices have no implicit defaults, and omitted
selections warn where they prevent checking. Invalid choices, selections, and role
overrides are blocking source errors. Configuration names do not waive conflicts.

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
`std.pull_reference` applications.

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
| `selector` | A predicate, `role`, `drive`, or `pull` value, boolean field name, or a list of them | A tuple of terms for `noscheme.erc_primitives.selected` |

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
from noscheme import net, std, use, drive

block B:
  net.digital signal:
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
policy also accounts for selectable roles, directional drive and passive pulls, tri-state
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
side effects. The Python engine runs extensions in its own process; the native
engine invokes them through Python subprocesses. Read-only views are not a
Python sandbox.

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
- a `drive` value, which tests effective drive capability, including attached equipment;
- a `pull` value, which compares the endpoint's `pull` field;
- the name of a boolean field, such as `tristate_capable`, which must be `true`.

A list such as `[pull.up, std.digital_compatible]` requires every term.
Selection is three-valued: missing attributes, unselected role alternatives, and
predicates returning `None` make it unknown. An empty list selects every endpoint.

### Rules

Every registered reducer is also a net rule with parameters
`(field, where=(), net=(), min, max)`. It reduces `field` over the endpoints
selected by `where`, adds the outermost-net quantities named by `net`, and asserts
`min` and/or `max`. For example, `std.sum(capacitance, max=400p)` and
`std.parallel(pull_resistance, where=pull.up, min=1.5k)`.

`std.count(what, min, max)` requires at least one bound and counts endpoints
matching `what`. `std.any(what)` asserts at least one match. `std.all(what,
where=())` asserts that every endpoint selected by `where` matches `what`. Empty
count is zero, empty any is false, and empty all is true. Available predicates
include `std.active_digital_driver`, `std.power_source`, `std.digital_compatible`,
`std.idle_high_compatible`, `std.idle_low_compatible`, and
`std.tristate_capable`. Active drive and the capability to tri-state are separate
facts; runtime enable states are not inferred.

Reducers receive `Interval(lower, upper, optional=False, unknown=False)` values
from `noscheme.erc_primitives`. Bounds describe possible contributions;
`optional=True` means selection is uncertain. Unknown nonnegative quantities
range from zero to infinity; general signed quantities are unbounded.
`std.sum` and `std.parallel` propagate intervals and completeness. A custom
reducer must return an ordered interval and retain uncertainty honestly. Infinities
represent internal bounds/open circuits and are never serialized as JSON numbers.
Electrical reductions and current budgets use binary64 arithmetic, with ordinary
comparisons and no implicit tolerance. Roundoff near an exact limit is not
compensated; choose limits with practical engineering margin. Contributions are
accumulated in circuit order in both implementations.

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
unambiguous aliases, written in the pin key (`"12" (PH0):`). A two-terminal
symbol whose reference prefix is `R`, `C`, or `L` gets a required `value`
parameter of type `resistance`, `capacitance`, or `inductance`, since its KiCad
`Value` is only a placeholder; other symbols keep `string value` with KiCad's
`Value` as the default. Ambiguous aliases
and unsupported electrical types are reported rather than guessed.

The converter also declares interchangeable pins for two-terminal symbols whose
pins are all passive and whose reference prefix is `R`, `C`, `L`, or `FB`, unless
the symbol's name, description, or keywords mark it as polarized. It also recognizes
two-terminal diode symbols with passive pins named `A1` and `A2`. Ordinary diodes,
polarized capacitors, and devices with more than two pins are excluded. This is a
library-data heuristic: review the generated group for specialized parts, and
clear it in a specialization when the terminals have different uses.

KiCad `no_connect` maps to `use.disallowed`. An explicit KiCad `tri_state`
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

`noscheme.<target>.state.json`, beside the configuration, stores identity UUIDs, reference
assignments, reference reservations, and the previous exported design. Commit
this file to version control. KiCad and BOM exports read and rewrite it; ordinary compilation
and editor analysis do not. Deleted reference assignments remain reserved.
Renaming a target renames its state file: rename `noscheme.<old>.state.json` too, or
the next export assigns new references and UUIDs. An export for a target without state
warns (`export.state`) when a state file for an unconfigured target remains.
There is no reservation-cleanup command yet.

The exporter stages output and atomically replaces state last. Ordinary
replacement failures roll back earlier replacements; abrupt process/machine
failure is not an all-files transaction.

### 13.3 Names and net classes

The KiCad adapter converts source names and writes a reversible mapping. This
does not synchronize CAD circuit edits back into source. Source names and source
locations remain in the circuit representation.

Exported net names follow KiCad's hierarchical form, so the netlist and the
generated schematic (section 13.7) name every net alike. A net's path splits at
its deepest enclosing block instance: block steps join with `/` (KiCad sheet
separators), and steps within that block, such as bundle fields, join with `.`.
Array indices attach to their step. A root net with a single step keeps its
bare name (`gnd`, `pps`); any other root net starts with `/` (`/leds.clock`).
A net inside `time_display.digits[0]` is `/time_display/digits[0]/segments[0]`.

`net_class: "Name"` assigns membership. KiCad owns class settings such as width
and clearance. The generated schematic (section 13.7) carries membership as
KiCad net-class directives, so updating the board from it assigns the classes.
For the netlist-import fallback, `update-kicad-classes` writes a new project
copy with the memberships, preserving those settings and rejecting undefined
classes. Language-level differential-pair metadata
is not implemented; it must not be inferred to work from source-name suffixes.

### 13.4 Bills of materials

`noscheme export-bom --project PATH [--target NAME]` writes `bom.csv` in the selected
target's output directory. It elaborates all component arrays and nested blocks,
but does not run ERC or require a footprint snapshot. Run `check` separately for
electrical validation. Parts without assigned footprints remain in the BOM with
an empty footprint cell and the usual compiler warning.

Components, component extensions, and physical component instances may specify:

```nosch
metadata:
  bom:
    manufacturer: "Example Components"
    mpn: "EXAMPLE-0603-10K"
    supplier: "Example Supplier"
    supplier_part_number: "001234"
    dnp: false
```

The four purchasing fields are optional literal strings; `dnp` is an optional
literal boolean, defaulting to `false`. Unknown keys and incorrect types are
errors. Inheritance and instance/array-element overrides merge individual fields.
BOM metadata cannot be applied to a block or block instance. `custom` purchasing
notes and `custom.dnp` are not interpreted by this exporter.

The UTF-8 CSV has these columns, in order:

`Quantity`, `References`, `Value`, `Footprint`, `Manufacturer`, `MPN`, `Supplier`,
`Supplier Part Number`, `DNP`.

Rows group parts only when their definition, evaluated parameters, displayed value,
footprint, purchasing fields, and DNP status all match. This conservative rule
keeps differently parameterized parts separate even when their displayed values
match. References are comma-separated within a quoted cell and sorted by prefix
and trailing reference number (`R2` before `R10`); rows follow their first reference.
Values use the same display formatting as KiCad export. Absent purchasing fields
are empty. CSV quoting preserves commas, quotes, newlines, Unicode, and leading
zeros in part-number strings; import part-number columns as text in spreadsheet
software to retain those zeros. A text cell beginning with `=`, `+`, `-`, `@`, a tab,
or a carriage return gets a leading `'`, so spreadsheet software shows it as text
rather than evaluating it as a formula.

DNP parts appear as separate rows marked `yes` in the `DNP` column; fitted rows use
`no`. Quantity is the number of physical instances in either case. DNP does not
remove components from elaboration, ERC, or the KiCad netlist, and is not selected
by ERC configurations. References beginning with `#` are included too.

BOM export allocates and persists references through the same per-target identity
state as KiCad export. Either export can run first, and subsequent exports agree
on references. BOM export preserves the last KiCad design snapshot, so the next
KiCad change report still compares against the last CAD export. CSV and state are
published together with the existing rollback behavior; state is replaced last.
The command prints a JSON summary containing output/state paths, grouped row count,
total physical parts, fitted parts, and DNP parts. Empty designs produce a CSV with
only the header. Both Python and native implementations support this command.

### 13.5 Hierarchy diagrams

`noscheme export-hierarchy --project PATH [--target NAME]` writes `hierarchy.svg`
in the selected target's output directory, or to `--output FILE`. It elaborates
the design but does not run ERC or require a footprint snapshot.

- Each block instance is a box nested inside its parent, labelled with its
  instance name, definition, and total part count, in source declaration order.
- Elements of a block array share one box labelled with the array shape, such
  as `digits[9]`; that box shows the first element.
- `--nets` lists each block's ports with the flattened net each binds to. A
  bundle port shows the bundle's common net prefix and field count; hovering
  shows every net.
  - Power and ground nets are bold with a dark marker.
  - A signal net bound by two or more sibling blocks gets the same colour
    wherever it appears.
  - Other nets are grey.
  - Colours repeat once the palette runs out.
- `--parts` lists each block's component instances, with arrays collapsed.
- `--depth N` draws N levels of nested blocks below the root and names the
  hidden ones.

The diagram describes structure only. It draws no wires, and it is not a
schematic. The command refuses to overwrite project inputs and prints a JSON
summary with the output path and the block and part counts. It is implemented
in Python only.

### 13.6 Schematic sheets

`noscheme export-schematic --project PATH [--target NAME] [--seed N] [--effort X]`
writes `schematic.svg`, or `--output FILE`: the whole design on one sheet, as
large as it needs to be. Like `export-hierarchy`, it elaborates the design but
does not run ERC or need a footprint snapshot. The drawing is generated from
the source, which stays the design of record.

- **Blocks are drawn open.** Each block instance is a box, nested to any depth.
  The header gives its instance name, its definition, and the rails its ports
  carry. Signal ports sit on the edge, labelled, and wires attach there inside
  and out. Block instances with the same definition, part values, and
  connectivity share one layout.
- **Placement** is annealed within each block to shorten wires, plus a cost on
  the space the contents take up. A bundle's connection weighs more than a
  single net, and rails only pull weakly. A greedy pass then slides items
  together wherever that does not raise the cost. Seeded runs are
  reproducible: the same `--seed` gives the same sheet. `--effort` scales the
  number of annealing moves (default 1).
- **Rotation.** Placement may turn each two-pin part instance by quarter
  turns; it never mirrors one. Larger symbols stay upright. A part's name runs along its longer edge: above a
  horizontal two-pin part, beside a vertical one.
- **Rail-only parts.** Parts whose only connections are rails or no-connects,
  such as decoupling capacitors, are not annealed. They are packed into a
  cluster, grouped by definition, at the bottom-right corner of their block.
- **Pin and port sides.** A pin may sit on any edge, but each component
  definition has one arrangement, shared by all its instances before
  rotation. Pin sides and
  block port sides are chosen again between placement passes, from where their
  connections lie. Along a side, pins are ordered by the mean position of the
  pins they connect to. Definitions update one at a time, so two parts wired
  pin for pin line their pins up and the wires run parallel. Rail pins go
  last on their side, clear of the signal rows. Two-pin parts
  keep their pins on opposite edges. Other parts start with power on top,
  ground below, and outputs on the right.
- **Wires** follow a grid in eight directions: orthogonal runs with 45- or
  90-degree bends. A dot marks every junction; a crossing has no mark. Wires
  avoid symbols through a high cost, not a ban. A net may cross the members of
  an instance array it connects to.
- **Bundles.** A bundle port is a single terminal, and its connection is one
  thick bus. A field that meets a single pin leaves the bus through a 45-degree
  breakout labelled with the field name. Where no 45-degree entry is free, it
  enters square on, still labelled.
- **Rails** (power and ground nets) are not wired. Each rail pin carries a power
  or ground flag naming its net.
- **Tags.** A net with one terminal in a block gets a net tag there: the net's
  name in a pointed outline. Tags with the same name are connected. When
  routing leaves connections unplaced, the block is routed again with the
  failed nets first, up to four times. Whatever still fails gets tags at its
  terminals, and the JSON summary counts these as `unrouted`.

The command refuses to overwrite project inputs. It prints a JSON summary with
the output path, the block and part counts, the number of distinct block
layouts, and `unrouted`. It is implemented in Python only.

### 13.7 KiCad schematics

`noscheme export-kicad ... --schematic PATH.kicad_sch` also writes a
hierarchical KiCad schematic rooted at `PATH.kicad_sch`, with its sub-sheets
beside it. Put it beside the board's `.kicad_pro`, with the project's name, and
use KiCad's **Update PCB from Schematic** (F8) to update the board. It is
written in the same atomic publication as the netlist and identity state, and
uses the same references, values, footprints, and net names. The sheets are an
export: the source stays the design of record. Every export rewrites them from
scratch, deterministically, so edits made in KiCad are overwritten. Only the
Python implementation supports it so far.

- **Sheets.** Each block instance is a sheet named for its instance (such as
  `digits[0]`), and the root is the root sheet. Block instances whose parts,
  values, footprints, BOM fields, and rail connections all match share one
  sheet file, so an instance array is one file used once per element. A sheet
  file is named for its block definition (`display_Digit.kicad_sch`).
- **Stale sheets.** A `.kicad_sch` in that directory that an earlier export
  wrote (its generator is `noscheme`) and this one does not is deleted. KiCad
  rewrites the generator when it saves a sheet, so a sheet saved from KiCad is
  never deleted.
- **Symbols** are generated boxes, not KiCad library art: pins in number order,
  first half on the left, two-pin parts small and horizontal. They are embedded
  in the sheets and also written to `noscheme.kicad_sym`, which the export
  registers in the directory's `sym-lib-table`, keeping other entries.
- **Connections** are labels on pin ends, never wires. Rails and single-step
  root nets use global labels; a net declared in a block uses a local label on
  its sheet; a net entering a block through a port uses a hierarchical label
  named for the port field, matched by a sheet pin on the block's sheet symbol.
  Rail ports, and ports the block does not use, have no sheet pin.
- **Net classes.** Each `net_class` membership becomes one KiCad net-class
  directive on the net. The class must be defined in the KiCad project.
- **KiCad's ERC.** NoScheme's ERC has already judged every connection, so the
  sheets are drawn for KiCad's schematic ERC to agree with it:
  - Every pin NoScheme leaves open gets a no-connect flag, as does a pin alone
    on its net. KiCad then names such a net `unconnected-(...)`.
  - A net with a power input but no power output gets a `PWR_FLAG`.
  - Pin types follow the netlist's role mapping, except that an `unspecified`
    type, or one of a pair KiCad's default pin-conflict matrix rejects, is
    drawn passive.

  A decoration in a shared sheet appears in every instance, so it is placed
  only where it is right for all of them. With NoScheme's ERC clean, KiCad's
  should report nothing.
- **Fields.** Each symbol carries `noscheme_path`, its path within its sheet,
  and the BOM fields (`Manufacturer`, `MPN`, `Supplier`,
  `Supplier Part Number`); `dnp: true` sets KiCad's DNP flag.
- **UUIDs** derive from the identity namespace, so re-exports are stable. KiCad
  links a footprint to `/root/sheet.../symbol` UUIDs, not the netlist's flat
  ones: the first update of a board placed from `board.net` needs F8's
  "Re-link footprints to schematic symbols based on their reference
  designators" option.

## 14. Complete example

This two-part test board connects a resistor between an external digital signal
and ground. `main.nosch`:

```nosch
from noscheme import role, net, use, drive

component Resistor:
  params:
    resistance value
  pins:
    1..2: [role.passive, use.required]
  metadata:
    ref_prefix: "R"
    footprint: "Resistor_SMD:R_0603_1608Metric"

component Header:
  pins:
    1..2: [role.passive, use.required]
  metadata:
    ref_prefix: "J"
    footprint: "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical_SMD_Pin1Left"

block Demo:
  global net.ground gnd
  net.digital signal
  Header connector:
    1: signal
    2: global.gnd
    metadata:
      pins:
        1: role.digital_output
        2: role.ground
  Resistor<value: 10k> load:
    1: signal
    2: global.gnd
```

Place this `noscheme.toml` beside it:

```toml
language_version = "0.1"
source_paths = ["."]
library_paths = []
output_dir = "build"

[targets.demo]
root = "main.Demo"
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

## 15. Limits and unsupported features

The implementation has configurable Python-API resource limits to reject runaway
expansion and oversized expressions. Integers always fit signed 64 bits; the API may
reduce that limit for testing but cannot enlarge it. Other defaults include 65,536
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
