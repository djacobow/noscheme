# NoScheme project design

## Purpose and status

I don't like drawing, reading, or maintaining schematics. I started my career in
semiconductors, where engineers largely moved to textual circuit descriptions
such as Verilog and VHDL. I would like to bring that workflow to PCB design.

NoScheme describes explicit PCB parts and connections in text. It sits between a
flat netlist and a behavioral HDL: reusable blocks and arrays reduce repetition,
while the author remains responsible for selecting parts and defining the circuit.
KiCad handles physical placement and routing.

The Python compiler implements parsing, project loading, resolution, parameter
evaluation, circuit expansion, ERC, library conversion, and KiCad netlist export.
The STM32 reference board's exported netlist has populated PCB Editor successfully;
it has not been routed or hardware-qualified. The Rulos example reconstructs an
existing design and verifies its connectivity against Eagle source data.

This document records goals, design rationale, and reference-hardware decisions.
The [Language Reference Manual](LRM.md) is the normative description of the
implemented language and compiler behavior. Use [DEVELOPMENT.md](DEVELOPMENT.md)
for commands and validation, [EDITOR_SUPPORT.md](EDITOR_SUPPORT.md) for editor
setup and supported LSP operations, and [PLAN.md](PLAN.md) for work status.
Historical [experiment findings](experiments/FINDINGS.md) record the parser and
KiCad feasibility results; they do not supersede the production reference.

## V1 scope and workflow

- Use `.nosch` source files, a grammar-driven parser, and one board per project.
- Explicitly instantiate physical components and reusable circuit blocks.
- Convert KiCad libraries in a separate step and review the resulting snapshots.
- Check declared connectivity and electrical constraints, then export a netlist
  for placement and routing in KiCad 10.
- Keep NoScheme authoritative for components and connectivity. Retain stable
  identities and references so compatible layout survives circuit updates.
- Provide source-located diagnostics, ERC reports, net tables, and change reports.

The compiler must produce a definite circuit without guessing. Structural errors
block publication; electrical errors permit inspection of the circuit in KiCad.
Unspecified footprints permit a partial export with explicit reporting. See
[LRM §14](LRM.md#14-commands-and-diagnostics) for commands and failure behavior.

### Deferred features

Algorithmic generation, conditional topology, quantity parameter types, assembly
variants, differential-pair declarations, and CAD-to-source circuit synchronization
remain outside the implemented language. A future generation API, potentially
Python, could create the same nets and instances as native source; its execution
model has not been chosen.

The design uses package-specific physical pins. Wrapper blocks provide logical
interfaces without a separate logical-pin/package abstraction. Named pin groups
and component-wide pin defaults remain deferred pending a stronger use case.

Vim/Neovim highlighting and the LSP are implemented. Canonical source formatting
and incremental compiler analysis remain future work. The complete supported and
unsupported feature lists belong in [LRM §16](LRM.md#16-limits-and-unsupported-features)
and [Editor support](EDITOR_SUPPORT.md).

## Reference board

The first worked board is an STM32H523 design with:

| Function | Requirement |
|---|---|
| MCU | STM32H523VCT6, 100-pin LQFP |
| Debug | Standard 10-pin Cortex SWD connector |
| USB | USB-C connector carrying data and supplying 5 V |
| Regulation | TLV1117LV33DCYR, 5 V to 3.3 V, SOT-223; ceramic input/output capacitors |
| Indicators | Three LEDs |
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

The UART circuitry uses `uart_tx[2]` and `uart_rx[2]` nets with
`inst uart_headers[2]: UartHeaderJST3(...)`. Ground is shared across both headers;
each TX/RX element connects to the corresponding header. The selected connector
is JST XH S3B-XH-SM4-TB, 2.50 mm pitch, side-entry SMT. Its mounting tabs
are electrically unconnected. See the [JST XH datasheet](https://www.jst-mfg.com/product/pdf/eng/eXH.pdf).

### Physical proof-of-concept implementation

The example has 58 physical parts and 52 nets with no placeholders or missing
footprints. Its project file is `examples/stm32h523/noscheme.toml`; the main source
remains `examples/stm32h523/stm32h523.nosch`. The imported `kicad_lib/mcu/MCU_ST_STM32H5.nosch` is the
explicit conversion of KiCad's `STM32H523VCTx` symbol. `STM32H523Board` specializes
its pin roles for this board; unused physical GPIOs explicitly bind `no_connect`.
Conversion reports and footprint snapshots are retained under `kicad_lib/`;
see its README for provenance and regeneration instructions.
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

## Rulos matrix example

The [six-line matrix example](../examples/rulos_6matrix/README.md) adapts Jeremy
Elson's Rulos Rev B schematic into four two-column blocks, each containing arrays
of six seven-segment displays. It contains 93 physical parts and 107 nets.
Bundles share the row buses and sliced array connections form the driver chain.

The example retains attribution, pinned source hashes, Eagle connectivity, and
an explicit map of deliberate footprint substitutions. Tests compare every pad,
value, and netlist connection with that evidence. Its nets deliberately use
passive checking while the original connectivity is transcribed; its ERC result
is not a claim of complete electrical validation. Package questions and KiCad
matches are recorded beside the example rather than duplicated here.

## Language design decisions

### Explicit names and connection syntax

Named connections prevent silent rewiring when declarations move. `:` indicates
a connection or binding; `=` is reserved for parameter defaults and formatting
arguments. Instance parentheses visually group connections and configuration,
while indentation separates their sections. Definition bodies hold internal
circuitry. These choices preserve explicit interfaces without positional wiring.

Named imports, import aliases, and qualified module references let authors choose
how visibly to show provenance. Builtin namespaces require explicit imports and
qualification so names such as `role.passive` retain a clear meaning. `#` is
allowed in identifiers for active-low signals, so comments use `//` and
non-nesting `/* ... */` instead.

Syntax and lookup rules are in [LRM §1–2](LRM.md#1-projects-and-modules);
instance bindings are in [LRM §5](LRM.md#5-instances-and-connections).

### Physical components and reusable blocks

`component` describes a physical part; `block` describes a circuit with private
internals and a public interface. Keeping distinct keywords makes that distinction
explicit even when a part has no selected footprint yet.

Physical pin identifiers retain exact text because package pad names need not be
integers. Unambiguous aliases improve readability without hiding the underlying
pad mapping. A wrapper can connect several physical pins to one logical port;
author-defined wrappers handle package-specific adaptations.

Specialization uses single inheritance to customize metadata and existing
parameter defaults. It cannot change structure or reach into private internals.
It can correct faulty upstream library metadata; an instance must then obey the
resulting definition. See [LRM §3](LRM.md#3-declarations) and
[§7](LRM.md#7-specialization).

### Parameters and metadata

Parameters begin with `number` and `string`, with explicit defaults and arguments.
Restricting their use keeps expansion predictable. Integer/double tags support
ordinary arithmetic while making conversion in integer contexts visible.
Part-value helpers format labels; they do not supply electrical quantities to ERC.
[LRM §4](LRM.md#4-parameters-and-expressions) defines evaluation and formatting.

Recognized metadata is checked so a typo does not silently disable a constraint.
User conventions such as purchasing notes and DNP belong under `custom`. ERC
extensions register net/pin rules, predicates, reducers, rule sets, and descriptive
metadata through the same API as built-ins. Named `checks` applications and
TOML `erc_rule_sets` select policies without changing the
language grammar. The authoritative field tables are in
[LRM §8](LRM.md#8-metadata-reference); the extension API is in
[§12](LRM.md#12-extending-erc).

### Encapsulation, globals, and arrays

Lexical scope and private internals make blocks independently understandable.
Globals are explicit dependencies: the author can use a global rail or expose a
port, and referencing an absent global fails. No additional dependency declaration
or private-net access is needed.

Every net is declared. The language does not merge independently declared nets
or provide pure-wiring adapter bodies. Interface adaptation uses explicit caller
bindings or a block containing physical endpoints. Bundles collect named fields;
arrays express repetition without generated topology. Scalar sharing is useful,
but implicit partial broadcasting could conceal a wiring mistake.

See [LRM §3.2–3.4](LRM.md#32-reusable-blocks) for scope and
[§6](LRM.md#6-bundles-and-arrays) for bundles, shapes, lists, ranges, and slices.

## Electrical checking rationale

Roles describe endpoints; net types describe constraints on their connections.
Keeping both concepts permits the same physical interface to be checked in
different contexts. Missing or ambiguous information must remain visible rather
than being treated as a passive role or selected to make a check pass. Supply
and ground roles are distinct even when an upstream library conflates them.
The compatibility tables are maintained only in
[LRM §9](LRM.md#9-electrical-roles-and-net-types).

Checks operate on physical nets and explicit modeled endpoints. They do not infer
behavior through resistors, regulators, or other components. Ports are contracts,
not extra drivers; hierarchy must not multiply current loads or driver counts.
Grouped supply loads and block aggregates express accounting boundaries explicitly.

Electrical quantities use metadata with units in field names. Budgets are opt-in
because many nets have no meaningful pull-resistance or capacitance limit. Missing
annotations do not imply zero load, and source capacities are not automatically
summed. Tri-state checking verifies declared capability, not runtime arbitration.
See [LRM §10](LRM.md#10-electrical-checks) for check behavior and waivers.

Connector roles describe the attached equipment at the point of use. Named ERC
configurations model alternative uses of one physical board, without changing
parts, connectivity, or footprints. Choices are forwarded through public block
interfaces, preserving encapsulation. See
[LRM §11](LRM.md#11-erc-choices-and-configurations).

Waivers record intentional exceptions with a reason and retain the original
diagnostic. They do not override mandatory connection policies or establish
unmodeled electrical behavior. Their scope belongs to the outermost net owner;
a library block must not silently suppress the caller's constraints.

## Library conversion and CAD integration

Explicit library conversion makes dependencies reviewable and keeps compiler
behavior independent of locally installed KiCad libraries. Footprint snapshots
record pad identifiers and provenance; selection hints assist authors without
silently choosing packages. Symbols and physical pads must agree at export.
See [LRM §13.1](LRM.md#131-library-conversion-and-footprints).

CAD name conversion belongs to an adapter. Preserve source identities and enough
mapping information to diagnose renamed nets and prevent collisions. Reverse
name mapping alone does not imply electrical synchronization from CAD to source.
KiCad owns placement, routing, and physical design-rule settings; NoScheme records
net-class membership for the handoff. Differential-pair declarations remain
unimplemented. Current export behavior is in
[LRM §13](LRM.md#13-cad-identities-and-export).

Stable identity is separate from the reference designator. Explicit identity
anchors can preserve layout associations through source renames and moves.
Version-controlled state retains assignments and reservations across exports.
Ordinary publication failures must not leave partial new state. Crash recovery
across all output files and reference-reservation cleanup remain future work.

## Reports and editor support

Source spans, stable check identifiers, instance paths, and configuration names
make errors actionable in reused blocks and arrays. Text reports serve people;
JSON reports serve automation. Independent resolution errors are collected where
continuing is safe, while invalid structure still blocks expansion/publication.
The reporting contract is in [LRM §14](LRM.md#14-commands-and-diagnostics).

The independent stdio language server reuses the compiler with unsaved-document
overlays. It provides diagnostics, completion, definition navigation, references,
rename, and formatted hover, including supported public bundle-member navigation.
It does not write build artifacts or identity state. Rename scope and syntax-error
limitations are documented in [EDITOR_SUPPORT.md](EDITOR_SUPPORT.md).

## Unresolved design and implementation work

The maintained backlog is the [review TODO section in PLAN.md](PLAN.md#todo-remaining-implementation-review-follow-up).
All remaining review follow-up items are deferred by user decision; see the
[deferred work list](PLAN.md#deferred-work). Board-level instance rename preserves
identities through existing metadata. Deferred proposals are not language
features or approval to change existing behavior.

## Research and review references

Prior ERC research informed the decision to keep checks local to each net:

- [KiCad 10 ERC documentation](https://docs.kicad.org/10.0/en/eeschema/eeschema.html#electrical-rules-checking).
- [Altium design validation](https://www.altium.com/documentation/altium-designer/schematic/design-validation).

Atopile and Flux.ai are prior-art references for further investigation. We may
borrow concepts, but will not use code wholesale.

The [archived design review](claude_review.md) predates the implementation. It
records alternatives and concerns, including syntax that was never adopted.
Adopted behavior is specified in the LRM; remaining review work belongs in PLAN.md.
