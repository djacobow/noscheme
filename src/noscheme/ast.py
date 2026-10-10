"""Immutable source AST; no resolution, expression execution, or CAD data."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from typing import Literal


@dataclass(frozen=True)
class Span:
    filename: str
    start_pos: int
    end_pos: int
    line: int
    column: int
    end_line: int
    end_column: int


@dataclass(frozen=True)
class Node:
    span: Span


@dataclass(frozen=True)
class Name(Node):
    text: str


@dataclass(frozen=True)
class Number(Node):
    spelling: str
    tag: Literal["integer", "double"]


@dataclass(frozen=True)
class String(Node):
    value: str
    spelling: str


@dataclass(frozen=True)
class ListExpr(Node):
    items: tuple[Expr, ...]


@dataclass(frozen=True)
class Unary(Node):
    operator: str
    operand: Expr


@dataclass(frozen=True)
class Binary(Node):
    operator: str
    left: Expr
    right: Expr


@dataclass(frozen=True)
class Member(Node):
    base: Expr
    name: Name


@dataclass(frozen=True)
class Index(Node):
    start: Expr
    stop: Expr | None


@dataclass(frozen=True)
class Subscript(Node):
    base: Expr
    selection: Index


@dataclass(frozen=True)
class KeywordArgument(Node):
    name: Name
    value: Expr


@dataclass(frozen=True)
class Call(Node):
    callee: Expr
    arguments: tuple[Expr, ...]
    keywords: tuple[KeywordArgument, ...] = ()


@dataclass(frozen=True)
class Group(Node):
    expression: Expr


Expr = Name | Number | String | ListExpr | Unary | Binary | Member | Subscript | Call | Group


@dataclass(frozen=True)
class Range(Node):
    """`low..high`, written as a field value."""

    low: Expr
    high: Expr


@dataclass(frozen=True)
class Tolerance(Node):
    """`center ± deviation`, or a percentage of center with `%`; a field value."""

    center: Expr
    deviation: Expr
    percent: bool


@dataclass(frozen=True)
class PinKey(Node):
    text: str
    spelling: str


@dataclass(frozen=True)
class FieldKey(Node):
    first: PinKey
    last: PinKey | None = None
    items: tuple[FieldKey, ...] = ()
    # Pin aliases written in the key: `1 (cathode, K):`.
    aliases: tuple[PinKey, ...] = ()

    @property
    def grouped(self):
        return self.last is not None or bool(self.items)


@dataclass(frozen=True)
class Field(Node):
    key: FieldKey
    value: Expr | Range | Tolerance | None
    default: Expr | None
    fields: tuple[Field, ...]


@dataclass(frozen=True)
class Section(Node):
    name: str
    entries: tuple[Field | Binding | Argument | ElementOverride, ...]


@dataclass(frozen=True)
class Endpoint(Node):
    instance_indices: tuple[Index, ...]
    head: FieldKey
    selectors: tuple[Name | Index, ...]


@dataclass(frozen=True)
class Binding(Node):
    endpoint: Endpoint
    target: Expr


@dataclass(frozen=True)
class Argument(Node):
    name: Name
    value: Expr


@dataclass(frozen=True)
class ElementOverride(Node):
    indices: tuple[Index, ...]
    sections: tuple[Section, ...]


@dataclass(frozen=True)
class Instance(Node):
    name: Name
    dimensions: tuple[Index, ...]
    component: tuple[Name, ...]
    sections: tuple[Section, ...]
    # Nets bound in order to the component's interchangeable pins.
    positional: tuple[Expr, ...] = ()
    # `*`: connect remaining pins and ports to same-name nets.
    wildcard: Span | None = None
    # `R<4.7k>`: the value of the one parameter without a default.
    parameter: Expr | None = None


@dataclass(frozen=True)
class Declarator(Node):
    name: Name
    dimensions: tuple[Index, ...]


@dataclass(frozen=True)
class Declaration(Node):
    """`Type<params> name(args):` in a block, board, or bundle, before its type resolves.

    The project loader makes it Nets or an Instance by the kind of its type. Sections
    are those of an instance, with `<...>` and `(...)` entries merged in; body lines
    that cannot be connections are fields, which only a net may have.
    """

    global_: bool
    type: tuple[Name, ...]
    declarators: tuple[Declarator, ...]
    parameters: Span | None  # `<...>`, when written.
    arguments: Span | None  # `(...)`, when written.
    sections: tuple[Section, ...]
    fields: tuple[Field, ...]
    parameter: Expr | None = None
    positional: tuple[Expr, ...] = ()
    wildcard: Span | None = None


@dataclass(frozen=True)
class Net(Node):
    name: Name
    type: Expr
    global_: bool
    fields: tuple[Field, ...]


@dataclass(frozen=True)
class Definition(Node):
    # "extend" until the project loader gives an extension its base's kind.
    kind: Literal["component", "block", "board", "bundle", "nettype", "extend"]
    name: Name
    base: tuple[Name, ...] | None
    body: tuple[Section | Declaration | Instance | Net | Field, ...]
    # `extend R<4.7k> Name`: the default of the one parameter without a default.
    parameter: Expr | None = None


@dataclass(frozen=True)
class ImportedName(Node):
    name: Name
    alias: Name | None = None


@dataclass(frozen=True)
class Import(Node):
    module: tuple[Name, ...]
    names: tuple[ImportedName, ...]


@dataclass(frozen=True)
class Module(Node):
    items: tuple[Import | Definition, ...]


def to_data(value):
    """Explicit node discriminators make JSON inspectable without Lark details."""
    if is_dataclass(value):
        result = {field.name: to_data(getattr(value, field.name)) for field in fields(value)}
        if isinstance(value, Node):
            result = {"node": type(value).__name__, **result}
        return result
    if isinstance(value, tuple):
        return [to_data(item) for item in value]
    if isinstance(value, dict):
        return {key: to_data(item) for key, item in sorted(value.items())}
    return value
