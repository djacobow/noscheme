"""Restricted expression evaluator and named parameter binding; never Python eval."""

from dataclasses import dataclass
import math
import re
from string import Formatter

from . import ast as a
from .diagnostics import Diagnostic, DiagnosticError, Related
from .metadata import Value
from .instances import arguments as instance_arguments, element_overrides
from .project import IDENTIFIER, expression_path
from .resolve import Resolver
from .units import QUANTITIES, format_quantity, number_value
from .integers import checked_integer


@dataclass(frozen=True)
class Number:
    value: int | float

    @property
    def tag(self):
        return "integer" if type(self.value) is int else "double"


@dataclass(frozen=True)
class Limits:
    integer_bits: int = 64
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
        if type(value) is int:
            try:
                checked_integer(value, self.limits.integer_bits)
            except ValueError:
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
        return self.number(int(number), span).value

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
                try:
                    value = number_value(expr.spelling)
                except ValueError:
                    self.error("number.limit", "integer exceeds configured bit limit", expr.span)
                return self.number(value, expr.span)
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
                if expr.operator == "-" and isinstance(expr.operand, a.Number):
                    try:
                        value = number_value(expr.operand.spelling, negative=True)
                    except ValueError:
                        self.error(
                            "number.limit", "integer exceeds configured bit limit", expr.span
                        )
                    return self.number(value, expr.span)
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
                    if right >= min(self.limits.integer_bits, 64):
                        self.error(
                            "number.limit", "shift count exceeds configured limit", expr.span
                        )
                    value = left << right if op == "<<" else left >> right
                elif op == "/":
                    value = float(left) / float(right)
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
                    or symbol.name != "num"
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
                        "numeric helpers take one positional argument",
                        expr.span,
                    )
                value = self.numeric(args[0], expr.span)
                if namespace == "num" and helper in {"int", "ceil", "floor", "round"}:
                    if helper == "round":
                        if type(value) is int:
                            rounded = value
                        else:
                            fraction, magnitude = math.modf(abs(value))
                            rounded = (int(magnitude) + (fraction >= 0.5)) * (
                                -1 if value < 0 else 1
                            )
                    else:
                        rounded = {"int": int, "ceil": math.ceil, "floor": math.floor}[helper](
                            value
                        )
                    return self.number(rounded, expr.span)
                self.error("expression.call", "unknown numeric helper", member.span)
            self.error("expression.unsupported", "unsupported expression", expr.span)
        except ZeroDivisionError:
            self.error("number.divide-by-zero", "division by zero", expr.span)
        except OverflowError:
            self.error(
                "number.overflow", "number cannot be represented as a finite double", expr.span
            )

    def display(self, value, span):
        if isinstance(value, str):
            return value
        number = self.numeric(value, span)
        if type(number) is int:
            return str(number)
        text = repr(number)
        return text[:-2] if text.endswith(".0") else text

    @staticmethod
    def displayed(expression, parameters, value):
        """Render a part value; a number gets a unit only as a bare quantity parameter.

        `parameters` are the declarations of the scope where `expression` is written:
        the component for its own `value` or `metadata.value`, the caller for an
        instance override. Computed and literal numbers display plainly.
        """
        name = expression.text if isinstance(expression, a.Name) else expression
        parameter = parameters.get(name)
        if not isinstance(value, Number) or parameter is None or parameter.type not in QUANTITIES:
            return value
        return format_quantity(value.value, parameter.type)

    def part_value(self, definition, parameters):
        """The displayed value from `metadata.value` or the `value` parameter, if any."""
        shown = definition.metadata.get("value")
        if shown is not None:
            value = self.evaluate(shown.value, shown.module, parameters)
            return self.displayed(shown.value, definition.parameters, value)
        if "value" in parameters:
            return self.displayed("value", definition.parameters, parameters["value"])
        return None

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
                            rendered = str(number)
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
            # A quantity also accepts a verbatim string, such as "10k 1%" or "DNP".
            if (
                parameter.type == "string"
                and not isinstance(value, str)
                or parameter.type == "number"
                and not isinstance(value, Number)
            ):
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
        # Decimal strings preserve all 64 integer bits in JSON consumers.
        return {
            "type": "number",
            "tag": value.tag,
            "value": str(value.value) if value.tag == "integer" else value.value,
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
        # An override is already rendered in its caller's scope.
        shown = (
            value_override
            if value_override is not None
            else evaluator.part_value(definition, parameters)
        )
        if shown is not None:
            values["value"] = encoded(shown)
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
            args = instance_arguments(node)
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
                evaluator.displayed(
                    override.value,
                    definition.parameters,
                    evaluator.evaluate(override.value, definition.body_module, parameters),
                )
                if override
                else None
            )
            elements = element_overrides(node, child_dimensions, evaluator)
            variants = []
            count = math.prod(child_dimensions)
            if len(elements) < count:
                variants.append((child_path, args, child_dimensions, override_value))
            for indices, element in elements.items():
                fields = next((s.entries for s in element.sections if s.name == "metadata"), ())
                field = next((f for f in fields if f.key.first.text == "value"), None)
                value = (
                    evaluator.displayed(
                        field.value,
                        definition.parameters,
                        evaluator.evaluate(field.value, definition.body_module, parameters),
                    )
                    if field
                    else override_value
                )
                variants.append(
                    (
                        child_path + "".join(f"[{i}]" for i in indices),
                        instance_arguments(node, element),
                        (),
                        value,
                    )
                )
            for variant_path, variant_args, variant_dimensions, variant_value in variants:
                children.append(
                    (
                        target,
                        variant_path,
                        ancestry + (key,),
                        variant_args,
                        parameters,
                        definition.body_module,
                        node.span,
                        variant_dimensions,
                        variant_value,
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
