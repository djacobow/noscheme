"""Resolve lexical ERC choices on isolated views of one unchanged circuit."""

from copy import deepcopy

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
        view = deepcopy(circuit)
        for record in [
            *(pin for part in view["parts"] for pin in part["pins"].values()),
            *(d for net in view["nets"] for d in net["declarations"]),
        ]:
            metadata = record["metadata"]
            for role, selection in metadata.get("_erc_selections", {}).get("value", {}).items():
                specification = selection["value"]
                environment = environments[tuple(specification["scope"]["value"])]
                chosen = select(selection, environment)
                if chosen is not None:
                    metadata[role]["value"] = [chosen]
        yield (
            name,
            view,
            [
                {"scope": list(path), "selections": selections}
                for path, selections in environments.items()
                if selections
            ],
        )
