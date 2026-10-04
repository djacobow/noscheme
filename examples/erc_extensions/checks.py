from noscheme.erc_registry import value


def register(registry):
    registry.fields("net", {"demo": {"max_pins": "nonnegative"}})
    registry.check("demo_pin_count", check, net_fields=("demo",))


def check(ctx):
    annotation = value(ctx.origin, "demo")
    if annotation and len(ctx.endpoints) > annotation["max_pins"]["value"]:
        yield ctx.diagnostic("demo_pin_count", "too many attached physical pins")
