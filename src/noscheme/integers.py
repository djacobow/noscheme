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
    digits = text.lstrip("+-").lstrip("0") or "0"
    if len(digits) > 19:
        raise ValueError("integer exceeds configured bit limit")
    return checked_integer(-int(digits) if negative else int(digits))
