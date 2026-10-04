"""Apply explicit memberships to a separate copy of a KiCad project file."""

import json
from pathlib import Path

from .export import read_json, publish, fail


def update_classes(project_path, memberships_path, output):
    project_path, memberships_path, output = map(Path, (project_path, memberships_path, output))
    if output.exists() or output.resolve() in {project_path.resolve(), memberships_path.resolve()}:
        raise FileExistsError("choose a new output project file; inputs are preserved")
    project, memberships = read_json(project_path), read_json(memberships_path)
    if not isinstance(project, dict) or not isinstance(memberships, dict):
        fail(project_path, "netclass.schema", "expected project and membership mappings")
    settings = project.setdefault("net_settings", {})
    if not isinstance(settings, dict) or not isinstance(settings.get("classes", []), list):
        fail(project_path, "netclass.schema", "invalid net_settings or class definitions")
    classes = settings.get("classes", [])
    if any(not isinstance(c, dict) or not isinstance(c.get("name"), str) for c in classes):
        fail(project_path, "netclass.schema", "each class must have a name")
    defined = {c["name"] for c in classes}
    for net, values in memberships.items():
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            fail(memberships_path, "netclass.schema", f"{net!r} must map to a list of class names")
        if set(values) - defined:
            fail(
                memberships_path,
                "netclass.undefined",
                f"undefined classes for {net!r}: {sorted(set(values) - defined)}",
            )
    assignments = settings.get("netclass_assignments") or {}
    if not isinstance(assignments, dict):
        fail(project_path, "netclass.schema", "invalid netclass_assignments")
    settings["netclass_assignments"] = {**assignments, **memberships}
    publish({output: json.dumps(project, indent=2, sort_keys=True) + "\n"})
    return {
        "output": str(output),
        "assignments_updated": len(memberships),
        "class_settings_preserved": True,
    }
