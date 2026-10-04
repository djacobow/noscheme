# NoScheme feasibility experiments

These tools implement the first milestone in [PLAN.md](../PLAN.md). They are
experiments, not a production compiler or exporter. In particular, successful
parsing does not imply name resolution, valid shapes, or electrical correctness.

## Setup and automated checks

From the repository root on the current Linux host:

```sh
uv venv experiments/.venv --python /usr/bin/python3
uv pip install --python experiments/.venv/bin/python -r experiments/requirements.txt
experiments/.venv/bin/python -m pytest experiments/tests -q
experiments/.venv/bin/python -m experiments.parser.parse examples/stm32h523.nosch --output experiments/output/stm32h523.syntax.json
experiments/.venv/bin/python -m experiments.kicad.fixtures
```

The fixture command uses `/usr/share/kicad/footprints`; override with
`--library-root` on other installations. Outputs go to `experiments/output/`,
which is ignored by version control. Rerunning generation rewrites its generated
netlists/manifests, not saved `.kicad_pcb` files. Version information is in
`experiments/output/kicad/versions.json`.

The parser has no evaluator or compiler side effects. It writes a JSON syntax
tree containing original token spelling and filename/line/column/offset spans.
Its field syntax is deliberately schema-neutral; it does not yet validate legal
metadata, electrical roles, or definitions. The grammar does not implement ERC
configuration or waiver syntax that the design has not specified.

## KiCad inputs

`experiments/output/kicad/` contains `.net` files, corresponding JSON manifests,
footprint pad snapshots/hashes, explicit class memberships, and a local
`fp-lib-table`. Each manifest says `editor_validation: pending`; it never changes
status merely because it was generated. Save actual import reports separately.

The baseline contains J1, R1, D1, R2, D2. R1/D1 are the branch to route and keep
unchanged. All variants are independently derived from the baseline:

| File stem | Change or purpose |
|---|---|
| `baseline` | Five components, four nets |
| `reordered` | Reversed source component order; byte-identical netlist |
| `added` | R3 added between v5 and gnd |
| `removed` | D2 removed; R2 and its net remain |
| `changed_connection` | R2 pin 1 moves from v5 to gnd |
| `renamed` | R1 source path renamed; explicit identity and UUID unchanged |
| `ref_changed` | R1 becomes R17 with the same identity and UUID |
| `unspecified_footprint` | D2 retained in manifest but omitted from import netlist |
| `unknown_footprint` | D2 names a deliberately nonexistent footprint |
| `pad_mismatch` | D2 logical pin 99 has no matching footprint pad; pad 2 lacks a pin |
| `names` | Tests leading/trailing #, slashes, and bracketed net names |
| `differential_pair` | Separate two-header circuit with /USB_P and /USB_N |

This is a direct netlist experiment: the component data is Python, not compiled
`.nosch`. Version-E netlist structure was compared with output from the locally
installed KiCad 10.0.6 schematic exporter. Parsing generated S-expressions is not
proof that PCBNew will import them correctly.

## Manual PCBNew checklist

### A. Establish a routed baseline

An empty board with an outline has been created at
`experiments/output/kicad/import-test.kicad_pcb`. To recreate it on another checkout:

```sh
/usr/bin/python3 experiments/kicad/prepare_board.py experiments/output/kicad/import-test.kicad_pcb
```

This uses the system KiCad Python module, not the venv. It refuses to overwrite
existing files. It creates no components and performs no netlist import.

1. Open `import-test.kicad_pcb` in PCBNew, then choose **File → Import →
   Netlist…**. This opens the Import Netlist dialog. Do not use Update PCB
   from Schematic; no schematic is supplied.
2. Use the browse button beside the netlist filename field to select
   `baseline.net` from `experiments/output/kicad/`. On this checkout its full
   path is `/home/david/personal/noscheme/experiments/output/kicad/baseline.net`;
   you can paste that path into the file chooser. Begin with **Link footprints using reference
   designators** unchecked, **Replace footprints with those specified in netlist**
   checked, and **Delete footprints with no components in netlist** checked.
   Leave lock override unchecked. Record actual options and any differences in
   the installed UI. Do not switch identity matching silently if an import fails.
3. Use **Load and Test Netlist**, review its report, then apply the update.
   Save/copy its report to `baseline.import.txt`. Expect five footprints and
   airwires for the four manifest nets. A failure is an experiment result.
4. Place the parts inside the outline. Route R1 pad 2 to D1 pad 2 on
   `/indicators[0]/anode`; this net stays unchanged in the mutation trials.
5. In Board Setup, create classes `Power` and `Signal`, with distinctive widths
   and clearances chosen for the experiment. Save and close PCBNew. Ensure the
   matching `import-test.kicad_pro` was saved.
6. Validate and capture the baseline:

```sh
experiments/.venv/bin/python -m experiments.kicad.inspect_board experiments/output/kicad/import-test.kicad_pcb --expected experiments/output/kicad/baseline.json --output experiments/output/kicad/baseline.check.json
```

Do not treat this baseline as a manufactured-board design. Its job is to exercise
import behavior and preserve a known route. Keep it unchanged after this point.

### B. Test each mutation independently

Use a fresh copy for every variant. For example:

```sh
experiments/.venv/bin/python -m experiments.kicad.trial experiments/output/kicad/import-test.kicad_pcb experiments/output/kicad/trial-added.kicad_pcb
```

Open that copy, import `added.net` with the same recorded options, save the
import report as `added.import.txt`, save the board, and close the editor. Then:

```sh
experiments/.venv/bin/python -m experiments.kicad.inspect_board experiments/output/kicad/trial-added.kicad_pcb --before experiments/output/kicad/import-test.kicad_pcb --baseline experiments/output/kicad/baseline.json --expected experiments/output/kicad/added.json --output experiments/output/kicad/added.check.json
```

Repeat for `baseline`, `reordered`, `removed`, `changed_connection`, `renamed`,
and `ref_changed`. In the latter trial R17 must occupy the former R1 footprint,
not appear as a newly placed object. Inspect the expected additions/deletions
before applying each update.

The checker requires exact UUID-path matches, correct references/values/footprints,
correct connected pad nets, no extra footprints, unchanged surviving footprint
UUIDs/placement/rotation/layer, and preservation of existing copper on unchanged
nets. It refuses to claim routing preservation if the baseline has no route on
such a net. Matching solely by reference is not silently accepted.

### C. CAD-side reannotation

Create a separate baseline copy. In PCBNew change R1's reference to R99, save
and close, then create another copy of that board for reimport. Import
`baseline.net` and compare against the *pre-import R99 board* using
`--allow-baseline-ref-change` in addition to the normal comparison arguments.
The expected final manifest is `baseline.json`: R1 should be restored without
losing its footprint identity or layout. Save the import report.

### D. Invalid footprint and name probes

- Import `unspecified_footprint.net` into a fresh baseline copy and inspect the
  removal report. D2 should have no placeable object; its source record remains.
- Import `unknown_footprint.net` and `pad_mismatch.net` only into fresh copies.
  Record errors/warnings and whether the editor applies partial changes. A normal
  zero-error comparison is not the expected outcome for these cases.
- Import `names.net` into a fresh empty board and inspect against `names.json`.
  Check visible net names as well. Do not compare route preservation after renaming
  every net; this fixture tests naming only.
- Import `differential_pair.net` into another empty board and validate against its
  manifest. Start the differential-pair router at a pad and confirm that it selects
  both /USB_P and /USB_N. Record the result; the file checker alone cannot establish
  router recognition.

### E. Class membership

With the editor closed and `Power`/`Signal` defined in the baseline project:

```sh
experiments/.venv/bin/python -m experiments.kicad.classes experiments/output/kicad/import-test.kicad_pro experiments/output/kicad/class-membership.json --output experiments/output/kicad/classes-candidate.kicad_pro
```

Copy the baseline board to `classes-candidate.kicad_pcb` so it has the same stem.
Open that board, verify v5 uses Power and `/indicators[0]/anode` uses Signal, and
verify both class definitions retained their widths/clearances. Save and retain
before/after project files. The tool changes only explicit membership, preserves
unrelated assignments/patterns/settings, rejects unknown classes, and never
rewrites the input file. Its JSON merge tests are not proof of GUI consumption.

### F. DRC and evidence

Run on an imported board, using a fresh evidence directory:

```sh
experiments/.venv/bin/python -m experiments.kicad.drc experiments/output/kicad/import-test.kicad_pcb --output experiments/output/drc-imported
```

This records JSON reports, stdout/stderr, exact commands, and return codes, with
and without parity. It does not use `--save-board` or refill zones. KiCad can return
zero while logging that it could not obtain a schematic; an empty parity result
must not be treated as a successful parity check.

Keep netlists/manifests, board/project snapshots, import reports, comparison JSON,
and tool versions together. Manual result status stays **pending** until these
operations have actually been performed. [FINDINGS.md](FINDINGS.md) separates
current automated evidence from editor-dependent checks.
