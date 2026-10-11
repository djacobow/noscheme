"""Checked signed 64-bit language integers, shared by literals and the bridge."""

MIN_INTEGER = -(1 << 63)
MAX_INTEGER = (1 << 63) - 1


def checked_integer(value, bits=64):
    bits = min(bits, 64)
    bound = 1 << (bits - 1) if bits else 1
    if not -bound <= value < bound or bits == 0 and value != 0:
        raise ValueError("integer exceeds configured bit limit")
    return value


def parse_integer(text):
    negative = text.startswith("-")
    digits = text.lstrip("+-").replace("_", "")
    base = 16 if digits.lower().startswith("0x") else 2 if digits.lower().startswith("0b") else 10
    if base != 10:
        digits = digits[2:]
    digits = digits.lstrip("0") or "0"
    if len(digits) > {10: 19, 16: 16, 2: 64}[base]:
        raise ValueError("integer exceeds configured bit limit")
    value = int(digits, base)
    return checked_integer(-value if negative else value)
