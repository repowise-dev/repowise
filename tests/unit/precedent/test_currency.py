"""The one git question, asked against a real checkout.

Real ``git`` rather than a mocked subprocess on purpose: the value of this
module is that it delegates the judgement to git, so a test that stubs git out
would assert only that the string formatting works.
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from repowise.core.precedent.currency import commits_since, describe_decision_currency

#: Commit timestamps are pinned rather than taken from the clock. ``--since``
#: has one-second resolution, so a fixture that commits twice in the same
#: second cannot distinguish "born before" from "born after" — which is the
#: only thing these tests are checking.
_BIRTH_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
_LATER_TIME = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
#: Between the two commits, so a record born here has one commit after it.
_BETWEEN = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)


def _git(repo: Path, *args: str, when: datetime | None = None) -> str:
    env = dict(os.environ)
    if when is not None:
        stamp = when.isoformat()
        env["GIT_AUTHOR_DATE"] = stamp
        env["GIT_COMMITTER_DATE"] = stamp
    out = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return out.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / "kept.py").write_text("original\n", encoding="utf-8")
    (root / "moved.py").write_text("original\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "birth", when=_BIRTH_TIME)
    return root


def _touch_and_commit(repo: Path, name: str, when: datetime = _LATER_TIME) -> None:
    (repo / name).write_text("changed\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"touch {name}", when=when)


# -- the primitive -----------------------------------------------------------


def test_counts_commits_since_a_birth_commit_scoped_to_nodes(repo: Path) -> None:
    birth = _git(repo, "rev-parse", "HEAD")
    _touch_and_commit(repo, "moved.py")

    assert commits_since(repo, since_commit=birth, nodes=["moved.py"]) == 1
    assert commits_since(repo, since_commit=birth, nodes=["kept.py"]) == 0


def test_counts_commits_since_a_date(repo: Path) -> None:
    """Decision records have a created_at and no birth commit column."""
    _touch_and_commit(repo, "moved.py")

    assert commits_since(repo, since_date=_BETWEEN, nodes=["moved.py"]) == 1
    assert commits_since(repo, since_date=_BETWEEN, nodes=["kept.py"]) == 0
    # A record born before the checkout's first commit sees all of it.
    assert commits_since(repo, since_date=_BIRTH_TIME - timedelta(days=1), nodes=["moved.py"]) == 2


def test_every_failure_mode_returns_none_rather_than_a_partial_count(
    repo: Path, tmp_path: Path
) -> None:
    birth = _git(repo, "rev-parse", "HEAD")

    assert commits_since(repo, since_commit="not-a-ref", nodes=["kept.py"]) is None
    assert commits_since(tmp_path / "nowhere", since_commit=birth) is None
    # Neither anchor given: nothing to measure from.
    assert commits_since(repo, nodes=["kept.py"]) is None


# -- the decision-facing sentence --------------------------------------------


def test_untouched_scope_reads_as_still_holding(repo: Path) -> None:
    _touch_and_commit(repo, "moved.py")

    sentence = describe_decision_currency(repo, created_at=_BETWEEN, nodes=["kept.py"])

    assert sentence is not None
    assert "nothing in the file it governs has changed" in sentence


def test_changed_scope_says_so_with_a_count(repo: Path) -> None:
    _touch_and_commit(repo, "moved.py")

    sentence = describe_decision_currency(
        repo, created_at=_BETWEEN, nodes=["moved.py", "kept.py"]
    )

    assert sentence is not None
    assert "the 2 files it governs changed in 1 commit since" in sentence


def test_unscoped_record_says_it_cannot_be_checked(repo: Path) -> None:
    """The half a 0.0 score cannot express: never checkable, not checked-and-fresh."""
    sentence = describe_decision_currency(repo, created_at=datetime.now(UTC), nodes=[])

    assert sentence == "not bound to any file, so whether it still holds cannot be checked"


def test_silence_when_git_cannot_decide(tmp_path: Path) -> None:
    sentence = describe_decision_currency(
        tmp_path / "not-a-repo", created_at=datetime.now(UTC), nodes=["a.py"]
    )

    assert sentence is None
