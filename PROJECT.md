# NoScheme

NoScheme is a declarative language for PCB design without schematics.

## Purpose and status

I don't like drawing, reading, or maintaining schematics. I started my career in
semiconductors, where engineers largely moved to textual circuit descriptions
such as Verilog and VHDL. I would like to bring that workflow to PCB design.

NoScheme describes component instantiation and connectivity at an abstraction
level between a low-level netlist and a behavioral, synthesizable HDL. Common
operations should be concise, with useful defaults and reusable definitions.
The compiler checks the circuit and exports it for PCB layout. NoScheme does
not perform layout, but carries information such as net classes and differential
pairs that helps the CAD tool.

This document consolidates the agreed design. It is not yet a complete grammar
or implementation specification. The [reference-board draft](examples/stm32h523.nosch)
exercises the design; it compiles structurally but is not electrically verified.
Examples below illustrate accepted syntax choices, with remaining details listed
under [Unresolved design and implementation work](#unresolved-design-and-implementation-work).
The external [design review](claude_review.md) is supporting discussion, not an
alternative specification. Its recommendations apply only where adopted here.

The implementation milestones are tracked in [PLAN.md](PLAN.md). The
[experiment findings](experiments/FINDINGS.md) record the completed grammar and
KiCad import/update experiments, including observed limitations. The parser
experiment accepts the reference draft. The compiler front end now produces a
typed, source-located AST using `noscheme parse`. `noscheme load` reads project
configuration and binds explicit imports. `noscheme resolve` resolves definitions,
inheritance, interfaces, and the initial metadata schema. `noscheme evaluate`
binds parameters and evaluates instance declaration templates without expanding
arrays or constructing connectivity. `noscheme check` expands and checks circuit
structure and runs implemented electrical checks; `noscheme compile` writes a
deterministic `circuit.json` plus `erc.txt` and `erc.json`.
`convert-library` explicitly converts KiCad libraries, and `export-kicad` emits
a netlist using converted footprint records and persistent identity state.
Initial ERC now checks physical pin connections, drivers, and per-net current budgets,
and writes text/JSON reports. Advanced ERC and GUI validation of the production
exporter remain. Development commands are in
[PLAN.md](PLAN.md).

## V1 scope and workflow

- Implement the compiler in Python. Source files use the `.nosch` suffix.
- Use a grammar-driven lexer/parser library rather than build a parser by hand.
- Support one board per project and KiCad 10.0.x as the first CAD backend.
- Explicitly instantiate physical parts and reusable circuit blocks.
- Convert KiCad libraries to NoScheme definitions in a separate, explicit step.
- Parse, resolve and expand the design, perform electrical rules checking (ERC),
  and export components and connectivity for placement and routing in PCBNew.
- Keep NoScheme authoritative for components and connectivity. Subsequent updates
  preserve compatible KiCad placement and routing and present removed parts and
  changed connections through an update workflow.
- Produce net tables, component/connectivity change reports, and ERC reports in
  `.txt` and `.json`.

ERC errors do not block export when the circuit is unambiguous. Users must be
able to inspect partial or electrically invalid designs in KiCad. The compiler
must not guess when it cannot construct a definite circuit: syntax errors,
unresolved names, nonexistent pins, and contradictory bindings block export.

An unspecified footprint produces a warning. The exporter reports affected
physical instances and exports the remaining footprints and connectivity.
Incomplete instances remain in the compiled circuit but have no placeable board
object until a footprint is supplied. The first fully placeable reference board
must have footprints for all its physical parts. Reusable blocks themselves do
not require footprints.

### Deferred features

V1 excludes algorithmic circuit generation, conditional part inclusion, physical
quantity parameter types, automatic I2C capacitance and pullup-strength checks,
and full assembly variants. Parameter use is limited as described below.

The design should leave room for a future generation API, potentially Python,
that creates the same nets and instances as the native language. Its execution
and extension model has not been chosen. A separate logical-pin/package layer
and a dedicated pin-group feature are not needed in v1; wrappers can provide
convenient interfaces over explicit physical pins.

Basic Vim/Neovim syntax highlighting accompanies the first usable compiler.
LSP diagnostics and completion follow afterward. Canonical formatting/linter
support follows once the syntax stabilizes.

## Reference board

The worked example is an STM32H523 board with:

| Function | Requirement |
|---|---|
| MCU | STM32H523VCT6, 100-pin LQFP |
| Debug | Standard 10-pin Cortex SWD connector |
| USB | USB-C connector carrying data and supplying 5 V |
| Regulation | 5 V to 3.3 V LDO, such as an 1117-style device |
| Indicators | A few LEDs; the draft uses three |
| Storage | microSD socket using SPI, with card detection if provided |
| Display | 1x4, 0.1-inch header carrying power, ground, SCL, SDA for an SSD1306-style I2C OLED |
| Expansion | 2x10, 0.1-inch header with two grounds, 5 V, 3.3 V, and sixteen GPIO connections, including I2C- and SPI-capable pins |
| UARTs | Two independent UART ports on three-pin JST headers, ordered GND, MCU TX, MCU RX; instantiate the headers as a two-element array |
| Buttons | Two normally-open, active-low buttons with individual pull-up resistors; GPIO assignments pending |
| BOOT0 | 2-pin, 0.1-inch header: jumper to 3.3 V, pull-down resistor for a default low level; verify MCU boot configuration |
| Passives | 0603 packages |

Prefer expansion I2C and SPI peripherals independent of the OLED and microSD
buses where MCU pins permit. The expansion 5 V pin is on the USB-C 5 V net; its
3.3 V pin is on the LDO output net. External equipment may draw from or supply
these rails. ERC configurations model the alternative uses without changing
those physical connections.

The UART draft uses `uart_tx[2]` and `uart_rx[2]` nets with
`inst uart_headers[2]: UartHeaderJST3(...)`. Ground is shared across both headers;
each TX/RX element connects to the corresponding header. JST series/pitch,
footprints, and UART-capable MCU pins remain to be selected.

Exact support parts, MCU pin assignments and alternate functions, connector
parts and pin order, LED details, and power arrangements need verification
against manufacturer documentation. The draft uses placeholder blocks where
those decisions have not been made.

## Projects and imports

Each project has a `noscheme.toml` file declaring:

- Language version.
- Top-level board.
- Source and library search paths.
- Output directory.

Paths resolve relative to this configuration file. Module lookup uses only
explicit search paths. Finding the same module in multiple locations is an
error, not a search-order preference. Component libraries can serve many projects.

The implemented configuration uses required keys `language_version` (currently
`"0.1"`), `board` (such as `"main.Blink"`), `source_paths`, `library_paths`, and
`output_dir`. Both search-path arrays participate in module lookup. A module
`parts.passives` maps to `parts/passives.nosch` under those roots. Search roots
must exist and cannot repeat; symlinks cannot take a module outside its root.
Imports load only reachable modules and reject cycles. Named imports may be
re-exported through another module, retaining the original definition identity.
The `noscheme` module is reserved for builtins.

Imports name their definitions explicitly; wildcard imports are not supported.
Built-in namespaces also require imports. The current draft uses this provisional
standard-library module path:

```text
from noscheme import role, nettype, unused, f, num
```

Built-in electrical values use explicit qualification, such as `role.passive`,
`nettype.power`, and `unused.never`. Attribute keys stay short:
`role: role.passive`. Attribute context does not implicitly resolve bare values.
User-defined types such as `I2C` are ordinary names resolved through lexical scope
and imports.

## Syntax and names

Indentation uses spaces; tabs in indentation are errors. Indentation defines
blocks. Instance declarations retain parentheses with
significant indentation inside them. The parentheses visually group the
connections and configuration; an instance has no separate implementation body.
Block definitions contain the internal circuit. Definition interfaces are not
being moved into parentheses.

Identifiers follow `[A-Za-z_#][A-Za-z0-9_#]*`: ASCII C-style names with `#`
allowed anywhere, including first. This applies to nets, ports, instances, and
other identifiers. Keep this rule unless concrete problems justify revisiting it.
Backend reference-designator validation is separate from source naming.

Comments use `//` to end of line and non-nesting `/* ... */` blocks. The first
`*/` closes a block comment. `#` never starts a comment.

String literals use double quotes and JSON-style escapes. Invalid escapes and
unpaired Unicode surrogate escapes are errors.

### Instance declarations

```text
inst series: Resistor(
    connections:
        1: source
        2: destination
    params:
        value: f.Ohm(33)
    metadata:
        footprint: "Resistor_SMD:R_0603_1608Metric"
)
```

The sections are `connections`, `params`, and `metadata`. Entries use `:` and
indentation rather than commas; empty sections may be omitted. Connections and
component parameters always bind by name, never position. Helpers may accept
positional arguments.

Declarations and connections within a block are order-independent. All v1
connections occur inside instance declarations; there are no separate connection,
net-merging, or net-aliasing statements.

## Components, pins, and specialization

### Physical identifiers and aliases

Physical pin identifiers are stored and matched as exact text. Source syntax
accepts integer tokens, identifiers, or quoted strings:

| Source forms | Meaning |
|---|---|
| `1`, `"1"` | The same physical pin |
| `01`, `"01"` | The same physical pin, distinct from `1` |
| `A1`, `TAB`, `MP1` | Bare identifiers are accepted |
| `"EP-1"` | Quotes allow unusual identifiers |

A component may contain both `"01"` and `"1"`; similarity is not an error.

Pins may declare multiple aliases:

```text
pins:
    1:
        aliases: [cathode, K]
        role: role.passive
        unused: unused.never
```

An alias and its physical identifier designate the same endpoint. Aliases must
resolve unambiguously within a component. An alias identifying different pins,
including a collision with another pin's physical identifier, is a definition
error. Imported KiCad names are available when unambiguous; the converter reports
ambiguous names rather than silently inventing replacement aliases.

V1 keeps package-specific physical definitions. A wrapper block may expose a
convenient port and explicitly bind several underlying pins to it. Those pins
retain their identities, metadata, and connection checks. Other package mappings
can use other wrappers and physical component definitions.

### Parameters and defaults

Parameters have type `number` or `string`. They may be used for array indexing,
array dimensions, and setting part values. A parameter with no default requires
an argument at instantiation.

| Context | Section and entry |
|---|---|
| Base definition, required parameter | `params:` with `value: string` |
| Base definition, defaulted parameter | `params:` with `resistance: string = f.kOhm(1)` |
| Specialization | `defaults:` with `value: f.kOhm(1)` |
| Instance | `params:` with `value: resistance` |

The enclosing construct distinguishes declaration from binding; no additional
`param` keyword is needed.

### Specialization

Specializations use single inheritance and can override metadata and supply or
replace defaults for existing parameters. Instances may override those defaults.
Specializations cannot add or remove pins or parameters, change parameter types,
modify internal structure or connectivity, or expose private internals. Existing
parameterized dimensions may still vary through the base definition's parameters.

These rules apply to physical components and reusable blocks. An internal part's
value is configurable from outside only through parameters exposed by the block.

- Follow the inheritance chain; child values override parent values.
- Merge metadata mappings recursively by named field.
- Replace lists, including `aliases`, as a whole rather than concatenate them.
- A child may replace a parameter default but cannot remove it in v1.
- Reject inheritance cycles; impose no language-level depth limit.
- Validate the resulting definition, including alias uniqueness.

At definition time, specialization may correct restrictive upstream metadata,
such as unused-pin policies and permitted roles. Instances must obey the resulting
definition. This is the mechanism for correcting erroneous library constraints.

### Metadata

Recognized metadata is validated; unknown fields outside `custom` are errors.
Arbitrary user metadata belongs under `metadata.custom`, including manufacturer
part numbers and purchasing notes.

Definitions and instances use the same per-pin structure: definitions declare
`pins`, while instances place pin annotations under `metadata.pins`. Instances
cannot create pins or override the definition's constraints. There is no separate
`pin_roles` shorthand.

DNP remains a user convention, such as `metadata.custom.dnp`. It has no built-in
assembly, export, or ERC behavior in v1.

#### Initial implemented metadata schema

The definition resolver currently supports the fields below. This is the starting
schema, not the complete v1 ERC vocabulary. Unknown fields are errors; advanced
ERC configurations, aggregate loads, and pair declarations are not yet
implemented and must not be silently ignored.

| Scope | Fields |
|---|---|
| Physical component definition | `footprint`, `ref_prefix` (strings), `value` (deferred expression), `custom` |
| Block, board, bundle definition | `custom` |
| Physical pin definition | `aliases` (name/string list), `role` (qualified role or list), `unused`, nonnegative literal `max_source_current_mA` and `max_load_current_mA`, `custom` |
| Port or bundle field | `placeholder` (boolean), `role`, `unused`, `waivers`, `custom` |
| Net | `placeholder` (boolean), `net_class` (string), `waivers`, `custom` |
| Physical instance | `footprint`, `ref`, `identity` (strings), `value` (deferred expression), `custom` |
| Block instance | `identity`, `custom` |
| Instance pin/port annotation | `role` (one permitted role), `external_role` (role or list), `custom` |

Roles currently include `passive`, `digital_input`, `digital_output`,
`digital_bidirectional`, `analog_input`, `analog_output`, `analog_bidirectional`,
`power_input`, `power_output`, `ground`, `open_drain`, `unchecked`, and `do_not_connect`.
These require the explicit `role` import and qualification. `unused.always`,
`unused.explicit`, and `unused.never` require the explicit `unused` import.
An instance role must belong to the definition's permitted roles when specified;
instance annotations cannot change `unused` or aliases. Current metadata is
retained with source locations; the initial ERC checks below use it.

`custom` contains indented mappings with literal strings, finite numbers,
booleans, lists, and nested mappings. Its values have no implicit electrical
meaning. Specialization merges mappings recursively and replaces scalar/list
values, retaining the source and lexical module of each inherited expression.

## Numbers and formatting

The public type is `number`; each value carries an integer or double tag.

Integers have arbitrary precision subject to configurable implementation resource
limits; doubles are finite IEEE-754 binary64 values. Non-finite literals/results
and failed conversions are errors. Decimal integer, fractional, and exponent
literals are supported. Unary signs bind most tightly, followed by multiply/divide,
add/subtract, and shifts. Binary operators at each level associate left.

| Operation | Behavior |
|---|---|
| `3` | Integer-tagged literal |
| `3.0`, `3e0` | Double-tagged literals |
| `+`, `-`, `*` | Numeric arithmetic; mixed operands produce a double |
| `/` | Always produces a double, including `4 / 2` |
| `<<`, `>>` | Require integer-tagged operands and a nonnegative shift count |
| Right shift | Preserves the sign |
| Implicit double-to-integer conversion | Truncates toward zero and warns, even for an integral value such as `3.0` |
| `num.int(x)` | Truncates toward zero, returning an integer without the implicit-conversion warning |
| `num.ceil(x)` | Rounds toward positive infinity, returning an integer |
| `num.floor(x)` | Rounds toward negative infinity, returning an integer |
| `num.round(x)` | Rounds to nearest, ties away from zero, returning an integer |

Thus `num.round(2.5)` is `3` and `num.round(-2.5)` is `-3`. Explicit helpers do
not emit implicit-conversion warnings. All conversions still undergo context
checks, including valid array dimensions and indices. Numeric expressions also
support parameter references and parentheses. `num` requires an explicit import;
its helper spelling is provisional.

String expressions support literals, parameter references, and a restricted
`.format()`-style feature. This is NoScheme formatting inspired by Python, not
Python evaluation. It supports substitutions and ordinary numeric format
specifications such as `{resistance:.1f}`. Replacement fields cannot traverse
Python objects, access methods, or execute code; `{value.__class__}` is invalid.

Use explicit positional indices (`"{0}".format(value)`) or named arguments
(`"{resistance:.1f}".format(resistance=value)`), with positional arguments before
named ones. Names are not implicitly captured from the surrounding scope.
`{{` and `}}` escape braces. Numeric format specifications support an optional
width, optional `.precision`, and a required `d`, `f`, `e`, or `g` suffix.
`d` requires an integer-tagged value and permits no precision. Formatting width,
precision, and output size are bounded. Empty replacement fields, conversion
flags, nested fields, and attribute/index traversal are rejected.

Formatting helpers such as `f.nF(10)`, `f.kOhm(4.7)`, and `f.uH(2.2)` turn a
magnitude in the named unit into a display string. For example, `f.nF(10)` produces
`"10nF"`. These helpers require the explicit `f` import and do not introduce
physical quantity types, unit conversion, or dimensional checking. ERC does not
infer quantities by parsing those strings.

Implemented unit helpers are `Ohm`, `kOhm`, `MOhm`, `pF`, `nF`, `uF`, `nH`,
`uH`, and `mH` under `f`. Each takes one positional numeric argument and appends
its fixed suffix, with no automatic rescaling. Integers render in decimal;
doubles use the shortest round-trip representation, removing a trailing `.0`.
Numeric helpers under `num` also take one positional argument.

## Scope, ports, and connectivity

Lexical scope is the default. Blocks expose declared interfaces and keep internal
instances and nets private. There is no access to or monkey-patching of internals.

All nets require declarations. A port declaration also declares the internal
connection, so internal instances can use that name without another net declaration.
A caller binds the port to its own declared net, allowing names to differ across
hierarchy.

### Globals

Only the top-level board may declare global nets. References are explicit, such
as `global.gnd`, and never create a net implicitly. Instantiating a block that
references a missing global is an error.

The block author chooses whether to use globals or ordinary ports. There is no
separate `requires` declaration: dependencies follow from actual references.

### Binding rules

- The left side addresses the instance's physical pin, alias, or public port,
  including fields and array elements of that port.
- The right side names a declared caller net or uses `no_connect` where allowed.
- `anode: anode` is ordinary: the sides resolve in different scopes, with no
  shadowing warning or special handling.
- Multiple distinct ports may bind to one caller net. Their attached endpoints
  then belong to one flattened electrical net. This produces no special
  "ports shorted" diagnostic.
- The outermost bound caller net supplies the source name for that flattened
  net; internal names remain available for diagnostics. The CAD adapter converts
  the source name to its exported spelling.
- Binding an endpoint twice is an error, even to the same net. Whole-port,
  field-level, and array bindings cannot overlap.
- A pin cannot connect to distinct caller nets. Port binding through hierarchy
  does not authorize arbitrary merging of independent net declarations.

### Bundles

Bundles are named net types with named fields. Their field structure is separate
from endpoint electrical roles. A whole-port binding requires the same named
bundle type and matches fields by name. Identical fields do not make differently
named types implicitly compatible.

Field bindings allow explicit connections between different bundle types:

```text
connections:
    bus.scl: control.scl
    bus.sda: control.sda
```

Crossed interfaces also use ordinary field bindings, such as `uart.rx: link.tx`
and `uart.tx: link.rx`. No protocol-specific reversal or wiring inference is
needed. Paths address public endpoints, never private internal components.

### Arrays, ranges, and slices

Instances, scalar nets, and bundles may be arrays, with at least two dimensions
supported. Bundle fields may themselves be arrays. Indexing is zero-based;
`signal[2][3]` means two groups of three. Field access and indexing compose left
to right, as in `buses[1].data[2]`.

An instance array's dimensions precede each port's dimensions. Three instances
with four-element ports have effective endpoint shape `[3][4]`. Connection checks
use this effective shape.

| Net and endpoint shapes | Result |
|---|---|
| Identical array shapes | Connect corresponding indices |
| Scalar net to an endpoint array | Share the scalar across all endpoints |
| Scalar bundle to an array of same-type bundle ports | Share each field's net |
| Different array shapes, even equal element counts | Error |
| `[4]` net array to `[3][4]` endpoints | Error; no partial broadcasting |
| Array of distinct nets to one pin | Error; would merge nets |
| One-element array versus scalar | Distinct shapes; no automatic flattening |

Sharing still applies normal ERC to all endpoints. No `each` syntax is needed.
Dimensions and indices use integer values, with the numeric conversion rules above.

Leading indices in an instance array's connection list select instances:

```text
inst indicators[3]: Indicator(
    connections:
        [0].drive: status
        [1].drive: activity
        [2].drive: fault
)
```

`[1].data[2]` selects element 2 of the second instance's `data` port. A bare port
name binds across the entire instance array using its effective shape. Indexed
bindings never override broader bindings; overlap is an error.

Physical pin ranges have inclusive bounds, while array slices exclude the stop:

```text
connections:
    11..20: expansion_gpio[0:10]
```

This connects ten pins to ten net elements in order. Physical ranges support
ordinary integer labels only, not alphanumeric or zero-padded labels. They can
apply shared attributes in definitions and instance pin metadata:

```text
pins:
    1..20:
        unused: unused.explicit
```

Normal shape, scalar-sharing, and overlap rules apply to ranges and slices.

## Electrical checking

Initial priorities are unconnected nets, conflicting power sources, and multiple
incompatible drivers. Pin roles describe endpoint behavior; net types express
permitted or required connection behavior. Keep both `role.open_drain` and
`nettype.open_drain`, with explicit compatibility rules.

Initial categories cover digital, power, ground, open-drain, bidirectional,
passive/analog, and explicitly unchecked connections. Simple digital nets default
to requiring exactly one output driver. Ground uses `nettype.ground`, not
`nettype.passive`. Additional categories can be added as concrete needs emerge.

Component definitions describe permitted roles. Instance role annotations are
optional for ordinary pins and may select among permitted roles without overriding
constraints. Without an instance annotation, checking uses definition metadata
and reports uncertainty where necessary.

### Implemented ERC coverage

`check`, `compile`, and `export-kicad` run the initial checks on flattened physical
endpoints. `check` returns 1 for electrical errors. `compile` and `export-kicad`
still return 0 and publish artifacts when only electrical errors are present;
structural and export errors continue to block publication. Reports list their
implemented and pending checks; `erc_performed: true` does not imply full v1
coverage or a validated hardware design.

Implemented checks use the `erc.` diagnostic prefix:

- `do_not_connect`, `required_connection`, and `unused_pin` enforce physical pin
  policies, including explicit disconnection and lone-pin nets.
- `unconnected_net` reports declared nets with no physical endpoints, except
  placeholder declarations. Placeholder presence itself is silent.
- `multiple_drivers` reports multiple known digital, analog, or power output
  drivers. `power_ground_conflict` reports a power output tied to ground.
- `missing_driver` requires a digital output on a digital net or a power output
  on a power net. Passive components do not propagate drivers between nets.
- `incomplete_roles` warns when missing/selectable/unchecked endpoint roles or
  bidirectional behavior prevent a definite single-driver check. Known output
  conflicts are still reported.
- `unchecked_net` reports deliberately skipped driver/current checks. Pin
  connection policies still apply.
- `current_overload` sums known per-pin power-input loads against one annotated
  power-output capacity. `incomplete_budget` warns about missing roles, capacities,
  or loads when any current metadata is present. Known overloads are errors even
  when additional loads are unknown; multiple source capacities are not summed.

External connector roles replace the physical contact's role for driver and load
accounting. Hierarchical ports never duplicate physical drivers or loads.
General role/net-type compatibility, public port contracts, ERC configurations,
bias/tristate models and aggregate budgets remain pending. Their missing
metadata forms are rejected rather than silently ignored.

### Required and unused pins

| Policy | Behavior |
|---|---|
| Always allowed | May remain unconnected without an instance annotation |
| `unused.never` | Must connect; instances and waivers cannot override this |
| `unused.explicit` (default) | Must connect or be marked `no_connect` |

`role.do_not_connect` means a pin must remain disconnected. Omission or an
explicit `no_connect` binding is allowed without an unused-pin diagnostic; assigning
any net is an ERC error, even if that net has no other endpoints. The existing
`no_connect` binding records an intentional disconnection; it is not a role.
KiCad library conversion maps its `no_connect` electrical type to this role.
Combining this role with `unused.never` produces a conflicting-constraints ERC
error; neither constraint silently overrides the other.

Generic connector definitions use the explicit policy. Optional pins can be
omitted; required pins reject `no_connect`.

A required pin needs at least one other physical or explicitly modeled external
endpoint on its net. Merely assigning a declared net is insufficient. Driver
checks separately determine electrical validity. A hierarchical port does not
add a physical endpoint.

### Hierarchy and placeholders

ERC counts physical endpoints across hierarchy once. Port roles are interface
constraints, not additional drivers or loads. An MCU output passing through
several enclosing ports still counts as one driver. The same principle applies
to explicitly modeled external endpoints and current loads.

Each block port must lead internally to a physical pin, an explicit external
endpoint, or a placeholder when expanded. This structural requirement is separate
from whether a caller may leave an optional port unconnected.

A net or port may carry placeholder metadata. Placeholders:

- Satisfy the structural port check without warnings merely for their presence.
- Create no footprint and imply no driver, power source, or load.
- Do not satisfy a physical pin's mandatory connection requirement by themselves.
- Do not suppress, waive, or downgrade other ERC diagnostics to "incomplete."

Modeling electrical behavior requires explicit endpoint roles. Diagnostics should
explain a placeholder's presence when useful. The reference draft is expected to
produce electrical diagnostics until its missing implementations or endpoint
models are supplied; those errors still permit export.

### Connectors and ERC configurations

Generic connector definitions do not prescribe attached equipment's behavior.
Instances assign it explicitly under `metadata.pins` using `external_role`.
For example, `external_role: role.digital_input` means the attached equipment
receives the signal. Ordinary components use `role`. An external endpoint does
not also make the physical connector contact a second active driver or load.

V1 supports optional named ERC configurations for alternative uses of one board.
Connector instances may declare multiple permitted roles; configurations select
the assumptions for checking. They never change components, connectivity, or the
exported board. Missing selections produce insufficient-information diagnostics
where checks depend on them. A configuration's name alone cannot waive a conflict.

Blocks expose named ERC choices with permitted alternatives through their public
interfaces. Board configurations select those choices; the block maps them to
internal endpoint-role assumptions. This lets a USB block expose `power_mode`
without allowing access to its private connector or nets.

### Current budgets

Electrical quantities use numeric metadata with units in field names, such as
`max_source_current_mA` and `max_load_current_mA`. Each recognized field defines
its unit. `capacitance_pF` illustrates the same convention, but automatic
capacitance checking is deferred.

- Sources may declare capacity; loads may declare maximum consumption.
- Sum explicit loads. Known overloads are errors; missing load annotations make
  a budget incomplete and produce a warning, not an assumed zero load.
- A component may declare one load for a group of supply pins rather than divide
  its consumption artificially across them.
- A block may explicitly declare an aggregate supply load. Its parent counts
  that aggregate once instead of the represented internal loads. Internal checks
  remain separate; aggregate and constituent loads are never added together.
- Do not calculate consumption from part values or propagate a regulator's
  output load to its input automatically.

### Passive components and waivers

V1 checks each physical net independently. It does not automatically follow
signal or power behavior through components such as series resistors.

Users may waive a specific check on a specific net with a required reason. Other
checks remain active. Waivers neither merge nets nor establish verified voltage,
timing, or current behavior across a component. They cannot override mandatory
pin connections. Narrow component models may be considered later.

Waivers are implemented as a mapping from check names to nonempty literal reasons:

```nosch
net signal: nettype.digital
    waivers:
        missing_driver: "Driver is across an explicitly modeled series resistor."
```

Only the outermost declaration of a flattened net may waive its checks. This is
normally the caller's `net` declaration; an unbound outermost port can also be the
net's declaration. A waiver on an inner port produces `erc.waiver_scope` and does
not suppress the check, even if the caller also supplies a waiver. Private nets
can have local waivers, independently for each expanded instance. Waivers on
bundle/array declarations apply to their expanded scalar nets under the same rule.

Only implemented net checks are waivable. Pin connection requirements,
`do_not_connect`, and waiver scope errors cannot be waived. Unknown check names,
empty reasons, and non-string reasons are source errors. Text and JSON reports
retain each waiver's reason, location, net, and original waived diagnostics;
unused waivers remain listed with zero matching diagnostics. Other checks remain
active, and waived diagnostics do not contribute to error/warning counts.

Checks have stable readable identifiers, such as `missing_driver`. Waivers refer
to those names, not message wording; a separate numeric code is not required.

## Library conversion and CAD integration

### Conversion and footprints

A separate converter reads KiCad libraries and emits NoScheme component
definitions, preserving relevant electrical metadata. Compilation consumes
NoScheme definitions only; it does not implicitly load or convert KiCad parts.

Conversion also inspects associated footprints and retains their pad identifiers
with converted library data. Later checks can compare component pins with known
pads without reading KiCad libraries during compilation. This does not introduce
a logical-pin/package mapping abstraction.

Distinguish an unspecified footprint from an unknown footprint name or a known
pin/pad mismatch. Only the unspecified-footprint warning behavior is settled;
other validation details are listed below.

### Names and layout attributes

Name conversion belongs to a pluggable CAD adapter. Preserve source names in the
compiled circuit; each adapter translates to names its tool accepts and provides
a reverse mapping. Retain mapping information for lossy conversions rather than
guess the original names. Detect exported-name collisions and handle them
explicitly; never silently merge distinct nets.

Start with a KiCad adapter using conventions established through investigation.
Reverse name mapping does not itself mean synchronizing CAD circuit edits back
to NoScheme source.

Net-class membership and differential-pair relationships are backend-independent:
a net references a named class, and a bundle can identify two fields as a pair.
The adapter translates these into tool settings and naming conventions. Pair
membership is explicit, not inferred solely from source-name suffixes.

In v1, NoScheme assigns class membership while KiCad owns widths, clearances,
and other class settings. Updates must preserve those settings.

### Identity and stable reference designators

Unchanged source elements must not change arbitrarily in generated output.
Reference designators are assigned automatically, with explicit per-instance
overrides. The default identity is a hierarchical path, such as
`indicators[0].led`. An optional explicit identity string preserves a link across
renames or moves.

The implemented identity annotation is `metadata.identity`. On an instance
array, the explicit string receives each element's index suffix. On a block,
it anchors descendant identities across source renames. `metadata.ref` supplies
an explicit physical reference. The KiCad adapter initially accepts references
matching `[A-Za-z_#][A-Za-z0-9_#]*` and rejects case-insensitive collisions.

A version-controlled project mapping file stores identities and reference
assignments. It is both compiler output and input to later runs:

1. Create it on the first successful run; read it on subsequent runs.
2. Match instances by identity and preserve existing assignments.
3. Assign unused numbers to new instances; reject reference collisions.
4. Retain deleted entries as reservations until an explicit cleanup reclaims them.
5. Write atomically. Failed compilation leaves the previous mapping intact.

Non-blocking ERC diagnostics do not make compilation fail. The initial state
commit behavior and CAD identity mechanism are described below.

The first exporter uses `noscheme.state.json` next to `noscheme.toml`, with a
versioned JSON schema. UUID paths and reference histories are stored by physical
identity. Export stages its files and commits the atomic state replacement last;
ordinary I/O failures roll back earlier replacements. Crash recovery across all
output files and an explicit reservation-cleanup command remain future work.

## Reports and diagnostics

V1 produces `.txt` and `.json` forms of:

- Net tables describing compiled connectivity.
- Component/connectivity change reports against the previous recorded design.
- ERC reports.

Diagnostics carry stable check identifiers, severity, messages, filenames, and
line numbers. Preserve source spans through compilation for columns and related
declarations or bindings. Include affected instance paths and ERC configurations
where relevant, so reused blocks and arrays remain traceable.

## Unresolved design and implementation work

These are remaining details, not permission to reverse the decisions above.

| Area | Work still needed |
|---|---|
| Grammar and parser | Lark LALR/contextual passed the feasibility experiments, including indentation inside instance parentheses and source spans. Build the typed AST and finalize complete syntax and reserved/contextual words. |
| Names and imports | Project loading implements the documented TOML keys, dotted module paths, explicit imports, and reserved builtin module. Define future version compatibility and finish lexical scope resolution. A similarity lint for distinct pin identifiers is optional, not required behavior. |
| Expressions | Tagged evaluation, finite binary64 doubles, integer resource limits, precedence, restricted formatting, helper inventory, and parameter binding are implemented above. Integrate values and integer-context warnings into full connectivity elaboration. |
| Arrays | Set rank and dimension limits, empty-array behavior, and rules for omitted slice bounds, strides, negative indices, and descending ranges. |
| Metadata | Initial fields, scopes, literal values, role lists, and `ref_prefix` are implemented as documented above. Extend the schema for advanced ERC configurations, budgets, waivers, and explicit differential pairs. |
| ERC | Complete role/net-type compatibility and driver tables, biasing and tristate rules, unchecked-net diagnostics, stable check names, and waiver syntax. |
| ERC configurations | Define public choices, permitted alternatives, internal role binding, board selection, and insufficient-information severity. |
| Current accounting | Define aggregate annotations and represented loads, grouping across supply pins and rails, and treatment of budget boundaries between components. |
| Footprints | Define converted footprint records, handling of footprints selected after conversion, mismatch severity, and validation of electrical versus mechanical pads. |
| KiCad handoff | Version E netlists and UUID-path matching passed the KiCad 10.0.6 import/update experiments, including layout retention and CAD-side reannotation. Implement the production adapter with footprint validation and explicit partial-export reporting; see experiment findings for limitations. |
| Names and layout export | Define adapter naming/escaping, collision handling, reverse-map storage, and syntax/translation for classes and differential pairs while preserving KiCad-owned settings. |
| Persistent state | `metadata.identity`, `metadata.ref`, versioned `noscheme.state.json`, UUID-path identity, reference reservations, ordinary-failure rollback, and previous-export change reports are implemented. Add explicit reservation cleanup and consider multi-file crash recovery. |
| Reports and tooling | Specify report schemas/text layouts and validation tests; choose highlighting, LSP, and formatting implementations according to the agreed staging. |
| Reference hardware | Verify MCU capabilities and pin assignments, select actual parts and footprints, complete support circuitry and external power assumptions, and replace placeholders. |

Reverse synchronization of electrical edits from CAD has not been specified.
Future generation APIs remain outside the current implementation scope.

## Research and review references

Prior ERC research informed the decision to keep v1 checks local to each net:

- [KiCad 10 ERC documentation](https://docs.kicad.org/10.0/en/eeschema/eeschema.html#electrical-rules-checking): passive pins satisfy the ordinary input-driven check; power inputs require power outputs, with power flags providing explicit assertions.
- [Altium design validation](https://www.altium.com/documentation/altium-designer/schematic/design-validation): series passives can cause missing-driver diagnostics; targeted No ERC directives support intentional exceptions.

Atopile and Flux.ai are prior-art references for further investigation. We may
borrow concepts, but will not use code wholesale.

The adopted review decisions are integrated by topic above. For cross-reference:

| Review findings | Resolution location |
|---|---|
| B1, B2, B9 | Syntax, parameters, identifiers |
| B3, B4, B10, B11 | Scope, connectivity, arrays, bundles |
| B5, B7, M5, M12 | Physical identifiers, aliases, wrappers, specialization |
| B8, H2–H6, H11, M16–M18 | Electrical checking, formatting, numbers |
| H8–H10, H12, §11 | Footprints, CAD adapters, layout attributes, persistent identity |
| M1, M3, M4, M6 | Ranges, globals, ordinary endpoint bindings |
| M8–M10, M14, M15 | Project configuration, reports, explicit imports, custom DNP, comments |

The role and metadata tables (H1/H7), complete executable examples (M2/M7),
backend proof (B6/M11), and implementation/testing recommendations still require
work. The review's remaining process and scope proposals are not automatically
adopted. This consolidated document remains the single design document for now.
