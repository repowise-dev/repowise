"""Every fix count counts the same commits: the ``is_fix_commit`` rule.

The per-file commit categories and the evolution timeline once each carried a
broader "fix" regex of their own, so a file page, the commits page and the
bug-fix history disagreed about how many fixes there were.
"""

from __future__ import annotations

import pytest

from repowise.core.ingestion.git_indexer._constants import (
    classify_commit_category,
    is_fix_commit,
)
from repowise.core.ingestion.git_indexer.file_history import _commit_category

SUBJECTS = [
    "fix: null check in parser",
    "fix(api): handle empty body",
    "Fix typo in README",
    "fix: bump deps",
    "chore: fix lint",
    'Revert "feat: add cache"',
    "revert: drop flaky retry",
    "handle crash on startup",
    "error handling for uploads",
    "feat: add retry, fixes #12",
    "refactor: move helpers, closes #40",
    "hotfix: restore login",
    "bugfix: off-by-one in pager",
    "Resolve race in scheduler",
    "docs: explain the bug in the FAQ",
    "patch the vendored parser",
    "feat: add export",
    "",
]


@pytest.mark.parametrize("subject", SUBJECTS)
def test_every_surface_labels_fix_exactly_as_is_fix_commit(subject) -> None:
    want = is_fix_commit(subject)
    assert (classify_commit_category(subject) == "fix") == want
    assert (_commit_category(subject) == "fix") == want
