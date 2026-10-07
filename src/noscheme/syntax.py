"""Internal grammar-driven syntax layer; public API is noscheme.parser.parse."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from lark import Lark, Tree
from lark.exceptions import UnexpectedInput
from lark.indenter import DedentError, Indenter

HERE = Path(__file__).parent


class SyntaxDiagnostic(ValueError):
    def __init__(self, filename, line, column, message):
        self.filename, self.line, self.column = str(filename), line, column
        self.message = message
        super().__init__(f"{filename}:{line}:{column}: {message}")


class NoschemeIndenter(Indenter):
    NL_type = "_NL"
    INDENT_type = "_INDENT"
    DEDENT_type = "_DEDENT"
    # Newlines inside parentheses and brackets, including instance arguments, are
    # not layout; an instance's sections are indented after its line.
    OPEN_PAREN_types = ["_LPAR", "_LSQB"]
    CLOSE_PAREN_types = ["_RPAR", "_RSQB"]
    tab_len = 8  # Tabs in indentation are rejected before parsing.

    def handle_NL(self, token):
        try:
            yield from super().handle_NL(token)
        except DedentError as exc:
            # The offending indentation follows the last newline in this token.
            exc.line = token.end_line
            exc.column = token.end_column
            raise


@lru_cache
def comment_lexer():
    return Lark.open(str(HERE / "comments.lark"), parser="lalr", lexer="basic")


def mask_comments(source: str, filename: str) -> str:
    """Keep every offset/newline while making comment text whitespace.

    A small grammar-generated lexical pass avoids phantom indentation on
    comment-only lines; no handwritten comment scanner is needed.
    """
    result = []
    for token in comment_lexer().lex(source):
        if token.type == "UNCLOSED_BLOCK":
            raise SyntaxDiagnostic(filename, token.line, token.column, "unterminated block comment")
        if token.type in {"LINE_COMMENT", "BLOCK_COMMENT"}:
            result.append("".join(c if c in "\r\n" else " " for c in token))
        else:
            result.append(str(token))
    return "".join(result)


@lru_cache
def make_parser():
    return Lark.open(
        str(HERE / "noscheme.lark"),
        parser="lalr",
        lexer="contextual",
        postlex=NoschemeIndenter(),
        propagate_positions=True,
        maybe_placeholders=False,
    )


def expected_syntax(names):
    """Describe terminals using source spellings, never parser implementation names."""
    labels = {
        "$END": "end of file",
        "_NL": "end of line",
        "_INDENT": "an indented block",
        "_DEDENT": "the end of an indented block",
        "NAME": "a name",
        "INT": "an integer",
        "NUMBER": "a number",
        "STRING": "a quoted string",
        "ADD": "'+' or '-'",
        "MUL": "'*' or '/'",
        "SHIFT": "'<<' or '>>'",
    }
    for terminal in make_parser().terminals:
        if terminal.pattern.type == "str":
            labels[terminal.name] = repr(terminal.pattern.value)
    # Omit unknown implementation terminals instead of leaking their names.
    return sorted({labels[name] for name in names if name in labels})


def parse(source: str, filename: str = "<input>") -> Tree:
    masked = mask_comments(source, filename)
    for line_number, line in enumerate(masked.splitlines(), 1):
        prefix = line[: len(line) - len(line.lstrip(" \t"))]
        if "\t" in prefix and line.strip():
            raise SyntaxDiagnostic(
                filename, line_number, prefix.index("\t") + 1, "tabs are not allowed in indentation"
            )
    if not masked.endswith("\n"):
        masked += "\n"
    try:
        return make_parser().parse(masked)
    except UnexpectedInput as exc:
        line, column = exc.line, exc.column
        if getattr(exc, "token", None) is not None and exc.token.type == "$END":
            line = len(source.split("\n"))
            column = len(source.rsplit("\n", 1)[-1]) + 1
        elif getattr(exc, "token", None) is not None and exc.token.type in {"_INDENT", "_DEDENT"}:
            line, column = exc.token.end_line, exc.token.end_column
        expected = sorted(getattr(exc, "expected", None) or getattr(exc, "allowed", None) or [])
        expected = expected_syntax(expected)
        detail = f"; expected {', '.join(expected)}" if expected else ""
        raise SyntaxDiagnostic(filename, line, column, "invalid syntax" + detail) from exc
    except DedentError as exc:
        raise SyntaxDiagnostic(filename, exc.line, exc.column, "inconsistent indentation") from exc
