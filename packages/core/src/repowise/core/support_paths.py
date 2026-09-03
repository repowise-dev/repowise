"""Which paths are support material rather than the system itself.

Sibling of :mod:`repowise.core.test_paths`, and there for the same reason: one
traversal, so callers asking the same question cannot get different answers.

Two predicates, deliberately not one. :func:`is_example_path` covers code that
demonstrates or measures the system; :func:`is_support_path` adds documentation
sites. A docs tree is not a module of the system the way an example crate is,
so callers meaning "written to illustrate the subject" want the former.
"""

from __future__ import annotations

from pathlib import PurePosixPath

__all__ = ["is_example_path", "is_support_path"]


# Example/demo/benchmark directories: documentation-by-code and support
# harnesses, not the system itself. Their files carry entry-style names
# (main.go, index.js) by convention, so without demotion they flood entry
# points and the tour on any repo that ships samples or benchmarks.
EXAMPLE_DIR_TOKENS = frozenset(
    {
        "examples",
        "_examples",
        "example",
        "samples",
        "sample",
        "demo",
        "demos",
        "bench",
        "benches",
        "benchmarks",
    }
)


# Documentation directories: static-site trees and runnable doc snippets. Like
# the example dirs above, their files carry entry-style names by convention
# but document the system rather than being it.
DOC_DIR_TOKENS = frozenset({"docs", "doc", "website"})


def _has_dir_token(path: str, tokens: frozenset[str]) -> bool:
    """Whether any directory segment of *path* is one of *tokens*.

    Matched at any depth, not just the repo root, since a workspace member
    keeps its own ``examples/``. The basename is excluded so a file named
    ``examples.py`` stays production code.
    """
    return any(s.lower() in tokens for s in PurePosixPath(path).parts[:-1])


def is_example_path(path: str) -> bool:
    """Whether *path* is example, demo, or benchmark code.

    It ships in the repo and is worth reading, but it is not a subsystem:
    grouped with the code it imports it inflates that module, and a large
    example tree can dominate a community's label.
    """
    return _has_dir_token(path, EXAMPLE_DIR_TOKENS)


def is_support_path(path: str) -> bool:
    """Whether *path* is support material (examples/benchmarks/docs sites).

    Support files never seed or anchor a tour and never surface as entry
    points: a reader orienting in the repo must land in the system itself,
    not in its documentation or sample harnesses.
    """
    return _has_dir_token(path, EXAMPLE_DIR_TOKENS | DOC_DIR_TOKENS)
