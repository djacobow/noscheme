"""Shared built-in electrical descriptions for editor implementations."""

BUILTIN_HELP = {
    "role": {
        "passive": "Passive contact; imposes no electrical domain and supplies no active driver.",
        "digital_input": "Receives a digital signal. An explicitly declared input port may be tied to a power or ground rail.",
        "digital_output": "Actively drives a digital signal; participates in multiple-driver checks.",
        "digital_bidirectional": "Can receive or drive digital signals. Single-driver nets need an operating assumption to resolve driver ambiguity.",
        "analog_input": "Receives an analog signal.",
        "analog_output": "Actively drives an analog signal; participates in multiple-driver checks.",
        "analog_bidirectional": "Can receive or drive analog signals.",
        "power_input": "Consumes power. Connecting to a ground net requires an explicit incompatible_role waiver. Optional max_load_current metadata contributes to current-budget checks.",
        "power_output": "Supplies power. Optional max_source_current metadata declares its current capacity.",
        "ground": "Ground connection; connecting a power output to ground is an ERC error.",
        "unchecked": "Electrical behavior is unspecified; ERC cannot treat this endpoint as passive or prove its driver behavior.",
    },
    "drive": {
        "low": "Actively pulls low or releases the net; does not actively drive high.",
        "high": "Actively pulls high or releases the net; does not actively drive low.",
    },
    "pull": {
        "up": "Provides a passive pull toward high on the connected net.",
        "down": "Provides a passive pull toward low on the connected net.",
        "none": "Provides no passive pull; other endpoints may still provide one.",
    },
    "net": {
        "passive": "Allows passive, digital, analog, power, and ground roles. Driver conflicts and power-to-ground conflicts are still checked.",
        "digital": "Digital net with driver checks. Requires a digital output or a compatible declared pull; conflicting active drivers are errors.",
        "analog": "Allows passive and analog endpoints; checks conflicting active drivers without requiring a driver.",
        "power": "Supply rail. Allows power inputs/outputs, signal inputs, and passive contacts; requires a power source and checks annotated current budgets.",
        "ground": "Ground rail. Allows ground, digital/analog inputs, and passive contacts; rejects power inputs and outputs.",
        "idle_high": "Requires at least one drive.low endpoint and one pull.up endpoint. Opposing pulls and other active drivers are errors.",
        "idle_low": "Requires at least one drive.high endpoint and one pull.down endpoint. Opposing pulls and other active drivers are errors.",
        "bidirectional": "Allows digital and passive endpoints without requiring a single known driver. Conflicting active output drivers are still errors.",
        "tristate": "Shared digital bus. Endpoints other than passive contacts and digital inputs require tristate_capable: true. Multiple capable drivers are allowed; enable timing and arbitration are not modeled.",
        "unchecked": "Skips this declaration's electrical compatibility constraints without a warning. Outermost unchecked nets are counted in the ERC report. Other declarations and mandatory connection policies still apply.",
    },
    "use": {
        "disallowed": "Must not be assigned to any net. Omission or no_connect is allowed; this connection rule cannot be waived.",
        "optional": "May be left unconnected without an explicit no_connect binding.",
        "explicit": "An unused pin or port must be explicitly bound to no_connect. This is the default connection policy.",
        "required": "Must be connected; no_connect is not allowed. An instance cannot weaken this policy.",
    },
}
