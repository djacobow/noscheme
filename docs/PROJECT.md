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
or implementation specification. [LRM.md](LRM.md) describes the implemented
language and compiler behavior. The [reference-board draft](../examples/stm32h523/stm32h523.nosch)
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
ERC now checks physical pin connections, role/net-type compatibility, public-port
contracts, drivers, and per-net current budgets,
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
quantity parameter types, and full assembly variants. Explicit metadata-based
pull-strength and capacitance checks are implemented; automatic extraction from
part values, routing, or datasheets remains deferred. Parameter use is limited as described below.

The design should leave room for a future generation API, potentially Python,
that creates the same nets and instances as the native language. Its execution
and extension model has not been chosen. A separate logical-pin/package layer
and a dedicated pin-group feature are not needed in v1; wrappers can provide
convenient interfaces over explicit physical pins.

Basic Vim/Neovim syntax highlighting is implemented in `editors/vim`; [EDITOR_SUPPORT.md](EDITOR_SUPPORT.md)
describes installation.
LSP diagnostics and completion follow afterward. Canonical formatting/linter
support follows once the syntax stabilizes.

## Reference board

The worked example is an STM32H523 board with:

| Function | Requirement |
|---|---|
| MCU | STM32H523VCT6, 100-pin LQFP |
| Debug | Standard 10-pin Cortex SWD connector |
| USB | USB-C connector carrying data and supplying 5 V |
| Regulation | TLV1117LV33DCYR, 5 V to 3.3 V, SOT-223; ceramic input/output capacitors |
| Indicators | A few LEDs; the draft uses three |
| Storage | microSD socket using SPI, with card detection if provided |
| Display | 1x4, 0.1-inch header carrying power, ground, SCL, SDA for an SSD1306-style I2C OLED |
| Expansion | 2x10, 0.1-inch header with two grounds, 5 V, 3.3 V, and sixteen GPIO connections, including I2C- and SPI-capable pins |
| UARTs | Two independent UART ports on three-pin JST headers, ordered GND, MCU TX, MCU RX; instantiate the headers as a two-element array |
| Buttons | Three TS18-compatible SMT buttons: PC4/PC5 with 10 kOhm pull-ups, plus NRST to ground |
| BOOT0 | 2-pin, 0.1-inch header: jumper to 3.3 V, pull-down resistor for a default low level; verify MCU boot configuration |
| Decoupling | Local 100 nF capacitors at all MCU supply pins, bulk/domain capacitors, two 2.2 uF VCAP capacitors, and local microSD/display capacitors |
| Crystal | 8 MHz, HC49/US SMT footprint, CL18pF target, two provisional 27 pF C0G load capacitors |
| Passives | 0603 packages |

Prefer expansion I2C and SPI peripherals independent of the OLED and microSD
buses where MCU pins permit. The expansion 5 V pin is on the USB-C 5 V net; its
3.3 V pin is on the LDO output net. External equipment may draw from or supply
these rails. ERC configurations model the alternative uses without changing
those physical connections.

The UART draft uses `uart_tx[2]` and `uart_rx[2]` nets with
`inst uart_headers[2]: UartHeaderJST3(...)`. Ground is shared across both headers;
each TX/RX element connects to the corresponding header. The selected connector
is JST XH S3B-XH-SM4-TB, 2.50 mm pitch, side-entry SMT. Its mounting tabs
are electrically unconnected. See the [JST XH datasheet](https://www.jst-mfg.com/product/pdf/eng/eXH.pdf).

### Physical proof-of-concept implementation

The example is now 58 physical parts and 52 nets with no placeholders or missing
footprints. Its project file is `examples/stm32h523/noscheme.toml`; the main source
remains `examples/stm32h523/stm32h523.nosch`. The imported `parts.nosch` is the
explicit conversion of KiCad's `STM32H523VCTx` symbol. `STM32H523Board` specializes
its pin roles for this board; unused physical GPIOs explicitly bind `no_connect`.
The conversion report and all 14 footprint snapshots are retained in the project.
Repeated exports retain reference assignments and UUIDs through the project state.

The MCU's USB FS support and package pinout were checked against
[ST DS14540](https://www.st.com/resource/en/datasheet/stm32h523vc.pdf), using the
installed KiCad symbol as the machine-readable pin map. Allocation:

| Function | MCU pins (LQFP100 pad numbers) |
|---|---|
| USB D-/D+ | PA11 (70), PA12 (71) |
| SWDIO / SWCLK / SWO / reset | PA13 (72), PA14 (76), PB3 (89), NRST (14) |
| microSD SPI1: CS / SCK / MISO / MOSI | PA4 (29), PA5 (30), PA6 (31), PA7 (32) |
| microSD detect | PC3 (18) |
| OLED I2C1 SCL / SDA | PB6 (92), PB7 (93) |
| Expansion I2C3 SCL / SDA | PA8 (67), PC9 (66) |
| Expansion SPI4: CS / SCK / MISO / MOSI | PE11 (42), PE12 (43), PE13 (44), PE14 (45) |
| UART header 0, USART2 TX / RX | PA2 (25), PA3 (26) |
| UART header 1, USART3 TX / RX | PD8 (55), PD9 (56) |
| Expansion GPIOs, in header order | PE2, PE3, PE4, PE5, PE6, PE7, PE8, PE9, PE10, PE15 |
| LEDs / buttons | PC0–PC2 / PC4–PC5 |
| BOOT0 / HSE crystal | BOOT0 (94) / PH0 (12), PH1 (13) |

The supply connections follow the LDO-enabled arrangement in
[ST AN5711](https://www.st.com/resource/en/application_note/an5711-getting-started-with-stm32h5-mcu-hardware-development-stmicroelectronics.pdf).
VDD, VDDA, VREF+, VDDUSB, and VBAT connect to 3.3 V; VSSA/VREF- connect to ground.
This package has no separate VDDIO2 pad. VCAP pads 48/98 share an isolated core
rail, with one 2.2 uF capacitor at each pad. A scoped `multiple_drivers` waiver
records that both pads belong to the same internal regulator. Never attach this
rail to 3.3 V. Firmware must leave VREFBUF disabled with VREF+ tied to VDDA.
Capacitor instance metadata records intended placement near the relevant pads.

The regulator is specifically the ceramic-stable
[TI TLV1117LV33](https://www.ti.com/lit/gpn/TLV1117LV), with 1 uF input and 4.7 uF
output capacitors. No reverse-feed isolation, power mux, or series jumper is
included: this board is an operator-controlled flow proof of concept. The header
and regulator output remain on the same 3.3 V net, as requested. USB-powered and
header-5-V-powered ERC configurations are implemented; externally supplied 3.3 V
with an unpowered 5 V rail is not yet modeled. These assumptions do not add
physical protection. No nominal LDO rating is treated as a verified board budget.

USB uses the HRO TYPE-C-31-M-12 footprint, two 5.1 kOhm CC pull-downs, tied duplicate
USB2 contacts, and a grounded shield. The microSD socket uses the Hirose
DM3AT-SF-PEJM5 footprint, explicit SPI roles and detect contacts, pull-ups, and
local 100 nF/4.7 uF capacitors. The OLED header has the same local capacitor pair.
Both I2C buses have 4.7 kOhm pull-ups; all these resistors/capacitors use 0603.
The Cortex connector uses a vertical SMT 2x5 1.27 mm footprint with the standard
SWD pinout. OLED, expansion, and BOOT0 headers also use vertical SMT footprints
at 2.54 mm pitch. Prefer SMT throughout; the USB connector retains its shield
anchors and locating holes.

All three buttons use `Button_Switch_SMD:SW_SPST_TL3342`, matching the
[TS18 land pattern](https://www.sameskydevices.com/product/resource/ts18.pdf):
four 1.7 x 1.0 mm pads at x = +/-3.15 mm, y = +/-1.9 mm. Each row is internally
common and maps to one logical switch pin. The reset button shorts NRST to ground,
using the MCU internal pull-up and the existing 100 nF capacitor.

The HC49/US SMT crystal target is 8 MHz with 18 pF load; its two 27 pF load capacitors
are provisional and need adjustment for the selected crystal and board stray
capacitance. This board has not been assembled, routed, or hardware-qualified.
The user confirmed that the production netlist populated the KiCad board correctly.
Placement and routing are deferred; layout-preserving updates on this reference
board still need a manual check.

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
schema, not the complete v1 ERC vocabulary. Unknown fields are errors. Pair
declarations are not yet implemented and must not be silently ignored.

| Scope | Fields |
|---|---|
| Physical component definition | `footprint`, `ref_prefix` (strings), `value` (deferred expression), `current_loads`, `custom` |
| Block definition | `erc_choices` (named string alternatives), `custom` |
| Board definition | `erc_choices`, `configurations` (named choice assignments), `custom` |
| Bundle definition | `custom` |
| Physical pin definition | `aliases` (name/string list), `role` (qualified role or list), `unused`, `tristate_capable` (boolean), nonnegative literal `max_source_current_mA` and `max_load_current_mA`, `custom` |
| Port or bundle field | `placeholder` (boolean), `role`, `unused`, `waivers`, `aggregate_load`, `custom` |
| Net | `placeholder` (boolean), `net_class` (string), `waivers`, `custom` |
| Physical instance | `footprint`, `ref`, `identity` (strings), `value` (deferred expression), `custom` |
| Block instance | `identity`, `erc_choices` (public choice bindings), `custom` |
| Instance pin/port annotation | `role` (one permitted role or choice/cases mapping), `external_role` (role, list, or choice/cases mapping), `tristate_capable` (boolean), `custom` |

The electrical-check registry additionally supplies pin/instance-pin `bias`,
`pull_resistance_ohm`, and `capacitance_pF`; net/port resistance limits and
`max_capacitance_pF`; and net `wiring_capacitance_pF`. See the extensible ERC
section for activation and contribution semantics. Project modules can register
additional fields without altering these core schemas or the grammar.

Roles currently include `passive`, `digital_input`, `digital_output`,
`digital_bidirectional`, `analog_input`, `analog_output`, `analog_bidirectional`,
`power_input`, `power_output`, `ground`, `open_drain`, `open_source`, `unchecked`, and `do_not_connect`.
These require the explicit `role` import and qualification. `unused.always`,
`unused.explicit`, and `unused.never` require the explicit `unused` import.
An instance role must belong to the definition's permitted roles when specified;
instance annotations cannot change `unused` or aliases. Current metadata is
retained with source locations; the initial ERC checks below use it.

`custom` contains indented mappings with literal strings, finite numbers,
booleans, lists, and nested mappings. Its values have no implicit electrical
meaning. Specialization merges mappings recursively and replaces scalar/list
values, retaining the source and lexical module of each inherited expression.

### Language server implementation

The first `noscheme-lsp` server is available over LSP stdio. It reuses the compiler
with open-document source overlays for diagnostics, definition navigation, and
basic symbol hover, and completion for visible component/block names, public
connection pins/ports, and imported role/nettype values. Completion recovers
incomplete statements in a private snapshot, including inherited pin aliases.
The server never writes build artifacts or export state. [EDITOR_SUPPORT.md](EDITOR_SUPPORT.md)
contains project discovery rules, Vim setup, and the currently supported
navigation cases. Unsaved dependency edits recheck open boards. Syntax errors
may limit semantic navigation until corrected. Rename, bundle-member
navigation, formatting, and incremental compilation remain future work.

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
- `current_overload` sums known per-pin and grouped power-input loads against one annotated
  power-output capacity. `incomplete_budget` warns about missing roles, capacities,
  or loads when any current metadata is present. Known overloads are errors even
  when additional loads are unknown; multiple source capacities are not summed.

External connector roles replace the physical contact's role for driver and load
accounting. Hierarchical ports never duplicate physical drivers or loads.
Explicit bias and opt-in resistance/capacitance budgets are implemented.
### Tri-state capability checks

`nettype.tristate` accepts passive attachments and digital inputs without further
metadata. Every output-capable endpoint (`digital_output`,
`digital_bidirectional`, `open_drain`, or `open_source`) must explicitly declare
`tristate_capable: true`. Missing or false capability produces
`erc.tristate_capability`; unknown or unselected roles produce the usual
`incomplete_roles` warning. Analog, power, and ground roles remain incompatible,
even with the flag. Multiple capable drivers are allowed.

The boolean belongs to pin definitions or instance-pin metadata, including
connector pins with `external_role`. Metadata specialization and instance
annotations can override it. Public `nettype.tristate` ports constrain the actual
connected endpoints; a port does not invent a driver or confer capability on its
internals. All applicable net constraints remain in force: an ordinary `digital`
declaration on the same flattened net retains its single-driver requirements.
An outermost `unchecked` net still does not disable inner type/capability checks.

This is a capability check. There is no enable-signal model, mutual-exclusion
proof, or requirement for a currently active driver. An all-input tri-state net
therefore passes this check. Existing connection policies still apply. The
`tristate_capability` diagnostic supports the normal outermost-net waiver with
an explicit reason. Named configurations use the selected endpoint roles.

```nosch
component BusDriver:
    pins:
        data:
            role: role.digital_output
            tristate_capable: true
```

See `examples/tristate` for a shared-bus example. It models electrical behavior
without assigning footprints. KiCad `tri_state` pins convert to
`role.digital_output` with `tristate_capable: true`; repeated representations
that also specify a non-tristating output do not acquire the capability.
Generic KiCad bidirectional pins do not imply this flag.

### Role/net-type compatibility

Each scalar net declaration retains its constraints after hierarchy is flattened.
A physical or explicitly modeled external endpoint must satisfy all applicable
checked declarations, including public-port types. The initial compatibility table
allows these roles (all rows also permit `role.passive`):

| Net type | Other permitted endpoint roles |
|---|---|
| `passive` | Any ordinary digital, analog, power, or ground role |
| `digital` | `digital_input`, `digital_output`, `digital_bidirectional`, `open_drain` |
| `bidirectional` | `digital_input`, `digital_output`, `digital_bidirectional`, `open_drain`, `open_source` |
| `tristate` | `digital_input`; digital output-capable roles with `tristate_capable: true` |
| `open_drain` | `digital_input`, `open_drain` |
| `analog` | `analog_input`, `analog_output`, `analog_bidirectional` |
| `power` | `power_input`, `power_output`, `digital_input`, `analog_input` |
| `ground` | `ground`, `power_input`, `digital_input`, `analog_input` |
| `unchecked` | No domain restriction from this declaration |

`do_not_connect` retains its separate mandatory disconnection rule. Missing or
unchecked roles produce `incomplete_roles` where a check cannot be established.
For unselected role lists, all declarations must admit at least one common role;
a definite mismatch produces `incompatible_role`, while a choice requiring
selection produces an uncertainty warning. The checker never chooses a role.
These checks do not establish voltage tolerance or a valid logic threshold.

Digital/power driver requirements remain separate from compatibility. Multiple
open-drain endpoints are allowed together, but an active digital/analog/power
output sharing a net with an open-drain endpoint produces `multiple_drivers`.
Only an outermost `nettype.unchecked` declaration disables the flattened net's
driver/current checks. Inner unchecked declarations do not suppress caller checks;
other declared type and public-port contracts still apply even when the outermost
net is unchecked.

A public port explicitly declaring `role.digital_input` may connect directly to
an outermost `nettype.power` or `nettype.ground` net as a fixed logic level.
Its digital/bidirectional/open-drain type then adds no signal-driver requirement
or restriction on the rail's other endpoints. Its internal input-role contract
still applies, as do the rail's type, power-driver, and current checks. Every
forwarding public port must explicitly declare the input role for this exception.
An unselected role list, an untyped/passive caller net, or an output/bidirectional
role does not qualify. This permits connectivity, not a claim of voltage tolerance.

### Public-port contracts

A public port's `role` describes behavior implemented inside its block. The port
adds no driver or load. Checking uses physical endpoints and explicitly modeled
external endpoints within that instance's scope; caller endpoints cannot prove
an internal output exists. Checks run separately for every array/bundle leaf.

Input contracts admit passive contacts and inputs of the same domain. Output
contracts also admit same-domain input feedback/load endpoints. Bidirectional
contracts admit the corresponding input/output/bidirectional roles (and open-drain
for digital). Open-drain contracts admit passive contacts, digital inputs, and
open-drain endpoints. Ground contracts admit passive contacts, ground, and input
roles. Passive/unchecked contracts impose no extra role restriction.

The declared behavior requires a directly connected implementing endpoint:
an output contract needs a physical or modeled external output; an input needs
an input; an open-drain contract needs an open-drain endpoint. Bidirectional
behavior may be represented by a bidirectional endpoint or by separate input and
output endpoints. Contradictions or missing evidence produce `port_role`;
unknown or unselected roles produce `incomplete_port_role`. There is no inference
through resistors or other components. Top-level net waivers may waive these role
checks, with the usual explicit reason and retained diagnostics.

A child port's default `unused.explicit` policy requires a caller binding or
`no_connect`; `unused.never` requires a real connection and cannot be waived.
Connected required ports need an endpoint outside the block; internal endpoints
and placeholder-only caller nets do not satisfy that boundary obligation.
`unused.always` permits omission. Board ports have no caller, so only their
implementation contracts apply. `role.do_not_connect` on a port forbids binding
it to a caller net. Violations use `unused_port`, `required_port_connection`, or
`do_not_connect` and cannot be waived.

An explicit `external_role` annotation on a block port represents an external
endpoint once. Ordinary enclosing ports do not copy it into additional endpoints.
The modeled endpoint participates in ERC but creates no physical CAD pin. On a
physical connector pin, `external_role` replaces the contact's role as before.

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

### Configuration syntax and checking

Named configurations are implemented with metadata; no new grammar keywords or
executable conditionals are added. Boards and blocks declare public string choices:

```nosch
metadata:
    erc_choices:
        power_mode: ["source", "load", "absent"]
```

An instance inside that definition can bind its pin role to the local choice:

```nosch
metadata:
    pins:
        1:
            external_role:
                choice: "power_mode"
                cases:
                    source: role.power_output
                    load: role.power_input
                    absent: role.passive
```

This syntax also works for `role`, provided every possible role is permitted by
the component/port definition. Every choice alternative must have exactly one
case; the compiler never picks a fallback. Choice and configuration names must
be identifiers; alternatives are distinct nonempty literal strings. An absent
external source can be modeled as a passive contact, but this does not remove a
part, disconnect its pins, change an unused-pin policy, or model an inactive
regulator automatically.

A parent selects only its child's declared public choices under instance
`metadata.erc_choices`. A binding can be a fixed alternative string or a
`choice`/`cases` mapping from one of the parent's own choices to child alternatives:

```nosch
metadata:
    erc_choices:
        power_mode:
            choice: "power_source"
            cases:
                usb: "source"
                header: "absent"
```

Choice references are lexical local names, never instance paths. Nested blocks
must forward choices explicitly through each public interface. All elements of
an instance array share that declaration's bindings, but their local choices and
physical endpoints remain distinct. Choices do not participate in numeric/string
parameter expressions, dimensions, values, footprints, or connectivity.

Board configurations assign literal alternatives to the board's own choices:

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

`check`, `compile`, and `export-kicad` check **all declared configurations by
default**. Repeat `--erc-configuration NAME` to select a subset; an unknown name
is an error before publication. A board without named configurations gets one
unconfigured check. Choices have no implicit defaults. Omitted selections remain
unknown and generate insufficient-information diagnostics where checks depend on
them; fixed roles or identical mapped roles need no speculative warning.

Each configuration gets an isolated set of effective roles on the same flattened
circuit. The original circuit retains every declared possibility. The checker
never changes physical connectivity, values, identities, references, or CAD
output as a side effect of selecting a configuration. Invalid choice names,
values, incomplete mappings, and illegal role overrides are blocking source errors.
Electrical errors in any configuration still permit compile/export; `check`
returns 1 if any selected configuration has errors.

Text/JSON reports include per-configuration counts, effective selections by
instance scope, configuration names on diagnostics, and configuration names on
waived diagnostics. Overall counts sum the selected configurations. The complete
[power-mode example](../examples/power_modes/main.nosch) demonstrates two sources
behind private block internals; both configurations produce the same netlist.

### Current budgets

Electrical quantities use numeric metadata with units in field names, such as
`max_source_current_mA` and `max_load_current_mA`. Each recognized field defines
its unit. Explicit capacitance budgets are implemented under the extensible ERC
requirements below; capacitance is not automatically extracted.

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

#### Grouped component loads (implemented)

Component metadata can name a load shared by several physical supply pins:

```nosch
metadata:
    current_loads:
        core:
            pins: [vdd, "2", "3", "4"]
            max_load_current_mA: 100
```

Members are pin aliases or quoted physical pin numbers. Resolve aliases to pads;
reject empty groups, repeated pads (including alias/number duplicates), unknown
pins, and membership in multiple groups. Group members must permit
`role.power_input` and cannot also declare individual `max_load_current_mA`.
Amounts are finite, nonnegative numeric literals in mA. Metadata inheritance
merges named groups, so a subclass can override just an amount or the member list.

Every group member must connect to the **same flattened electrical net**. A
split, omitted, or `no_connect` member produces `erc.current_load_group`, with
locations for its member pins. Separate supplies require separate groups and
explicit budgets; no current is apportioned between rails automatically.

A valid group counts once per physical component instance, including each array
element. It replaces its members' individual demands when checking the source
capacity. Each selected ERC configuration must give every member the effective
role `power_input`. An unknown/unselected role leaves the group unverified with
a warning; a definite incompatible role is an error. Invalid groups do not hide
missing individual load information. Group diagnostics cannot be waived, and
are checked even on unchecked nets; unchecked nets still skip overload checks.
These electrical diagnostics remain nonblocking for compile/export.

#### Block aggregate loads (implemented)

A block can declare an aggregate on a supply port:

```nosch
ports:
    power: nettype.power
        role: role.power_input
        aggregate_load:
            max_load_current_mA: 100
```

The aggregate represents all descendant power-input loads on that same flattened
net. It counts once in the parent's budget, replacing those individual/grouped
loads. No behavior propagates through resistors or regulators; loads on other
nets retain their independent checks. Each scalar port in an array has its own
annotation, and each block instance has an independent budget.

Check nested boundaries from the inside outward. Known internal loads exceeding
the declared aggregate produce `aggregate_current_overload`; missing internal
load annotations or unresolved endpoint roles produce `incomplete_aggregate_load`.
For example, 120 mA internally against a declared 100 mA is an error, while the
parent still counts the declared 100 mA. A nested block contributes its declared
aggregate to its enclosing block, and retains its own internal diagnostics.

An aggregate requires an explicit effective `role.power_input` port. Reject
multiple aggregate ports belonging to the same block on the same net, an
aggregate with a modeled external endpoint on that port, or a same-net power
source inside the boundary (`aggregate_load_boundary`). Board ports cannot
create parent-budget boundaries. Invalid boundaries leave internal loads visible
to the parent. Supply groups connected to distinct nets use distinct ports and
explicit budgets. Block metadata inheritance can override the declared amount.

These checks run separately for every selected ERC configuration and even when
the outermost net is unchecked. Aggregate diagnostics cannot be waived; source
capacity checks retain their normal waiver/unchecked behavior. Reports include
port and internal endpoint locations. Compile/export remain available with
electrical errors, and budget annotations never change physical connectivity.

### Extensible electrical checks (implemented)

Checks apply where metadata explicitly requests them. Merely enabling a Python
module does not opt every net into its electrical budgets. The built-in budgets
use the same registry as project extensions and require no new grammar forms.

#### Bias and pull resistance

Import `bias` from `noscheme`. Physical pin definitions and instance pin
annotations may declare `bias: bias.pull_up`, `bias.pull_down`, or `bias.none`,
and positive numeric `pull_resistance_ohm`. Bias is an explicit assertion about
the signal-side pin. It does not create an active output driver or verify the
other terminal, supply voltage, or displayed part value. A digital input net may
have a default level established by one declared bias direction; open-drain
outputs require pull-up bias for this purpose, and open-source outputs require
pull-down bias. Bias cannot implement a public output-port contract or power a rail.
`role.open_source` and `nettype.open_source` mirror the open-drain roles/types.

Opt a scalar net (or port constraint) into resistance checking with any of:

- `min_pull_up_resistance_ohm`, `max_pull_up_resistance_ohm`
- `min_pull_down_resistance_ohm`, `max_pull_down_resistance_ohm`

Each limit is a positive numeric literal. Combine declared contributors of the
matching direction using `1/R = sum(1/R_i)`. Count each physical endpoint once,
regardless of hierarchy, and each arrayed physical instance independently.
Limits on multiple public boundaries are all enforced. Annotated pulls without
resistance, or a requested budget with no declared matching pulls, produce an
incomplete warning. Known pulls below the minimum still establish a violation;
a maximum-resistance violation cannot be established when additional pulls have
unknown resistance. Unannotated pins are not inferred to contain internal pulls.

The initial parallel model assumes each direction's declared pulls share the
intended reference rail; authors must verify that assumption. Mixed-rail models,
resistance tolerance, parameter-derived metadata, and automatic supply/driver
limits remain future work. Display strings such as `"4.7k"` are not parsed.

#### Capacitance

A net or port opts in with nonnegative `max_capacitance_pF`. Connected physical
pins declare nonnegative `capacitance_pF`; instance annotations may override it.
The outermost net declares `wiring_capacitance_pF` for wiring and any other
unmodeled external capacitance. Explicit external endpoints also count, while
ordinary public forwarding ports do not duplicate physical contributions.

Sum contributions once and enforce every declared limit. Missing endpoint or
outermost wiring data produces an incomplete warning; explicit zero means a
known zero contribution. Known overloads remain errors even with missing data.
Nets without a corresponding limit receive no budget or missing-data diagnostics.
No capacitance is inferred from footprints, routing, or part-value strings.

For I2C, minimum pull-up resistance depends on supply voltage and sink capability;
maximum resistance additionally depends on capacitance and permitted rise time.
A future protocol profile can derive limits from those inputs. The initial
checks use explicit limits rather than one universal I2C constant. See
[TI's pull-up calculation application note](https://www.ti.com/lit/pdf/slva689).

#### Python check modules

Optionally enable Python files relative to `noscheme.toml`:

```toml
erc_modules = ["checks.py"]
```

These are explicitly loaded Python code, running in the compiler process. A
module defines `register(registry)` and uses:

- `registry.fields(scope, schema)` to register fields in the existing metadata
  scopes. Schemas accept `positive`, `nonnegative`, `string`, `bool`, `literal`,
  or nested field dictionaries. Use a project-specific mapping name for your
  fields, such as `demo: {max_pins: ...}`, to avoid collisions. Unknown fields,
  duplicate registrations, and attempts to replace core fields are errors.
- `registry.check(name, callback, net_fields=(...))` to register a stable check
  identifier and optional activation fields. With activation fields, invoke the
  callback only on nets with at least one matching declaration field. Without
  them, the callback decides applicability. Identifiers should include a module
  prefix, such as `demo_pin_count`.
- A callback receives `NetContext` with `net`, `origin`, `endpoints` (label/record
  pairs), and the resolved `circuit`. It returns or yields diagnostics created
  with `ctx.diagnostic(check_name, message, record=..., severity=..., related=...)`.
  `noscheme.erc_registry.value(record, field, default)` reads metadata values.

Callbacks receive isolated copies, preserving source spans and hierarchy, so
mutating a context cannot change another check's inputs or CAD connectivity.
Each selected configuration gets its own run with effective roles, plus
`circuit.erc_configuration` and `circuit.erc_effective_choices`. Reports include
registered check names, source locations, and configuration labels. Registered
checks use the existing outermost-net waiver mechanism. An outermost unchecked
net skips these net callbacks.

Loading/registration failures, invalid diagnostic results, and callback exceptions
are blocking errors: no partial success report or export is published. Electrical
violations remain nonblocking for compile/export. Plugins must be deterministic
and side-effect free; isolated context copies are not a Python security sandbox.

The runnable [extension example](../examples/erc_extensions/main.nosch) combines
pull-resistance and capacitance budgets with a project-local
[pin-count check](../examples/erc_extensions/checks.py). Its simplified electrical
models are not a fabrication-ready board.

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
`do_not_connect`, grouped/aggregate load constraints, and waiver scope errors cannot be waived. Unknown check names,
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
| ERC | Role/net-type compatibility, public port contracts, driver checks, and scoped waivers are implemented. Explicit bias and opt-in pull/capacitance budgets are implemented through the extension registry. Tri-state rules remain. |
| ERC configurations | Public choices, exhaustive role/choice mappings, board configurations, selected/all checking, and labeled reports are implemented. Choices currently have no defaults. |
| Current accounting | Component supply-pin groups and hierarchical block aggregates are implemented. Each budget covers one electrical net; no automatic propagation through parts. |
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

### Import forms

Modules support both `import Device` (referenced as `Device.R`) and
`from Device import R as KiCadResistor`. Existing named imports remain valid.
Qualified references work for bases, instance types, and bundle types. Aliases
preserve definition identity; module qualification does not expose private
component or block internals. See [LRM imports](LRM.md#12-modules-and-imports).

### Reference example modules

The board entry point stays in `examples/stm32h523/stm32h523.nosch`. MCU circuitry,
shared components, interface bundles, and peripheral subsystems live in
`mcu.nosch`, `components.nosch`,
`interfaces.nosch`, and `peripherals.nosch`, respectively.
The board demonstrates module-qualified imports for subsystems and named imports
for components and bundles. See the [source module map](DEVELOPMENT.md#example-source-modules).
