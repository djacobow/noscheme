# NoScheme

NoScheme is a language and compiler for designing PCB circuits in text.
Write explicit parts and named connections in `.nosch` files, organize
circuits into reusable blocks, and export a netlist to KiCad for placement
and routing. It is designed as a replacement for `eeschema` and a source
of netlists for `pcbnew`.

The aim is to make circuit descriptions easy to edit, review, and version
alongside firmware. Arrays and reusable component definitions reduce
repetition. Electrical rule checks use declared pin roles and metadata to
find connection errors, driver conflicts, and current, pull-resistance,
or capacitance budget violations.

The compiler and language server are implemented in Python. Vim, Neovim, and the
local VS Code extension provide syntax highlighting and LSP integration for
diagnostics, definition navigation, references, rename, hover, completion, and
formatting while editing unsaved designs. `noscheme format` applies the same
canonical layout from the command line.

## History and Rationale

I started my career as a circuit designer in the semiconductor industry,
designing on-chip memories, which is a mixed analog-digital activity. For this
I used schematics, but as tools matured and more work became strictly digital,
I, and the whole industry moved largely towards hardware definition languages
such as iHDL (google it), VHDL, and Verilog.

Much later in my career, I found myself designing PCBs, and I was dismayed
to find that the state-of-the-art in PCBA design is still very much schematic
driven. This, I think, is a shame, because schematics are rather error prone
and, in particular, slow to work on, and hard to add arbitrary checks and
constraints to. Most CAD tools have ERC checks, but to get the full value of
them, you have to annotate nets and pins, and it's tedious in a gui.

Small analog circuits really are easiest to understand in a visual format,
but that comprises a smaller and smaller amount of what goes into PCB
design these days. Instead, we have complex, sprawling nests of digital
wiring and components. You can try to squeeze all those elements onto
one sheet, so you can trace wires, or you can spread them out into many
sheets using tags and named nets, but both ways are very hard for me
to trace.

Because of this, for more than a decade, I've had the idea to make a
text entry system for PCB design rattling around my head. Rather than
start with a general purpose HDL that supports simulation, sophisticated
generators, and other fancy features, I wanted a DSL that is really
tightly bound to the target : PCBs.  That's NoScheme. I don't expect
to convince many PCB people to this way of working, but I intend to
use NoScheme personally for as many projects as I can manage, and will
extend it as required when I run into limitations.  Because of that, the
language should be considered very much experimental, at least for the
time being.

## Status

This is an actively developed proof of concept. The 58-part STM32 reference
board compiles and its netlist has populated a KiCad board successfully.
Placement, routing, and hardware validation remain separate steps.

## Documentation

- [Language reference](docs/LRM.md) — normative syntax, scope, metadata, electrical rules,
  and a complete example.
- [Development](docs/DEVELOPMENT.md) — installation, tests, example projects,
  KiCad export, CI, and repository search.
- [Editor support](docs/EDITOR_SUPPORT.md) — Vim, Neovim, and VS Code setup and LSP capabilities.
- [Project design](docs/PROJECT.md) — goals, design decisions, and reference-board details.
- [Implementation plan](docs/PLAN.md) — completed milestones and remaining work.
- [KiCad experiments](docs/experiments/README.md) and
  [findings](docs/experiments/FINDINGS.md) — manual import/update checks and results.
- [Archived design review](docs/claude_review.md) — review of the design before
  implementation; includes proposals that were not adopted.

Runnable designs are in [examples/](examples/), including the
[STM32 board](examples/stm32h523/stm32h523.nosch) and the
[Rulos six-line display matrix](examples/rulos_6matrix/README.md), transcribed
from an existing schematic using nested block and component arrays.
The [WREN examples](examples/wren/README.md) include a readable 189-component
output subsystem and a generated 1,507-component compiler/LSP benchmark.

## License

NoScheme is free software, licensed under the
[GNU General Public License v3.0 or later](LICENSE.md). Designs you write in
NoScheme and the files it generates from them are your own; the GPL does not
apply to them.

The KiCad library snapshots under `examples/*/kicad_lib/` are derived from the
KiCad libraries and keep their CC-BY-SA 4.0 license; see the `LICENSE.md` in
each of those directories.

The [Rulos](examples/rulos_6matrix/README.md#attribution-and-sources) and
[WREN](examples/wren/README.md#attribution-and-provenance) examples also record
their upstream design attribution and source-license information.
