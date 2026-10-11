# ERC extensions

This small electrical model demonstrates both registered Python rules and generic
reductions. It is not a fabrication design: `Pull` models only the bus-side pin
of a pull-up. The STM32 example's `I2CPullup` shows a real two-terminal resistor.

`noscheme.toml` loads `checks.py`. That module registers `demo.pin_count`, with a
numeric `max_pins` parameter. `main.nosch` imports that namespace with
`import demo` and applies `demo.pin_count(max_pins=3)` alongside two generic
reductions: `std.parallel` for pull resistance and `std.sum` for capacitance.
The device declares `pull.none`; it is known not to contribute a pull.

Run `uv run --locked noscheme check --project examples/erc_extensions` from the repository
root. Lower a limit to see an application-specific diagnostic, or remove a
contribution to see how unknown data is reported. See [LRM §12](../../docs/LRM.md#12-extending-erc)
for the API, rule sets, immutable contexts, and missing-data policies.
