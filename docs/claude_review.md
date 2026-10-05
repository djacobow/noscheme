# NoScheme — Design Review

> Historical review of the October 3, 2026 pre-implementation design. Preserve
> the findings and proposals below as written; they do not describe the current
> compiler. Use the [LRM](LRM.md) for implemented behavior and the
> [plan](PLAN.md) for adopted decisions and deferred work. Paths and line counts
> in the review refer to the source snapshot available to its author.

**Reviewer:** Claude (Opus 5)
**Date:** 2026-10-03
**Inputs:** `PROJECT.md` (480 lines), `examples/stm32h523.nosch` (433 lines)
**Nothing else exists yet** — no grammar, no parser, no converter, no tests.
**Revision 2:** 2026-10-03, full re-review by Claude Opus 5.5. See §0.

---

## 0. Revision 2: What Changed

I re-read both inputs and my own rev 1 from scratch. Some of rev 1 was wrong,
and those errors are corrected in place, marked *rev 2*:

- **B8 overcounted.** Rev 1 said ~30 mandatory-connection errors. The real count
  is **20**. Nets with no physical pins at all have no pin that could fail the
  rule; they produce a different diagnostic. The finding stands, but the number
  was wrong.
- **B9's KiCad argument was weak.** Instance names never become refdes (refdes is
  `ref_prefix` + a number), so a `#`-named instance cannot vanish from pcbnew.
  The recommendation is unchanged, but the reason given for it was wrong.
- **My own grammar had a lexer bug.** The `PIN_KEY` terminal in §8 overlapped both
  `IDENT` and `INT`, so a standard lexer could not tokenize it. Fixed. The fix
  also changes B5's recommendation and the §17 default.
- **Proportions overstated.** Rev 1 said the header `unused.never` lines were
  "a third" of the file (H3) and the boilerplate was "40%" (M1). The actual
  figures are about 12% and 25%.
- **H9's diff-pair suffix rule was stated too narrowly.**
- **Removed an unsupported claim** comparing the bundle design with Flux.

New findings:

| ID | Finding | Why it matters |
|---|---|---|
| **H12** | Footprint names and pin↔pad agreement cannot be checked at compile time | **The one most likely to cost a board respin.** The compiler has the complete pin model, which makes it the best place to catch pin/pad mismatches, but the purity rule keeps it from seeing the pads. |
| **B10** | Binding two ports to one caller net must merge nets, but the spec says port binding cannot merge | EN-tied-to-VIN is an everyday idiom, and as written the spec either forbids it or contradicts itself |
| **B11** | Several spec features assume a net-to-net connection statement, but the syntax has none | Bundle-to-bundle and cross-type field connection currently have no syntax |
| **H11** | ERC configurations cannot reach connectors inside blocks without breaking block privacy | Collides directly with the reference board's USB-C power case |
| **M17** | `.format()`-style strings are a known injection surface | Cheap to prevent now |
| **M18** | Number semantics are undefined, and Python's defaults are wrong for this use | Float noise in part values, and `/` in array sizes |
| B4+ | No rule for partial (NumPy-style) broadcasting, and no syntax for per-element connections on instance arrays | Users will meet this early |
| M3+ | Globals become a naming contract between libraries; the example uses two idioms for ground | A vendor's `global.GND` will not compose with a board's `global.gnd` |
| §9+ | A board with no schematic runs against KiCad's schematic-centric parity and update defaults | Added to the Phase 0 spike |

The §7 consistency table grows from 18 rows to 21, and §17 gains six defaults.

---

## 1. Verdict

The *premise* is sound and the *judgment* on display is better than most EDA-DSL
attempts. Several decisions in `PROJECT.md` are things projects in this space
usually get wrong and only discover after a year (explicit net declaration, no
monkey-patching across block boundaries, no implicit KiCad library loading,
waivers that require a reason, ERC configurations that cannot change the
netlist). That part is genuinely good.

The *document* is not yet a specification. It is a well-written transcript of a
requirements negotiation. I count **29 sentences** that defer a decision
("remains open", "remains to be designed", "not yet decided"), and they are not
peripheral — they include the export mechanism, the import syntax, the module
resolution rules, the role vocabulary, the net-declaration syntax, the parser
library, and the identity/refdes storage mechanism. Those are the load-bearing
walls.

The example is the most valuable artifact here, because it is where the prose
stops being plausible. Reading it against `PROJECT.md` surfaces **three
different parameter-declaration syntaxes**, a role vocabulary **2.3× larger**
than the one defined, an undocumented recognized-metadata field, a grammar that
no standard Python indentation lexer can parse, and a reference board that —
applied against its own stated ERC rules — emits mandatory-connection errors on
about twenty nets.

**My recommendation: stop adding prose to `PROJECT.md`.** The next artifact
should be a real grammar file plus a decisions log, developed against the
example as a conformance corpus. Sections 8–12 below propose specifics. Sections
4–6 are the findings, ordered by whether they block writing that grammar.

### The one strategic risk

Everything in v1 depends on a mechanism the document explicitly has not chosen:
"The precise export/import mechanism remains to be chosen." If the KiCad hand-off
cannot preserve placement and routing across re-runs, the tool is a toy
regardless of how good the language is — because the *second* run is the whole
value proposition. I would resolve this **before** finalizing syntax, because
the answer constrains the language (see B6, §9). It is also the cheapest thing
to de-risk: a 200-line spike that emits a hand-written netlist and imports it
into pcbnew twice will tell you more than another 500 lines of `PROJECT.md`.

---

## 2. What's Right (do not relitigate these)

Listed because a review that only finds problems gives you no way to tell which
decisions are load-bearing and should be defended under pressure.

| Decision | Why it's right |
|---|---|
| **All nets must be declared** | Implicit net creation makes every typo a silent floating node. This is the single highest-value correctness decision in the document. Verilog's implicit wires are universally regretted; `default_nettype none` exists for a reason. |
| **No monkey-patching into blocks; no access to internals** | atopile and SKiDL both let you reach in. It feels convenient and destroys the ability to reason about a block locally. |
| **Port declaration implies the internal net** | Removes real boilerplate without weakening the "declare everything" rule. Good instinct. |
| **Explicit KiCad library conversion, not implicit loading** | Makes compilation a pure function of the source tree. Protects reproducibility and keeps upstream library churn out of your build. |
| **Bundle field structure separated from endpoint electrical roles** | This is the thing most bus/interface systems get wrong — they bake direction into the bundle and then need `flipped`/`reverse` modifiers everywhere. An I²C bundle is just two nets; who drives is a property of the endpoints. Correct. |
| **Waivers require a reason; waive one check, not the net** | Blanket `NOERC` directives are how real boards ship with real faults. A required reason string turns a waiver into reviewable documentation. |
| **No ERC propagation through passives in v1, documented honestly** | Including the KiCad/Altium research is the right way to make this call. Half-working component models are worse than none. |
| **ERC errors do not block export** | Matches how hardware actually gets designed — iteratively, with a half-finished board open in pcbnew. |
| **Named ERC configurations that change assumptions but not the netlist** | Genuinely novel and genuinely useful. The USB-powered / header-powered split on the reference board is exactly the class of bug that escapes review. Keep this and make it prominent. |
| **Aggregate load counted once, never with its constituents** | Shows someone thought about hierarchical current budgets rather than hand-waving them. |
| **Array instantiation + scalar-to-array broadcast** | `inst caps[10]: Cap0603(connections: 1: v3v3, 2: gnd)` expresses the most repeated pattern in PCB design in four lines, with no generation feature. This is the concision win the document is looking for. Lead with it in documentation. |

---

## 3. How to read the findings

- **B** — Blocking. Resolve before writing the grammar; each one changes the
  token stream or the type system.
- **H** — High. Resolve before the first usable compiler; each one is expensive
  to retrofit.
- **M** — Medium. Should be decided, cheap to defer.

---

## 4. Blocking Findings

### B1. Indentation is significant *inside* parentheses. No standard lexer does this.

`PROJECT.md` requires both:

> "logic blocks are defined by indentation, like python"

> "Parentheses may span multiple lines ... Section entries use `:`; indentation separates entries without requiring commas."

These are in direct conflict with how indentation-sensitive lexers work. Python's
rule is *implicit line joining*: inside `(`/`[`/`{`, the tokenizer **suppresses**
NEWLINE/INDENT/DEDENT entirely. Every off-the-shelf indentation postlexer in the
Python ecosystem — including Lark's `Indenter` — implements exactly that rule,
because it is the rule everyone expects.

NoScheme's instance form requires the opposite:

```text
inst resistor: LedResistor(
    connections:        <- INDENT must be emitted here, inside the paren
        1: drive        <- and here
        2: anode
    params:             <- DEDENT then INDENT
        value: resistance
)                       <- closing paren at the *statement's* indent level
```

You need indentation tracking to remain active inside the parens, with the
closing `)` legal at an outer indentation than the content it closes. That is a
custom scanner, and it is a *fiddly* custom scanner — the interaction between
"DEDENT back to the `inst` level" and "then expect `)`" is where you will spend
a week and still have ambiguous error recovery.

**The parentheses buy you nothing.** Compare, and note that `PROJECT.md`'s stated
motivation for the parens — avoiding "placing values and footprints in an
indented instance body" — is not actually achieved, because the example *does*
place them in an indented body; it just wraps that body in parens:

```text
inst resistor: LedResistor:          # consistent with component/block/board/bundle
    connections:
        1: drive
        2: anode
    params:
        value: resistance
```

**Recommendation:** Drop the parentheses and use `:`-introduced indented suites
uniformly, matching `component`, `block`, `bundle`, and `board`. The language
then has exactly one block mechanism and you can use a stock indenter.

If you keep parens, pick the *other* standard option and make them a genuine
implicit-join region with commas (`Resistor(connections: {1: drive, 2: anode})`).
Do not ship the hybrid. **Whichever you choose, write the decision down before
the grammar** — this one choice determines whether your lexer is 40 lines or 400.

---

### B2. `params:` means three different things, with identical syntax

The example uses the same section with three distinct semantics, distinguishable
only by inspecting the right-hand side:

```text
component Resistor:
    params:
        value: string                      # (a) TYPE DECLARATION

component LedResistor extends Resistor:
    params:
        value: f.kOhm(1)                   # (b) DEFAULT VALUE (specialization)

block Indicator:
    params:
        resistance: string = f.kOhm(1)     # (c) TYPE *AND* DEFAULT — third form

    inst resistor: LedResistor(
        params:
            value: resistance              # (d) ARGUMENT BINDING
    )
```

Three declaration forms for one concept, in a 433-line file. And (a) is
genuinely ambiguous: `string` is an ordinary identifier, so `value: string`
parses equally well as "default value is the contents of parameter `string`".
The parser cannot distinguish (a) from (b) without resolving names — which means
type information leaks into name resolution, and a typo (`strng`) becomes a
confusing "unknown parameter" instead of "unknown type".

**Recommendation:** One form, with types in a syntactically distinct position
and a distinct keyword:

```text
params:
    param value: string                 # required, no default
    param resistance: string = f.kOhm(1)
```

and have specialization use a different word for what it does, because it *is* a
different operation:

```text
component LedResistor extends Resistor0603:
    defaults:
        value: f.kOhm(1)
```

Also reserve type names (`string`, `number`) as keywords so `value: string` can
never be read as an expression.

---

### B3. The net type system conflates two unrelated kinds

A `net` declaration's type position currently accepts at least four different
sorts of thing:

```text
net sd_detect#: nettype.digital                 # an electrical category
net oled: I2C                                   # a bundle (struct of nets)
net expansion_gpio: nettype.bidirectional[10]   # an array of categories
ports:
    expansion_gpio: nettype.bidirectional[10]   # array port
    oled: I2C                                   # bundle port
```

`PROJECT.md` describes these as separate concepts — "Nets have electrical
categories" and "Bundles are named net types with named fields" — but the
grammar gives them one slot, and never defines the composition rules. Open and
unanswered:

- Is `I2C[4]` legal? (An array of bundles — `PROJECT.md` says "An array of
  arrays of buses is interesting to consider, but we might not do it," which
  does not answer it.)
- Is `nettype.digital[4][2]` legal, and is it `[4]` of `[2]` or the reverse?
- Can a bundle field be an array (`bundle Parallel: data: nettype.digital[8]`)?
  This is the obvious thing a user will try first, and the reference board's own
  description — "if a simple signal bus is represented by eight signals" — asks
  for it.
- Is `sd_bus[2].mosi` a legal reference? What about `oled.scl[0]`?

**Recommendation:** Write the type grammar explicitly, as a small algebra, and
state what is in v1:

```
net_type   := base_type array_suffix*
base_type  := nettype_ref | bundle_name
array_suffix := "[" int_expr "]"
```

Then define the **shape algebra** for connections, in one table, including the
case the example already depends on (B4). My suggested v1 line: bundles may
contain arrays of scalars; arrays of bundles are legal; nested arrays are legal
to two dimensions; arrays inside arrays-of-bundles are not. Field access binds
tighter than indexing, and both compose left-to-right.

---

### B4. The example uses a connection rule that `PROJECT.md` does not define

```text
net led_drive: nettype.digital[3]

inst indicators[3]: Indicator(     # 3 instances, each with a SCALAR port `drive`
    connections:
        drive: led_drive           # connected to a 3-element net array
)
```

This is elementwise distribution across an instance array. `PROJECT.md` defines
three rules — array-to-array of identical shape, scalar net to array of pins,
scalar bundle to array of bundle ports — and **this is none of them**, because
the port is scalar and the instance is the thing that is arrayed.

The rule you want is: *an instance array's dimensions prepend to each port's
dimensions.* So `indicators[3].drive` has effective shape `[3]`, which then
matches `led_drive`'s `[3]` under the ordinary array-to-array rule. That is the
right rule, it is consistent with everything else, and it is not written down.

**Recommendation:** State the prepend rule, then re-derive all connection cases
from exactly two primitives:

1. **Shape match** — identical shapes connect elementwise.
2. **Broadcast** — a scalar net (or scalar bundle) connects to every element of
   an array of endpoints.

Everything else follows, including the example above. Then add the exhaustive
table, with the error cases `PROJECT.md` already calls out (`[2][3]` vs `[3][2]`
is an error even though both have 6 elements; `[1]` ≠ scalar; array-of-distinct-nets
to one pin is an error because it would merge nets).

*Rev 2: two holes remain even after the prepend rule.*

1. **Partial broadcasting.** Take `inst a[3]` with a port `p` of shape `[3]`.
   Under the prepend rule, its effective shape is `[3][3]`. Bind it to a `[3]`
   net. A user who has used NumPy expects every instance to get the whole bus,
   broadcast over the leading dimension. The stated rules say this is an error,
   because the shapes differ and the net is not scalar. Either answer can be
   defended, but the user and the compiler must not quietly disagree.
2. **No per-element connections on instance arrays.** An `inst a[3]` declaration
   has one `connections` section, shared by all elements. "Individual elements
   may be connected explicitly" covers nets but not instances. So for the case
   above the user has *no* way to express what they want.

Recommendation: keep implicit broadcasting to scalars only, so the case above
is an error. Add an explicit form for the leading-dimension case, such as
`p: broadcast(bus)`. `PROJECT.md` objects to `each` for scalars, but this is a
different and rarer case, and here explicitness is the point. Also decide
whether `a[1].p: special_net` overrides are allowed in the array's connection
section. I would allow them, because it is the only way to vary a single element
without generation.

---

### B5. Pin designators: "numbers as strings" vs. bare tokens, and no BGA story

`PROJECT.md`:

> "Physical pins can be addressed explicitly by pin number, with numbers represented as strings"

The example header:

> "Numeric physical pin labels are bare tokens whose spelling is preserved."

The example body: `1:`, `2:`, ... `20:` — bare, unquoted.

These are three different answers. And the question is harder than it looks:

- `1` lexes as an INTEGER. A pin key of `A1` lexes as an IDENTIFIER. BGA parts
  need `A1`, `AB12`. TO-220 tabs are often `TAB`. Connectors use `MP1`, `SH`,
  `S1`. So the pin-key lexical class is `INTEGER | IDENTIFIER | STRING` — three
  token types in one syntactic position, which the grammar must unify.
- Is pin `1` the same pin as `"1"`? As `01`? "Spelling is preserved" plus
  numeric literals implies `01` and `1` are distinct keys that print differently
  but index the same pad. That is a bug factory, and it will show up the first
  time someone converts a KiCad library that zero-pads.
- Identifiers permit `#` (B9), so `cs#` is a legal pin key too.

**Recommendation:** Pick one lexical form and normalize once, at the boundary:

- Pin designators are `IDENT | INT | STRING` **at the grammar level, not a
  separate token**. (*Rev 2:* rev 1 proposed a `[A-Za-z0-9_#]+` terminal. That
  terminal overlaps both `IDENT` and `INT`, so a standard lexer cannot tokenize
  it.) Bare `1` and `A1` both work. Anything else, such as `1A`, `-` or `EP-1`,
  must be quoted; `"1"` is accepted and
  normalizes identically.
- Normalization rule, written down: exact string match, **no** numeric
  equivalence. `01` ≠ `1`, and the compiler errors on a definition containing
  both. The converter emits exactly what KiCad has.
- The KiCad netlist `(pin (num "..."))` field is a string, so string-native pin
  keys line up with the backend and nothing is lost.

**Separately — and this is the bigger half of this finding — the pin model has no
package layer.** `component` declares pins by pad number with `footprint` as
metadata. But your headline part, the STM32H523, ships in LQFP-64/100/144 and
several BGAs, and **PA9 is a different pad number in each**. Today you must
duplicate the entire 100-pin definition per package, and KiCad's "alternate
footprint" story will not save you because the pin numbers genuinely differ.

The fix is a separation that costs little now and is very expensive later:

```text
component STM32H523:
    pins:                       # LOGICAL pins, by name. No pad numbers.
        PA9: { role: role.digital_bidirectional }
        VDD: { role: role.power_input }
        ...
    package LQFP100:
        footprint: "Package_QFP:LQFP-100_14x14mm_P0.5mm"
        pads:
            PA9: 68
            VDD: [11, 19, 32, 48, 61, 74, 94]   # see B7: one logical pin, many pads
```

Instances then select a package. I would rank this the most consequential
architectural decision in the whole design, because it is the one that cannot be
added later without rewriting every converted library.

---

### B6. The KiCad hand-off is unchosen, and it constrains the language

Six separate statements defer this: "The precise export/import mechanism remains
to be chosen", "The export and update mechanism remains to be selected",
"reverse synchronization ... has not been specified", "The exact identity
annotation syntax, uniqueness rules, and storage mechanism remain to be
designed", and more. Meanwhile the v1 acceptance criterion is "On opening
PCBNew, all devices should be present with their footprints and connectivity."

You cannot specify the language around an unknown backend, because the backend
dictates what identity means — and identity is a *language-visible* feature
(`PROJECT.md` already promises an "optional explicit identity string"). Decide
now. §9 has my concrete recommendation, with reasoning.

---

### B7. One logical pin, many pads — no model at all

Every real part has this. VSS×10 on the MCU. Ground pads on a QFN. Shield and
mounting pins on USB-C (which the reference board needs). A TO-220 tab tied to
the output. Thermal pads.

`PROJECT.md` gets *near* it — "A component may declare a single total load for a
group of supply pins, avoiding the need to divide its consumption artificially
among those pins" — but that is a current-budget accommodation, not a
connectivity model. There is no way to say "these 10 pads are one node; connect
the node once."

Without it, the MCU definition needs ten `VSS1: global.gnd` ... `VSS10:
global.gnd` lines per instance, and every one is a chance to miss one — with
`unused.never` turning the omission into an error you then route around by
copy-paste. Note also that this interacts with B10: a converted KiCad symbol
with stacked/hidden power pins has no clean representation at all today.

**Recommendation:** Make it a first-class part of the package mapping (see B5):
a logical pin maps to a *list* of pads. Connectivity, roles, and unused policy
attach to the logical pin; the exporter fans out to all pads. This also makes
the current-budget grouping fall out for free rather than needing its own
feature.

---

### B8. The reference example fails its own mandatory-connection rule

`PROJECT.md` states two rules that collide:

> "A required pin must share its net with at least one other physical or explicitly modeled external endpoint; merely attaching it to a declared net is insufficient."

> "Placeholder metadata does not by itself satisfy a physical pin's mandatory connection requirement."

Every `*_Pending` block in the example has an **empty body** — ports with
`placeholder: true` and no instances. So those ports contribute no physical
endpoints. Tracing the nets:

| Net | Physical endpoints | Verdict |
|---|---|---|
| `global.gnd` | both headers, LED cathodes | OK |
| `global.v3v3` | oled hdr p1, 2 pullups, exp hdr p4 | OK |
| `global.v5` | exp hdr p3 only | **1 physical endpoint — error** |
| `oled.scl` / `oled.sda` | oled hdr + pullup | OK |
| `expansion_spi.{sck,mosi,miso,cs#}` | exp hdr only | **error ×4** |
| `expansion_i2c.{scl,sda}` | exp hdr only | **error ×2** |
| `expansion_gpio[0..9]` | exp hdr only | **error ×10** |
| `sd_detect#` | *none* | no pin can fail the rule; reported as an unconnected net instead |
| `led_drive[0..2]` | resistor pin 1 only | **error ×3** |
| `usb` (2), `debug` (4), `sd` (4) fields | none | same: 10 more nets with **zero** physical endpoints |

That is **20 mandatory-connection errors**, one per affected net (*rev 2:* rev 1
said ~30), plus 11 nets with no physical endpoint at all, from a file whose own
header says "the presence of a placeholder itself does not produce a warning."
The intent is clearly that a WIP design should be quiet; the rules as written
make it loud.

This is not a bug in the example — it is the rules telling you something. The
`placeholder` concept is doing two jobs badly: it satisfies the *structural*
port check but deliberately fails the *connectivity* check, with no third state
for "I know this is incomplete, tell me once, not thirty times."

**Recommendation:** Replace the boolean with a first-class, greppable,
*reportable* endpoint:

```text
ports:
    supply: nettype.power
        todo: "MCU pin assignment pending datasheet review"
```

Semantics, stated explicitly:

1. A `todo` endpoint **satisfies** mandatory-connection for pins on its net.
   The user has asserted "something will be here."
2. It contributes **no** driver, load, or footprint. Driver/budget checks on that
   net become `incomplete` rather than `error`.
3. Every `todo` is reported **once**, in a summary section — "17 incomplete
   endpoints across 5 blocks" — not as a per-net diagnostic.
4. `--strict` promotes all `todo`s to errors. This is your release gate, and it
   is the feature that makes the whole mechanism safe.

The required reason string is what distinguishes this from a silent escape
hatch, and it matches the design instinct you already applied to waivers.

---

### B9. `#` as the *first* identifier character collides with KiCad

Allowing a trailing `#` for active-low (`RESET#`, `cs#`) is reasonable and reads
well to a hardware engineer. Allowing it **anywhere, including first** —
`[A-Za-z_#][A-Za-z0-9_#]*` — buys nothing and costs you:

- In KiCad, a reference designator beginning with `#` marks a **virtual /
  power-symbol** component, which is excluded from the board and the BOM.
  *Rev 2 correction:* instance names never become refdes (refdes is
  `ref_prefix` + a number), so this applies only to an explicit refdes override
  or a `ref_prefix` that contains `#`. Those two fields need a validation rule.
  This point is not, after all, an argument about identifiers. The case for
  restricting identifiers rests on the two bullets below.
- `##`, `#`, and `#1` are all legal identifiers. `#` alone as an identifier is
  actively confusing in diagnostics.
- It forecloses `#` for anything else, forever — including the pragma/attribute
  syntax you will eventually want.

**Recommendation:** `[A-Za-z_][A-Za-z0-9_]*#?` — trailing `#` only, at most one.
Then either reject `#` in refdes entirely, or define the net-name escaping rule
(§10). Keep `#` out of the comment syntax as already decided; that part is fine.

---

### B10. Two ports bound to one caller net: the spec says this cannot merge, but it must *(rev 2)*

`PROJECT.md`:

> "This binding identifies the port's internal connection with the caller's net; it does not merge unrelated declared nets."

Take an ordinary LDO block whose enable is tied to its input:

```text
inst regulator: Ldo:
    connections:
        input:  global.v5
        enable: global.v5
```

Inside `Ldo`, `input` and `enable` are two distinct declared nets, because the
spec makes a port declaration also a net declaration. The caller binds both to
`v5`, so physically they are now one node. That leaves two readings. Either the
binding merges two declared nets, which contradicts the sentence above, or this
is an error. The error reading outlaws some of the most common idioms in board
design: EN tied to VIN, address straps tied to GND, two ground ports tied
together, a pass-through bus.

It has to be legal. The sentence guards against something real, which is
*accidental* merging by name, but it is worded so that it also forbids a
deliberate merge through hierarchy.

**Recommendation:**
1. Restate the rule. A port binding identifies the port's internal net with the
   caller's net, so several ports bound to one caller net become **one flattened
   net**. This is the only way distinct declarations can merge, and it always
   happens visibly at a call site.
2. Specify which name survives flattening (H10). I suggest the outermost
   declaration. The IR keeps the inner names as aliases for diagnostics.
3. Add an info-level `ports-shorted` lint that lists blocks whose ports share a
   caller net. An *unintended* short through hierarchy then stays visible, which
   keeps the spirit of the original sentence.
4. State that "one pin bound to two distinct declared nets" is checked
   **per scope, before flattening**. After flattening, the merge makes that
   condition impossible by construction.

### B11. Several features assume a net-to-net connection statement that does not exist *(rev 2)*

In the draft, the only way to connect anything is an instance binding,
`pin_or_port: net`. No statement connects two nets. `PROJECT.md` leaves this open
("Whether separate connection statements are also supported remains open"), yet
other sentences already assume such a statement exists:

> "A single statement can connect a bundle to another bundle of the same type, matching fields by name."

> "Different named bundle types ... connections between them must be made explicitly by field."

Which statement? Connecting two declared nets means merging them, and the spec
forbids that without an aliasing mechanism, which is *also* open.

Here is a case with no syntax today. An MCU block exposes `i2c: I2C`. A vendor
sensor block exposes `bus: TwoWire`, with the same field names. Bundle types are
nominal, so the two cannot be bound to each other as wholes. Connecting them
needs either a net-merge statement or **per-field binding on the left-hand
side**:

```text
inst sensor: VendorSensor:
    connections:
        bus.scl: i2c_main.scl
        bus.sda: i2c_main.sda
```

Neither the spec nor the example shows that. The example's left-hand side is
only ever a pin key or a whole port.

**Recommendation:** Decide now that **all connectivity is endpoint binding**.
v1 has no net-to-net statement, and the left-hand side of a connection may be a
port path (`bus.scl`, `data[3]`, `bus[2].scl`). Rewrite the two bundle
sentences in those terms. This closes both open questions:

- Separate connection statements: no.
- Aliasing: no. If two names really must sit on one node, use a 0 Ω part or a
  block whose ports short together, via B10.

It also keeps "no silent merging" airtight, because the only merge mechanism
left is B10's, and that always happens at a call site. §8 now reflects this.

---

## 5. High-Priority Findings

### H1. The role vocabulary in the example is 2.3× the one in the spec

| Defined in `PROJECT.md` | Used in the example |
|---|---|
| `role.passive`, `role.ground`, `role.open_drain` | those three, **plus** `role.power_input`, `role.digital_input`, `role.digital_output`, `role.digital_bidirectional` |

Same for net types: `PROJECT.md` names `nettype.digital` and `nettype.power`
concretely; the example uses five. `PROJECT.md` acknowledges "The complete role
vocabulary ... remain[s] open," but the example is already shipping an
undeclared one, which means the gap is now actively accumulating decisions by
accident.

The vocabulary is not cosmetic — it *is* the ERC engine. Each role needs:
permitted net categories, driver/load contribution, and conflict rules.

**Recommendation:** Write the complete v1 table before any ERC code. Minimum
columns: role · legal nettypes · drives? · loads? · conflicts-with. My suggested
v1 set: `passive`, `ground`, `power_input`, `power_output`, `power_bidirectional`
(for externally-powerable rails — the reference board needs it), `digital_input`,
`digital_output`, `digital_bidirectional`, `digital_tristate`, `open_drain`,
`analog`, `unspecified`, `no_connect`. Thirteen roles, one table, and the ERC
implementation becomes a lookup rather than a pile of conditionals.

### H2. `role.open_drain` *and* `nettype.open_drain` — pick one layer

Open-drain appears as both a pin role and a net category. If a net is
`nettype.open_drain`, the role is redundant; if the roles say it, the net
category is. Worse, the combinations are unchecked: what does a
`role.digital_output` pin on a `nettype.open_drain` net mean? (In real hardware:
a bug, and a blown pin.)

**Recommendation:** Open-drain is a **pin** property — it describes a transistor.
The net category should be the *discipline* (`digital`, `power`, `analog`,
`unchecked`), and "this net is pulled up and driven by open-drain pins" is a
*conclusion* ERC reaches from its endpoints, not an input. Then "an I²C net with
no pullup" becomes a derivable diagnostic instead of an unverifiable declaration.
Related: there is no `nettype.ground`, yet the example declares
`global net gnd: nettype.passive` and every block's `ground` port as
`nettype.passive`. A ground net is not passive — it is the reference. Give it its
own category so power-conflict checks can special-case it; otherwise your highest
fan-out net is in your least-checked category.

### H3. `unused.never` on connectors is backwards and unescapable

Both headers declare `unused: unused.never` on all 24 of their pins. That is
about 50 lines, or 12% of the file (*rev 2:* rev 1 said "a third"). But `PROJECT.md` says of `never`:

> "the pin must be connected, and **no instance-level annotation may override this requirement**"

A generic `Header2x10` whose every pin is mandatory is unusable for any design
that populates 14 of 20 pins — which is most designs. The only escape is
definition-time specialization (`component Header2x10_Partial extends
Header2x10`), i.e. a new type per usage pattern. That is a type explosion driven
purely by a policy default.

Connectors are the canonical case for *permissive* unused policy: a header pin
with nothing on it is a normal, intentional design outcome.

**Recommendations:**
1. Connector definitions should default to `explicit` (or `allowed`), not
   `never`. `never` is for VDD/VSS/EP — pins where omission is always a bug.
2. Reconsider the unoverridability. You already have a waiver mechanism with a
   required reason; that is the right tool here too. `PROJECT.md` says "These
   waivers do not override the mandatory connection policy of a pin," which
   leaves a user with an incorrect converted library and no path forward except
   subclassing. Allow `unused: unused.waived("no-connect on this variant")` and
   report it in the summary.
3. Add array/range pin declarations so this stops costing 40 lines (M1).

### H4. `f.kOhm(4.7)` produces a string, which forecloses the checks you want

`PROJECT.md` is firm:

> "In v1, parameter types are limited to number and string, without physical-quantity types"
> "Checks do not infer quantities by parsing formatted part-value strings."

And also wants:

> "Similar optional checks for I2C capacitance and pullup strength are of interest"

These cannot both hold. A pullup-strength check needs the resistance as a
*number with a unit*. With `value` as an opaque string and no parsing allowed,
the information is destroyed at the point of authorship — `I2CPullup` in the
example *has* 4.7 kΩ, and the compiler can never know it.

The stated workaround is parallel metadata (`capacitance_pF`, `max_load_current_mA`),
which means every quantity is written **twice** — once for display, once for
checking — with nothing keeping them in sync. That is a worse outcome than a
quantity type, and it is the kind of redundancy that is silently wrong on real
boards within a month.

**Recommendation:** Add a `quantity` type now. It is cheap if you do it at the
lexer level and refuse to do dimensional analysis in v1:

```text
params:
    param value: quantity = 4.7kOhm      # one token class: NUMBER + UNIT
```

- Store as `(mantissa: Decimal, unit: str)`. Canonicalize the unit string only.
- **No** dimensional arithmetic in v1 — no adding ohms to farads, because there
  is no arithmetic on quantities at all. This keeps the type system trivial.
- Rendering to a part value is a formatting function, so `f.*` becomes a display
  helper on quantities rather than a string constructor. `f.kOhm(4.7)` and
  `4.7kOhm` stop being different things.
- Checks read the quantity directly. The duplicate-metadata vocabulary
  (`capacitance_pF` et al.) mostly disappears, and with it a whole class of
  "the annotation says 100nF but the value says 10nF" bugs.

If you genuinely want to defer this, then **also defer** the capacitance and
pullup checks explicitly, and say in `PROJECT.md` that they require the quantity
type. Do not leave them listed as "of interest" when the type system cannot
express them — that is how a roadmap item becomes a surprise rewrite.

### H5. Connector role reference frame is ambiguous and will be got wrong

```text
inst expansion: Header2x10(
    metadata:
        pins:
            7: { role: role.digital_input }      # input to WHAT?
```

The example's comment resolves it — "Connector roles above describe the external
equipment's endpoints" — but a comment is not a type system. This convention
inverts the natural reading (for every other component, a role describes *that
part's* pin), and it inverts it precisely on the component type where a mistake
causes a double-driver that ERC will then confidently approve.

**Recommendation:** Make the frame lexically explicit and un-confusable:

```text
            7: { external: role.digital_input }   # or
            7: { role: role.external_load }
```

Any of these beats a convention. My preference is a distinct keyword
(`external:`) so a reader cannot apply the wrong frame even without the comment.

### H6. `unused` is both a namespace and a bare value

`unused.never` (policy, in a namespace) and `PA7: unused` (bare marker, in a
connection). `PROJECT.md` notes they are "distinct" — but it also insists "Bare
values are not implicitly resolved based on the attribute context," which is
exactly the kind of resolution a reader must now do mentally. Gratuitous.

**Recommendation:** Rename one. `PA7: no_connect` / `PA7: nc` for the marker,
keeping `unused.*` for policy; or `policy.unused_never`. Either way the two
concepts should not share a spelling.

### H7. `ref_prefix` is undocumented, and the recognized-metadata vocabulary doesn't exist

The example uses `ref_prefix` four times. It appears **zero** times in
`PROJECT.md`. Meanwhile `PROJECT.md` says:

> "Recognized metadata fields, such as `footprint`, are validated. ... Unknown fields outside `custom` produce errors so that misspelled recognized fields are not silently accepted."

So the example, under its own spec, would fail to compile on four lines. The
underlying problem is that "recognized metadata" is a closed set with no
members enumerated — while the example has already added `ref_prefix`,
`placeholder`, `alias`, and `pins` to it informally.

**Recommendation:** Enumerate the closed set as a table now, with type, legal
scope (component / block / instance / net / port), and whether specialization may
override it. Candidates from the two documents: `footprint`, `ref_prefix`,
`refdes`, `identity`, `datasheet`, `mpn`, `dnp`, `netclass`, `pins`, `alias`,
`role`, `unused`, `todo`, `max_source_current_mA`, `max_load_current_mA`,
`aggregate_load`. Also pick one word for the ERC-variant concept — `PROJECT.md`
says "configuration", the example comment says "profile."

### H8. No net-class or layout-aid attributes anywhere in the example

`PROJECT.md` lists this as a core requirement:

> "It should be possible to attach attributes to objects and nets such as net classes, bus types, etc"

and frames the layout connection as the one concession to PCB-land:

> "The language will not attempt to address PCB layout, except perhaps by adding types and attributes to objects and nets that will aid in layout."

The example contains **zero** net-class annotations. This is the stated bridge to
layout and it is entirely unexercised, which means it is entirely unvalidated.
It also has a backend consequence most people discover late: KiCad net classes
live in the **project file** (`net_settings` in `.kicad_pro`), not the netlist.
Netlist import will not carry them. So this feature requires a second emitter
and a second file to manage — worth knowing before you promise it.

**Recommendation:** Add `netclass:` to net declarations, exercise it in the
example on USB and the power rails, and spec the `.kicad_pro` emission. §10
covers the differential-pair case, which is the acute instance of this.

### H9. Differential pairs will not survive export

The example declares `bundle USB2: dp, dm`. On the reference board, `usb` carries
real USB 2.0 — the one net pair on this design that genuinely must be
length-matched and impedance-controlled.

KiCad identifies differential pairs **by net name suffix**: the two nets must
share a base name and end in `+`/`-` or `P`/`N`. A flattened pair
`usb_dp`/`usb_dm` will **not** be recognized, because `dm` does not end in `N`
(also check whether 10.0 matches case-sensitively), so the pair will not appear in
pcbnew's differential-pair router, and the single most layout-critical
relationship on the board is lost in translation with no warning.

**Recommendation:** Two parts, both cheap:
1. A `diffpair` net/bundle attribute that drives export naming, so the exporter
   emits `/USB_P` and `/USB_N` regardless of the internal names.
2. A standard-library `bundle DiffPair: p, n` with the attribute pre-set, so
   `USB2` is built from it and users get it right by default.

Add a check: a net marked `diffpair` whose partner is missing is an error.

### H10. Flattened net naming is completely unspecified

What is the KiCad net name for `indicators[0].anode`? For `oled.scl`? For
`global.gnd`? Nothing in either document says. This matters more than it looks:

- It is **user-visible forever** — in pcbnew, in DRC reports, in the netclass
  patterns of H8, in every layout conversation.
- `/` is KiCad's hierarchy separator. `#`-leading names are special (B9).
- Global rails should emit as `GND`, `+5V`, `+3V3` so KiCad's conventions,
  power-symbol expectations, and default netclass patterns line up.
- It must be **stable**, or every re-run churns the netlist and defeats the
  stability requirement (§11).

**Recommendation:** Specify it, with: separator (`/` to match KiCad hierarchy,
or `_` for flat readability — pick one), array index rendering (`anode[0]` vs
`anode_0`), the charset escape rule, and an `export_name` override on net
declarations. Have globals default to KiCad-conventional spellings. Then add a
collision check, because `a_b` and `a.b` can both flatten to `a_b`.

---

### H11. ERC configurations cannot reach connectors inside blocks *(rev 2)*

An ERC configuration selects among a connector instance's permitted roles, and
configurations are board-level because they describe uses of the board. But a
connector inside a reusable block is private: "Callers cannot access internal
instances or nets." That means a board-level configuration cannot select roles
for it.

This is not hypothetical on the reference board. Once `UsbC_Pending` is
implemented, the USB-C receptacle will sit inside it. "USB-powered versus
header-powered" is a choice about exactly *that* connector's VBUS role.

The same mechanism has two more gaps:
- In the example's syntax, an instance pin role is scalar
  (`role: role.ground`). "Multiple permitted roles" needs a set-valued form,
  and none exists.
- Expansion pins 3 and 4, the motivating case, are left as a comment for this
  very reason.

**Recommendation:** Make a role choice part of a block's *interface*, in the
same way as a parameter:

```text
block UsbCPower:
    erc_modes:
        vbus_source: [role.power_output, role.no_connect]
    inst conn: UsbC_Receptacle:
        metadata:
            pins:
                A4: { external: mode.vbus_source }

board STM32H523Reference:
    erc_config usb_powered:
        usb_c.vbus_source:  role.power_output
        expansion.pins.3:   role.power_input
```

The board binds mode names that blocks expose, which respects privacy the same
way parameters do, and it binds pins of connectors in its own scope. If a
configuration leaves a mode unbound, ERC reports "insufficient information",
which is the behaviour the spec already describes. The set of configurations
becomes enumerable, so CI can run every declared one.

### H12. Footprint names and pin↔pad agreement cannot be checked at compile time *(rev 2)*

Two decisions interact. First, compilation reads only NoScheme definitions
("does not implicitly load or convert KiCad components"). Second, footprints are
referenced by KiCad library name (`"Resistor_SMD:R_0603_1608Metric"`). Together
they mean the compiler **cannot know**:

1. **Whether the named footprint exists.** "Missing footprint" can only mean
   *unspecified*. A misspelled name compiles cleanly and fails at pcbnew import,
   outside NoScheme's diagnostics.
2. **Whether the footprint's pads match the component's pins.** This is the one
   that matters. Suppose a component has pins `1`–`5` but the footprint is a
   3-pad SOT-23, or the part calls its exposed pad `EP` where the footprint
   calls it `9`, or a USB-C footprint has `S1` shield pads the component never
   declared. Each of these imports into pcbnew with nets that have nowhere to
   land, or with copper silently left unconnected. pcbnew logs warnings in an
   import dialog, which most users dismiss.

Pin/pad mismatch is among the commonest ways a board with correct connectivity
comes back from fab wrong. It is exactly the error a compiler is best placed to
catch, because the compiler alone holds the complete pin model. The
package/pad-map design from B5/B7 is where this check belongs.

**Recommendation:**
- Define "missing footprint" precisely as *unspecified*, and say that name
  validity is checked elsewhere.
- Have the library converter **snapshot each footprint's pad list** into the
  generated definition (`package LQFP100: pads_available: [...]`, plus a
  content hash). The converter reads the footprint library anyway.
  Compilation stays pure, and the compiler can then:
  - error when a pin maps to a pad that is not in the footprint;
  - warn when a footprint pad has no pin, with a per-package allowlist for
    `MP*` and `S*` pads;
  - warn when a snapshot is stale.
- Add an explicit `nosch check --kicad-libs` command that validates footprint
  names and refreshes snapshots. Like library conversion, it is a deliberate
  step, so it does not break the purity rule.
- For hand-written components without a snapshot, report "pad map unverified"
  once per component, in the summary, the same way as `todo`s.

---

## 6. Medium Findings

**M1. No pin ranges or array slices → about 25% of the example is boilerplate.**
`Header2x10` spends ~40 lines declaring 20 identical pins; the expansion header
spends 20 lines on `expansion_gpio[0]`..`[9]` and 20 more on identical roles.
This is a language that claims concision as a goal. Range syntax is pure
sugar — no generation, no loops, no new semantics:
```text
pins:
    1..20: { unused: unused.explicit }        # definition
connections:
    11..20: expansion_gpio[0..9]              # elementwise, shapes must match
```
Highest concision-per-unit-of-work item in this review. A converted 100-pin MCU
definition is unreadable without it.

**M2. Block ports cannot declare direction, so block interfaces can't be checked
locally.** `drive: nettype.digital` on `Indicator` says nothing about who drives.
`PROJECT.md` asserts "Block-port roles are interface constraints, not additional
drivers or loads" — but the example shows no syntax for such a role, so the
constraint is unimplementable. Either add port roles (`drive: nettype.digital
expects role.digital_output`) or drop the claim and state plainly that interface
checking happens post-flatten. The latter is a legitimate v1 choice; the current
middle position is not.

**M3. Blocks depend on globals invisibly.** `Indicator` references `global.gnd`
in its body. If the board omits `global net gnd`, `PROJECT.md` correctly makes it
an error — but the error surfaces *inside* the block, pointing at a line the
caller may not own, in a library they did not write. Let blocks declare their
requirements so the diagnostic lands at the interface:
```text
block Indicator:
    requires global gnd: nettype.ground
```
This also makes a block's full dependency surface readable from its header.

*Rev 2: globals are also a naming contract between libraries.* A library block
that uses `global.gnd` composes only with a board that spells its ground `gnd`.
A vendor library written against `global.GND` or `global.vss` cannot be used
without editing it. Renaming nets inside a library is exactly the kind of
coupling hierarchy exists to remove. The example already shows two idioms for
the same job: every `*_Pending` block takes `ground` as a **port**, while
`Indicator` reaches for `global.gnd`. Recommendation: in reusable blocks, power
and ground are ports, as in the pending-block style. `global` is a board-level
convenience, and `requires global` is the escape hatch for blocks that really
are board-specific. Call-site remapping (`globals: { gnd: global.vss }`) would
also work, but I would keep it out of v1.

**M4. UART-shaped bundles need a documented answer.** Role-free bundles (a real
strength, §2) work beautifully for I²C and SPI, where fields are symmetric. They
do not answer `bundle UART: tx, rx` — the classic case where one side's `tx` is
the other's `rx`. Your options are a naming convention (`a_to_b`/`b_to_a`),
per-field binding at the call site, or a future `reversed` qualifier. Pick one
and document it, because a user will hit this in week one and whatever they
invent will become the de facto answer.

**M5. One alias per pin is not enough.** `alias: cathode` is singular. Real pins
carry several names: the KiCad library name, the datasheet name, the functional
name, the net-ish name (`PA9` / `USART1_TX`). `PROJECT.md` anticipates this —
"Imported KiCad pin names should also be available when unambiguous. Name
collisions and ambiguous imported names need explicit handling" — but a single
`alias` field cannot express it. Use `aliases: [cathode, K, "-"]`, and spec the
collision rule (I suggest: aliases live in one namespace per component;
duplicates are a definition-time error; the converter prefixes on collision and
warns).

**M6. The example shadows a net with a pin alias.** In `Indicator`, `net anode`
and the LED's `anode` pin alias produce `anode: anode`. It resolves correctly
(LHS is pin namespace, RHS net namespace) but it is exactly the line a reader
will misread, and it is a free lint rule. Add it, and fix the example.

**M7. The example exercises ~40% of the described feature set.** Absent
entirely: waivers (5 mentions in `PROJECT.md`), ERC configurations (9 mentions,
present only as a comment block), current-budget annotations (3), identity
strings (2), the `unused` connection marker (1), net classes (a core
requirement), imports beyond the single prelude line, and multi-file module
resolution. The features with no example are precisely the ones whose syntax is
still "open" — which is not a coincidence, and is a decent prioritization signal.

**M8. No project configuration format.** "A project configuration file
identifies the top-level board, source directories, library paths, and output
directory. Configuration syntax and module resolution rules remain to be
designed." Recommend TOML (`noscheme.toml`), with explicit search paths, **no**
implicit CWD on the path, and a `noscheme_version` field for forward
compatibility. Module resolution: explicit paths relative to declared roots,
first match wins, shadowing is an error rather than a silent override.

**M9. No human-readable review artifact.** The honest tradeoff of abandoning
schematics is that schematics are genuinely good at one thing: *review*. A
reviewer cannot diff a board by reading `.nosch` alone any more than they can by
reading EDIF. You do not need to generate schematics to fix this — a generated
net table, a per-block Graphviz/SVG connectivity graph, and a BOM are cheap,
diffable, and arguably better for review than a sheet. Also: generate a **diff
report** between the last export and the current one; `PROJECT.md` requires that
"Removed parts and changed connections must be presented through an update
workflow," and this is that workflow's artifact.

**M10. Required prelude import is friction with no benefit.**
`from noscheme import role, nettype, unused, f` is mandatory boilerplate in
every file. The justification — "This boilerplate leaves room for future
evolution while maintaining backward compatibility" — does not hold up, because
`PROJECT.md` *already* requires explicit namespace qualification (`role.passive`,
never bare `passive`). Collisions are therefore already impossible. The import
protects against nothing and every file pays. Make the prelude implicit, allow a
user import to shadow it, and add `--no-implicit-prelude` later if a real
conflict ever appears. Also: `f` is a poor name for a global namespace; `fmt` or
`unit` costs two characters and reads.

**M11. Refdes edits in KiCad will silently break identity.** Identity defaults
to the hierarchical path, with an explicit override available. But if a user
re-annotates in KiCad (or renames a part by hand), the link breaks and the next
export sees a new part — losing placement. Mitigation: write the NoScheme
identity into a KiCad footprint **property/field** on export, and match on that
field first, refdes second. This survives re-annotation and costs one field.

**M12. Specialization is single-inheritance with no stated conflict rules.**
`Resistor` → `Resistor0603` → `LedResistor` is three levels deep in the example.
Spec the lookup: linear chain, child overrides parent, metadata merges
key-by-key (or replaces wholesale — say which, because `metadata.pins` makes the
difference observable), and `params` defaults override. State whether a child
may *remove* a default. State the max depth or that there is none, and detect
cycles.

**M13. One board per project blocks panels and multi-board products.** Fine for
v1; say so explicitly so the project-file format reserves room (e.g. a
`[[board]]` array rather than a scalar `board = `).

**M14. No DNP / assembly-variant concept.** Out of v1 scope is defensible, but
`custom` metadata already carries MPNs, so BOM-adjacent data is arriving anyway.
Spec where `dnp` will live before users invent three conventions in `custom`.

**M15. Block comments: decide nesting now.** "Whether block comments may nest
remains open." Non-nesting is the C answer and surprises people who comment out
a region containing a comment; nesting is strictly more useful and costs three
lines of lexer. Recommend nesting, and note it in the tree-sitter grammar too.

**M16. Waivers reference checks, but checks have no identifiers.** "Users may
waive a specific electrical check on a specific net, with a required reason."
A waiver needs a stable name for the thing it waives. Assign every diagnostic a
stable code (`NS-ERC-0012`) **and** a stable slug (`missing-driver`) at the same
time you write the role table (H1); let waivers use the slug. Retrofitting codes
after users have written waivers against ad-hoc strings is painful.

---

**M17. `.format()`-style strings are a known injection surface** *(rev 2)*.
`PROJECT.md` specifies "Python `.format()`-style formatting" and says this "does
not imply arbitrary Python execution." That is true for execution. But if this
is implemented by calling `str.format` on user-supplied strings, the field
syntax permits attribute and index traversal. `"{p.__class__.__mro__}"` walks
the object graph and exposes internals, and template engines have a long
history of exactly this bug. It starts to matter once libraries are shared
between people. Implement the chosen subset as your own small formatter, or as a
`string.Formatter` subclass whose `get_field` rejects `.` and `[`. Allow only
named lookups into the parameter dictionary and a fixed list of format specs.

**M18. Number semantics are undefined, and Python's defaults are wrong for this use** *(rev 2)*.
The spec allows `+ - * /` and requires that "dimensions and indices must evaluate
to integers", but never says what a number *is*. With Python defaults, `/`
returns a float, so `n/2` used as an array size is either an error for odd `n`
or silently truncated, depending on the implementation. And
`f.kOhm(0.1 + 0.2)` renders as `0.30000000000000004kOhm`. Recommendation:
numbers are `Decimal`, which matches the exact decimal literals engineers
write. `/` is exact division. A non-integer used as a size or an index is an
error that shows the offending value. If floor division is needed, add `//`.
Render with the decimal's normalized form. String-valued part values cause a
related problem that strengthens H4: `f.nF(100)` and `f.uF(0.1)` are the same
part but different strings, and KiCad's BOM groups parts by the Value field.

---

## 7. Spec ↔ Example Consistency Audit

Mechanical diff of the two documents. Every row is a decision made in one place
and not the other.

| # | `PROJECT.md` says | Example does | Finding |
|---|---|---|---|
| 1 | Pin numbers "represented as strings" | bare `1:` tokens | B5 |
| 2 | `role.{passive,ground,open_drain}` | +4 more roles | H1 |
| 3 | `nettype.{digital,power}` named | 5 used | H1 |
| 4 | Recognized metadata is validated, unknown = error | `ref_prefix` ×4, undocumented | H7 |
| 5 | "configuration" for ERC variants | comment says "profile" | H7 |
| 6 | Instance sections in parens | parens + significant indentation | B1 |
| 7 | Params are declarations | 3 syntaxes for decl/default/bind | B2 |
| 8 | 3 array connection rules | uses an undefined 4th | B4 |
| 9 | Placeholder ≠ mandatory-connection satisfied | 20 latent errors + 11 endpoint-free nets | B8 |
| 10 | `never` is unoverridable | applied to all 24 connector pins | H3 |
| 11 | Net classes are a core requirement | zero occurrences | H8 |
| 12 | Waivers (5 mentions) | zero occurrences | M7 |
| 13 | ERC configs (9 mentions) | comment block only | M7 |
| 14 | Current budgets, identity strings | zero occurrences | M7 |
| 15 | Blocks may be specialized | only components use `extends` | M12 |
| 16 | `#` legal anywhere in identifiers | only used trailing | B9 |
| 17 | — | `global net gnd: nettype.passive` | H2 (no ground category) |
| 18 | "Only the top-level board may declare global nets" | `Indicator` *uses* `global.gnd` with no declaration of the dependency | M3 |
| 19 | Pending blocks take `ground` as a **port** | `Indicator` reaches for `global.gnd` | M3 (two idioms in one file) |
| 20 | Port binding "does not merge unrelated declared nets" | Any block with two ports on one caller net must merge them | B10 |
| 21 | Bundles connect "in a single statement", or "explicitly by field" | No such statement exists; the left-hand side is only ever a pin or a whole port | B11 |

---

## 8. Proposed Grammar Skeleton

Offered as a strawman to make the B-findings concrete, not as a finished design.
It assumes: B1 resolved toward uniform indented suites, B2 toward `param`
keyword, B5 toward `IDENT | INT | STRING` pin keys, B9 toward trailing `#` only.

```ebnf
file            ::= (import_stmt | definition)*
import_stmt     ::= "from" module_path "import" ident ("," ident)* NEWLINE
definition      ::= bundle_def | component_def | block_def | board_def

bundle_def      ::= "bundle" IDENT ":" INDENT field_decl+ DEDENT
field_decl      ::= IDENT ":" net_type NEWLINE

component_def   ::= "component" IDENT extends? ":" INDENT comp_section+ DEDENT
extends         ::= "extends" IDENT
comp_section    ::= params_sect | pins_sect | package_sect | metadata_sect | defaults_sect

params_sect     ::= "params" ":" INDENT param_decl+ DEDENT
param_decl      ::= "param" IDENT ":" type_name ("=" expr)? NEWLINE
type_name       ::= "string" | "number" | "quantity"          /* reserved words */
defaults_sect   ::= "defaults" ":" INDENT (IDENT ":" expr NEWLINE)+ DEDENT

pins_sect       ::= "pins" ":" INDENT pin_decl+ DEDENT
pin_decl        ::= pin_key_set ":" (attr_inline | attr_suite)
pin_key         ::= IDENT | INT | STRING          /* B5; a rule, not a token (rev 2) */
pin_key_set     ::= pin_key | pin_key ".." pin_key | "[" pin_key ("," pin_key)* "]"

package_sect    ::= "package" IDENT ":" INDENT pad_map_sect metadata_sect? DEDENT
pad_map_sect    ::= "pads" ":" INDENT (IDENT ":" pad_list NEWLINE)+ DEDENT
pad_list        ::= pin_key | "[" pin_key ("," pin_key)* "]"      /* B7 */

block_def       ::= "block" IDENT extends? ":" INDENT block_item+ DEDENT
block_item      ::= params_sect | ports_sect | requires_stmt | net_decl
                  | inst_decl | metadata_sect
ports_sect      ::= "ports" ":" INDENT port_decl+ DEDENT
port_decl       ::= IDENT ":" net_type (attr_suite)?             /* todo:, netclass: */
requires_stmt   ::= "requires" "global" IDENT ":" net_type NEWLINE   /* M3 */

board_def       ::= "board" IDENT ":" INDENT board_item+ DEDENT
board_item      ::= global_net_decl | net_decl | inst_decl | erc_config | metadata_sect
global_net_decl ::= "global" "net" IDENT ":" net_type attr_suite? NEWLINE

net_decl        ::= "net" IDENT ":" net_type attr_suite? NEWLINE
net_type        ::= (ns_ref | IDENT) array_suffix*               /* B3 */
array_suffix    ::= "[" expr "]"

inst_decl       ::= "inst" IDENT array_suffix* ":" IDENT ":" INDENT inst_section+ DEDENT
inst_section    ::= conn_sect | args_sect | metadata_sect
conn_sect       ::= "connections" ":" INDENT conn_entry+ DEDENT
conn_entry      ::= (pin_key_set | port_path) ":" conn_target NEWLINE   /* B11 */
port_path       ::= IDENT ("." IDENT | index)+
conn_target     ::= "no_connect" | net_ref
net_ref         ::= ("global" "." )? IDENT (("." IDENT) | index)*
index           ::= "[" expr "]" | "[" expr ".." expr "]"

expr            ::= literal | ns_call | net_ref_expr | expr binop expr | "(" expr ")"
literal         ::= STRING | INT | DECIMAL | QUANTITY                   /* H4 */
ns_call         ::= ns_ref "(" expr ("," expr)* ")"
ns_ref          ::= IDENT "." IDENT

IDENT           ::= /[A-Za-z_][A-Za-z0-9_]*#?/                   /* B9 */
QUANTITY        ::= /[0-9]+(\.[0-9]+)?[pnumkKMG]?[A-Za-z]+/      /* H4 */
```

Notes:

- `attr_suite` is the indented `key: value` block the example already uses under
  ports and pins. `attr_inline` is the `{ k: v, k: v }` form, useful for M1's
  range declarations. Supporting both is a small amount of grammar for a large
  amount of readability on wide parts.
- `inst` loses its parentheses, making every construct in the language a
  `header:` + indented suite. One indentation mechanism, stock lexer.
- `requires global` makes a block's dependency surface local (M3).
- `package` is the B5/B7 fix and is the piece I would most want to prototype
  before committing, since it touches the converter, the IR, and the exporter.
- *Rev 2: lexer hygiene.* `INT`, `DECIMAL` and `QUANTITY` overlap in their
  prefixes, so tokenization must rely on longest match and the token order
  must be stated. `1A` lexes as a quantity (one ampere), which is why pin keys
  like it must be quoted. Ranges such as `1..20` work only if `DECIMAL` requires
  a digit after the `.`, so `1.` must not be a legal literal. Write corpus tests
  for each of these. With Lark, prefer the contextual lexer, which removes most
  of these collisions.
- The left-hand `port_path` in `conn_entry` is the B11 decision written into the
  grammar. It is how cross-type bundles connect field by field.

---

## 9. KiCad Export: Recommendation

This is B6, answered. Three viable paths:

### Option A — Emit a KiCad netlist, import into pcbnew *(recommended for v1)*

Emit KiCad's s-expression netlist (`(export (version ...) (components ...)
(libparts ...) (nets ...))`) and use pcbnew's netlist import. Matching is by
reference designator and/or UUID path, selectable in the dialog, with options
controlling deletion of extra footprints and nets.

- **Pro:** By far the smallest backend. One file format, well documented, and
  the format is stable across KiCad versions. Placement and routing are
  preserved by construction, because pcbnew is *updating* an existing board.
- **Pro:** It is the correct conceptual match. A netlist is exactly what NoScheme
  produces, so there is no lossy intermediate representation.
- **Con:** No schematic, so no cross-probing, and schematic-driven BOM tooling
  does not work. Mitigate with M9's generated review artifacts.
- **Con:** Net classes need separate `.kicad_pro` emission (H8).
- **Con:** Verify the netlist-import path still exists and behaves in 10.0 —
  recent KiCad versions have pushed hard toward schematic-driven updates, and
  this is the one assumption in this recommendation I would spike first.
- **Con (rev 2):** A board with no schematic works against KiCad's
  schematic-centric defaults. DRC's schematic-parity test may flag every
  footprint or may be skipped, so find out which. In a project that *does*
  contain a `.kicad_sch`, even an empty one, a single accidental
  *Update PCB from Schematic* can propose deleting every footprint. The exporter
  should write a project with no schematic file and with parity checking
  configured accordingly. Phase 0 must exercise both cases.

### Option B — Generate `.kicad_sch`, use the normal schematic→PCB flow

- **Pro:** Full KiCad integration — cross-probe, ERC, BOM, netclass directives.
- **Con:** You must generate *geometry*. Symbol placement, wire routing, sheet
  layout. For a 100-pin MCU this is an auto-layout problem, and the output will
  be ugly enough that no one reviews it — so you pay the full cost of schematics
  while getting none of their review benefit.
- **Con:** `.kicad_sch` is a much larger surface area and changes more between
  KiCad versions than the netlist format.
- **Verdict:** This is the "create schematics for KiCad's benefit" path
  `PROJECT.md` floats. I would not do it in v1. Revisit only if a concrete
  workflow need appears that A cannot serve.

### Option C — Drive pcbnew via its Python API

- **Pro:** Total control over matching and incremental update.
- **Con:** Couples you to a specific KiCad build's Python bindings, which are
  the least stable interface KiCad exposes. Hard to test in CI.
- **Verdict:** No.

### Recommended v1 architecture

```
.nosch sources
      ↓  parse → AST → resolve → elaborate
  Circuit IR  (JSON-serializable; the stable contract)
      ↓
  ├─→ design.net          KiCad netlist               (Option A)
  ├─→ design.kicad_pro    net_settings / netclasses    (H8)
  ├─→ design.refdes.lock  refdes + identity map        (§11)
  ├─→ erc.json / erc.txt  diagnostics, per config
  └─→ review/             net tables, block graphs, BOM, diff (M9)
```

Make the **Circuit IR the contract**, not the netlist. It is what you golden-test
against (§12), what a future Python API produces, and what a future
Eagle/Altium/JITX backend consumes. The KiCad specifics then live in one
replaceable emitter.

**Identity:** derive a deterministic UUIDv5 from the hierarchical instance path
(or the explicit identity string when given), namespaced per project. Emit it as
the netlist `tstamps`/UUID **and** as a footprint property (M11). Then matching
works by UUID with refdes as fallback, and survives both renames in NoScheme and
re-annotation in KiCad.

---

## 10. Net Naming and Netclass Export

Fills the H8/H9/H10 gap. Concrete proposal:

1. **Separator:** `/` for hierarchy, matching KiCad's own convention, so
   `indicators[0].anode` → `/indicators[0]/anode`. KiCad tolerates brackets in
   net names; verify against 10.0 and fall back to `indicators_0` if not.
2. **Globals** emit unqualified and KiCad-conventional: `gnd` → `GND`,
   `v5` → `+5V`, `v3v3` → `+3V3`. Overridable per net.
3. **`export_name`** metadata on any net declaration wins over all of the above.
4. **Bundle fields** flatten as `<net>/<field>`: `oled.scl` → `/oled/SCL`.
5. **Diff pairs** are the exception and must bypass the scheme: a net or bundle
   marked `diffpair: "USB"` emits `/USB_P` and `/USB_N` so pcbnew recognizes the
   pair. Error if a marked net's partner is absent (H9).
6. **Collision check** after flattening — distinct IR nets mapping to one
   exported name is an error, not a merge. This is mandatory given rules 1–5 can
   collide (`a_b` vs `a.b`).
7. **Netclasses** emit into `.kicad_pro` `net_settings` as explicit net lists
   rather than patterns, since NoScheme knows the exact membership and patterns
   would be a lossy re-derivation. Preserve any user-added classes not mentioned
   by NoScheme — do not clobber the file.

Write these rules into the spec. They are user-visible forever and very annoying
to change after the first board is laid out.

---

## 11. Determinism and Reference Designators

`PROJECT.md` is right to demand stability:

> "whatever output comes from our tool to be ingested by KiCad should be stable such that when the tool is re-run, elements corresponding to part of the input that have not changed will also not change. For example, refdes will not change arbitrarily."

A path-sorted assignment does **not** achieve this. Add `indicators[1]` and
every later `R`/`D` renumbers — which is precisely the churn the requirement
forbids, and it invalidates placement notes, assembly drawings, and review
comments.

**Recommendation:** Persist the map, and check it in.

```toml
# design.refdes.lock — generated; commit this file
[instances]
"indicators[0].resistor" = { refdes = "R1", uuid = "..." }
"indicators[0].led"      = { refdes = "D1", uuid = "..." }
"oled_scl_pullup"        = { refdes = "R4", uuid = "..." }
```

Rules:
1. An identity present in the lock keeps its refdes, always.
2. New instances get the lowest unused number for their prefix.
3. Removed instances are retained as tombstones (so their numbers are not
   immediately recycled into a different part), with `nosch refdes --compact` as
   an explicit, reviewable operation.
4. The lock file is an output but a **committed** one, and a conflict in it is a
   real review event.

Also enforce determinism generally: every emitter iterates sorted collections;
no `set`/`dict` iteration order leaks into output; byte-identical output for
identical input. Make this a CI test (§12), because it regresses silently.

---

## 12. Testing Strategy

Not mentioned in `PROJECT.md`. For a compiler, the test strategy *is* the design
document for the IR, so this is a real gap.

1. **Parser corpus.** Every construct, each as a tiny `.nosch` file plus its
   expected AST (JSON golden). Include negative cases with expected diagnostic
   codes **and spans** — span regressions are the most common silent quality
   loss in a compiler.
2. **Elaboration goldens.** `.nosch` → Circuit IR JSON. This is where array
   shapes (B3/B4), bundle flattening, hierarchy, and specialization get pinned.
   Highest value per test in the whole suite.
3. **ERC table tests.** One case per cell of the role/nettype matrix (H1).
   Generate the skeletons from the table so a new role cannot be added without
   its tests.
4. **Export goldens.** Circuit IR → `.net` text, byte-exact.
5. **Stability tests.** Compile, mutate (add a part, rename a net, reorder
   declarations), recompile; assert refdes and UUIDs for *untouched* instances
   are byte-identical. Assert that declaration reordering changes **nothing**,
   which is the executable form of the order-independence promise.
6. **KiCad round trip.** The reference board, imported into a scratch
   `.kicad_pcb`, re-imported after a mutation, asserting placement survives.
   Use `kicad-cli` so it runs headless in CI.
7. **Example as conformance corpus.** `examples/stm32h523.nosch` should compile
   in CI from the moment the parser exists, with its expected diagnostics
   recorded. It is already your best spec; make it executable.

---

## 13. Implementation Choices

**Parser.** `PROJECT.md` requires grammar-driven generation and leaves the
library open. Given Python + indentation:

- **Lark** — best fit. Earley for development, LALR for speed once the grammar
  is unambiguous; good error reporting; `postlex` hook for the indenter. Its
  stock `Indenter` will *not* do B1's paren-internal indentation, which is one
  more reason to resolve B1 toward uniform suites.
- **tree-sitter** — I would write this grammar *too*, and early. It gives you
  vim/neovim highlighting (an explicit `PROJECT.md` deliverable), plus Helix,
  Zed, and GitHub highlighting for free. Its external-scanner mechanism is the
  standard, well-trodden solution for indentation-sensitive languages. Compare
  with hand-writing a vim syntax file, which gets you one editor and no parse
  tree.
- ANTLR4 (heavy, JVM toolchain), parsimonious (no indentation story), textX
  (weaker error reporting) — all worse fits here.

Two grammars means a drift risk; manage it with a shared corpus (§12.1) that
both must accept, run in CI. In practice this is a smaller cost than maintaining
a hand-written highlighter, and you get a real parse tree for the LSP.

**LSP.** `pygls`. Reuse the Lark parse tree; incremental reparse at
top-level-definition granularity is almost certainly sufficient for files of
this size, so do not build fine-grained incrementality on spec.

**KiCad file I/O.** `kiutils` or `sexpdata` for reading converted libraries;
`kicad-cli` for CI validation. Do not hand-roll an s-expression parser.

**Diagnostics.** Every AST node carries a span; every diagnostic carries a
stable code and slug (M16); render with caret/underline. Budget real time for
this — "reasonable precise error messages" is listed as a goal in `PROJECT.md`
and it is the single thing that most determines whether people tolerate a new
language.

---

## 14. Prior Art Worth Mining

`PROJECT.md` names atopile and Flux.ai. The most directly relevant project is
missing:

- **SKiDL** (Python → KiCad netlist). The closest existing thing to NoScheme's
  backend, and it has already solved — or documented the pain of — netlist
  emission, refdes stability, and pin-name ambiguity in converted libraries.
  Read its exporter before writing yours.
- **atopile** (`.ato`). The closest existing thing to the *front end*. Study
  specifically: their module/interface model (your blocks/bundles), their KiCad
  backend, and how they handle the package/pin-mapping problem (B5). Also worth
  knowing *why* they landed on their current connection syntax — likely the same
  forces as B1.
- **tscircuit** (React/TS) — interesting for its footprint/package DSL, which is
  the part of the problem you are deferring.
- **JITX** — the most ambitious take on parametric/solver-driven design; worth
  understanding as a sketch of where generation could go after v1.
- **PHDL** — an older academic PCB HDL; its retrospective on why it did not
  catch on is directly relevant.
- **Zener**, **Circuit-Synth** — recent entrants, useful for seeing which
  decisions are converging across independent attempts.

The convergent signal across all of them: **the library/footprint problem, not
the language, is what determines adoption.** Your explicit-conversion decision
is good; invest more in the converter than feels proportionate.

---

## 15. Document Process Recommendation

`PROJECT.md` is doing four incompatible jobs at once: vision, requirements,
specification, and decision log. That is why it has 29 open questions and why
the example could drift from it in 21 places without anyone noticing. Split it:

1. **`README.md`** — vision and motivation. The opening three paragraphs of
   `PROJECT.md` are genuinely good; keep them and stop.
2. **`SPEC.md`** — the language, normative, with the grammar as the source of
   truth and **no open questions**. If something is undecided it is not in the
   spec.
3. **`decisions/`** — one ADR per resolved question: context, options,
   decision, consequences. The 29 open items become the initial backlog, and
   each one gets closed by an ADR rather than by a sentence edit. This is the
   artifact you are actually missing, and it is why the role vocabulary could
   grow by four members without a decision being made.
4. **`ROADMAP.md`** — milestones (§16).
5. **`examples/`** — grown into the conformance corpus, with expected output
   checked in.

The mechanical test for whether this worked: `SPEC.md` contains zero instances
of "remains open."

---

## 16. Suggested Sequencing

Ordered so that each step de-risks the next, and so the expensive-to-change
decisions happen before anything is built on them.

**Phase 0 — De-risk the backend (days, not weeks).**
Hand-write a KiCad netlist for a 5-part circuit. Import into pcbnew 10.0. Place
and route it. Modify the netlist (add a part, change a net, delete a part).
Re-import. Confirm placement and routing survive and that identity matching
behaves as Option A assumes. *(Rev 2:)* Also run DRC with schematic parity
enabled, try the accidental *Update PCB from Schematic* path, and record how
pcbnew handles a misspelled footprint name and a pin/pad mismatch (H12). **If this fails, the entire v1 plan changes**, so it
must come first. Nothing else in this list is worth starting until it passes.

**Phase 1 — Close the blocking decisions (B1–B9).**
ADRs for: block syntax, parameter declaration, type algebra, connection shape
rules, pin/package model, export mechanism, multi-pad pins, placeholder/todo
semantics, identifier charset. Plus the H1 role table, since ERC cannot be
designed without it. Then rewrite `examples/stm32h523.nosch` against the
decisions — it is your best test of whether they hold up, and rewriting it is
cheap now and expensive once a parser exists.

**Phase 2 — Parser and AST.**
Lark grammar, indenter, spans, diagnostic infrastructure, parser corpus. Gate:
the rewritten example parses, and the negative corpus produces correct codes and
spans.

**Phase 3 — Elaboration and Circuit IR.**
Name resolution, imports, specialization, hierarchy flattening, array expansion,
bundle flattening, net merging, identity assignment. Gate: IR goldens, including
the full example, and the order-independence test.

**Phase 4 — ERC.**
Mandatory-connection, unconnected nets, driver conflicts, power conflicts, from
the role table. Waivers with codes. ERC configurations. Gate: the role-matrix
test suite, and the example's expected diagnostics.

**Phase 5 — KiCad export.**
Netlist, `.kicad_pro` netclasses, refdes lock, net naming, diff pairs, review
artifacts. Gate: the Phase 0 round-trip, automated.

**Phase 6 — The library converter.**
KiCad symbol → NoScheme component, with pin names, roles, and the package
mapping. This is where real-world adoption lives, and it is where the B5/B7
decisions get stress-tested against libraries you did not write. Budget more
than feels reasonable.

**Phase 7 — The actual reference board.**
Replace every `*_Pending` block with real parts: MCU pinout, USB-C with CC
resistors, LDO with its capacitors, microSD socket, decoupling, reset/boot,
crystal. Gate: a board that routes in KiCad. This is the milestone that proves
the language, and it is also where you will find the next twenty findings — so
do not schedule anything important after it.

**Phase 8 — Editor support.**
tree-sitter grammar, LSP diagnostics and completion, canonical formatter.

Note what this ordering implies: the reference board — the thing `PROJECT.md`
treats as the v1 deliverable — comes near the end, because every earlier phase
needs a *small* test case, not a real one. The current example is simultaneously
the spec's best artifact and too large to be the first thing that compiles. Keep
it as the conformance target; write a 10-line `blinky.nosch` as the thing Phase 2
actually gates on.

---

## 17. Open Questions, Ranked, With Recommended Defaults

If you want to move fast, adopt these and revisit later. Each is a real decision,
not a coin flip — but each is also reversible more cheaply than leaving it open.

| Priority | Question | Recommended default |
|---|---|---|
| 1 | Export mechanism | KiCad netlist → pcbnew import (Option A) |
| 2 | Instance syntax | Drop parens; uniform `:` + indented suite |
| 3 | Parameter declaration | `param name: type = default`; `defaults:` for specialization |
| 4 | Pin/package model | Logical pins + named `package` with pad lists |
| 5 | Pin designator lexing | `IDENT \| INT \| STRING` grammar rule, exact-string match, no numeric equivalence |
| 6 | Type algebra | `base array_suffix*`; bundles may hold arrays; 2-D max |
| 7 | Placeholder semantics | `todo: "reason"`, satisfies connection, summary-reported, `--strict` promotes |
| 8 | Identifier charset | `[A-Za-z_][A-Za-z0-9_]*#?` — trailing `#` only |
| 9 | Quantity type | Add now, no dimensional arithmetic |
| 10 | Role vocabulary | The 13-role table in H1 |
| 11 | Open-drain layer | Pin role only; derive the net conclusion |
| 12 | Ground net type | Add `nettype.ground` |
| 13 | Net naming | `/` hierarchy, KiCad-conventional globals, `export_name` override, collision check |
| 14 | Connector role frame | Distinct `external:` keyword |
| 15 | Unused marker | `no_connect`; keep `unused.*` for policy |
| 16 | Prelude | Implicit; user import shadows; rename `f` → `fmt` |
| 17 | Config format | `noscheme.toml`, explicit search paths, `noscheme_version` |
| 18 | Block comments | Nesting allowed |
| 19 | Parser library | Lark (compiler) + tree-sitter (editors), shared corpus |
| 20 | Diagnostic identity | Stable code + slug from day one |
| 21 | Ports sharing a caller net (B10) | Legal; flattens to one net; outermost name wins; `ports-shorted` lint |
| 22 | Net-to-net statement (B11) | None in v1; the left-hand side of a connection may be a port path |
| 23 | Configs vs. block privacy (H11) | Blocks expose `erc_modes`; boards bind them |
| 24 | Pad verification (H12) | Converter snapshots pad lists; compiler checks pins against pads |
| 25 | Number type (M18) | `Decimal`; exact `/`; non-integer size or index is an error |
| 26 | Instance-array broadcast (B4) | Scalar-only implicit; explicit `broadcast()`; per-element overrides allowed |

---

## 18. Closing

The strategic bet — that PCB design should be text, for the same reasons chip
design became text — is correct, and the quality of judgment in `PROJECT.md`'s
*semantic* decisions is high enough that this is worth building. The explicit-net
rule, the sealed block boundaries, the role-free bundles, and the ERC
configurations are all decisions I would defend.

What is missing is not insight. It is **closure**. Twenty-nine deferred
decisions, a syntax that no stock lexer can tokenize, and a type system with
three names for one concept are all the same problem wearing different clothes:
the document has been optimized for capturing possibilities rather than
eliminating them. The example is where that becomes visible, which makes it the
most useful thing in the repository.

Two things I would do this week, in this order:

1. **The Phase 0 netlist round-trip.** It is a few days of work and it is the
   only experiment that can invalidate the plan. Everything else is downstream
   of its answer.
2. **Write the grammar.** Not more prose — an actual `.lark` file. A grammar
   cannot contain "remains open," which is exactly why it is the right forcing
   function. The twenty-one inconsistencies in §7 were all invisible in prose and
   all obvious the moment the example was read against it; a grammar makes that
   check automatic.
