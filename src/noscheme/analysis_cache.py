"""Opt-in, content-checked compiler caches for long-lived editor workers."""

from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path

from .diagnostics import DiagnosticError

_ACTIVE = ContextVar("noscheme_analysis_cache", default=None)


class ParseCache:
    """Bound retained source variants; failures never retain exception tracebacks."""

    def __init__(self, max_entries=256, max_bytes=16 * 1024 * 1024):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.entries = OrderedDict()
        self.bytes = 0
        self.hits = 0
        self.misses = 0

    def parse(self, callback, source, filename):
        key = (str(filename), source)
        if key in self.entries:
            self.hits += 1
            result, errors, size = self.entries.pop(key)
        else:
            self.misses += 1
            try:
                result, errors = callback(source, filename), None
            except DiagnosticError as exc:
                result, errors = None, exc.diagnostics
            size = len(source.encode("utf-8")) + len(str(filename).encode("utf-8"))
            self.bytes += size
        self.entries[key] = result, errors, size
        while self.entries and (
            len(self.entries) > self.max_entries or self.bytes > self.max_bytes
        ):
            _, (_, _, removed) = self.entries.popitem(last=False)
            self.bytes -= removed
        if errors is not None:
            raise DiagnosticError(errors)
        return result


class AnalysisCache:
    """Memoized compiler objects belong to one exact editor/disk snapshot."""

    def __init__(self, parses=None):
        self.parses = parses if parses is not None else ParseCache()
        self.memo = {}
        self.disk = {}
        self.directories = {}

    @contextmanager
    def activate(self):
        token = _ACTIVE.set(self)
        try:
            yield self
        finally:
            _ACTIVE.reset(token)

    def valid(self):
        try:
            return all(read_record(path, data) == data for path, data in self.disk.items()) and all(
                directory_entries(path) == names for path, names in self.directories.items()
            )
        except (OSError, UnicodeError):
            return False

    def watch_config(self, config):
        for root in config.search_paths:
            self.directories[root] = directory_entries(root)
        for path in config.erc_modules:
            record_candidate(path)
            self.disk[path] = path.read_bytes()


def read_record(path, previous):
    if previous is None:
        return None if not path.exists() else path.read_bytes()
    return path.read_text(encoding="utf-8") if isinstance(previous, str) else path.read_bytes()


def record_candidate(path):
    cache = _ACTIVE.get()
    if cache is not None and not path.exists():
        cache.disk[path] = None


def watch_config(config):
    cache = _ACTIVE.get()
    if cache is not None:
        cache.watch_config(config)
    return config


def directory_entries(root):
    return tuple(sorted(str(p) for p in root.rglob("*.nosch"))) if root.exists() else ()


def record_source(path, text):
    cache = _ACTIVE.get()
    if cache is not None:
        cache.disk[path] = text


def cached_parse(callback):
    @wraps(callback)
    def wrapped(source, filename="<input>"):
        cache = _ACTIVE.get()
        if cache is None:
            return callback(source, filename)
        return cache.parses.parse(callback, source, filename)

    return wrapped


def cached_project(callback):
    @wraps(callback)
    def wrapped(path=".", *, sources=None, parsed_sources=None):
        cache = _ACTIVE.get()
        if cache is None:
            return callback(path, sources=sources, parsed_sources=parsed_sources)
        key = ("project", Path(path).resolve(), tuple(sorted((sources or {}).items())))
        if key not in cache.memo:
            project = callback(path, sources=sources, parsed_sources=parsed_sources)
            cache.memo[key] = project
        return cache.memo[key]

    return wrapped


def cached_resolution(callback):
    @wraps(callback)
    def wrapped(self):
        cache = _ACTIVE.get()
        if cache is None:
            return callback(self)
        key = (callback.__name__, id(self.project), self.max_pins, self.max_expression_depth)
        if key not in cache.memo:
            cache.memo[key] = callback(self)
        # Resolvers mutate their own maps when expanding positional bindings.
        result = dict(cache.memo[key])
        self.definitions = dict(result)
        return result

    return wrapped
