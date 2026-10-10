"""Public bundle lookup shared by completion and source navigation."""

from . import ast as a
from .diagnostics import DiagnosticError
from .metadata import Value
from .resolve import Resolver


class BundleLookup:
    def __init__(self, project, definitions):
        self.resolver = Resolver(project)
        self.resolver.definitions = definitions
        self.project = project

    def members(self, value):
        if value is None:
            return {}
        try:
            bundle = self.resolver.type_definition(value)
        except DiagnosticError:
            return {}
        return bundle.interface if bundle else {}

    def member(self, value, name):
        return self.members(value).get(name)

    def reference_type(self, expr, owner, *, ports_only=False):
        if owner is None:
            return None
        if isinstance(expr, a.Subscript):
            # Indexing and broadcasting preserve the element's bundle type.
            return self.reference_type(expr.base, owner, ports_only=ports_only)
        if isinstance(expr, a.Name):
            canonical = owner.aliases.get(expr.text, expr.text)
            if canonical in owner.interface:
                return owner.interface[canonical].type
            if not ports_only:
                for node in owner.body:
                    if isinstance(node, a.Net) and node.name.text == expr.text and not node.global_:
                        return Value(node.type, node.type.span, owner.body_module)
            return None
        if isinstance(expr, a.Member):
            if isinstance(expr.base, a.Name) and expr.base.text == "global" and not ports_only:
                symbol = self.project.board
                board = self.resolver.definitions.get((symbol.module, symbol.name))
                body = board.body if board else symbol.declaration.body
                for node in body:
                    if (
                        isinstance(node, a.Net)
                        and node.global_
                        and node.name.text == expr.name.text
                    ):
                        return Value(
                            node.type, node.type.span, board.body_module if board else symbol.module
                        )
                return None
            field = self.member(
                self.reference_type(expr.base, owner, ports_only=ports_only), expr.name.text
            )
            return field.type if field else None
        return None
