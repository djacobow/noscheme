def register(registry):
    registry.net_rule("demo.pin_count", check, parameters={"max_pins": "nonnegative"})


def check(ctx, *, max_pins):
    if len(ctx.pins) > max_pins:
        yield ctx.error("too many attached physical pins")
