# NoScheme Language Reference Manual

**Language version:** `0.1`

**Scope:** the implemented language and compiler in this repository.

NoScheme describes a circuit as explicit physical parts connected by named nets.
The compiler expands reusable blocks and arrays, checks connectivity and declared
electrical constraints, and exports a netlist for KiCad. Placement and routing
belong to the CAD tool.

This manual describes source-language behavior. [DEVELOPMENT.md](DEVELOPMENT.md)
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
from noscheme import role, nettype, unused, bias, f, num
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
| `f` | Part-value formatting helpers |
| `num` | Numeric conversion and rounding helpers |

Use qualified builtin values: `role.passive`, not bare `passive`. An attribute's
context does not supply an implicit namespace. Builtins also support aliases,
such as `from noscheme import role as electrical` with
`electrical.passive`, and module imports, such as `import noscheme` with
`noscheme.role.passive`. Module qualification exposes exported definitions;
it never grants access to a component or block's private internals.

## 2. Lexical rules

### 2.1 Layout and punctuation

Indentation defines blocks and must use spaces; indentation tabs are errors.
Four spaces per level is the repository convention. Blank and comment-only lines
do not declare blocks. Instance parentheses retain significant indentation.
Statements and mapping entries use newlines, not semicolons.

`:` separates names from types, bindings, or metadata values. `=` appears in
parameter defaults and named formatting arguments; it is not a connection
operator. Lists and helper calls use commas. Instance sections do not.

The structural words are `from`, `import`, `as`, `component`, `block`, `board`,
`bundle`, `extends`, `inst`, `net`, and `global`. Section names are `pins`,
`ports`, `params`, `defaults`, `connections`, and `metadata`. These words are
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

In a physical pin key, integer spelling is preserved as text:

| Spelling | Physical identity |
|---|---|
| `1`, `"1"` | The same pin |
| `01`, `"01"` | The same pin, distinct from `1` |
| `A1`, `TAB` | Bare identifier pin names |
| `"EP-1"` | A quoted pin name containing punctuation |

Physical pin identifiers are not evaluated as numbers. Numeric expressions and
array indices use the numeric rules in section 4.

## 3. Declarations

A module contains imports and definitions. Definitions are `component`, `block`,
`board`, or `bundle`; their bodies must contain declarations or sections.
Duplicate names, duplicate sections, and overlapping pin declarations are errors.

### 3.1 Physical components

A component describes one physical part and its pin interface:

```nosch
from noscheme import role, unused

component Resistor:
    params:
        value: string
    pins:
        1..2:
            role: role.passive
            unused: unused.never
    metadata:
        ref_prefix: "R"
        footprint: "Resistor_SMD:R_0603_1608Metric"
```

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
        resistance: string = "1kOhm"
    ports:
        input: nettype.passive
        output: nettype.passive
    inst resistor: Resistor(
        connections:
            1: input
            2: output
        params:
            value: resistance
    )
```

Ports declare internal connections; do not separately declare a net of the same
name. Internal parts and nets are private. A caller may configure only the public
parameters, ports, choices, and supported instance metadata. It cannot access
`resistor.1` through the block or modify the internal resistor directly.

Each expanded port must lead internally to a physical pin, an explicitly modeled
external endpoint, or a placeholder. Recursive block instantiation is an error.

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

Public parameter types are `number` and `string`. Definition-body fragment:

```nosch
params:
    count: number = 2
    value: string
```

A parameter without a default is required at instantiation. Instance arguments
are named entries under `params:`. Unknown arguments and wrong types are errors.
Defaults may depend on other parameters; dependency cycles are errors. An
explicit argument replaces its default, so the replaced default is not evaluated.

Parameters may determine dimensions, indices, and physical part values, and may
be forwarded to child parameters. They do not select topology, imported types,
footprints, electrical roles, or arbitrary metadata. ERC choices are a separate
mechanism, described in section 11.

A parameter named `value` supplies the conventional displayed part value.
`metadata.value` can explicitly provide a value expression. Formatting strings
are display values, not electrical quantities.

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

### 4.4 String formatting and unit helpers

Supported string expressions are literals, parameter references, formatting, and
unit-helper results. Formatting is inspired by Python, but does not evaluate
Python. Expression examples:

```text
"{0}".format(value)
"{resistance:.1f}kOhm".format(resistance=r)
"{{{0}}}".format(value)
f.nF(100)
f.kOhm(4.7)
```

Fields must use explicit numeric indices or named arguments. Positional arguments
precede named arguments. Names are not captured implicitly from the surrounding
scope. `{{` and `}}` insert literal braces. Automatic `{}` fields, attribute/index
traversal, conversion flags such as `!r`, and nested format fields are errors.

Numeric format specifications accept optional width, optional `.precision`, and a
required `d`, `f`, `e`, or `g` suffix. `d` requires an integer and forbids precision.
Formatting width, precision, and output size are bounded.

Import `f` for `Ohm`, `kOhm`, `MOhm`, `pF`, `nF`, `uF`, `nH`, `uH`, and `mH`.
Each takes one positional number and appends its fixed suffix: `f.nF(100)` yields
`"100nF"`. There is no automatic rescaling, dimensional type, or unit conversion.
Integers render in decimal; doubles use their shortest round-trip representation,
with a trailing `.0` removed. ERC never derives a quantity from a displayed value.

## 5. Instances and connections

### 5.1 Instance form

Definition-body fragment:

```nosch
inst series: Resistor(
    connections:
        1: source
        2: destination
    params:
        value: f.Ohm(33)
    metadata:
        identity: "series-resistor"
        ref: "R7"
)
```

`connections`, `params`, and `metadata` may be omitted when unnecessary. Omit an
empty section rather than writing its header without entries. The parentheses
contain the instance's contract; there is no additional instance implementation
body. Part and block instances always bind by name, never position.

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
inst receivers[4]: Receiver(
    connections:
        input: signals
)
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
inst indicators[3]: Indicator(
    connections:
        [0].drive: status
        [1].drive: activity
        [2].drive: fault
)
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

## 7. Specialization

Components and blocks use `extends` for single inheritance. Boards and bundles
cannot specialize definitions. Definition fragment:

```nosch
component Pullup extends Resistor:
    defaults:
        value: "4.7kOhm"
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
and types. Most metadata is literal; the notable deferred expression is `value`.
Numeric electrical metadata uses units in field names, not formatted strings.

### 8.1 Core fields

| Scope | Fields |
|---|---|
| Component `metadata` | `footprint`, `ref_prefix`, `value`, `current_loads`, `custom` |
| Block `metadata` | `erc_choices`, `custom` |
| Board `metadata` | `erc_choices`, `configurations`, `custom` |
| Bundle `metadata` | `custom` |
| Physical pin declaration | `aliases`, `role`, `unused`, `tristate_capable`, `max_source_current_mA`, `max_load_current_mA`, `custom` |
| Port/bundle field declaration | `role`, `unused`, `placeholder`, `waivers`, `aggregate_load`, `custom` |
| Net declaration | `placeholder`, `net_class`, `waivers`, `custom` |
| Physical instance `metadata` | `footprint`, `ref`, `identity`, `value`, `pins`, `custom` |
| Block instance `metadata` | `identity`, `erc_choices`, `pins`, `custom` |
| Instance `metadata.pins` entries | `role`, `external_role`, `tristate_capable`, `custom` |

`footprint`, `ref_prefix`, `ref`, `identity`, and `net_class` are strings.
`tristate_capable` and `placeholder` are booleans. Current amounts are finite,
nonnegative numeric literals. `aliases` is a list of names/strings. `role` on a
definition can be one role or a list of permitted roles; an instance's `role`
selects one permitted role, directly or through a choice mapping.

Definitions declare `pins` directly. Instances annotate existing pins under
`metadata.pins`; they cannot add pins or change aliases or unused policies.
`external_role` describes equipment attached to a connector or an explicitly
modeled endpoint on a block port.

`custom` accepts nested mappings, lists, strings, finite numbers, and booleans.
It has no built-in electrical meaning. For example, `custom.dnp` is a user
convention and does not automatically omit a part or change ERC.

### 8.2 Built-in electrical extension fields

These fields use the same registry available to project ERC extensions:

| Scope | Fields and types |
|---|---|
| Pin or instance pin | `bias`: `bias.pull_up`, `bias.pull_down`, or `bias.none` |
| Pin or instance pin | `pull_resistance_ohm`: positive numeric literal |
| Pin or instance pin | `capacitance_pF`: nonnegative numeric literal |
| Net or port | `min_pull_up_resistance_ohm`, `max_pull_up_resistance_ohm`, `min_pull_down_resistance_ohm`, `max_pull_down_resistance_ohm`: positive numeric literals |
| Net or port | `max_capacitance_pF`: nonnegative numeric literal |
| Net | `wiring_capacitance_pF`: nonnegative numeric literal |

All numeric metadata must be finite. Neither parameters nor part-value strings
supply these quantities implicitly.

## 9. Electrical roles and net types

### 9.1 Roles

Import `role` and use one of:

| Role | Meaning |
|---|---|
| `passive` | No active driver or electrical-domain assertion |
| `digital_input`, `digital_output` | Digital receiver or active output |
| `digital_bidirectional` | Digital input/output capability; not automatically tri-state |
| `analog_input`, `analog_output`, `analog_bidirectional` | Analog endpoint behavior |
| `power_input`, `power_output` | Supply load or supply source |
| `ground` | Ground endpoint |
| `open_drain` | Output that pulls low and can release the net |
| `open_source` | Output that pulls high and can release the net |
| `unchecked` | Endpoint behavior is not checked as a known role |
| `do_not_connect` | Must remain disconnected |

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
| `ground` | Ground, power input, digital input, analog input |
| `unchecked` | No domain restriction from this declaration |

`do_not_connect` always retains its disconnection rule. Every checked declaration
on a flattened net contributes constraints; a caller does not erase an inner
port's constraints. An explicit digital-input port may connect to power/ground
as a fixed logic level while retaining its internal input contract.

Only an outermost `unchecked` declaration skips net driver/current checks and
registered net callbacks. It does not suppress pin connection policies, inner
compatibility constraints, or grouped/aggregate-load validity checks.

### 9.3 Public port roles

A port's `role` promises behavior implemented inside the block. It adds no driver
or load. Internal physical or modeled endpoints must implement the promise;
caller circuitry cannot supply the evidence for an internal output.

Input contracts admit inputs and passive contacts. Output contracts admit outputs,
feedback inputs, and passive contacts in the same domain. Bidirectional contracts
admit the corresponding directional roles. Open-drain/open-source contracts admit
their respective drivers, digital inputs, and passive contacts. Ground contracts
admit ground, passive, and input roles. Passive/unchecked contracts add no domain
restriction.

A directional contract requires a corresponding implementing endpoint. A
bidirectional contract may be implemented by a bidirectional endpoint or separate
input and output endpoints. Bias through passive components does not implement
an output contract.

### 9.4 Tri-state buses

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
hierarchy. Ordinary ports do not duplicate endpoints. Connector `external_role`
replaces the contact's role for driver/load checking. An explicitly modeled
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

Pin definitions may supply `max_source_current_mA` and `max_load_current_mA`.
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
            max_load_current_mA: 100
```

Members are aliases or quoted physical pin IDs. Groups must be nonempty, resolve
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
            max_load_current_mA: 100
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
resistor's other terminal, rail, displayed resistance, or supply voltage.
Open-drain default-level checks require pull-up bias; open-source default-level
checks require pull-down bias. Bias cannot power a rail or satisfy an output-port
contract.

A net/port opts into pull-resistance checks by declaring a min/max resistance
limit. Matching annotated pulls combine as `1/R = sum(1/R_i)`. Each physical
endpoint counts once. Missing annotated resistance or no matching pulls warns.
Known pulls below the minimum prove a violation even with unknown contributors;
a maximum-resistance violation requires complete information about contributors.
Unannotated pins are not inferred to have internal pulls.

A net/port opts into capacitance checking with `max_capacitance_pF`. Sum annotated
endpoint capacitances and the outermost net's `wiring_capacitance_pF`. Missing
endpoint/wiring values warn; explicit zero declares a known zero contribution.
Known overloads remain errors even with unknown contributions. All limits on
public boundaries are enforced without duplicating endpoints.

Nets without the corresponding limits do not receive these budget or missing-data
diagnostics. Tolerance, mixed reference rails, and protocol-derived limits are
not automatically modeled.

### 10.5 Waivers and check names

Net-declaration fragment:

```nosch
net signal: nettype.digital
    waivers:
        missing_driver: "Driver is across the explicitly modeled series resistor."
```

A waiver names one implemented check and gives a nonempty literal reason. Only
the outermost declaration of the flattened net may waive its checks. Inner-port
waivers produce `waiver_scope` and do not suppress diagnostics. Private-net
waivers apply independently to each block instance. Reports retain reasons,
locations, and waived diagnostics, including waivers with no matching diagnostic.

Core check identifiers are:

```text
do_not_connect             required_connection          unused_pin
unconnected_net            missing_driver               multiple_drivers
power_ground_conflict      incomplete_roles             unchecked_net
aggregate_load_boundary    aggregate_current_overload   incomplete_aggregate_load
current_load_group         current_overload             incomplete_budget
waiver_scope               incompatible_role            tristate_capability
port_role                  incomplete_port_role         required_port_connection
unused_port
```

Built-in registry checks are `pull_resistance_budget` and `capacitance_budget`.
Diagnostic codes prefix these names with `erc.`; waiver keys omit the prefix.

`do_not_connect`, `required_connection`, `unused_pin`, `required_port_connection`,
`unused_port`, `waiver_scope`, `current_load_group`, `aggregate_load_boundary`,
`aggregate_current_overload`, and `incomplete_aggregate_load` cannot be waived.
Other implemented net checks and registered checks support scoped waivers.

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
            external_role:
                choice: "mode"
                cases:
                    source: role.power_output
                    load: role.power_input
                    absent: role.passive
```

The same mapping is available for `role`, subject to the definition's permitted
roles. Every alternative must have exactly one case. Alternatives are distinct,
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
All elements of an instance array share the declaration's choice bindings while
retaining distinct expanded instances.

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

Declare extension files relative to `noscheme.toml`:

```toml
erc_modules = ["checks.py"]
```

Each Python file defines `register(registry)`. This extends metadata and checks
without adding grammar productions.

| API | Purpose |
|---|---|
| `registry.fields(scope, schema)` | Add recognized metadata fields |
| `registry.check(name, callback, net_fields=(...))` | Add a named net check, optionally activated by declaration fields |
| `value(record, key, default=None)` | Read a metadata value; import from `noscheme.erc_registry` |
| `ctx.diagnostic(name, message, record=..., severity=..., related=...)` | Construct a source-located result |

Register fields in `pin`, `port`, `net`, `instance_pin`, `component`, `block`,
`board`, or `instance` scopes. Field schemas may use `positive`, `nonnegative`,
`string`, `bool`, `literal`, or nested dictionaries. Duplicate fields, replacement
of core fields, and duplicate/core check identifiers are errors. Prefer
project-specific field groups and check-name prefixes.

A callback receives `NetContext`: `net`, `origin`, `endpoints` as label/record
pairs, and `circuit`. Return an iterable of diagnostics or yield them; yield
nothing when the check does not apply. A callback's diagnostic code must match
its registered name. `net_fields` activates a check when any flattened declaration
contains one of the named fields; without it, the callback decides applicability.

Each invocation receives an isolated copy with source information and effective
roles for its ERC configuration. `circuit.erc_configuration` and
`circuit.erc_effective_choices` identify that context. A callback cannot mutate
another check's input or CAD connectivity through this copy. Registered checks
use ordinary net waivers and are skipped by outermost unchecked nets.

Extensions execute Python in the compiler process, including editor checks.
They should be deterministic and side-effect free. Context isolation is not a
Python sandbox. Import/registration errors, exceptions, and invalid diagnostic
results block publication; reported electrical violations follow ordinary ERC
publication rules.

See [examples/erc_extensions/checks.py](../examples/erc_extensions/checks.py) for a
complete extension.

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
into a new directory. It preserves physical pin text, follows symbol inheritance,
folds duplicate representations, and keeps unambiguous aliases. Ambiguous aliases
and unsupported electrical types are reported rather than guessed.

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

`metadata.ref` requests an explicit physical reference. Otherwise the exporter
allocates references using the component prefix (default `"U"`) and persistent reservations.
KiCad reference names must match `[A-Za-z_#][A-Za-z0-9_#]*`, and case-insensitive
reference collisions are rejected.

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
| `noscheme load --project PATH` | Load configuration and imports |
| `noscheme resolve --project PATH` | Resolve definitions and public interfaces |
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
`load`, `resolve`, and `evaluate` are inspection stages, not electrical validation.

### 14.1 Failures and publication

Diagnostics carry a stable code, severity, message, filename, line/column, and
related source locations. Expanded instance paths and configuration labels are
retained where applicable. Text and JSON reports describe the same checks.

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

The language server supports diagnostics, go-to-definition, basic hover, and
contextual completion. It uses unsaved buffers and a short typing debounce.
See [EDITOR_SUPPORT.md](EDITOR_SUPPORT.md#language-server) for client setup and current limits.

## 15. Complete example

This two-part test board connects a resistor between an external digital signal
and ground. `main.nosch`:

```nosch
from noscheme import role, nettype, unused, f

component Resistor:
    params:
        value: string
    pins:
        1..2:
            role: role.passive
            unused: unused.never
    metadata:
        ref_prefix: "R"
        footprint: "Resistor_SMD:R_0603_1608Metric"

component Header:
    pins:
        1..2:
            role: role.passive
            unused: unused.never
    metadata:
        ref_prefix: "J"
        footprint: "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical_SMD_Pin1Left"

board Demo:
    global net gnd: nettype.ground
    net signal: nettype.digital
    inst connector: Header(
        connections:
            1: signal
            2: global.gnd
        metadata:
            pins:
                1:
                    external_role: role.digital_output
                2:
                    external_role: role.ground
    )
    inst load: Resistor(
        connections:
            1: signal
            2: global.gnd
        params:
            value: f.kOhm(10)
    )
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
[tristate](../examples/tristate), [erc_extensions](../examples/erc_extensions), and the
[STM32 reference board](../examples/stm32h523).

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
- Implicit nets, positional instance arguments, private-instance access, or net merging.
- Physical quantity types or automatic electrical inference from part-value strings.
- Tri-state output-enable simulation or arbitration proofs.
- Resistance/capacitance tolerance models and automatic protocol-limit derivation.
- Language-level differential-pair declarations.
- A compiler formatter, rename/refactoring, or complete bundle-member LSP navigation.
- CAD-to-source circuit synchronization or automatic placement/routing.

The parser grammar is maintained in
[src/noscheme/noscheme.lark](../src/noscheme/noscheme.lark); metadata schemas are in
[src/noscheme/metadata.py](../src/noscheme/metadata.py) and built-in electrical schemas
in [src/noscheme/erc_electrical.py](../src/noscheme/erc_electrical.py). Parsing alone
does not establish semantic validity: resolution, elaboration, ERC, and export
validation apply the additional rules described in this manual.
