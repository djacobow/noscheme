"""Grouped CSV BOMs using the same persistent references as CAD export."""

import csv
from io import StringIO
import json
import re

from .kicad.export import (
    assign,
    attribute,
    part_value,
    publish,
    read_state,
    state_warnings,
    validate_export_paths,
    validate_state,
)

COLUMNS = (
    "Quantity",
    "References",
    "Value",
    "Footprint",
    "Manufacturer",
    "MPN",
    "Supplier",
    "Supplier Part Number",
    "DNP",
)


# A spreadsheet evaluates text cells beginning with these as formulas.
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def spreadsheet_text(cell):
    """Keep a text cell text: a leading quote stops formula evaluation."""
    return "'" + cell if isinstance(cell, str) and cell.startswith(FORMULA_STARTS) else cell


def reference_key(reference):
    """Sort trailing reference numbers numerically without a fixed integer limit."""
    match = re.fullmatch(r"(.*?)([0-9]+)", reference)
    prefix, number = match.groups() if match else (reference, "")
    number = number.lstrip("0")
    return prefix.casefold(), len(number), number, reference


def bom_rows(circuit, state):
    groups = {}
    for part in circuit["parts"]:
        metadata = attribute(part, "bom", {})

        def value(key, default=""):
            return metadata.get(key, {}).get("value", default)

        cells = (
            str(part_value(part)),
            attribute(part, "footprint", ""),
            value("manufacturer"),
            value("mpn"),
            value("supplier"),
            value("supplier_part_number"),
            "yes" if value("dnp", False) else "no",
        )
        # A matching displayed value alone does not make two parts interchangeable.
        key = (*cells, part["definition"], json.dumps(part["parameters"], sort_keys=True))
        groups.setdefault(key, []).append(state["entries"][part["identity"]]["reference"])
    rows = []
    for key, references in groups.items():
        references.sort(key=reference_key)
        rows.append((references[0], [len(references), ", ".join(references), *key[:7]]))
    return [row for _, row in sorted(rows, key=lambda item: reference_key(item[0]))]


def export_bom(project, circuit, *, warnings=None):
    """Publish CSV and reference reservations atomically; preserve the CAD baseline."""
    state_path = project.config.state_path
    if warnings is not None:
        warnings.extend(state_warnings(project.config))
    state = read_state(state_path)
    validate_state(state, state_path)
    updated = assign(circuit, state, state_path)
    rows = bom_rows(circuit, updated)
    stream = StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(COLUMNS)
    writer.writerows([spreadsheet_text(cell) for cell in row] for row in rows)
    output = project.config.output_dir / "bom.csv"
    files = {
        output: stream.getvalue(),
        state_path: json.dumps(updated, indent=2, sort_keys=True) + "\n",
    }
    validate_export_paths(project, files)
    publish(files)
    dnp = sum(row[0] for row in rows if row[-1] == "yes")
    return {
        "output": str(output),
        "state": str(state_path),
        "groups": len(rows),
        "parts": len(circuit["parts"]),
        "fitted": len(circuit["parts"]) - dnp,
        "dnp": dnp,
    }
