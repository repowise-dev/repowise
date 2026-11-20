"""Gitignore-style exclusions shared by traversal and Git history."""

from __future__ import annotations

from collections.abc import Iterable

import pathspec


def file_patterns(patterns: Iterable[str]) -> list[str]:
    """Keep ``/**/`` from matching the directory preceding it."""
    result = []
    for pattern in patterns:
        stripped = pattern.rstrip(" ")
        if stripped.endswith("/**/"):
            pattern = stripped[:-1] + "/*/" + pattern[len(stripped):]
        result.append(pattern)
    return result


def directory_patterns(patterns: Iterable[str]) -> list[str]:
    """Keep the directory itself traversable for a trailing ``/**`` rule."""
    result = []
    for pattern in file_patterns(patterns):
        stripped = pattern.rstrip(" ")
        if stripped.endswith("/**"):
            pattern = stripped + "/*" + pattern[len(stripped):]
        result.append(pattern)
    return result


def excluded_file(path: str, files: pathspec.PathSpec, directories: pathspec.PathSpec) -> bool:
    """A file is excluded if it or any parent directory is excluded."""
    if files.match_file(path):
        return True
    parts = path.split("/")
    return any(directories.match_file("/".join(parts[:i]) + "/") for i in range(1, len(parts)))
