"""Restricted expression evaluator and named parameter binding; never Python eval."""

from dataclasses import dataclass
import math
import re
from string import Formatter

from . import ast as a
from .diagnostics import Diagnostic, DiagnosticError, Related
from .metadata import Value
from .project import IDENTIFIER, expression_path
from .resolve import Resolver


@dataclass(frozen=True)
class Number:
    value: int | float

    @property
    def tag(self):
        return "integer" if type(self.value) is int else "double"


@dataclass(frozen=True)
class Limits:
    integer_bits: int = 65536
    string_length: int = 65536
    format_width: int = 4096
    format_precision: int = 100
    expression_depth: int = 128
    instance_declarations: int = 10000
    dimension: int = 100000


class Evaluator:
    def __init__(self, project, limits=None):
        self.project = project
        self.limits = limits or Limits()
        self.warnings = []
        self.instance_path = None

    def error(self, code, message, span, related=()):
        raise DiagnosticError(
            [Diagnostic(code, "error", message, span, tuple(related), self.instance_path)]
        )

    def number(self, value, span):
        if isinstance(value, float) and not math.isfinite(value):
            self.error("number.nonfinite", "numeric result must be finite", span)
        if type(value) is int and value.bit_length() > self.limits.integer_bits:
            self.error("number.limit", "integer exceeds configured bit limit", span)
        return Number(value)

    def numeric(self, value, span):
        if not isinstance(value, Number):
            self.error("expression.type", "expected a number", span)
        return value.value

    def integer(self, value, span, *, explicit=False):
        number = self.numeric(value, span)
        if type(number) is float and not explicit:
            self.warnings.append(
                Diagnostic(
                    "number.implicit-integer",
                    "warning",
                    "double converted to integer by truncation toward zero; use num.int to make this explicit",
                    span,
                    instance_path=self.instance_path,
                )
            )
        return int(number)

    def dimension(self, value, span):
        number = self.integer(value, span)
        if not 1 <= number <= self.limits.dimension:
            self.error(
                "array.dimension", f"dimension must be between 1 and {self.limits.dimension}", span
            )
        return number

    def index(self, value, span, size, *, stop=False):
        number = self.integer(value, span)
        valid = 0 <= number <= size if stop else 0 <= number < size
        if not valid:
            self.error("array.index", "index is outside the array bounds", span)
        return number

    def bounded_string(self, text, span):
        if len(text) > self.limits.string_length:
            self.error("format.limit", "string exceeds configured length limit", span)
        return text

    def evaluate(self, expression, module, environment, depth=0):
        if depth > self.limits.expression_depth:
            self.error(
                "expression.limit", "expression exceeds configured depth limit", expression.span
            )
        expr = expression

        def run(child):
            return self.evaluate(child, module, environment, depth + 1)

        try:
            if isinstance(expr, a.Number):
                if expr.tag == "integer":
                    # Parse in chunks to avoid Python's process-global decimal conversion limit.
                    if len(expr.spelling.lstrip("0")) > self.limits.integer_bits:
                        self.error(
                            "number.limit", "integer literal exceeds configured limit", expr.span
                        )
                    value = 0
                    for i in range(0, len(expr.spelling), 1000):
                        chunk = expr.spelling[i : i + 1000]
                        value = value * (10 ** len(chunk)) + int(chunk)
                    return self.number(value, expr.span)
                return self.number(float(expr.spelling), expr.span)
            if isinstance(expr, a.String):
                return self.bounded_string(expr.value, expr.span)
            if isinstance(expr, a.Name):
                if expr.text not in environment:
                    self.error(
                        "parameter.missing", f"no value for parameter {expr.text!r}", expr.span
                    )
                return environment[expr.text]
            if isinstance(expr, a.Group):
                return run(expr.expression)
            if isinstance(expr, a.Unary):
                value = self.numeric(run(expr.operand), expr.span)
                return self.number(-value if expr.operator == "-" else value, expr.span)
            if isinstance(expr, a.Binary):
                left, right = (
                    self.numeric(run(expr.left), expr.left.span),
                    self.numeric(run(expr.right), expr.right.span),
                )
                op = expr.operator
                if op in {"<<", ">>"}:
                    if type(left) is not int or type(right) is not int or right < 0:
                        self.error(
                            "number.shift",
                            "shifts require integer-tagged operands and a nonnegative count",
                            expr.span,
                        )
                    if right > self.limits.integer_bits:
                        self.error(
                            "number.limit", "shift count exceeds configured limit", expr.span
                        )
                    value = left << right if op == "<<" else left >> right
                elif op == "/":
                    value = left / right
                else:
                    if type(left) is float or type(right) is float:
                        left, right = float(left), float(right)
                    value = (
                        left + right if op == "+" else left - right if op == "-" else left * right
                    )
                return self.number(value, expr.span)
            if isinstance(expr, a.Call) and isinstance(expr.callee, a.Member):
                member = expr.callee
                args = [run(x) for x in expr.arguments]
                kwargs = {x.name.text: run(x.value) for x in expr.keywords}
                if member.name.text == "format":
                    template = run(member.base)
                    if not isinstance(template, str):
                        self.error("expression.type", ".format requires a string", member.base.span)
                    return self.format(template, args, kwargs, expr.span)
                if expression_path(member.base) is None:
                    self.error(
                        "expression.call", "arbitrary method calls are not allowed", expr.span
                    )
                namespace, helper = expression_path(member.base), member.name.text
                symbol = self.project.modules[module].symbols.get(namespace)
                if (
                    namespace.split(".")[0] in environment
                    or symbol is None
                    or symbol.module != "noscheme"
                    or symbol.name not in {"num", "f"}
                ):
                    self.error(
                        "expression.call",
                        "helper namespace must be explicitly imported",
                        member.span,
                    )
                namespace = symbol.name
                if len(args) != 1 or kwargs:
                    self.error(
                        "expression.arguments",
                        "numeric and unit helpers take one positional argument",
                        expr.span,
                    )
                value = self.numeric(args[0], expr.span)
                if namespace == "num" and helper in {"int", "ceil", "floor", "round"}:
                    if helper == "round":
                        if type(value) is int:
                            rounded = value
                        else:
                            numerator, denominator = value.as_integer_ratio()
                            magnitude, remainder = divmod(abs(numerator), denominator)
                            rounded = (magnitude + (2 * remainder >= denominator)) * (
                                -1 if numerator < 0 else 1
                            )
                    else:
                        rounded = {"int": int, "ceil": math.ceil, "floor": math.floor}[helper](
                            value
                        )
                    return self.number(rounded, expr.span)
                if namespace == "f" and helper in {
                    "Ohm",
                    "kOhm",
                    "MOhm",
                    "pF",
                    "nF",
                    "uF",
                    "nH",
                    "uH",
                    "mH",
                }:
                    return self.bounded_string(self.display(args[0], expr.span) + helper, expr.span)
                self.error("expression.call", "unknown numeric or unit helper", member.span)
            self.error("expression.unsupported", "unsupported expression", expr.span)
        except ZeroDivisionError:
            self.error("number.divide-by-zero", "division by zero", expr.span)
        except OverflowError:
            self.error(
                "number.overflow", "number cannot be represented as a finite double", expr.span
            )

    @staticmethod
    def decimal(value):
        sign = "-" if value < 0 else ""
        value = abs(value)
        chunks = []
        while value:
            value, remainder = divmod(value, 10**9)
            chunks.append(remainder)
        return sign + (
            str(chunks[-1]) + "".join(f"{x:09d}" for x in reversed(chunks[:-1])) if chunks else "0"
        )

    def display(self, value, span):
        if isinstance(value, str):
            return value
        number = self.numeric(value, span)
        if type(number) is int:
            return self.decimal(number)
        text = repr(number)
        return text[:-2] if text.endswith(".0") else text

    def format(self, template, args, kwargs, span):
        result, length = [], 0
        try:
            fields = Formatter().parse(template)
            for literal, name, spec, conversion in fields:
                text = literal
                if name is not None:
                    if conversion or not (
                        name.isascii() and name.isdecimal() or re.fullmatch(IDENTIFIER, name)
                    ):
                        self.error(
                            "format.field",
                            "use an explicit numeric index or name; traversal and conversions are forbidden",
                            span,
                        )
                    if name.isascii() and name.isdecimal():
                        if len(name) > 9 or int(name) >= len(args):
                            self.error(
                                "format.argument",
                                f"missing positional format argument {name!r}",
                                span,
                            )
                        value = args[int(name)]
                    else:
                        if name not in kwargs:
                            self.error(
                                "format.argument", f"missing named format argument {name!r}", span
                            )
                        value = kwargs[name]
                    if spec:
                        match = re.fullmatch(
                            r"(?P<width>[0-9]*)(?:\.(?P<precision>[0-9]+))?(?P<type>[dfeg])", spec
                        )
                        if not match:
                            self.error(
                                "format.spec",
                                "supported numeric formats are [width][.precision]d/f/e/g",
                                span,
                            )
                        width_text, precision_text = (
                            match["width"] or "0",
                            match["precision"] or "0",
                        )
                        if len(width_text) > 6 or len(precision_text) > 6:
                            self.error(
                                "format.limit", "format width or precision exceeds limits", span
                            )
                        width, precision = int(width_text), int(precision_text)
                        if (
                            width > self.limits.format_width
                            or precision > self.limits.format_precision
                        ):
                            self.error(
                                "format.limit", "format width or precision exceeds limits", span
                            )
                        number = self.numeric(value, span)
                        if match["type"] == "d":
                            if type(number) is not int or match["precision"] is not None:
                                self.error(
                                    "format.type",
                                    "d format requires an integer tag and no precision",
                                    span,
                                )
                            rendered = self.decimal(number)
                            if width_text.startswith("0") and width:
                                rendered = rendered.zfill(width)
                            else:
                                rendered = rendered.rjust(width)
                        else:
                            rendered = format(float(number), spec)
                    else:
                        rendered = self.display(value, span)
                    text += rendered
                length += len(text)
                if length > self.limits.string_length:
                    self.error(
                        "format.limit", "formatted string exceeds configured length limit", span
                    )
                result.append(text)
        except (ValueError, OverflowError) as exc:
            if isinstance(exc, DiagnosticError):
                raise
            self.error("format.invalid", f"invalid formatting: {exc}", span)
        return "".join(result)

    def bind(self, definition, arguments, caller_module, caller_environment, span):
        bound, supplied = {}, {}
        for argument in arguments:
            name = argument.name.text
            if name not in definition.parameters or name in supplied:
                self.error(
                    "parameter.argument", f"unknown or duplicate argument {name!r}", argument.span
                )
            supplied[name] = argument
            bound[name] = self.evaluate(argument.value, caller_module, caller_environment)
        for name, parameter in definition.parameters.items():
            if name not in bound and parameter.default is None:
                self.error(
                    "parameter.required",
                    f"missing required parameter {name!r}",
                    span,
                    [Related("parameter declared here", parameter.span)],
                )
        pending = set(definition.parameters) - bound.keys()

        # Resolve default dependencies without evaluating overridden defaults.
        def references(expr):
            if isinstance(expr, a.Name):
                return {expr.text}
            if isinstance(expr, (a.Number, a.String)):
                return set()
            if isinstance(expr, a.Group):
                return references(expr.expression)
            if isinstance(expr, a.Unary):
                return references(expr.operand)
            if isinstance(expr, a.Binary):
                return references(expr.left) | references(expr.right)
            if isinstance(expr, a.Call):
                children = list(expr.arguments) + [x.value for x in expr.keywords]
                if expr.callee.name.text == "format":
                    children.append(expr.callee.base)
                return set().union(*(references(x) for x in children))
            return set()

        dependencies = {
            name: references(definition.parameters[name].default.value) for name in pending
        }
        while pending:
            ready = sorted(name for name in pending if not (dependencies[name] & pending))
            if not ready:
                name = sorted(pending)[0]
                self.error(
                    "parameter.cycle",
                    "cyclic parameter defaults: " + ", ".join(sorted(pending)),
                    definition.parameters[name].default.span,
                )
            for name in ready:
                default = definition.parameters[name].default
                bound[name] = self.evaluate(default.value, default.module, bound)
                pending.remove(name)
        for name, value in bound.items():
            parameter = definition.parameters[name]
            if (parameter.type == "number") != isinstance(value, Number):
                location = supplied[name].span if name in supplied else parameter.default.span
                self.error(
                    "parameter.type",
                    f"parameter {name!r} requires {parameter.type}",
                    location,
                    [Related("parameter declared here", parameter.span)],
                )
        return bound


def encoded(value):
    if isinstance(value, Number):
        # Decimal strings preserve arbitrarily large integers in JSON consumers.
        return {
            "type": "number",
            "tag": value.tag,
            "value": Evaluator.decimal(value.value) if value.tag == "integer" else value.value,
        }
    return {"type": "string", "value": value}


def evaluate_project(project, definitions, limits=None):
    """Evaluate declaration templates; instance arrays are not expanded here."""
    evaluator = Evaluator(project, limits)
    resolver = Resolver(project)
    resolver.definitions = definitions
    root_key = (project.board.module, project.board.name)
    root = definitions[root_key]
    stack = [(root_key, root.name, (), (), {}, root.module, root.source.span, (), None)]
    records = []
    while stack:
        key, path, ancestry, arguments, caller, caller_module, span, dimensions, value_override = (
            stack.pop()
        )
        evaluator.instance_path = path
        if key in ancestry:
            evaluator.error(
                "instance.cycle",
                "recursive block instantiation cannot produce a finite circuit",
                span,
            )
        if len(records) >= evaluator.limits.instance_declarations:
            evaluator.error(
                "instance.limit", "instance declaration count exceeds configured limit", span
            )
        definition = definitions[key]
        parameters = evaluator.bind(definition, arguments, caller_module, caller, span)

        def shape(value):
            selectors = []
            expr = value.value
            while isinstance(expr, a.Subscript):
                selectors.append(expr.selection)
                expr = expr.base
            return [
                evaluator.dimension(evaluator.evaluate(s.start, value.module, parameters), s.span)
                for s in reversed(selectors)
            ]

        shapes = {
            name: shape(port.type) for name, port in definition.interface.items() if port.type
        }
        values = {}
        if value_override is not None:
            values["value"] = encoded(value_override)
        elif "value" in definition.metadata:
            value = definition.metadata["value"]
            values["value"] = encoded(evaluator.evaluate(value.value, value.module, parameters))
        records.append(
            {
                "path": path,
                "definition": ".".join(key),
                "kind": definition.kind,
                "dimensions": list(dimensions),
                "parameters": {k: encoded(v) for k, v in sorted(parameters.items())},
                "interface_dimensions": shapes,
                "values": values,
                "connection_selections": {},
                "net_dimensions": {
                    n.name.text: shape(Value(n.type, n.span, definition.body_module))
                    for n in definition.body
                    if isinstance(n, a.Net)
                },
            }
        )
        children = []
        for node in definition.body:
            if not isinstance(node, a.Instance):
                continue
            target = resolver.symbol(definition.body_module, node.component, {"component", "block"})
            child_path = path + "." + node.name.text
            evaluator.instance_path = child_path
            child_dimensions = tuple(
                evaluator.dimension(
                    evaluator.evaluate(s.start, definition.body_module, parameters), s.span
                )
                for s in node.dimensions
            )
            args = next((s.entries for s in node.sections if s.name == "params"), ())
            selected = []
            for section in node.sections:
                if section.name != "connections":
                    continue
                for binding in section.entries:
                    selections = [
                        x
                        for x in (*binding.endpoint.instance_indices, *binding.endpoint.selectors)
                        if isinstance(x, a.Index)
                    ]
                    target_expr = binding.target
                    rhs = []
                    while isinstance(target_expr, (a.Member, a.Subscript)):
                        if isinstance(target_expr, a.Subscript):
                            rhs.append(target_expr.selection)
                        target_expr = target_expr.base
                    selections.extend(reversed(rhs))
                    for selection in selections:
                        start = evaluator.integer(
                            evaluator.evaluate(selection.start, definition.body_module, parameters),
                            selection.span,
                        )
                        stop = (
                            evaluator.integer(
                                evaluator.evaluate(
                                    selection.stop, definition.body_module, parameters
                                ),
                                selection.span,
                            )
                            if selection.stop
                            else None
                        )
                        if start < 0 or stop is not None and stop <= start:
                            evaluator.error(
                                "array.selection",
                                "indices must be nonnegative and slices nonempty and ascending",
                                selection.span,
                            )
                        selected.append(
                            {"start": start, "stop": stop, "span": a.to_data(selection.span)}
                        )
            records[-1]["connection_selections"][node.name.text] = selected
            # Instance value overrides are evaluated in the caller, not the child's parameter scope.
            overrides = next((s.entries for s in node.sections if s.name == "metadata"), ())
            override = next((f for f in overrides if f.key.first.text == "value"), None)
            override_value = (
                evaluator.evaluate(override.value, definition.body_module, parameters)
                if override
                else None
            )
            children.append(
                (
                    target,
                    child_path,
                    ancestry + (key,),
                    args,
                    parameters,
                    definition.body_module,
                    node.span,
                    child_dimensions,
                    override_value,
                )
            )
        stack.extend(reversed(children))
    report = {
        "schema_version": 1,
        "stage": "expression-evaluation",
        "array_representation": "unexpanded declaration templates",
        "instances": records,
        "warnings": [a.to_data(d) for d in evaluator.warnings],
        "remaining_stages": ["connectivity-elaboration", "ERC", "export"],
    }
    return report, tuple(evaluator.warnings)
