"""Stable diagnostic records and equivalent text/JSON presentations."""

from dataclasses import dataclass
from contextlib import contextmanager
from typing import Literal
import json
from .ast import Span, to_data


@dataclass(frozen=True)
class Related:
    message: str
    span: Span


@dataclass(frozen=True)
class Diagnostic:
    code: str
    severity: Literal["error", "warning", "unknown"]
    message: str
    span: Span
    related: tuple[Related, ...] = ()
    instance_path: str | None = None
    configuration: str | None = None
    application: str | None = None


class DiagnosticError(ValueError):
    def __init__(self, diagnostics):
        self.diagnostics = tuple(diagnostics)
        super().__init__(render_text(self.diagnostics))


@contextmanager
def collect_errors(diagnostics):
    """Collect a failed independent check; unexpected exceptions still propagate."""
    try:
        yield
    except DiagnosticError as exc:
        diagnostics.extend(exc.diagnostics)


def render_text(diagnostics):
    lines = []
    for d in diagnostics:
        s = d.span
        configuration = f" [{d.configuration}]" if d.configuration is not None else ""
        application = f" ({d.application})" if d.application else ""
        lines.append(
            f"{s.filename}:{s.line}:{s.column}: {d.severity} {d.code}{configuration}{application}: {d.message}"
        )
        for r in d.related:
            s = r.span
            lines.append(f"{s.filename}:{s.line}:{s.column}: note: {r.message}")
    return "\n".join(lines)


def render_json(diagnostics):
    return json.dumps(
        {"schema_version": 1, "diagnostics": [to_data(d) for d in diagnostics]}, indent=2
    )
