"""Import-stem resolution helpers for the dependency graph builder."""

from __future__ import annotations

from pathlib import Path

# Path segments that mark a file as low-value for stem-based import resolution.
_LOW_VALUE_PATH_SEGMENTS = frozenset(
    {
        "tests",
        "test",
        "_tests",
        "__tests__",
        "testing",
        "test_apps",
        "testdata",
        "test_data",
        "fixtures",
        "examples",
        "example",
        "samples",
        "sample",
        "scripts",
        "benchmarks",
        "bench",
        "docs",
        "doc",
    }
)


def _stem_priority(path: str, stem: str) -> tuple[int, int, int, str]:
    """Sort key for choosing among files that share an import stem.

    Lower tuples sort first; callers take ``candidates[0]`` as the resolution.
    """
    path_obj = Path(path)
    parts = path_obj.parts
    if path_obj.name == "__init__.py":
        parent_match = 0
    else:
        parent_dir = parts[-2].lower() if len(parts) >= 2 else ""
        parent_match = 0 if parent_dir == stem else 1
    low_value = 1 if any(seg.lower() in _LOW_VALUE_PATH_SEGMENTS for seg in parts) else 0
    return (parent_match, low_value, len(parts), path)


def build_stem_map(path_set: set[str]) -> dict[str, list[str]]:
    """Map import-stems to candidate file paths, sorted best-first."""
    buckets: dict[str, list[str]] = {}
    for p in path_set:
        path_obj = Path(p)
        if path_obj.name == "__init__.py":
            parent = path_obj.parent.name
            if not parent:
                continue
            stem = parent.lower()
        else:
            stem = path_obj.stem.lower()
        buckets.setdefault(stem, []).append(p)

    for stem, paths in buckets.items():
        paths.sort(key=lambda candidate: _stem_priority(candidate, stem))
    return buckets
