"""Record CLI DRC behavior with and without schematic parity, without saving boards."""

import argparse
import json
from pathlib import Path
import subprocess


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("board", type=Path)
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    outcomes = []
    for parity in (False, True):
        name = "parity" if parity else "normal"
        report = args.output / (name + ".json")
        if report.exists():
            cli.error(f"{report} exists; choose a new evidence directory")
        command = [
            "kicad-cli",
            "pcb",
            "drc",
            "--format",
            "json",
            "--output",
            str(report),
            "--exit-code-violations",
        ]
        if parity:
            command.append("--schematic-parity")
        command.append(str(args.board))
        run = subprocess.run(command, text=True, capture_output=True)
        (args.output / (name + ".stdout.txt")).write_text(run.stdout)
        (args.output / (name + ".stderr.txt")).write_text(run.stderr)
        outcomes.append(
            {
                "command": command,
                "returncode": run.returncode,
                "report_exists": report.exists(),
                "schematic_parity": parity,
                "parity_unavailable": parity
                and "Failed to fetch schematic netlist" in (run.stdout + run.stderr),
            }
        )
    (args.output / "commands.json").write_text(json.dumps(outcomes, indent=2) + "\n")
    print(json.dumps(outcomes, indent=2))


if __name__ == "__main__":
    main()
