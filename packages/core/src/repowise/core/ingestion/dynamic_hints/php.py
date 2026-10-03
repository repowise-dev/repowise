"""Dynamic-hint extractor for PHP reflective / container patterns."""

from __future__ import annotations

import bisect
import posixpath
import re
from pathlib import Path

from ...code_origin import code_origin
from ..languages.php_same_namespace import blank_php_comments
from .base import DynamicEdge, DynamicHintExtractor

_SKIP_DIRS = {"vendor", "node_modules", ".git", "storage", "bootstrap"}

# call_user_func(['Foo', 'method']) / call_user_func('Foo::method')
_CALL_USER_FUNC_RE = re.compile(
    r"call_user_func(?:_array)?\s*\(\s*(?:\[\s*[\"']([A-Z]\w*)[\"']\s*,\s*[\"']\w+[\"']\s*\]|[\"']([A-Z]\w*)::\w+[\"'])"
)
# new ReflectionClass(Foo::class) / new ReflectionClass('Foo')
_REFLECTION_CLASS_RE = re.compile(
    r"new\s+ReflectionClass\s*\(\s*(?:([A-Z]\w*)::class|[\"']([A-Z]\w*)[\"'])"
)
# $container->get(Foo::class) / app(Foo::class) / resolve(Foo::class)
_CONTAINER_GET_RE = re.compile(
    r"(?:->\s*get|\bapp|\bresolve|\bmake)\s*\(\s*([A-Z]\w*)::class"
)
# new $varname(...)  — pure variable instantiation, no static target
_NEW_DOLLAR_RE = re.compile(r"new\s+\$\w+\s*\(")

_CLASS_DECL_RE = re.compile(r"^\s*(?:abstract\s+|final\s+)?(?:class|interface|trait|enum)\s+([A-Z]\w*)", re.MULTILINE)

# A path the code builds from its own directory: `__DIR__.'/views'`,
# `dirname(__DIR__, 2)."/config/$name.php"`. Group `up` is dirname's level
# count, `path` the literal up to any interpolation.
_DIR_PATH_RE = re.compile(
    r"(?P<call>dirname\(\s*)?__DIR__(?(call)\s*(?:,\s*(?P<up>\d+)\s*)?\))"
    r"""\s*\.\s*(?P<q>['"])(?P<path>/[^'"$\{]*)"""
)
# The literal ends the path: its quote closes with no `.` joining more on.
_CLOSED_RE = re.compile(r"""(['"])(?!\s*\.)""")
# The import graph already follows a `require`/`include` of a literal file.
_INCLUDE_TAIL_RE = re.compile(r"\b(?:require|include)(?:_once)?\s*\(?\s*$")
# A statement that loads the files of a folder it names: a view, migration or
# translation namespace, a publish map, a Finder, glob or directory scan, an
# include of a variable name.
_FOLDER_LOADER_RE = re.compile(
    r"\b(?:load(?:Views|Migrations|Translations|JsonTranslations)From"
    r"|(?:add|replace|prepend)Namespace|publishes|glob|scandir|opendir|in)\s*\("
    r"|\b(?:include|require)(?:_once)?\b"
)


class PhpDynamicHints(DynamicHintExtractor):
    """Discover PHP reflection, container, and dynamic-instantiation patterns."""

    name = "php"

    def extract(self, repo_root: Path) -> list[DynamicEdge]:
        edges: list[DynamicEdge] = []
        sources = self._read_sources(repo_root)

        type_to_file: dict[str, str] = {}
        for rel, text in sources:
            for match in _CLASS_DECL_RE.finditer(text):
                type_to_file.setdefault(match.group(1), rel)

        for rel, text in sources:
            for match in _CALL_USER_FUNC_RE.finditer(text):
                name = match.group(1) or match.group(2)
                target = type_to_file.get(name)
                if target and target != rel:
                    edges.append(DynamicEdge(
                        source=rel, target=target,
                        edge_type="dynamic_uses",
                        hint_source=f"{self.name}:call_user_func",
                    ))

            for match in _REFLECTION_CLASS_RE.finditer(text):
                name = match.group(1) or match.group(2)
                target = type_to_file.get(name)
                if target and target != rel:
                    edges.append(DynamicEdge(
                        source=rel, target=target,
                        edge_type="dynamic_uses",
                        hint_source=f"{self.name}:reflection_class",
                    ))

            for match in _CONTAINER_GET_RE.finditer(text):
                target = type_to_file.get(match.group(1))
                if target and target != rel:
                    edges.append(DynamicEdge(
                        source=rel, target=target,
                        edge_type="dynamic_uses",
                        hint_source=f"{self.name}:container_get",
                    ))

            if _NEW_DOLLAR_RE.search(text):
                edges.append(DynamicEdge(
                    source=rel,
                    target="external:php_dynamic:new_var",
                    edge_type="dynamic_uses",
                    hint_source=f"{self.name}:new_var",
                ))

        edges.extend(self._dir_path_edges(sources))
        return edges

    def _read_sources(self, repo_root: Path) -> list[tuple[str, str]]:
        """``(repo-relative path, text)`` of every first-party PHP file."""
        sources: list[tuple[str, str]] = []
        repo_root_resolved = repo_root.resolve()
        for src in self._rglob(repo_root, "*.php"):
            try:
                rel_path = src.resolve().relative_to(repo_root_resolved)
            except ValueError:
                continue
            if any(part in _SKIP_DIRS for part in rel_path.parts):
                continue
            try:
                text = src.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            sources.append((rel_path.as_posix(), text))
        return sources

    def _dir_path_edges(self, sources: list[tuple[str, str]]) -> list[DynamicEdge]:
        paths = sorted(rel for rel, _ in sources)
        return [
            DynamicEdge(
                source=rel,
                target=target,
                edge_type="dynamic_imports",
                hint_source=f"{self.name}:dir_path",
            )
            for rel, text in sources
            for target in sorted(_dir_path_targets(rel, blank_php_comments(text), paths))
        ]


def _dir_path_targets(rel: str, text: str, paths: list[str]) -> set[str]:
    """The PHP files a ``__DIR__``-relative path in *text* names.

    A file path names that file. A folder names its files only when the
    statement hands it to a loader (:data:`_FOLDER_LOADER_RE`): every file
    under it (a view namespace, a config directory a ``Finder`` reads), or,
    joined with a variable (``"/components/$view.php"``), the files of that
    folder starting with the literal part. *text* has its comments blanked;
    *paths* is the sorted list of the repo's PHP files.
    """
    out = {p for m in _DIR_PATH_RE.finditer(text) for p in _named_files(rel, text, m, paths)}
    out.discard(rel)
    return out


def _named_files(rel: str, text: str, m: re.Match[str], paths: list[str]) -> list[str]:
    """The files one ``__DIR__`` path match in *text* names."""
    target = _dir_path(posixpath.dirname(rel), m)
    if target is None:
        return []
    statement = text[text.rfind(";", 0, m.start()) + 1 : m.start()]
    inside = _under(paths, target)
    if inside or _CLOSED_RE.match(text, m.end("path")) is None:
        if not _loads_folder(rel, target, statement):
            return []
        return inside or _prefixed(paths, target)
    if _contains(paths, target) and not _INCLUDE_TAIL_RE.search(statement):
        return [target]
    return []


def _dir_path(here: str, m: re.Match[str]) -> str | None:
    """The repo-relative path a ``__DIR__`` match builds, or None outside the repo."""
    base = here
    for _ in range(int(m.group("up") or 1) if m.group("call") else 0):
        base = posixpath.dirname(base)
    target = posixpath.normpath(posixpath.join(base, m.group("path").lstrip("/")))
    return None if target in (".", "..") or target.startswith("../") else target


def _loads_folder(rel: str, folder: str, statement: str) -> bool:
    """Whether naming *folder* in *statement* of file *rel* loads its files.

    Only production code handing the folder to a loader counts. A test or a
    tool naming a folder (``tests/bootstrap.php``, a lint config listing its
    scan scope) loads nothing, and a folder holding *rel* itself
    (``__DIR__.'/..'``) says nothing about which sibling is used.
    """
    return (
        _FOLDER_LOADER_RE.search(statement) is not None
        and not (posixpath.dirname(rel) + "/").startswith(folder + "/")
        and code_origin(rel) == "production"
    )


def _prefixed(paths: list[str], prefix: str) -> list[str]:
    """The files directly in *prefix*'s folder whose name starts with its last part."""
    folder, stem = posixpath.split(prefix)
    return [
        p
        for p in _under(paths, folder)
        if posixpath.dirname(p) == folder and posixpath.basename(p).startswith(stem)
    ]


def _contains(paths: list[str], path: str) -> bool:
    i = bisect.bisect_left(paths, path)
    return i < len(paths) and paths[i] == path


def _under(paths: list[str], folder: str) -> list[str]:
    """Every path in sorted *paths* inside *folder*."""
    start = bisect.bisect_left(paths, folder + "/")
    end = bisect.bisect_left(paths, folder + "0")  # "0" sorts right after "/"
    return paths[start:end]
