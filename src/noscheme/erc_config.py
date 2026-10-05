"""Resolve lexical ERC choices with copies only along changed record paths.

Unchanged data is shared with the source circuit. These internal views must be
treated as read-only; the scheduler freezes them before exposing them to rules.
"""

from .ast import Span
from .diagnostics import Diagnostic, DiagnosticError


def select(binding, environment):
    value = binding["value"]
    if isinstance(value, str):
        return value
    choice = environment.get(value["choice"]["value"])
    return value["cases"]["value"][choice]["value"] if choice is not None else None


def configuration_views(circuit, requested=None):
    configurations = circuit.get("erc_configurations", {})
    names = sorted(configurations) if not requested else list(dict.fromkeys(requested))
    for name in names:
        if name not in configurations:
            raise DiagnosticError(
                [
                    Diagnostic(
                        "erc.unknown_configuration",
                        "error",
                        f"unknown ERC configuration {name!r}",
                        Span(**circuit["source"]),
                    )
                ]
            )
    for name in names or [None]:
        root_values = configurations[name]["value"] if name is not None else {}
        environments = {}
        for scope in sorted(circuit.get("erc_choice_scopes", []), key=lambda s: len(s["scope"])):
            path = tuple(scope["scope"])
            parent = environments.get(tuple(scope["parent_scope"]), {})
            bindings = scope["bindings"] if path else root_values
            environments[path] = {
                key: select(bindings[key], parent) if key in bindings else None
                for key in scope["choices"]
            }

        def selected_record(record):
            metadata = record["metadata"]
            changes = {}
            for role, selection in metadata.get("_erc_selections", {}).get("value", {}).items():
                specification = selection["value"]
                environment = environments[tuple(specification["scope"]["value"])]
                chosen = select(selection, environment)
                if chosen is not None:
                    changes[role] = dict(metadata[role], value=[chosen])
            return dict(record, metadata=dict(metadata, **changes)) if changes else record

        def selected_records(records):
            updated = [selected_record(record) for record in records]
            return records if all(a is b for a, b in zip(records, updated)) else updated

        parts = []
        for part in circuit["parts"]:
            pins = part["pins"]
            updated = selected_records(list(pins.values()))
            parts.append(
                part
                if all(a is b for a, b in zip(pins.values(), updated))
                else dict(part, pins=dict(zip(pins, updated)))
            )
        nets = []
        for net in circuit["nets"]:
            declarations = selected_records(net["declarations"])
            nets.append(
                net if declarations is net["declarations"] else dict(net, declarations=declarations)
            )
        view = dict(circuit, parts=parts, nets=nets)
        yield (
            name,
            view,
            [
                {"scope": list(path), "selections": selections}
                for path, selections in environments.items()
                if selections
            ],
        )
