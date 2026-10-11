"""Read project configuration and load the explicit, reachable module graph.

This layer binds imported names only. It does not resolve component bodies,
inheritance, metadata, or expressions, and does not elaborate a circuit.
"""

from dataclasses import dataclass, replace
from pathlib import Path
import re
import tomllib
from functools import cached_property

from . import ast as a
from .analysis_cache import cached_project, record_source, record_candidate, watch_config
from .diagnostics import Diagnostic, DiagnosticError, Related, collect_errors
from .parser import Builder, classify, kind_errors, parse

IDENTIFIER = r"[A-Za-z_#][A-Za-z0-9_#]*"
QUALIFIED = re.compile(rf"{IDENTIFIER}(?:\.{IDENTIFIER})*\Z")
BUILTINS = frozenset({"role", "net", "use", "num", "pull", "drive", "std"})
# Symbol.module for a rule or predicate imported from a project ERC namespace.
ERC_MODULE = "<erc>"


class ProjectError(DiagnosticError):
    """Configuration and filesystem errors use command exit status 2."""

    exit_code = 2


@dataclass(frozen=True)
class ProjectConfig:
    path: Path
    language_version: str
    board_module: str
    board_name: str
    source_paths: tuple[Path, ...]
    library_paths: tuple[Path, ...]
    output_dir: Path
    span: a.Span
    target: str
    erc_modules: tuple[Path, ...] = ()
    erc_rule_sets: tuple[str, ...] = ("std.default",)
    # Every configured target name, for recognizing other targets' state files.
    targets: tuple[str, ...] = ()

    @property
    def state_path(self):
        return self.path.parent / f"noscheme.{self.target}.state.json"

    @cached_property
    def erc_registry(self):
        from .erc_registry import load_registry

        return load_registry(self.erc_modules, self.span)

    @property
    def search_paths(self):
        return self.source_paths + self.library_paths


@dataclass(frozen=True)
class Symbol:
    module: str
    name: str
    declaration: a.Definition | None  # None denotes an explicitly imported builtin.


@dataclass(frozen=True)
class LoadedModule:
    name: str
    path: Path
    ast: a.Module
    symbols: dict[str, Symbol]


@dataclass(frozen=True)
class Project:
    config: ProjectConfig
    modules: dict[str, LoadedModule]
    board: Symbol


def expression_path(expr):
    """Return the dotted spelling of a name/member expression, or None."""
    if isinstance(expr, a.Name):
        return expr.text
    if isinstance(expr, a.Member):
        base = expression_path(expr.base)
        return base + "." + expr.name.text if base else None
    return None


def builtin_net(symbols, spelling):
    """Whether a dotted spelling is a builtin net value such as net.power."""
    prefix = symbols.get(spelling.rpartition(".")[0])
    return prefix is not None and prefix.declaration is None and prefix.name == "net"


def net_type(symbols, spelling):
    """Whether a declared type is a net type: a builtin net value, bundle, or net type."""
    symbol = symbols.get(spelling)
    if symbol is not None:
        return symbol.declaration is not None and symbol.declaration.kind in {"bundle", "nettype"}
    return "." in spelling and builtin_net(symbols, spelling)


def extension_kinds(module, tree, symbols):
    """Give each `extend` the kind of its base: a component, block, or net type.

    An extension of an unknown base stays an "extend", whose resolution reports it.
    """
    kinds = {}

    def kind(definition, chain):
        name = definition.name.text
        if name in kinds:
            return kinds[name]
        if name in chain:
            cycle = [*chain[chain.index(name) :], name]
            message = "inheritance cycle: " + " -> ".join(f"{module}.{n}" for n in cycle)
            fail("resolve.inheritance-cycle", message, definition.span)
        names = definition.base
        spelling = ".".join(n.text for n in names)
        symbol = symbols.get(spelling)
        if symbol is None and builtin_net(symbols, spelling):
            result = "nettype"
        elif symbol is None or symbol.declaration is None:
            result = "extend"
        elif symbol.declaration.kind == "extend":
            result = kind(symbol.declaration, [*chain, name])
        else:
            result = symbol.declaration.kind
        kinds[name] = result
        return result

    items = []
    for item in tree.items:
        if isinstance(item, a.Definition) and item.kind == "extend":
            item = replace(item, kind=kind(item, []))
            errors = () if item.kind == "extend" else kind_errors(item)
            if errors:
                raise DiagnosticError(errors)
        items.append(item)
    return replace(tree, items=tuple(items))


def classify_module(tree, symbols):
    """Make each body declaration Nets or an Instance by the kind of its type.

    A type that names nothing makes an instance, whose resolution reports it,
    unless the declaration cannot be one. A declaration of an extension whose own
    base is unknown is left out; that extension's resolution reports the base.
    """
    errors, items = [], []
    for item in tree.items:
        if isinstance(item, a.Definition):
            body = []
            for node in item.body:
                if not isinstance(node, a.Declaration):
                    body.append(node)
                    continue
                spelling = ".".join(n.text for n in node.type)
                symbol = symbols.get(spelling)
                net = net_type(symbols, spelling)
                with collect_errors(errors):
                    try:
                        body.extend(classify(node, net))
                    except DiagnosticError:
                        declaration = symbol and symbol.declaration
                        if declaration and declaration.kind == "extend":
                            continue
                        if net or symbol is not None:
                            raise
                        message = f"unknown definition {spelling!r}"
                        fail("resolve.missing-definition", message, node.type[0].span)
            item = replace(item, body=tuple(body))
        items.append(item)
    if errors:
        raise DiagnosticError(tuple(errors))
    return replace(tree, items=tuple(items))


def bind_definitions(module, tree, symbols):
    for item in tree.items:
        if isinstance(item, a.Definition):
            symbols[item.name.text] = Symbol(module, item.name.text, item)


def fail(code, message, span, *, config=False, related=()):
    cls = ProjectError if config else DiagnosticError
    raise cls([Diagnostic(code, "error", message, span, tuple(related))])


def source_text(path, sources=None):
    """Read a source snapshot, preferring an editor-owned buffer when present."""
    path = Path(path).resolve()
    if sources is not None and path in sources:
        return sources[path]
    record_candidate(path)
    text = path.read_text(encoding="utf-8")
    record_source(path, text)
    return text


def read_config(path: Path | str = ".", *, sources=None, target=None) -> ProjectConfig:
    path = Path(path)
    if path.is_dir():
        path /= "noscheme.toml"
    path = path.resolve()
    source = source_text(path, sources)
    builder = Builder(source, str(path))
    file_span = builder.position_span(0, len(source))

    def span(key, target_name=None):
        # TOML handles parsing. Locate ordinary/quoted top-level keys for diagnostics.
        offset, text = 0, source
        if target_name is not None:
            name = re.escape(target_name)
            header = re.search(
                rf"(?m)^\s*\[\s*targets\s*\.\s*(?:{name}|\"{name}\"|'{name}')\s*\]", source
            )
            if header is None:
                return file_span
            offset = header.end()
            text = re.split(r"(?m)^\s*\[", source[offset:], maxsplit=1)[0]
        match = re.search(rf'(?m)^[ \t]*(?:{key}|"{key}"|\'{key}\')[ \t]*=', text)
        return (
            builder.position_span(offset + match.start(), offset + match.end())
            if match
            else file_span
        )

    def invalid(key, message, target_name=None):
        fail("project.config", message, span(key, target_name), config=True)

    try:
        data = tomllib.loads(source)
    except tomllib.TOMLDecodeError as exc:
        fail("project.toml", str(exc), file_span, config=True)
    expected = {"language_version", "targets", "source_paths", "library_paths", "output_dir"}
    unknown = sorted(data.keys() - expected - {"erc_modules", "erc_rule_sets", "default_target"})
    if unknown:
        invalid(re.escape(unknown[0]), f"unknown configuration key {unknown[0]!r}")
    missing = sorted(expected - data.keys())
    if missing:
        invalid(missing[0], f"missing required configuration key {missing[0]!r}")
    if data["language_version"] != "0.1":
        invalid("language_version", 'language_version must be the string "0.1"')
    targets = data["targets"]
    if not isinstance(targets, dict) or not targets:
        invalid("targets", "targets must be a nonempty table")
    for name, settings in targets.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", name):
            invalid("targets", f"invalid target name {name!r}")
        if not isinstance(settings, dict) or set(settings) - {"root", "output_dir"}:
            invalid("targets", f"target {name!r} allows only root and output_dir")
        root = settings.get("root")
        if not isinstance(root, str) or not QUALIFIED.fullmatch(root) or "." not in root:
            invalid("root", f"target {name!r} must name a qualified root block", name)
        output = settings.get("output_dir")
        if "output_dir" in settings and (
            not isinstance(output, str) or not output or "\0" in output
        ):
            invalid("output_dir", "output_dir must be a nonempty path string", name)
    default = data.get("default_target")
    if "default_target" in data and (not isinstance(default, str) or default not in targets):
        invalid("default_target", "default_target must name a configured target")
    selected = target if target is not None else default
    if selected is None:
        if len(targets) != 1:
            invalid("targets", "select a target with --target or default_target")
        selected = next(iter(targets))
    if selected not in targets:
        invalid("targets", f"unknown target {selected!r}")
    settings = targets[selected]
    base_output = data.get("output_dir")
    if not isinstance(base_output, str) or not base_output or "\0" in base_output:
        invalid("output_dir", "output_dir must be a nonempty path string")
    target_output = settings.get("output_dir", str(Path(base_output) / selected))
    module, name = settings["root"].rsplit(".", 1)
    if module.split(".")[0] == "noscheme":
        invalid("root", "noscheme is reserved for builtins and cannot contain the root", selected)

    def directories(key):
        values = data[key]
        if not isinstance(values, list) or not all(
            isinstance(v, str) and v and "\0" not in v for v in values
        ):
            invalid(key, f"{key} must be an array of nonempty path strings")
        paths = tuple((path.parent / v).resolve() for v in values)
        for directory in paths:
            if not directory.is_dir():
                invalid(key, f"search path is not a directory: {directory}")
        return paths

    sources, libraries = directories("source_paths"), directories("library_paths")
    roots = sources + libraries
    if not roots:
        invalid("source_paths", "at least one source or library search path is required")
    if len(set(roots)) != len(roots):
        invalid("source_paths", "search paths contain duplicate directories")
    output = (path.parent / target_output).resolve()
    if output.exists() and not output.is_dir():
        invalid("output_dir", "output_dir is not a directory")
    modules = data.get("erc_modules", [])
    if not isinstance(modules, list) or any(
        not isinstance(v, str) or not v or "\0" in v for v in modules
    ):
        invalid("erc_modules", "erc_modules must be an array of Python file paths")
    modules = tuple((path.parent / v).resolve() for v in modules)
    if len(set(modules)) != len(modules) or any(
        not v.is_file() or v.suffix != ".py" for v in modules
    ):
        invalid("erc_modules", "erc_modules must name distinct existing Python files")
    rule_sets = data.get("erc_rule_sets", ["std.default"])
    if (
        not isinstance(rule_sets, list)
        or any(not isinstance(v, str) or not v for v in rule_sets)
        or len(set(rule_sets)) != len(rule_sets)
    ):
        invalid("erc_rule_sets", "erc_rule_sets must be an array of distinct rule-set names")
    return watch_config(
        ProjectConfig(
            path,
            "0.1",
            module,
            name,
            sources,
            libraries,
            output,
            span("root", selected),
            selected,
            modules,
            tuple(rule_sets),
            tuple(targets),
        )
    )


def module_path(config, name, span, sources=None):
    if not QUALIFIED.fullmatch(name) or name.split(".")[0] == "noscheme":
        fail("import.module", f"invalid or reserved module name {name!r}", span)
    relative = Path(*name.split(".")).with_suffix(".nosch")
    matches = []
    for root in config.search_paths:
        candidate = root / relative
        record_candidate(candidate)
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root):
            fail("import.escape", f"module {name!r} escapes search root {root}", span)
        if candidate.is_file() or sources is not None and resolved in sources:
            matches.append(resolved)
    if not matches:
        fail("import.missing-module", f"module {name!r} not found in configured search paths", span)
    if len(matches) != 1:
        fail(
            "import.ambiguous-module",
            f"module {name!r} matches multiple paths: " + ", ".join(str(p) for p in matches),
            span,
        )
    return matches[0]


def erc_exports(config):
    """Map each project ERC namespace to its rules and predicates as importable names.

    The standard namespace is a builtin (`from noscheme import std`), not a module.
    """
    if not config.erc_modules:
        return {}
    from .erc_registry import exported_names

    return {
        space: {name: Symbol(ERC_MODULE, qualified, None) for name, qualified in names.items()}
        for space, names in exported_names(config.erc_registry).items()
    }


@cached_project
def load_project(
    path: Path | str = ".", *, sources=None, parsed_sources=None, target=None
) -> Project:
    """Load a project, optionally reusing ASTs from the same analysis.

    parsed_sources maps resolved paths to (source text, AST). Check the text
    before reuse so changed disk files or unsaved buffers never use stale ASTs.
    This mapping is caller-owned and is not a persistent cache.
    """
    config = read_config(path, sources=sources, target=target)
    extensions = erc_exports(config)
    parsed = {}
    dependencies = {}
    order = []
    active = []
    # Iterative traversal keeps large import graphs off the Python call stack.
    stack = [(config.board_module, config.span, False)]
    while stack:
        name, origin, finish = stack.pop()
        if finish:
            active.pop()
            order.append(name)
            continue
        if name in active:
            cycle = active[active.index(name) :] + [name]
            fail("import.cycle", "import cycle: " + " -> ".join(cycle), origin)
        if name in parsed:
            continue
        file = module_path(config, name, origin, sources)
        try:
            text = source_text(file, sources)
            cached = parsed_sources.get(file) if parsed_sources is not None else None
            tree = cached[1] if cached is not None and cached[0] == text else parse(text, str(file))
        except (OSError, UnicodeError) as exc:
            fail("project.io", f"cannot read module {name!r}: {exc}", origin, config=True)
        parsed[name] = (file, tree)
        imports = [item for item in tree.items if isinstance(item, a.Import)]
        dependencies[name] = imports
        active.append(name)
        stack.append((name, origin, True))
        for imp in reversed(imports):
            target = ".".join(n.text for n in imp.module)
            if target in extensions:
                if any(
                    (root / Path(*target.split("."))).with_suffix(".nosch").is_file()
                    for root in config.search_paths
                ):
                    fail(
                        "import.ambiguous-module",
                        f"{target!r} names both an ERC namespace and a source module",
                        imp.span,
                    )
            elif target != "noscheme":
                stack.append((target, imp.span, False))

    modules = {}
    for name in order:
        file, tree = parsed[name]
        symbols = {}
        locations = {}
        namespaces = set()
        for item in tree.items:
            if isinstance(item, a.Definition):
                symbols[item.name.text] = Symbol(name, item.name.text, item)
                locations[item.name.text] = item.name.span
        for imp in dependencies[name]:
            target = ".".join(n.text for n in imp.module)
            exports = (
                {key: Symbol("noscheme", key, None) for key in BUILTINS}
                if target == "noscheme"
                else extensions[target]
                if target in extensions
                else modules[target].symbols
            )

            def bind(key, symbol, span):
                if key in symbols or any(
                    key == ns or ns.startswith(key + ".") for ns in namespaces
                ):
                    fail(
                        "import.duplicate-name",
                        f"name {key!r} is already declared or imported",
                        span,
                        related=[Related("previous declaration", locations[key])],
                    )
                symbols[key] = symbol
                locations[key] = span

            if not imp.names:
                prefixes = [
                    ".".join(target.split(".")[:i]) for i in range(1, len(target.split(".")) + 1)
                ]
                for prefix in prefixes:
                    if prefix in symbols or prefix == target and prefix in namespaces:
                        fail(
                            "import.duplicate-name",
                            f"name {prefix!r} is already declared or imported",
                            imp.span,
                            related=[Related("previous declaration", locations[prefix])],
                        )
                for prefix in prefixes:
                    namespaces.add(prefix)
                    locations.setdefault(prefix, imp.span)
                for key, symbol in exports.items():
                    bind(target + "." + key, symbol, imp.span)
            else:
                for imported in imp.names:
                    original = imported.name.text
                    key = (imported.alias or imported.name).text
                    symbol = exports.get(original)
                    if symbol is None:
                        fail(
                            "import.missing-name",
                            f"module {target!r} does not export {original!r}",
                            imported.name.span,
                        )
                    bind(key, symbol, (imported.alias or imported.name).span)
        # Declarations name types by kind, so extensions take their kinds first.
        tree = extension_kinds(name, tree, symbols)
        bind_definitions(name, tree, symbols)
        tree = classify_module(tree, symbols)
        bind_definitions(name, tree, symbols)
        modules[name] = LoadedModule(name, file, tree, symbols)
    board = modules[config.board_module].symbols.get(config.board_name)
    if board is None or board.declaration is None or board.declaration.kind != "block":
        fail(
            "project.board",
            f"{config.board_module}.{config.board_name} must name a block definition",
            config.span,
        )
    return Project(config, dict(sorted(modules.items())), board)


def project_summary(project):
    """Inspection output, deliberately distinct from a compiled circuit artifact."""
    return {
        "schema_version": 1,
        "stage": "project-loading",
        "config": str(project.config.path),
        "language_version": project.config.language_version,
        "board": f"{project.board.module}.{project.board.name}",
        "output_dir": str(project.config.output_dir),
        "erc_modules": [str(p) for p in project.config.erc_modules],
        "modules": [
            {
                "name": name,
                "path": str(module.path),
                "symbols": {
                    key: {
                        "module": symbol.module,
                        "name": symbol.name,
                        "kind": symbol.declaration.kind if symbol.declaration else "builtin",
                    }
                    for key, symbol in sorted(module.symbols.items())
                },
            }
            for name, module in project.modules.items()
        ],
        "remaining_stages": ["definition-resolution", "elaboration", "ERC", "export"],
    }
