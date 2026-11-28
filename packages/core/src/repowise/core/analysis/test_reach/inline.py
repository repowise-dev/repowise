"""Files that carry their own tests, so no separate test file has to reach them.

Rust compiles a ``#[cfg(test)]`` module inside the file it tests, and Go runs a
package's ``_test.go`` files in that package's directory. Neither needs an
import edge, so a graph walk from test files can miss both.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from pathlib import PurePosixPath

# ``#[cfg(test)]`` on its own attribute line. Ceiling: a line comment is
# skipped but a block comment is not; the parser's attribute nodes are the
# upgrade path if commented-out test modules show up.
_RUST_CFG_TEST_RE = re.compile(rb"^[ \t]*#\s*\[\s*cfg\s*\(\s*test\s*\)\s*\]", re.MULTILINE)


def rust_has_inline_tests(content: str | bytes) -> bool:
    """Whether Rust source holds a ``#[cfg(test)]`` item: tests compiled into the file."""
    data = content.encode("utf-8", "replace") if isinstance(content, str) else content
    return bool(_RUST_CFG_TEST_RE.search(data))


def go_inline_tests(path: str, test_files: Collection[str]) -> list[str]:
    """The ``_test.go`` files in a Go file's directory, which are its package's tests.

    The directory is the package, so every one of them runs with it. That
    includes an external ``package x_test`` file, which tests the package
    through its exports.
    """
    p = PurePosixPath(path)
    if p.suffix != ".go" or p.name.endswith("_test.go"):
        return []
    folder = str(p.parent)
    return sorted(
        t
        for t in test_files
        if t.endswith("_test.go") and str(PurePosixPath(t).parent) == folder
    )


def inline_tests(path: str, content: str | bytes | None, test_files: Collection[str]) -> list[str]:
    """What runs *path*'s own tests: itself (Rust), its package's test files (Go), or nothing."""
    if path.endswith(".rs") and content is not None and rust_has_inline_tests(content):
        return [path]
    return go_inline_tests(path, test_files)
