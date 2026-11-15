"""The carried-files list names every cache the source writes under ``.repowise/``.

Scans the source text rather than a hand list, so a new cache module fails here
until it is carried. The strong guard is any pickle literal; JSON caches are
caught when named like a cache or a scan record.
"""

from __future__ import annotations

import ast
import re
from fnmatch import fnmatchcase
from functools import cache
from pathlib import Path

from repowise.core.ingestion.git_commit_index import _DEEP_CACHE_GLOB, _deep_cache_name
from repowise.core.pipeline.incremental.carried_files import (
    CARRIED_DIRS,
    CARRIED_FILES,
    CARRIED_GLOBS,
    SEALED_FILES,
)

_PACKAGES = Path(__file__).resolve().parents[3] / "packages"
_SOURCE_ROOTS = [_PACKAGES / name / "src" / "repowise" for name in ("core", "cli", "server")]

# Any pickle, or a data file named like a cache or a scan record.
_CACHE_NAME = re.compile(
    r"^[\w.-]+\.(pkl|pickle)$|^[\w.-]*(cache|scan)[\w.-]*\.(json|jsonl|pkl|pickle|db|bin|msgpack)$",
    re.IGNORECASE,
)


def _literal(node: ast.AST) -> str | None:
    """A string constant, or an f-string with each placeholder read as ``1``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value if isinstance(part, ast.Constant) else "1" for part in node.values
        )
    return None


@cache
def _modules() -> tuple[tuple[Path, ast.Module], ...]:
    files = sorted(p for root in _SOURCE_ROOTS for p in root.rglob("*.py"))
    assert files, f"no sources under {_SOURCE_ROOTS}"
    return tuple((p, ast.parse(p.read_text(encoding="utf-8"))) for p in files)


def _cache_names(tree: ast.Module) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        value = _literal(node)
        if value is not None and _CACHE_NAME.match(value):
            names.add(value)
    return names


def _carried(name: str) -> bool:
    return name in CARRIED_FILES or any(fnmatchcase(name, glob) for glob in CARRIED_GLOBS)


def test_every_cache_filename_in_the_source_is_carried() -> None:
    missing = {
        f"{path.relative_to(_PACKAGES).as_posix()}: {name}"
        for path, tree in _modules()
        for name in _cache_names(tree)
        if not _carried(name)
    }
    assert not missing, (
        "cache files not in repowise.core.pipeline.incremental.carried_files: "
        f"{sorted(missing)}"
    )


def test_sealed_files_are_the_pickles_of_cache_seal_users() -> None:
    sealed = set()
    for _path, tree in _modules():
        uses_seal = any(
            isinstance(node, ast.ImportFrom) and node.module == "repowise.core.cache_seal"
            for node in ast.walk(tree)
        )
        if uses_seal:
            sealed |= {n for n in _cache_names(tree) if n.endswith((".pkl", ".pickle"))}
    assert sealed == set(SEALED_FILES)


def test_deep_cache_glob_matches_every_region() -> None:
    assert _DEEP_CACHE_GLOB in CARRIED_GLOBS
    for region in (1, 2, 7, 12):
        assert fnmatchcase(_deep_cache_name(region), _DEEP_CACHE_GLOB)


def test_lists_have_no_duplicates_or_paths() -> None:
    names = [*CARRIED_FILES, *CARRIED_GLOBS, *CARRIED_DIRS]
    assert len(names) == len(set(names))
    assert set(SEALED_FILES) <= set(CARRIED_FILES)
    assert all("/" not in n and "\\" not in n for n in names)
