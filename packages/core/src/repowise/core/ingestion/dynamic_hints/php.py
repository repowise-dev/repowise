"""Dynamic-hint extractor for PHP reflective / container patterns."""

from __future__ import annotations

import bisect
import posixpath
import re
from pathlib import Path

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


class PhpDynamicHints(DynamicHintExtractor):
    """Discover PHP reflection, container, and dynamic-instantiation patterns."""

    name = "php"

    def extract(self, repo_root: Path) -> list[DynamicEdge]:
        edges: list[DynamicEdge] = []

        type_to_file: dict[str, str] = {}
        php_files: list[tuple[Path, str]] = []
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
            rel = rel_path.as_posix()
            php_files.append((src, text))
            sources.append((rel, text))
            for match in _CLASS_DECL_RE.finditer(text):
                type_to_file.setdefault(match.group(1), rel)

        for src, text in php_files:
            try:
                rel = src.resolve().relative_to(repo_root.resolve()).as_posix()
            except ValueError:
                continue

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

        edges.extend(self._dir_path_edges(sources, repo_root))
        return edges

    def _dir_path_edges(
        self, sources: list[tuple[str, str]], repo_root: Path
    ) -> list[DynamicEdge]:
        paths = sorted(rel for rel, _ in sources)
        return [
            DynamicEdge(
                source=rel,
                target=target,
                edge_type="dynamic_imports",
                hint_source=f"{self.name}:dir_path",
            )
            for rel, text in sources
            for target in sorted(_dir_path_targets(rel, text, paths, repo_root))
        ]



def _dir_path_targets(rel: str, text: str, paths: list[str], repo_root: Path) -> set[str]:
    """The PHP files a ``__DIR__``-relative path in *text* names.

    A file path names that file. A directory path names every file under it:
    a view namespace, a config directory a ``Finder`` reads, a folder joined
    with a variable name (``'/stubs/'.$name``, ``"/components/$view.php"``,
    which keeps only the files of that folder starting with the literal
    part). *paths* is the sorted list of the repo's PHP files.
    """
    here = posixpath.dirname(rel)
    out: set[str] = set()
    for m in _DIR_PATH_RE.finditer(text):
        target = _dir_path(here, m)
        if target is None:
            continue
        inside = _under(paths, target)
        if inside:
            if _loads_folder(here, target, repo_root):
                out.update(inside)
        elif _CLOSED_RE.match(text, m.end("path")) is None:  # joined with a variable
            out.update(_prefixed(paths, target))
        elif _contains(paths, target) and not _INCLUDE_TAIL_RE.search(
            text[max(0, m.start() - 20) : m.start()]
        ):
            out.add(target)
    out.discard(rel)
    return out


def _dir_path(here: str, m: re.Match[str]) -> str | None:
    """The repo-relative path a ``__DIR__`` match builds, or None outside the repo."""
    base = here
    for _ in range(int(m.group("up") or 1) if m.group("call") else 0):
        base = posixpath.dirname(base)
    target = posixpath.normpath(posixpath.join(base, m.group("path").lstrip("/")))
    return None if target in (".", "..") or target.startswith("../") else target


def _loads_folder(here: str, folder: str, repo_root: Path) -> bool:
    """Whether naming *folder* from a file in *here* says its files are loaded.

    Not for a folder holding the file itself (``__DIR__.'/..'`` says nothing
    about which sibling is used), nor for a top folder named from a package
    root file (``rector.php`` listing ``src`` and ``tests``), which is a tool's
    scan scope rather than a load.
    """
    if (here + "/").startswith(folder + "/"):
        return False
    return not (posixpath.dirname(folder) == here and _is_package_root(repo_root, here))


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


def _is_package_root(repo_root: Path, folder: str) -> bool:
    return not folder or (repo_root / folder / "composer.json").is_file()
