# Generation inside blocks (proposal)

Status: proposal for later consideration, not implemented. Written October 7, 2026.

## Motivation

The LRM (section 15) lists macros, loops, conditionals, and automatic part
generation as not implemented. Arrays, parameterized blocks, and element
overrides cover regular repetition. Still missing:

- **Variants and options:** choosing a regulator topology, including or omitting
  bulk capacitance, assembly options.
- **Irregular repetition:** element *i* of an array needs something computed from
  *i*: a value from a table, or a connection to `taps[i + 1]`.

Two text-level approaches were considered and set aside:

- **A Python preprocessor** (`;` line escapes and backtick interpolation). The
  file on disk would no longer be the program. Diagnostics, navigation, rename,
  completion, and the formatter all work on source text and would need source
  maps or a second code path. The language server would run arbitrary user Python
  on every edit, and outputs could depend on files, time, or randomness, which
  breaks analysis caching.
- **Generator scripts** (Python that writes `.nosch`, like the WREN extractor),
  run either by hand or automatically. These are exact for the tools but separate
  logic from the design.

This proposal puts generation in the language, limited to block internals, so the
source remains the program and every tool keeps working on it. The proposal
applies equally to reusable blocks and blocks selected as project roots. Example
syntax combines existing declarations with the proposed constructs; these
examples are not accepted by the current compiler.

## Ground rules

1. **Only internals vary.** A block's ports, port types, and parameters are fixed.
   Callers and ERC contracts see one interface regardless of which branch is
   built.
2. **Conditions are compile-time parameter expressions,** evaluated by the
   existing evaluator that handles defaults and dimensions. Nothing depends on
   nets, connectivity, or ERC results.
3. **All branches are checked; one is built.** Resolution checks names, types,
   and bindings in every branch, so an unselected variant cannot rot. Only the
   selected branch is elaborated and reaches ERC and export.
4. **No new kinds of value.** No user functions, recursion, or computed names.
   Existing resource limits (for example 10,000 expanded instances) still bound
   the output.

This reverses LRM §4.1's rule that parameters "do not select topology". The
reversal is limited to the internals of the block that declares the parameter.

## Construct 1: conditionals

`if` / `elif` / `else` are block body items with indented bodies, like sections.

```nosch
block Supply:
  params:
    string regulator = "ldo"
    bool bulk = true
  ports:
    net.power vin
    net.power vout
    net.ground gnd
  if regulator == "ldo":
    Ldo3v3 reg(input: vin, output: vout, ground: gnd)
  elif regulator == "buck":
    net.passive sw
    Buck reg(vin: vin, sw: sw, fb: vout, gnd: gnd)
    Inductor<4u7> l(sw, vout)
  if bulk:
    Capacitor<22u> c_bulk(vout, gnd)
```

A condition whose value is not a boolean, or which cannot be evaluated from
parameters, is an error. With no matching branch and no `else`, nothing is built.

## Construct 2: an element index for arrays

`as NAME` on an array instance binds the element index in that instance's
arguments and sections:

```nosch
block Ladder:
  params:
    list[resistance] values = [1k, 2k2, 4k7, 10k]
  ports:
    net.analog taps[5]
  Resistor<values[i]> r[4] as i(taps[i], taps[i + 1])
```

This covers rings, chains, rotations, and per-element values without a general
loop (for example `taps[(i + 1) % 4]`). Element overrides remain for hand-picked
exceptions. Multi-dimensional arrays bind one name per dimension:
`Part m[2][3] as (row, col)(...)`.

A general `for` statement is deliberately left out. Loops that declare nets and
instances under computed names make them impossible to find, rename, or give
stable CAD identities. Indexed arrays already give every element a predictable
path such as `r[2]`.

## Language changes required

- **Grammar:** `if`/`elif`/`else` as definition body items in blocks; `as NAME`
  (or a tuple of names) after an instance's dimensions.
- **Expressions:** comparison operators (`==`, `!=`, `<`, `<=`, `>`, `>=`),
  boolean operators (`and`, `or`, `not`), and `%`. The expression grammar
  currently has arithmetic and shifts, but no comparison or boolean operators.
- **Types:** a `bool` parameter type (`true`/`false` already exist as metadata
  literals) and list parameters (`list[resistance]`). Today public parameter types
  are `number`, `string`, and quantities.

## Semantics

- **Scope.** Declarations in all branches share the block's scope.
  - A name may be declared in several branches of the same `if` (for example
    `reg`), never twice in one branch or both inside and outside the `if`.
  - A name declared in only some branches is visible only inside those branches.
  - A reference from outside the `if` to a name declared inside it must be valid
    in every branch, or it is an error.
- **Element index.** The index name is visible only within that instance's
  arguments and sections, and it shadows nothing. Expressions that use it are
  evaluated per element, after dimensions are known.
- **Identity.** An instance's path does not include the branch: `supply.reg` is
  the same path in every variant, so switching variants reuses the KiCad identity
  and reference slot. Treat this as a decision to confirm: it is right when the
  branches occupy the same physical position.
- **ERC.** Runs on the elaborated circuit, so it is unchanged. ERC configurations
  (LRM §11) already select choices by name; a root-block parameter can drive a
  block's `if` the same way.

## Tooling impact

- **Navigation, references, rename:** unchanged in principle. Every declaration
  is in the source; a name declared in several branches groups like an imported
  alias with its original.
- **Hover:** can add "conditional on `regulator`" and show the branch selected
  for the selected target.
- **Formatter:** one new statement form and the `as` clause.
- **Diagnostics:** errors in unselected branches are still reported. Their wording
  should say the branch is not selected, so the user knows the error is latent.

## Open questions

- **Different types under one name.** May `reg` be an `Ldo3v3` in one branch and
  a `Buck` in another? This is fine if outsiders reach `reg` only through the
  block's ports. References like `reg.output` from outside the `if` would then
  need to be valid in every branch.
- **Identity across variants.** Is reusing the identity slot (see Semantics) the
  desired default, or should a variant switch be able to request a new identity?
- **Coverage.** Collect three or four real cases from rulos, the STM32 board, and
  Smart Sniffer to check what this handles and misses. Tables loaded from files,
  such as pinouts or datasheet extracts, are the case in-language generation
  cannot cover; that need would still argue for external generator scripts.

## Suggested implementation order

1. Expression additions (`bool`, comparisons, boolean operators, `%`) and list
   parameters, with evaluator and type-check tests.
2. `as` element indices: grammar, per-element evaluation, and tests for rings and
   value tables. This is self-contained and useful immediately.
3. `if`/`elif`/`else` in blocks: scope rules, checking all branches, elaborating
   one, identity tests, and diagnostics for unselected branches.
4. LSP hover for conditional declarations, formatter support, and LRM sections
   replacing the §4.1 topology rule and the §15 "not implemented" entry.
