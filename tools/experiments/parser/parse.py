"""Parse the draft language, preserving token spelling and source spans.

Run: python -m tools.experiments.parser.parse examples/stm32h523/stm32h523.nosch --output PATH
No elaboration, expression execution, or electrical validation occurs here.
"""

from __future__ import annotations

import argparse
import json
from functools import lru_cache
from pathlib import Path

from lark import Lark, Token, Tree
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
    # Instance delimiters are distinct terminals selected by the contextual
    # lexer. Only expression/list delimiters suppress indentation.
    OPEN_PAREN_types = ["_LPAR", "_LSQB"]
    CLOSE_PAREN_types = ["_RPAR", "_RSQB"]
    tab_len = 8  # Experimental tab interpretation, not a language decision.

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


def parse(source: str, filename: str = "<input>") -> Tree:
    masked = mask_comments(source, filename)
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
        detail = f"; expected {', '.join(expected)}" if expected else ""
        raise SyntaxDiagnostic(filename, line, column, "invalid syntax" + detail) from exc
    except DedentError as exc:
        raise SyntaxDiagnostic(filename, exc.line, exc.column, "inconsistent indentation") from exc


def located_tree(node: Tree | Token, filename: str):
    meta = node.meta if isinstance(node, Tree) else node
    span = {"filename": str(filename)}
    for key in ("line", "column", "end_line", "end_column", "start_pos", "end_pos"):
        span[key] = getattr(meta, key, None)
    if isinstance(node, Token):
        return {"token": node.type, "text": str(node), "span": span}
    return {
        "kind": str(node.data),
        "span": span,
        "children": [located_tree(c, filename) for c in node.children],
    }


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("source", type=Path)
    cli.add_argument("--output", type=Path)
    args = cli.parse_args()
    try:
        result = located_tree(parse(args.source.read_text(), str(args.source)), str(args.source))
    except SyntaxDiagnostic as exc:
        cli.exit(1, str(exc) + "\n")
    data = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(data)
    else:
        print(data, end="")


if __name__ == "__main__":
    main()
