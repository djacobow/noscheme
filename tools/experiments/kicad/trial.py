"""Copy a saved baseline into a fresh manual-import trial without overwriting it."""

import argparse
from pathlib import Path
import shutil


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("baseline", type=Path)
    cli.add_argument("output", type=Path)
    args = cli.parse_args()
    baseline, output = args.baseline.resolve(), args.output.resolve()
    if baseline.suffix != ".kicad_pcb" or output.suffix != ".kicad_pcb":
        cli.error("both paths must end in .kicad_pcb")
    sources = [baseline, baseline.with_suffix(".kicad_pro")]
    destinations = [output, output.with_suffix(".kicad_pro")]
    if not all(p.exists() for p in sources):
        cli.error("baseline board and matching project file must both exist")
    if any(p.exists() for p in destinations):
        cli.error("trial board/project already exists; choose a fresh name")
    output.parent.mkdir(parents=True, exist_ok=True)
    for src, dst in zip(sources, destinations):
        shutil.copy2(src, dst)
    table = baseline.parent / "fp-lib-table"
    destination_table = output.parent / "fp-lib-table"
    if table.exists() and not destination_table.exists():
        shutil.copy2(table, destination_table)
    print(f"Created trial {output}. Baseline was not changed.")


if __name__ == "__main__":
    main()
