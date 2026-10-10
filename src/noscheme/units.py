"""SI-prefixed number literals and the physical quantities that give numbers a unit."""

import re

from .integers import parse_integer

# Exponent of ten for each literal prefix. R is the unity decimal mark of 4R7.
PREFIXES = {
    "a": -18,
    "f": -15,
    "p": -12,
    "n": -9,
    "u": -6,
    "m": -3,
    "R": 0,
    "k": 3,
    "M": 6,
    "G": 9,
    "T": 12,
    "P": 15,
}
SCALED = re.compile(r"([0-9]+)(?:\.([0-9]+))?([afpnumkMGTPR])([0-9]+)?\Z")

# Quantity name -> (unit symbol, sign constraint for metadata fields).
QUANTITIES = {
    "voltage": ("V", "number"),
    "current": ("A", "nonnegative"),
    "resistance": ("Ohm", "positive"),
    "capacitance": ("F", "nonnegative"),
    "inductance": ("H", "nonnegative"),
    "frequency": ("Hz", "nonnegative"),
    "time": ("s", "nonnegative"),
    "power": ("W", "nonnegative"),
}
# Display prefixes, smallest first; R is input-only.
DISPLAY = [(e, p) for p, e in sorted(PREFIXES.items(), key=lambda item: item[1]) if p != "R"]


def is_scaled(spelling):
    return SCALED.match(spelling) is not None


def number_value(spelling, *, negative=False):
    """Parse a signed literal, preserving integral SI-prefixed values as integers."""
    if is_scaled(spelling):
        return scaled_value(spelling, negative=negative)
    text = ("-" if negative else "") + spelling
    return float(text) if any(c in ".eE" for c in spelling) else parse_integer(text)


def scaled_parts(spelling):
    """Normalize decimal digits and exponent without evaluating the number."""
    match = SCALED.match(spelling)
    if match is None or match[2] is not None and match[4] is not None:
        raise ValueError(f"invalid scaled number {spelling!r}")
    whole, fraction, prefix, rkm = match.groups()
    fraction = fraction or rkm or ""
    digits = (whole + fraction).lstrip("0")
    if not digits:
        return "0", 0
    exponent = PREFIXES[prefix] - len(fraction)
    while exponent < 0 and digits.endswith("0"):
        digits = digits[:-1]
        exponent += 1
    return digits, exponent


def scaled_value(spelling, *, negative=False):
    """Parse an SI-prefixed literal, converting its decimal spelling only once."""
    digits, exponent = scaled_parts(spelling)
    sign = "-" if negative else ""
    if exponent >= 0:
        if len(digits) + exponent > 19:
            raise ValueError("integer exceeds configured bit limit")
        return parse_integer(sign + digits + "0" * exponent)
    return float(f"{sign}{digits}e{exponent}")


def constraint(kind):
    """The numeric sign constraint of a field kind, including quantity kinds."""
    return QUANTITIES[kind][1] if kind in QUANTITIES else kind


def format_quantity(value, quantity):
    """Render with an engineering prefix and six significant digits, such as 4.7kOhm."""
    unit = QUANTITIES[quantity][0]
    if value == 0:
        return "0" + unit
    coefficient, scientific_exponent = format(abs(float(value)), ".5e").split("e")
    exponent = int(scientific_exponent)
    engineering = min(max(3 * (exponent // 3), DISPLAY[0][0]), DISPLAY[-1][0])
    digits = coefficient.replace(".", "")
    places = 5 - exponent + engineering
    if places <= 0:
        text = digits + "0" * -places
    else:
        digits = digits.zfill(places + 1)
        text = (digits[:-places] + "." + digits[-places:]).rstrip("0").rstrip(".")
    prefix = dict(DISPLAY).get(engineering, "")
    return ("-" if value < 0 else "") + text + prefix + unit
