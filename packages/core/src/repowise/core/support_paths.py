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
from typing import Literal

__all__ = [
    "CONFIG_EXTENSIONS",
    "DOC_EXTENSIONS",
    "FilePopulation",
    "classification_token",
    "file_population",
    "is_doc_or_config_path",
    "is_example_path",
    "is_support_path",
]


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


# Shared with the knowledge graph's node classifier.
CONFIG_EXTENSIONS = frozenset(
    {
        ".yaml", ".yml", ".toml", ".json", ".env", ".ini", ".cfg", ".conf",
        ".properties", ".xml",
    }
)
DOC_EXTENSIONS = frozenset({".md", ".mdx", ".rst", ".txt", ".adoc"})


def classification_token(path: str) -> str:
    """The lowercased token *path* is classified by.

    The extension in almost every case.  Pathlib reports **no** suffix for a
    dotfile whose only dot is the leading one — ``PurePosixPath(".env").suffix
    == ""`` — and the sets above name exactly such files (``".env"`` is an
    entry of ``CONFIG_EXTENSIONS``).  The whole name is the token there, so
    falling back to it keeps entries that name a file rather than an extension
    working (#2379).
    """
    parsed = PurePosixPath(path)
    return (parsed.suffix or parsed.name).lower()


def is_doc_or_config_path(path: str) -> bool:
    """Whether *path* is documentation or configuration rather than code."""
    token = classification_token(path)
    return token in CONFIG_EXTENSIONS or token in DOC_EXTENSIONS


FilePopulation = Literal["production", "test", "example", "doc"]


def file_population(path: str, *, is_test: bool) -> FilePopulation:
    """Which population a file belongs to, for surfaces that hide non-production.

    Disjoint, in precedence order: ``tests/data/x.json`` is a test, not a doc.
    *is_test* is the flag ingestion stored; the path rules cover the other two.
    """
    if is_test:
        return "test"
    if is_example_path(path):
        return "example"
    if is_doc_or_config_path(path):
        return "doc"
    return "production"


def is_support_path(path: str) -> bool:
    """Whether *path* is support material (examples/benchmarks/docs sites).

    Support files never seed or anchor a tour and never surface as entry
    points: a reader orienting in the repo must land in the system itself,
    not in its documentation or sample harnesses.
    """
    return _has_dir_token(path, EXAMPLE_DIR_TOKENS | DOC_DIR_TOKENS)
