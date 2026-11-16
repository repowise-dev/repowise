"""Resolve gitignore-style history exclusions without losing re-included paths."""

from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Any

import pathspec

from ..exclusions import directory_patterns, excluded_file, file_patterns


def historical_pathspecs(
    repo: Any, prefix: str, boundaries: tuple[str, ...], patterns: list[str], revision: str
) -> tuple[str, ...]:
    """Find included historical file names, including paths deleted from HEAD."""
    spec = pathspec.PathSpec.from_lines("gitwildmatch", file_patterns(patterns))
    dirs = pathspec.PathSpec.from_lines("gitwildmatch", directory_patterns(patterns))
    paths: set[str] = set()
    excluded: set[str] = set()
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            [
                "git", "-C", str(repo.working_tree_dir), "log", "-m", "--name-only",
                "--no-renames", "--format=", "-z", revision,
            ],
            stdout=subprocess.PIPE,
            stderr=errors,
        )
        assert process.stdout is not None
        pending = b""
        while chunk := process.stdout.read(65536):
            entries = (pending + chunk).split(b"\0")
            pending = entries.pop()
            for entry in entries:
                if not entry:
                    continue
                path = os.fsdecode(entry)
                if path in paths or path in excluded:
                    continue
                relative = path
                if prefix:
                    relative = path[len(prefix) + 1:] if path.startswith(prefix + "/") else ""
                included = bool(relative) and not excluded_file(relative, spec, dirs)
                if included and boundaries and not any(
                    relative == boundary or relative.startswith(boundary + "/")
                    for boundary in boundaries
                ):
                    included = False
                if included:
                    paths.add(path)
                else:
                    excluded.add(path)
        process.stdout.close()
        returncode = process.wait()
        if returncode:
            errors.seek(0)
            message = errors.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"Could not enumerate Git history paths: {message}")
        if pending:
            raise RuntimeError("Git history path output ended without a NUL delimiter")
    if not paths:
        return (":(top,glob)**", ":(top,exclude,glob)**")
    if not excluded:
        return (":(top,glob)**",)

    includes = _compress_paths(paths, excluded, exclude=False)
    excludes = _compress_paths(excluded, paths, exclude=True)
    if len(excludes) + 1 < len(includes):
        return (":(top,glob)**", *excludes)
    return tuple(includes)


def _compress_paths(paths: set[str], opposite: set[str], *, exclude: bool) -> list[str]:
    """Collapse a directory only when no opposite-class history path is beneath it."""
    blocked: set[str] = set()
    for path in opposite:
        parts = path.split("/")
        blocked.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    compressed: set[str] = set()
    for path in sorted(paths):
        parts = path.split("/")
        for i in range(1, len(parts)):
            parent = "/".join(parts[:i])
            if parent not in blocked:
                compressed.add(parent)
                break
        else:
            compressed.add(path)
    specs = []
    for path in sorted(compressed):
        magic = "top,exclude" if exclude else "top"
        if path not in blocked:
            specs.append(f":({magic},literal){path}")
            continue
        # A literal pathspec also matches descendants. Force glob matching for
        # a historical file whose name was later reused as a directory.
        encoded = os.fsencode(path)
        prefix = b"".join(
            (b"\\" + bytes([char])) if char in b"\\*?[" else bytes([char])
            for char in encoded[:-1]
        )
        exact = prefix + b"[\\" + encoded[-1:] + b"]"
        specs.append(f":({magic},glob)" + os.fsdecode(exact))
    return specs


def history_git(
    repo: Any, command: str, options: list[str], revision: str, pathspecs: tuple[str, ...]
) -> str:
    """Run a history command with pathspecs on stdin, avoiding argument-size limits."""
    if not pathspecs:
        return getattr(repo.git, command)(*options, revision)

    unusual = {spec for spec in pathspecs if "\n" in spec or "\r" in spec}
    with tempfile.TemporaryFile() as source:
        source.write(os.fsencode(revision + "\n--\n"))
        for spec in pathspecs:
            if spec not in unusual:
                source.write(os.fsencode(spec + "\n"))
        source.seek(0)
        args = [*options, "--stdin"]
        if unusual:
            args.extend(["--", *sorted(unusual)])
        return getattr(repo.git, command)(*args, istream=source)
