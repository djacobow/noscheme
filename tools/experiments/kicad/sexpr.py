"""Small structural helpers on top of the sexpdata parser."""

from pathlib import Path

import sexpdata


def form(name, *children):
    return [sexpdata.Symbol(name), *children]


def tag(value):
    return str(value[0]) if isinstance(value, list) and value else None


def children(value, name):
    return [v for v in value[1:] if tag(v) == name]


def child(value, name, default=None):
    return next(iter(children(value, name)), default)


def scalar(value, name, default=None):
    found = child(value, name)
    return found[1] if found and len(found) > 1 else default


def load(path):
    # Preserve all symbols; KiCad's bare nil/t are not Lisp booleans here.
    return sexpdata.loads(Path(path).read_text(), nil=None, true=None, false=None)


def dump(value):
    return sexpdata.dumps(value) + "\n"
