# Parameterized pull resistors

This electrical-model example combines numeric ERC expressions, explicit array
element overrides, and opt-in reference-terminal and voltage checks.

```sh
uv run --locked noscheme check --project examples/parameterized_erc
uv run --locked noscheme compile --project examples/parameterized_erc
```

The abstract external endpoint intentionally has no footprint; its footprint
warning is expected. This is not a fabrication-ready board.

Both array elements are real two-terminal resistors. Element 0 uses 2.2 kΩ;
element 1 keeps the 4.7 kΩ default. Each part's `value` parameter is a
`resistance`: it displays as `2.2kOhm` or `4.7kOhm` and also supplies the pin's
`pull_resistance`. Their parallel resistance is about 1.50 kΩ, above the example's
1 kΩ minimum, asserted by the named check
`pull_up: std.parallel(pull_resistance, where=bias.pull_up, min=1k)`. Per-element
placement notes appear under custom metadata.

The pin check `reference: std.bias_reference("supply")` names the resistor's supply
terminal. ERC checks its actual connection to a declared power net. It does not
infer voltage or trace through other components. The design can therefore fail this
check even if the resistance budget passes.

Changing parameters or placement notes preserves identities `pulls[0]` and
`pulls[1]`. Inserting/reordering array elements is still a physical identity change;
explicit element overrides do not provide per-element identity or reference
assignments.

The supply and bus ranges are declared explicitly, including a 5% upper supply
margin. The open-drain endpoint's -0.3 V / 3.6 V limits are illustrative model
values. ERC compares these declared limits with the bus range; it does not derive
the bus voltage from the pull-up or infer a supply domain.
