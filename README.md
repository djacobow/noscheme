# NoScheme

NoScheme is a language and compiler for designing PCB circuits in text. Write
explicit parts and named connections in `.nosch` files, organize circuits into
reusable blocks, and export a netlist to KiCad for placement and routing.

The aim is to make circuit descriptions easy to edit, review, and version alongside
firmware. Arrays and reusable component definitions reduce repetition. Electrical
rule checks use declared pin roles and metadata to find connection errors, driver
conflicts, and current, pull-resistance, or capacitance budget violations.

The compiler and language server are implemented in Python. Vim and Neovim have
syntax highlighting; LSP clients can provide diagnostics, definition navigation,
hover, and completion while editing unsaved designs.

## Status

This is an actively developed proof of concept. The 58-part STM32 reference board
compiles and its netlist has populated a KiCad board successfully. Placement,
routing, and hardware validation remain separate steps.

## Documentation

- [Language reference](docs/LRM.md) — syntax, scope, metadata, electrical rules,
  and a complete example.
- [Development](docs/DEVELOPMENT.md) — installation, tests, example projects,
  KiCad export, CI, and repository search.
- [Editor support](docs/EDITOR_SUPPORT.md) — Vim/Neovim highlighting and LSP setup.
- [Project design](docs/PROJECT.md) — goals, design decisions, and reference-board details.
- [Implementation plan](docs/PLAN.md) — completed milestones and remaining work.
- [KiCad experiments](docs/experiments/README.md) and
  [findings](docs/experiments/FINDINGS.md) — manual import/update checks and results.
- [Archived design review](docs/claude_review.md) — earlier review and discussion.

Runnable designs are in [examples/](examples/), including the
[STM32 board](examples/stm32h523/stm32h523.nosch) and the
[Rulos six-line display matrix](examples/rulos_6matrix/README.md), transcribed
from an existing schematic using nested block and component arrays.
