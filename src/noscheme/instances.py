"""Shared handling of explicit array element overrides."""


def arguments(node, element=None):
    result = {}
    for source in (node, element):
        if source:
            for section in source.sections:
                if section.name == "params":
                    result.update((arg.name.text, arg) for arg in section.entries)
    return tuple(result.values())


def element_overrides(node, dimensions, evaluator):
    result = {}
    for section in node.sections:
        if section.name != "elements":
            continue
        for element in section.entries:
            indices = tuple(
                evaluator.index(evaluator.evaluate(index.start, "", {}), index.span, size)
                for index, size in zip(element.indices, dimensions, strict=True)
            )
            if indices in result:
                evaluator.error(
                    "array.element-overlap",
                    "array element is overridden more than once",
                    element.span,
                )
            result[indices] = element
    return result
