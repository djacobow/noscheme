"""Change explicit net-class memberships in a *copy* of a scratch project.

No class definitions or unrelated settings are rewritten semantically. Close
PCBNew before using this file experiment. GUI reload validation is still needed.
"""

import argparse
from copy import deepcopy
import json
from pathlib import Path


def merge_membership(project, membership):
    result = deepcopy(project)
    settings = result.setdefault("net_settings", {})
    defined = {c["name"] for c in settings.get("classes", [])}
    if not all(
        isinstance(net, str)
        and isinstance(classes, list)
        and all(isinstance(c, str) for c in classes)
        for net, classes in membership.items()
    ):
        raise ValueError("membership must map net names to lists of class names")
    unknown = {c for classes in membership.values() for c in classes} - defined
    if unknown:
        raise ValueError(f"undefined classes: {sorted(unknown)}; define them in KiCad first")
    assignments = settings.get("netclass_assignments") or {}
    assignments.update(membership)
    settings["netclass_assignments"] = assignments
    return result


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("project", type=Path)
    cli.add_argument("membership", type=Path)
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    if args.output.resolve() == args.project.resolve():
        cli.error("choose a separate output file; this experiment preserves the original")
    if args.output.exists():
        cli.error("output already exists; choose a new scratch copy")
    result = merge_membership(
        json.loads(args.project.read_text()), json.loads(args.membership.read_text())
    )
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
