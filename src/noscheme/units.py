"""SI-prefixed number literals and the physical quantities that give numbers a unit."""

from decimal import Decimal
import math
import re

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


def number_value(spelling):
    """The value of any number literal spelling."""
    if is_scaled(spelling):
        return scaled_value(spelling)
    return float(spelling) if any(c in ".eE" for c in spelling) else int(spelling)


def scaled_value(spelling):
    """Exact value of 4.7k, 4k7, or 100n: an int when integral, otherwise a double."""
    match = SCALED.match(spelling)
    if match is None or match[2] is not None and match[4] is not None:
        raise ValueError(f"invalid scaled number {spelling!r}")
    whole, fraction, prefix, rkm = match.groups()
    # Build the decimal exactly, then convert once, so 100n is the double nearest 1e-7.
    value = Decimal(f"{whole}.{fraction or rkm or '0'}e{PREFIXES[prefix]}")
    return int(value) if value == value.to_integral_value() else float(value)


def constraint(kind):
    """The numeric sign constraint of a field kind, including quantity kinds."""
    return QUANTITIES[kind][1] if kind in QUANTITIES else kind


def format_quantity(value, quantity):
    """Render with an engineering prefix and six significant digits, such as 4.7kOhm."""
    unit = QUANTITIES[quantity][0]
    if value == 0:
        return "0" + unit
    exponent = 3 * math.floor(math.log10(abs(value)) / 3)
    exponent = min(max(exponent, DISPLAY[0][0]), DISPLAY[-1][0])
    mantissa = Decimal(format(Decimal(value).scaleb(-exponent), ".6g"))
    if abs(mantissa) >= 1000 and exponent < DISPLAY[-1][0]:
        exponent, mantissa = exponent + 3, Decimal(format(mantissa.scaleb(-3), ".6g"))
    text = format(mantissa, "f")
    text = text.rstrip("0").rstrip(".") if "." in text else text
    prefix = dict(DISPLAY).get(exponent, "")
    return text + prefix + unit
