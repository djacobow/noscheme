"""Read project configuration and load the explicit, reachable module graph.

This layer binds imported names only. It does not resolve component bodies,
inheritance, metadata, or expressions, and does not elaborate a circuit.
"""

from dataclasses import dataclass
from pathlib import Path
import re
import tomllib
from functools import cached_property

from . import ast as a
from .analysis_cache import cached_project, record_source, record_candidate, watch_config
from .diagnostics import Diagnostic, DiagnosticError, Related
from .parser import Builder, parse

IDENTIFIER = r"[A-Za-z_#][A-Za-z0-9_#]*"
QUALIFIED = re.compile(rf"{IDENTIFIER}(?:\.{IDENTIFIER})*\Z")
BUILTINS = frozenset({"role", "nettype", "unused", "num", "bias", "std"})
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
    erc_modules: tuple[Path, ...] = ()
    erc_rule_sets: tuple[str, ...] = ("std.default",)

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


def read_config(path: Path | str = ".", *, sources=None) -> ProjectConfig:
    path = Path(path)
    if path.is_dir():
        path /= "noscheme.toml"
    path = path.resolve()
    source = source_text(path, sources)
    builder = Builder(source, str(path))
    file_span = builder.position_span(0, len(source))

    def span(key):
        # TOML handles parsing. Locate ordinary/quoted top-level keys for diagnostics.
        match = re.search(rf'(?m)^[ \t]*(?:{key}|"{key}"|\'{key}\')[ \t]*=', source)
        return builder.position_span(match.start(), match.end()) if match else file_span

    def invalid(key, message):
        fail("project.config", message, span(key), config=True)

    try:
        data = tomllib.loads(source)
    except tomllib.TOMLDecodeError as exc:
        fail("project.toml", str(exc), file_span, config=True)
    expected = {"language_version", "board", "source_paths", "library_paths", "output_dir"}
    unknown = sorted(data.keys() - expected - {"erc_modules", "erc_rule_sets"})
    if unknown:
        invalid(re.escape(unknown[0]), f"unknown configuration key {unknown[0]!r}")
    missing = sorted(expected - data.keys())
    if missing:
        invalid(missing[0], f"missing required configuration key {missing[0]!r}")
    if data["language_version"] != "0.1":
        invalid("language_version", 'language_version must be the string "0.1"')
    board = data["board"]
    if not isinstance(board, str) or not QUALIFIED.fullmatch(board) or "." not in board:
        invalid("board", 'board must name a module and definition, such as "main.Blink"')
    module, name = board.rsplit(".", 1)
    if module.split(".")[0] == "noscheme":
        invalid("board", "noscheme is reserved for builtins and cannot contain the board")

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
    output = data["output_dir"]
    if not isinstance(output, str) or not output or "\0" in output:
        invalid("output_dir", "output_dir must be a nonempty path string")
    output = (path.parent / output).resolve()
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
            span("board"),
            modules,
            tuple(rule_sets),
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
def load_project(path: Path | str = ".", *, sources=None, parsed_sources=None) -> Project:
    """Load a project, optionally reusing ASTs from the same analysis.

    parsed_sources maps resolved paths to (source text, AST). Check the text
    before reuse so changed disk files or unsaved buffers never use stale ASTs.
    This mapping is caller-owned and is not a persistent cache.
    """
    config = read_config(path, sources=sources)
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
        modules[name] = LoadedModule(name, file, tree, symbols)
    board = modules[config.board_module].symbols.get(config.board_name)
    if board is None or board.declaration is None or board.declaration.kind != "board":
        fail(
            "project.board",
            f"{config.board_module}.{config.board_name} must name a board definition",
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
