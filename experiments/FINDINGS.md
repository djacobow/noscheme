# Feasibility findings

Recorded 2026-10-03. The feasibility experiments are complete: the grammar parses
the reference draft, and manual KiCad import/update trials have been checked
against saved boards. The results support proceeding with the compiler and
KiCad netlist adapter, subject to the limitations below.

## Environment and evidence

- Python 3.14.4; KiCad CLI 10.0.6 and system PCBNew Python bindings installed.
- Lark 1.3.1, pytest 9.1.1, sexpdata 1.0.2 in `experiments/.venv`.
- Full dependency pins: `requirements.txt`.
- Automated result: **40 tests passed**, no skips.
- Test report: `output/tests.xml`.
- Parsed reference draft: `output/stm32h523.syntax.json`.
- Generated fixture versions, manifests, netlists, and footprint snapshots:
  `output/kicad/`.
- Empty-board DRC commands, JSON, stdout, and stderr:
  `output/drc-empty-verified/`.

The system PCBNew module prints three enum-choice assertions during import on
this installation. The empty-board creation completed successfully; this is a
local tooling observation, not a reason to depend on that API in the compiler.

## Parser: demonstrated

The complete STM32H523 draft parses under Lark LALR with contextual lexing,
including its significant indentation inside instance parentheses. No parentheses
were removed and no comma-based instance syntax was substituted.

The implementation distinguishes `_INST_LPAR`/`_INST_RPAR` from expression
parentheses. The contextual lexer selects the appropriate terminal. The reusable
Lark `Indenter` suppresses indentation only for expression parentheses and square
brackets, so multiline calls/lists coexist with indented instance sections.
The only override of indentation handling adds a location to a dedent error.

Comments are recognized by a separate small Lark lexer and replaced with
whitespace while retaining offsets and line breaks. This avoids phantom indents
on comment-only lines, preserves comment delimiters inside strings, and implements
non-nesting block comments. The parser never evaluates a formatting expression.

Tests cover the reference draft, ranges/slices, multidimensional selections,
qualified names, # names, numeric and quoted pins, aliases, defaults, helpers,
comments, blank lines, multiline expressions, empty instance sections, malformed
bindings, missing parentheses, unterminated strings/comments, and indentation
errors. Numeric spelling such as `01` survives in the source-located tree.

**Limits:** this is a concrete syntax experiment with generic metadata fields.
It does not resolve names, verify schemas/shape compatibility, evaluate expressions,
flatten circuits, or run ERC. The proposed grammar uses eight-column tabs and a
specific arithmetic precedence for the experiment; neither settles the open
language policy. The approved numeric semantics still need an evaluator.
Formal grammar choices not exercised here remain in PROJECT.md's backlog.

**Recommendation:** use Lark LALR/contextual as the production front-end starting
point and retain the chosen parenthesized syntax. Grow this corpus while replacing
the generic syntax tree with a typed AST. No handwritten lexer/parser is needed
for the demonstrated subset.

Reference: [Lark's configurable indentation example](https://lark-parser.readthedocs.io/en/stable/examples/indented_tree.html).

## KiCad: import and update behavior verified

The user imported `baseline.net` using unique-ID matching. The load/test report
listed all five expected footprints with zero warnings and errors. After applying
the update, the user routed R1 pad 2 to D1 pad 2 and saved the board.
`output/kicad/baseline.check.json` passes: all five UUID paths, references,
values, footprints, and pad connections match the manifest. The saved board has
three track segments on `/indicators[0]/anode`. The saved project contains
`Power` and `Signal` classes with track widths 0.5 mm and 0.25 mm respectively.
The user also reapplied `baseline.net` to `trial-unchanged.kicad_pcb` using
unique-ID matching, with zero warnings/errors and no reported additions or
removals. After saving, `output/kicad/unchanged.check.json` passes: all five
footprint identities, placements, rotations, layers, and pad connections are
preserved, as are all three track segments. Custom identity properties are absent
from the saved footprints; matching is verified through their UUID paths.
The addition trial also passes. The user imported `added.net`, whose load/test
report proposed only R3 with zero warnings/errors, then applied and saved it.
`output/kicad/added.check.json` verifies six expected footprints and connections,
preserved identities and placement of all five original parts, and preservation
of all three track segments on the unchanged LED net.

The deletion trial also passes. The user imported `removed.net`, whose load/test
report proposed only removing D2 with zero warnings/errors, then applied and
saved it. `output/kicad/removed.check.json` verifies four expected footprints and
their connections, preserved identities and placement of all surviving parts,
and preservation of all three track segments on the unchanged LED net. Rewiring,
source renaming, reference changes, CAD-side reannotation, and reordered reimport
also passed the saved-board comparisons listed below.

Twelve deterministic netlist variants and manifests were generated. The baseline
and reordered inputs produce byte-identical netlists. Source renames and reference
changes preserve the experimental UUIDs. The export structure uses version E and
was compared with an actual `kicad-cli sch export netlist --format kicadsexpr`
result from an installed demo.

The installed resistor, LED, and header footprints were inspected through
sexpdata. The baseline has no pin/pad mismatches. Separate probes distinguish:

- D2 has no footprint assignment: retained in the manifest, omitted from netlist.
- D2 names a nonexistent footprint: reported as an unknown footprint.
- D2 uses pin 99: reported as a missing pad, with footprint pad 2 also unmatched.

The saved-board reader was exercised on an installed KiCad demo. Its regression
tests use clearly identified synthetic board data to prove it detects changed
placement, rotation, side, footprint UUID, pad nets, missing pads, duplicates, and
lost copper. It ignores numeric net-code churn and compares net names instead.
It cannot claim routing preservation if the baseline contains no eligible route.
These tests validate the checker, not PCBNew's import behavior.

Class membership merge tests preserve all unrelated project data and class
settings, and reject undefined classes. The explicit assignment shape matches
installed KiCad project files. The user confirmed both assignments and class
widths in the editor; the saved project retains the assignments and unchanged
class definitions.

The installed `kicad-cli pcb` exposes DRC/export/import/render/upgrade, but `pcb
import` imports non-KiCad board formats. There is no CLI netlist-update command.
The installed editor contains Import Netlist and identity/reference matching
controls, also documented in the [KiCad 10 PCB Editor manual](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html).

### Actual DRC observation

An empty scratch board with an outline and no schematic was created using native
KiCad serialization. It has zero footprints; it is not a successful imported
fixture. DRC reported zero violations and zero unconnected items, returning 0
both with and without `--schematic-parity`.

With parity enabled, stderr also reported:

```text
Failed to fetch schematic netlist for parity tests.
Schematic parity tests require a fully annotated schematic.
```

The parity JSON contains no parity results. Therefore exit code 0 does **not**
establish that parity checks ran. The DRC capture tool now records this condition
as `parity_unavailable: true`. On the populated, partially routed baseline, both
runs returned 5 and reported five unconnected items with zero other violations.
Parity remained unavailable. Reports are in `output/drc-imported/`.

## Acceptance status

| Check | Status |
|---|---|
| Reference draft and positive grammar corpus | Pass |
| Negative syntax diagnostics with tested positions | Pass |
| Deterministic fixture generation and stable experimental identity | Pass |
| Footprint inspection and deliberate mismatch classification | Pass |
| Saved-board comparison and class-merge regression tests | Pass |
| CLI DRC on empty board, with and without parity | Observed; parity unavailable |
| Initial netlist import and airwires | Pass: user import and saved-board validation |
| No duplicates or layout changes on unchanged reimport | Pass: saved-board comparison |
| Reordered reimport | Pass: all five identities, placements, connections, and three trace segments preserved (`output/kicad/reordered.check.json`); generated netlist is byte-identical to baseline |
| Layout preservation on addition of R3 | Pass: saved-board comparison |
| Layout preservation on deletion of D2 | Pass: saved-board comparison |
| Layout preservation on rewiring | Pass: R2 pad 1 changed from v5 to gnd; all five parts and three LED trace segments preserved (`output/kicad/changed_connection.check.json`) |
| Identity-preserving source rename | Pass: all five footprint identities, placements, connections, and three trace segments preserved (`output/kicad/renamed.check.json`) |
| Identity-preserving reference change | Pass: R1 became R17 with all five footprint identities, placements, connections, and three trace segments preserved (`output/kicad/ref_changed.check.json`) |
| CAD-side reannotation followed by reimport | Pass after repeating update/save: R99 restored to R1; all five footprint identities, placements, connections, and three trace segments preserved (`output/kicad/cad-reimport.check.json`). |
| Unspecified footprint omitted from export | Pass: D2 removed; four surviving footprint identities, placements, connections, and three trace segments preserved (`output/kicad/unspecified_footprint.check.json`) |
| Unknown-footprint behavior in the editor | Observed partial update: missing D2 footprint reported (0 warnings, 1 error in summary); saved D2 retains its original LED footprint but gains `dnp`. All five identities, placements, connections, and three trace segments preserved against baseline (`output/kicad/unknown_footprint.preservation.check.json`); attributes recorded separately in `output/kicad/unknown_footprint.attributes.json`. This is not successful validation against the requested unknown footprint. |
| Mismatched-pad behavior in the editor | Observed: saved D2 pad 2 is disconnected, and requested pin 99 has no pad. KiCad reported 0 warnings/errors. All five identities and placements, other connections, and three trace segments on the unchanged LED net are preserved (`output/kicad/pad_mismatch.observed.json`). Requested-manifest validation correctly fails for missing pad 99; converter-side pad validation is needed. |
| Special net-name preservation | Pass: all five footprints and pad connections match; saved net names are exactly `#supply`, `RESET#`, `/bank[0]/A#`, and `/bank[1]/A#` (`output/kicad/names.check.json`) |
| Differential-pair import | Pass: J1 and J2 have pad 1 on `/USB_P` and pad 2 on `/USB_N` (`output/kicad/differential_pair.check.json`). No copper saved. |
| Differential-pair router recognition | Pass: user confirmed that KiCad routed `/USB_P` and `/USB_N` as a pair. |
| KiCad-owned class settings across membership updates | Pass: user confirmed memberships and widths in KiCad; saved project retains both assignments and unchanged class definitions (`output/kicad/classes.check.json`) |
| DRC behavior on the populated imported fixture | Observed: both runs return 5 with five unconnected items and zero other violations; parity unavailable without schematic (`output/drc-imported/`). The fixture intentionally has only one routed branch. |

Follow [README.md](README.md) for the exact manual steps and comparison commands.
The CAD-side reannotation test now passes after repeating the update/save.
The first inspected save still contained R99; the cause was not established.
The saved `trial-cad-reannotated.kicad_pcb` remains the pre-import comparison
baseline. All planned manual trials have now been performed.

## Recommendations and limits

- Proceed with Lark LALR/contextual and the existing parenthesized syntax.
- Start the KiCad adapter with version E netlists and persistent UUID paths.
  The tested editor matched parts by unique IDs through source and reference
  changes; custom identity properties were not retained on footprints.
- Validate footprint availability and pin/pad mappings before export. KiCad can
  partially update a board after a missing-footprint error, and silently omit a
  connection to a nonexistent pad. Keep partial-design export explicit in reports.
- Keep NoScheme net-class membership separate from KiCad-owned class settings.
  The tested project-file merge survives editor reload and save.
- Use board DRC for layout checks and NoScheme ERC for circuit checks. Schematic
  parity is unavailable in this workflow.

These results cover KiCad 10.0.6, the small fixtures, and the tested names and
footprints. They do not prove arbitrary name compatibility, preservation of
routing on changed nets, or the full STM32 design. The import workflow remains
manual. The prescribed settings were unique-ID matching, footprint replacement
and deletion enabled, and lock override disabled; reports explicitly confirm
unique-ID matching, and observed replacements/deletions cover those operations.
The next milestone is a source-located AST and compiler core producing a
CAD-independent circuit representation, followed by the converter and adapter.
