"""What a bug-fix commit's diff actually touches.

``is_fix_commit`` reads the commit *subject*; it cannot tell a one-line null
guard from a docstring typo sweep. On doc-heavy upstream repos that gap is
large: on flask, 53.5% of the commits the subject rule counts as fixes change
no production code at all, which is pure noise in the ``prior_defect``
biomarker (measured in ``local-stash/fix-diff-intelligence``).

So the prior-defect pass opens each matched fix commit's ``-U0`` diff and
buckets it:

``code_fix``
    Touches production code in a way that changes what the code does. The only
    kind that increments ``prior_defect_count``.
``test_only`` / ``doc_only`` / ``config_other``
    Touches only tests, only docs, or only non-code files (build config, CI,
    lockfiles, assets).
``comment_only``
    Touches code files, but the executable content is byte-identical on both
    sides - a typo sweep through docstrings, or a dropped ``# type: ignore``.
``empty``
    No file-level changes at all (an empty commit, or one whose whole diff is
    a mode change).

``is_fix_commit`` itself stays untouched: it is contractually byte-identical
to the defect benchmark's ``lib/defect_counter``, so filtering happens *after*
it and the unfiltered total is kept alongside as ``prior_defect_raw_count``.

Zero LLM, pure path rules + line comparison, deterministic. Ported from the
evaluation harness (``local-stash/fix-diff-intelligence/evallib.py``), whose
frozen 240-commit label set is what the path and comment rules below are tuned
against.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from ._constants import _CODE_EXTENSIONS

if TYPE_CHECKING:
    # Type-only: ``analysis.change_risk`` imports back into this package, so a
    # runtime import here would close an import cycle.
    from ...analysis.changed_lines import FileDiff

__all__ = ["SHAPE_KINDS", "classify_fix_shape", "is_code_path", "is_test_path"]

SHAPE_KINDS: tuple[str, ...] = (
    "code_fix",
    "test_only",
    "doc_only",
    "config_other",
    "comment_only",
    "empty",
)

# ---------------------------------------------------------------------------
# Path rules
# ---------------------------------------------------------------------------

_DOC_EXT = frozenset({".md", ".mdx", ".rst", ".txt", ".adoc"})
_DOC_DIRS = frozenset({"docs", "doc", "documentation"})

# Directory components that mean "this is somebody's test suite". Deliberately
# NOT bare ``test``: ``django/test/`` and ``src/flask/testing.py`` are shipped
# test-*framework* library code that every downstream suite imports, and the
# old ``"test" in path`` substring rule silently dropped every fix to them from
# the defect count. The plural/underscored forms below are the ones repos
# actually use for their own suites.
_TEST_DIRS = frozenset({"tests", "__tests__", "testsuite", "test_suite", "spec", "specs"})

# ``test_x.py`` / ``x_test.go`` / ``x.test.ts`` / ``x.spec.tsx`` / ``XTest.java``
# / ``conftest.py``. Anchored to the basename, so ``vitest.config.ts`` and
# ``testing.py`` stay production code.
_TEST_FILE_RE = re.compile(
    r"^(?:test_.+|.+_test|conftest|.+\.(?:test|spec)|.+Tests?|.+TestCase)\.[^.]+$"
)

# Build/tool configuration that happens to carry a code extension
# (``vitest.config.ts``, ``webpack.config.js``, ``.eslintrc.js``). Fixing one
# is a toolchain fix, not a defect in the product's code.
_CONFIG_FILE_RE = re.compile(r"^(?:\..+rc|.+\.(?:config|conf)|.+\.d)\.[^.]+$")


def _parts(path: str) -> tuple[str, ...]:
    return tuple(p.lower() for p in PurePosixPath(path.replace("\\", "/")).parts)


def is_doc_path(path: str) -> bool:
    p = PurePosixPath(path.replace("\\", "/"))
    return p.suffix.lower() in _DOC_EXT or bool(_DOC_DIRS.intersection(_parts(path)[:-1]))


def is_test_path(path: str) -> bool:
    name = PurePosixPath(path.replace("\\", "/")).name
    if _CONFIG_FILE_RE.match(name):
        return False
    return bool(_TEST_DIRS.intersection(_parts(path)[:-1])) or bool(_TEST_FILE_RE.match(name))


def is_code_path(path: str) -> bool:
    """Production code: a known code extension that is neither test nor config."""
    p = PurePosixPath(path.replace("\\", "/"))
    if p.suffix.lower() not in _CODE_EXTENSIONS:
        return False
    return not _CONFIG_FILE_RE.match(p.name) and not is_test_path(path)


# ---------------------------------------------------------------------------
# Line rules
# ---------------------------------------------------------------------------

# Leading markers that make a whole line a comment or a docstring delimiter.
_COMMENT_PREFIXES = ("#", "//", "/*", "*/", "*", "--", "'''", '"""', "<!--", ";")

# Punctuation that no natural-language sentence carries but almost every line
# of code does. Used to tell reflowed docstring prose from real statements.
_CODE_PUNCT = frozenset("(){}[]=;<>|&\\%@")

# Statement keywords that can open a punctuation-free line of real code
# (``return a or b``, ``import os, sys``, ``del first second``).
_CODE_LEAD_WORDS = frozenset(
    {
        "return",
        "import",
        "from",
        "del",
        "global",
        "nonlocal",
        "raise",
        "assert",
        "yield",
        "pass",
        "break",
        "continue",
        "export",
        "var",
        "let",
        "const",
        "public",
        "private",
        "protected",
        "static",
        "func",
        "fn",
        "package",
        "use",
        "require",
        "module",
        "end",
        "then",
        "do",
        "elif",
        "else",
        "case",
        "default",
        "type",
        "struct",
        "interface",
        "impl",
        "match",
        "with",
    }
)


def _strip_trailing_comment(line: str) -> str:
    """Drop a trailing ``#``/``//`` comment, ignoring tokens inside quotes."""
    quote: str | None = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in ("'", '"', "`"):
            quote = ch
        elif ch == "#" or (ch == "/" and line[i + 1 : i + 2] == "/"):
            return line[:i]
        i += 1
    return line


def _is_prose(text: str) -> bool:
    """Whether a punctuation-free, multi-word line reads as documentation.

    Docstring bodies are the one kind of "code line" that carries no comment
    marker of its own (``some very advanced use cases for which ...``), so a
    prefix test alone reads a reflowed docstring as a code change. Kept
    deliberately strict - any code punctuation, a trailing ``:``, a ``:=``, or
    a leading statement keyword disqualifies the line - because the shape only
    changes when *every* changed line in the commit passes.
    """
    if ":=" in text or text.endswith(":"):
        return False
    if any(c in _CODE_PUNCT for c in text):
        return False
    words = text.split()
    if len(words) < 3:
        return False
    return words[0].strip(":.,").lower() not in _CODE_LEAD_WORDS


def _code_part(raw: str) -> str:
    """A changed line with its trailing comment removed, indentation preserved.

    Indentation is load-bearing: it is what separates "the same statement, now
    nested one level deeper" (a real change) from "the same statement, minus a
    ``# type: ignore``".
    """
    return _strip_trailing_comment(raw).rstrip()


def _is_inert(raw: str) -> bool:
    """Whether a changed line carries no executable content of its own."""
    text = _code_part(raw).strip()
    return not text or text.startswith(_COMMENT_PREFIXES) or _is_prose(text)


def _is_comment_only_change(removed: list[str], added: list[str]) -> bool:
    """Whether a diff's code files changed nothing but comments.

    Two mechanisms count, and nothing else:

    * every changed line is inert - a comment, a blank, or docstring prose;
    * the executable lines pair up one-for-one and differ *only* in their
      trailing comment (``foo(x)  # type: ignore`` -> ``foo(x)``).

    The second clause is why the comparison is ordered and requires the raw
    lines to differ. Sorting the two sides instead would read a swap of two
    adjacent statements as "no change", and dropping the ``raw != raw`` test
    would do the same for a statement moved between hunks - both are real
    behaviour changes that must stay ``code_fix``.
    """
    code_removed = [line for line in removed if not _is_inert(line)]
    code_added = [line for line in added if not _is_inert(line)]
    if not code_removed and not code_added:
        return True
    if len(code_removed) != len(code_added):
        return False
    return all(
        r != a and _code_part(r) == _code_part(a)
        for r, a in zip(code_removed, code_added, strict=True)
    )


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify_fix_shape(files: Mapping[str, FileDiff]) -> str:
    """Bucket a parsed fix diff into one of :data:`SHAPE_KINDS`.

    Order matters. ``doc_only`` needs *every* file to be documentation, so a
    docs-plus-tests commit falls through to ``test_only``; ``comment_only`` is
    only reachable once production-code files are in play. All four non-code
    kinds are excluded from the count either way, so the ordering decides how
    the noise is labelled, not how much of it there is.
    """
    if not files:
        return "empty"

    paths = list(files)
    if all(is_doc_path(p) for p in paths):
        return "doc_only"
    if all(is_test_path(p) or is_doc_path(p) for p in paths):
        return "test_only"

    code_files = [f for p, f in files.items() if is_code_path(p)]
    if not code_files:
        return "config_other"

    removed = [line for f in code_files for line in f.removed]
    added = [line for f in code_files for line in f.added]
    if not removed and not added:
        # Code files present but no changed lines: a pure rename or mode change.
        return "config_other"
    return "comment_only" if _is_comment_only_change(removed, added) else "code_fix"
