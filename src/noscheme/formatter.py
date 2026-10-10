"""Canonical source layout: print the concrete syntax tree and reattach comments.

Formatting changes only layout: indentation, spacing, line breaks, blank lines, the
trailing comma of a list broken one item per line, and an empty `<>` or `()`.
Spellings, order, and the choice between `<...>` or `(...)` entries and body sections
are the author's. Every result is checked to parse to the same tree with the same
comments; a comment the formatter cannot place without moving it is reported instead.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from contextlib import suppress
from dataclasses import dataclass
import os
from pathlib import Path
import re

from lark import Token, Tree

from .diagnostics import Diagnostic, DiagnosticError
from .parser import Builder, syntax_tree
from .project import read_config
from .syntax import comment_lexer, mask_comments

WIDTH = 100
INDENT = "  "
BLANK = re.compile(r"\n[ \t\r]*\n")
SECTIONS = {
    "arguments": "params",
    "instance_metadata": "metadata",
    "elements": "elements",
}
# The children of a declaration that its indented body holds.
BODY = ("field", "body_entry", *SECTIONS)


def is_tree(node, *kinds):
    return isinstance(node, Tree) and node.data in kinds


def arguments_of(node):
    """The entries of a `<...>` or `(...)` list: inline_arguments wraps those of `(...)`."""
    return node.children if is_tree(node, "inline_arguments") else (node,)


@dataclass
class Seq:
    """A bracketed, comma-separated list that layout may break one item per line."""

    open: str
    items: list  # (parts, start, end) per item
    close: str
    start: int  # offset of the opening bracket
    end: int  # offset just past the closing bracket
    comma: bool  # a broken list ends every item with a comma
    magic: bool  # the source ends the list with a comma, so it stays broken


@dataclass
class Line:
    depth: int
    text: str
    start: int  # source offsets of the code (or comment) this line prints
    end: int
    opens: bool = False  # a block or bracket follows: no blank line after
    closes: bool = False  # a closing bracket: no blank line before
    definition: bool = False
    own_comment: bool = False
    comment: Token | None = None  # trailing comment


def flat(parts):
    return "".join(
        part
        if isinstance(part, str)
        else part.open + ", ".join(flat(item[0]) for item in part.items) + part.close
        for part in parts
    )


class Formatter:
    def __init__(self, source, filename):
        self.source, self.filename = source, str(filename)
        self.builder = Builder(source, self.filename)
        self.tree = syntax_tree(source, self.filename, self.builder)
        # The parser ends the last line, so positions may run one past the source.
        self.masked = mask_comments(source, self.filename) + "\n"
        self.comments = [
            token
            for token in comment_lexer().lex(source)
            if token.type in {"LINE_COMMENT", "BLOCK_COMMENT"}
        ]
        self.comment_starts = [token.start_pos for token in self.comments]
        self.lines = []

    def refuse(self, comment, message):
        span = self.builder.position_span(comment.start_pos, comment.end_pos)
        raise DiagnosticError([Diagnostic("format.comment", "error", message, span)])

    def has_comment(self, start, end):
        """Whether a comment starts strictly between two offsets."""
        return bisect_left(self.comment_starts, end) > bisect_right(self.comment_starts, start)

    def trim(self, end):
        """Move back over layout and comments to the end of the preceding code."""
        while end and self.masked[end - 1] in " \t\r\n":
            end -= 1
        return end

    def column(self, offset):
        return offset - self.source.rfind("\n", 0, offset) - 1

    def span(self, node):
        if isinstance(node, Token):
            return node.start_pos, node.end_pos
        return node.meta.start_pos, self.trim(node.meta.end_pos)

    # Expressions become parts: strings and the bracketed lists layout may break.

    def seq(self, open_, close, start, end, items, comma=True):
        last = items[-1][2] if items else start + 1
        magic = comma and bool(items) and "," in self.masked[last : end - 1]
        return Seq(open_, items, close, start, end, comma, magic)

    def items(self, children):
        return [(self.expr(child), *self.span(child)) for child in children]

    def bracketed(self, node, open_, close, comma=True):
        start, end = node.meta.start_pos, node.meta.end_pos
        return self.seq(open_, close, start, end, self.items(node.children), comma)

    def index(self, node):
        c = node.children
        stop = [":", *self.expr(c[1])] if len(c) > 1 else []
        return ["[", *self.expr(c[0]), *stop, "]"]

    def expr(self, node):
        if isinstance(node, Token):
            return [str(node)]
        kind, c = node.data, node.children
        if kind in {"scaled", "number", "integer", "string", "name"}:
            return [str(c[0])]
        if kind == "grouped":
            return [self.bracketed(node, "(", ")", comma=False)]
        if kind == "list_value":
            return [self.bracketed(node, "[", "]")]
        if kind == "unary":
            return [str(c[0]), *self.expr(c[1])]
        if kind in {"shift", "sum", "product"}:
            parts = self.expr(c[0])
            for operator, operand in zip(c[1::2], c[2::2]):
                parts += [f" {operator} ", *self.expr(operand)]
            return parts
        if kind in {"postfix", "reference"}:
            parts = self.expr(c[0])
            for suffix in c[1:]:
                if isinstance(suffix, Token):
                    parts.append(f".{suffix}")
                elif suffix.data == "member":
                    parts.append(f".{suffix.children[0]}")
                elif suffix.data == "index":
                    parts += self.index(suffix)
                else:
                    parts.append(self.bracketed(suffix, "(", ")"))
            return parts
        if kind == "keyword_argument":
            return [f"{c[0]}=", *self.expr(c[1])]
        if kind == "call_target":
            start = self.masked.index("(", c[0].end_pos)
            return [
                str(c[0]),
                self.seq("(", ")", start, node.meta.end_pos, self.items(c[1:]), False),
            ]
        if kind == "range_value":
            return [*self.expr(c[0]), "..", *self.expr(c[1])]
        if kind == "tolerance_value":
            return [*self.expr(c[0]), f" {c[1]} ", *self.expr(c[2]), *(["%"] if len(c) > 3 else [])]
        raise AssertionError(f"unhandled expression: {kind}")

    def key(self, node):
        if node.data == "aliased_key":
            return f"{node.children[0]} ({', '.join(node.children[1:])})"
        if node.data == "single_pin_list":
            return flat(self.index(node.children[0]))
        c = node.children
        if len(c) == 1 and isinstance(c[0], Tree):
            group = c[0]
            if group.data == "range_group":
                keys = [f"{group.children[0]}..{group.children[1]}"]
                keys += [self.key(x) for x in group.children[2:]]
            else:
                keys = [str(group.children[0]), *(self.key(x) for x in group.children[1:])]
            # A one-key list keeps its comma; without it the key is a single pin.
            single = group.data == "list_group" and len(keys) == 1
            return "[" + ", ".join(keys) + ("," if single else "") + "]"
        return "..".join(c)

    def endpoint(self, node):
        if node.data in {"field_key", "single_pin_list"}:
            return [self.key(node)]
        if node.data == "singleton_endpoint":
            return self.index(node.children[0])
        c, parts, i = node.children, [], 0
        while c[i].data == "index":
            parts += self.index(c[i])
            i += 1
        parts += [".", self.key(c[i])] if i else [self.key(c[i])]
        for suffix in c[i + 1 :]:
            parts += [f".{suffix}"] if isinstance(suffix, Token) else self.index(suffix)
        return parts

    def arguments(self, node, open_, close):
        """`<...>` parameters or `(...)` connections."""
        items = []
        for argument in (x for child in node.children for x in arguments_of(child)):
            c = argument.children
            if argument.data == "wildcard_argument":
                parts = ["*"]
            elif argument.data in {"positional_argument", "positional_parameter"}:
                parts = self.expr(c[0])
            else:
                parts = [f"{c[0]}: ", *self.expr(c[1])]
            items.append((parts, *self.span(argument)))
        return self.seq(open_, close, node.meta.start_pos, node.meta.end_pos, items)

    def declarators(self, nodes):
        parts = []
        for i, node in enumerate(nodes):
            parts += [", " if i else "", str(node.children[0])]
            for index in node.children[1:]:
                parts += self.index(index)
        return parts

    # Layout: one line when it fits; otherwise break one bracketed list per line.

    def broken(self, part):
        """A list stays broken for a trailing comma or a comment inside it."""
        return isinstance(part, Seq) and (
            part.magic
            or self.has_comment(part.start, part.end - 1)
            or any(self.broken(p) for item in part.items for p in item[0])
        )

    def layout(self, parts, depth, start, end, suffix=""):
        text = flat(parts) + suffix
        forced = [i for i, part in enumerate(parts) if self.broken(part)]
        breakable = [i for i, part in enumerate(parts) if isinstance(part, Seq) and part.items]
        if not forced and (len(INDENT * depth + text) <= WIDTH or not breakable):
            return [Line(depth, text, start, end)]
        k = forced[0] if forced else breakable[-1]
        seq = parts[k]
        lines = [Line(depth, flat(parts[:k]) + seq.open, start, seq.start + 1, opens=True)]
        for item, item_start, item_end in seq.items:
            lines += self.layout(item, depth + 1, item_start, item_end, "," if seq.comma else "")
        rest = self.layout([seq.close, *parts[k + 1 :]], depth, seq.end - 1, end, suffix)
        rest[0].closes = True
        return lines + rest

    def statement(self, node, depth, parts, body=(), definition=False):
        end = self.trim(body[0].meta.start_pos if body else node.meta.end_pos)
        lines = self.layout(parts, depth, node.meta.start_pos, end)
        lines[0].definition = definition
        lines[-1].opens = bool(body)
        self.lines += lines
        for child in body:
            self.item(child, depth + 1)

    def item(self, node, depth):
        c, kind = node.children, node.data
        path = lambda name_path: ".".join(name_path.children)  # noqa: E731
        if kind == "import_stmt":
            text = f"import {path(c[0])}"
            if len(c) > 1:
                names = (" as ".join(x.children) for x in c[1:])
                text = f"from {path(c[0])} import {', '.join(names)}"
            self.statement(node, depth, [text])
        elif kind == "definition":
            head = f"{c[0].data.removesuffix('_kind')} {c[1]}:"
            self.statement(node, depth, [head], c[2:], definition=True)
        elif kind == "extension":
            parts, rest = ["extend " + path(c[0])], c[1:]
            if is_tree(rest[0], "type_arguments"):
                parts.append(self.arguments(rest[0], "<", ">"))
                rest = rest[1:]
            body = rest[1:]
            parts.append(f" {rest[0]}" + (":" if body else ""))
            self.statement(node, depth, parts, body, definition=True)
        elif kind == "section":
            self.statement(node, depth, [c[0].data.removesuffix("_name") + ":"], c[1:])
        elif kind in SECTIONS:
            self.statement(node, depth, [SECTIONS[kind] + ":"], c)
        elif kind in {"declaration", "typed_field"}:
            global_ = isinstance(c[0], Token)
            c = c[1:] if global_ else c
            parts = [("global " if global_ else "") + path(c[0])]
            declarators = [x for x in c if is_tree(x, "declarator")]
            body = [x for x in c[1:] if is_tree(x, *BODY)]
            for child in c[1:]:
                if is_tree(child, "type_arguments"):
                    parts.append(self.arguments(child, "<", ">"))
                elif child is declarators[0]:
                    parts += [" ", *self.declarators(declarators)]
                elif is_tree(child, "argument_list"):
                    parts.append(self.arguments(child, "(", ")"))
                elif not is_tree(child, "declarator", "type_arguments", "argument_list", *BODY):
                    parts += [" = ", *self.expr(child)]
            self.statement(node, depth, [*parts, ":" if body else ""], body)
        elif kind == "body_entry":
            parts = [*self.endpoint(c[0]), ":"]
            values = [x for x in c[1:] if not is_tree(x, "field")]
            for value in values:
                parts += [" ", *self.expr(value)]
            self.statement(node, depth, parts, c[1 + len(values) :])
        elif kind == "field":
            parts = [self.key(c[0]) + ":"]
            values = [x for x in c[1:] if x.data != "field"]
            for separator, value in zip((" ", " = "), values):
                parts += [separator, *self.expr(value)]
            self.statement(node, depth, parts, c[1 + len(values) :])
        elif kind == "element_override":
            indices = [x for x in c if x.data == "index"]
            parts = [part for x in indices for part in self.index(x)]
            self.statement(node, depth, [*parts, ":"], c[len(indices) :])
        elif kind == "argument":
            self.statement(node, depth, [f"{c[0]}: ", *self.expr(c[1])])
        else:
            raise AssertionError(f"unhandled declaration: {kind}")

    # Comments: trailing ones stay after their code, own-line ones before the next line.

    def place_comments(self):
        lines, starts = self.lines, [line.start for line in self.lines]
        leading = defaultdict(list)
        for comment in self.comments:
            line_end = self.source.find("\n", comment.end_pos)
            if self.source[comment.end_pos : line_end if line_end >= 0 else None].strip():
                self.refuse(comment, "a comment must end its line; move the code after it")
            line_start = self.source.rfind("\n", 0, comment.start_pos) + 1
            if not self.source[line_start : comment.start_pos].strip():
                i = bisect_right(starts, comment.start_pos)
                if i and lines[i - 1].end > comment.start_pos:
                    self.refuse(comment, "cannot place a comment inside this expression")
                leading[i].append(comment)
                continue
            code = self.trim(comment.start_pos) - 1
            i = bisect_right(starts, code) - 1
            if lines[i].end > comment.start_pos or lines[i].comment:
                self.refuse(comment, "cannot place a comment inside this expression")
            lines[i].comment = comment
        # Own-line comments take the depth of the code they precede, unless they are
        # indented past it: then they close the deepest enclosing block that fits.
        elements, path = [], []
        for i, line in enumerate([*lines, None]):
            for comment in leading[i]:
                column = self.column(comment.start_pos)
                depth = line.depth if line else 0
                if line is None or column > self.column(line.start):
                    depth = next((d for c, d in reversed(path) if c <= column), depth)
                end = comment.end_pos
                elements.append(Line(depth, str(comment), comment.start_pos, end, own_comment=True))
            if line:
                while path and path[-1][1] >= line.depth:
                    path.pop()
                path.append((self.column(line.start), line.depth))
                elements.append(line)
        return elements

    def render(self):
        for node in self.tree.children:
            self.item(node, 0)
        elements = self.place_comments()
        blank = [False] * len(elements)
        for j in range(1, len(elements)):
            before, line = elements[j - 1], elements[j]
            end = before.comment.end_pos if before.comment else before.end
            gap = self.source[end : line.start]
            blank[j] = bool(BLANK.search(gap)) and not before.opens and not line.closes
        # One blank line before each top-level definition and the comments leading it.
        for j, line in enumerate(elements):
            if line.definition:
                while j and elements[j - 1].own_comment and elements[j - 1].depth == 0:
                    j -= 1
                blank[j] = blank[j] or j > 0
        out = []
        for line, space in zip(elements, blank):
            if space:
                out.append("")
            comment = f"  {line.comment}" if line.comment else ""
            out.append(INDENT * line.depth + line.text + comment)
        return "\n".join(out) + "\n" if out else ""


def format_source(source: str, filename: str = "<input>") -> str:
    """Return the canonical layout of a source file, or raise DiagnosticError."""
    formatter = Formatter(source, filename)
    result = formatter.render()
    if result == source:
        return result
    try:
        formatted = syntax_tree(result, filename)
    except DiagnosticError:
        formatted = None
    kept = [str(t) for t in comment_lexer().lex(result) if t.type.endswith("_COMMENT")]
    if formatted != formatter.tree or kept != [str(t) for t in formatter.comments]:
        span = Builder(source, filename).position_span(0, 0)
        message = "formatting would change this file's meaning or comments; please report it"
        raise DiagnosticError([Diagnostic("format.internal", "error", message, span)])
    return result


def source_files(paths):
    """Expand directories to their sources, skipping hidden and project output directories."""
    for path in paths:
        if not path.is_dir():
            yield path
            continue
        for root, directories, files in os.walk(path):
            root = Path(root)
            output = None
            if (root / "noscheme.toml").is_file():
                with suppress(DiagnosticError):
                    output = read_config(root).output_dir
            directories[:] = sorted(
                d for d in directories if not d.startswith(".") and (root / d).resolve() != output
            )
            yield from (root / name for name in sorted(files) if name.endswith(".nosch"))
