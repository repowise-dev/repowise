"""A decision recorded on a branch is measured from the commit that landed it.

Real ``git``: the rule is an identity between a branch commit and the squash
that carried it, so a stubbed subprocess would test only the parsing.
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from repowise.core.analysis.decisions.landing import evidence_landings, record_landing
from repowise.core.analysis.decisions.staleness import compute_staleness
from repowise.core.precedent.currency import describe_decision_currency

_T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
_BRANCH = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
_RECORDED = datetime(2026, 1, 3, 12, 0, tzinfo=UTC)
_SQUASH = datetime(2026, 1, 4, 12, 0, tzinfo=UTC)
_AFTER = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)


def _git(repo: Path, *args: str, when: datetime | None = None) -> str:
    env = dict(os.environ)
    if when is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when.isoformat()
    out = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True, env=env
    )
    return out.stdout.strip()


def _commit(repo: Path, message: str, when: datetime, content: str) -> str:
    (repo / "rule.py").write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message, when=when)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _commit(root, "start", _T0, "start\n")
    return root


def _branch_commit(repo: Path, subject: str) -> str:
    _git(repo, "checkout", "-q", "-b", "topic")
    sha = _commit(repo, subject, _BRANCH, "branch\n")
    _git(repo, "checkout", "-q", "main")
    return sha


def test_a_squash_titled_after_the_branch_commit_lands_it(repo: Path) -> None:
    branch = _branch_commit(repo, "feat: keep the rule")
    squash = _commit(repo, "feat: keep the rule (#7)", _SQUASH, "branch\n")

    landing = evidence_landings(repo, [branch[:9]])[branch[:9]]

    assert landing.sha == squash
    assert landing.at == _SQUASH


def test_a_squash_body_listing_the_branch_commit_lands_it(repo: Path) -> None:
    branch = _branch_commit(repo, "fix: tighten the rule")
    squash = _commit(
        repo, "feat: a different title (#8)\n\n* feat: other\n\n* fix: tighten the rule", _SQUASH, "b\n"
    )

    assert evidence_landings(repo, [branch])[branch].sha == squash


def test_two_commits_naming_it_leave_it_unresolved(repo: Path) -> None:
    branch = _branch_commit(repo, "feat: keep the rule")
    _commit(repo, "feat: keep the rule (#7)", _SQUASH, "one\n")
    _commit(repo, "feat: keep the rule (#9)", _AFTER, "two\n")

    assert evidence_landings(repo, [branch]) == {}


def test_a_main_line_commit_lands_as_itself_and_session_ids_are_skipped(repo: Path) -> None:
    own = _commit(repo, "feat: direct", _SQUASH, "direct\n")

    landings = evidence_landings(repo, [own, "06e70f9f-2295-4dff-aed1-01a84b843684"])

    assert list(landings) == [own]
    assert landings[own].sha == own


def test_only_a_landing_after_the_record_moves_its_anchor(repo: Path) -> None:
    branch = _branch_commit(repo, "feat: keep the rule")
    _commit(repo, "feat: keep the rule (#7)", _SQUASH, "branch\n")
    landings = evidence_landings(repo, [branch])

    assert record_landing([branch], landings, _RECORDED) is not None
    assert record_landing([branch], landings, _AFTER) is None


def test_the_landing_commit_is_not_drift_but_a_later_one_is(repo: Path) -> None:
    branch = _branch_commit(repo, "feat: keep the rule")
    _commit(repo, "feat: keep the rule (#7)", _SQUASH, "branch\n")
    landing = record_landing([branch], evidence_landings(repo, [branch]), _RECORDED)
    meta = {"rule.py": {"last_commit_at": _SQUASH}}

    assert compute_staleness(_RECORDED, ["rule.py"], meta) == 1.0
    assert compute_staleness(_RECORDED, ["rule.py"], meta, landed_at=landing.at) == 0.0
    later = {"rule.py": {"last_commit_at": _AFTER}}
    assert compute_staleness(_RECORDED, ["rule.py"], later, landed_at=landing.at) == 1.0


def test_decision_show_counts_from_after_the_landing(repo: Path) -> None:
    branch = _branch_commit(repo, "feat: keep the rule")
    _commit(repo, "feat: keep the rule (#7)", _SQUASH, "branch\n")

    sentence = describe_decision_currency(
        repo, created_at=_RECORDED, nodes=["rule.py"], evidence_commits=[branch]
    )
    assert sentence is not None and sentence.startswith("nothing in the file it governs")

    _commit(repo, "refactor: move the rule", _AFTER, "moved\n")
    sentence = describe_decision_currency(
        repo, created_at=_RECORDED, nodes=["rule.py"], evidence_commits=[branch]
    )
    assert sentence is not None and "changed in 1 commit since 2026-01-04" in sentence


def test_a_same_named_commit_that_changes_none_of_its_files_does_not_land_it(repo: Path) -> None:
    branch = _branch_commit(repo, "fix typo")
    (repo / "other.py").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fix typo (#900)", when=_SQUASH)

    assert evidence_landings(repo, [branch]) == {}
