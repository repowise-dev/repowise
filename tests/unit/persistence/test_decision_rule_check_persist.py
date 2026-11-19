"""The staleness pass reads whether a record's rule still holds, and currency follows it.

Real ``git`` and an in-memory store: the pass reads diffs from the checkout and
writes its verdict on the record, and the review lanes read that verdict.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.analysis.decisions.lifecycle import effective_currency
from repowise.core.persistence.crud import recompute_decision_staleness
from repowise.core.persistence.crud.authority import current_currency, reaffirm_decision
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import (
    DecisionAcceptance,
    DecisionEvidence,
    DecisionRecord,
    GitMetadata,
    Repository,
)

_REPO_ID = "repo1"
_T0 = datetime.now(UTC).replace(microsecond=0) - timedelta(days=30)
_RECORDED = _T0 + timedelta(days=1)
_LATER = _T0 + timedelta(days=2)

_SRC = '''\
def retry_limit() -> int:
    # Three tries: the upstream rate limit resets within that window.
    return 3


def unrelated() -> int:
    return 1
'''


def _git(repo: Path, *args: str, when: datetime | None = None) -> str:
    env = dict(os.environ)
    if when is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when.isoformat()
    out = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True, env=env
    )
    return out.stdout.strip()


def _commit(repo: Path, files: dict[str, str], when: datetime) -> None:
    for name, content in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change", when=when)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _commit(root, {"src/retry.py": _SRC, "tests/test_retry.py": "def test_x():\n    pass\n"}, _T0)
    return root


@pytest.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add(Repository(id=_REPO_ID, name="r", local_path="/tmp/r"))
        await s.flush()
        yield s
    await engine.dispose()


async def _accepted(
    session, *, decision: str, files: list[str], quote: str = "", moved: tuple[str, ...] = ()
) -> DecisionRecord:
    rec = DecisionRecord(
        id="d1",
        repository_id=_REPO_ID,
        title="Retry three times",
        decision=decision,
        rationale="because",
        evidence_file=files[0],
        status="active",
        staleness_score=0.0,
        affected_files_json=json.dumps(files),
        created_at=_RECORDED,
    )
    session.add(rec)
    if quote:
        session.add(
            DecisionEvidence(
                decision_id="d1", source="comment", evidence_file=files[0], source_quote=quote
            )
        )
    session.add(
        DecisionAcceptance(
            repository_id=_REPO_ID,
            decision_id="d1",
            seq=1,
            action="accepted",
            currency="active",
            reason="reviewed",
            scope_json=json.dumps(files),
            evidence_json=json.dumps(files),
            accepter="tester",
            accepter_kind="import",
            created_at=_RECORDED,
        )
    )
    for path in files:
        when = _LATER if not moved or path in moved else _T0
        session.add(GitMetadata(repository_id=_REPO_ID, file_path=path, last_commit_at=when))
    await session.flush()
    return rec


async def test_moved_files_whose_rule_is_untouched_read_active(session, repo: Path) -> None:
    _commit(repo, {"src/retry.py": _SRC.replace("return 1", "return 2")}, _LATER)
    rec = await _accepted(session, decision="`retry_limit` allows three tries", files=["src/retry.py"])

    await recompute_decision_staleness(session, _REPO_ID, {}, repo)

    assert rec.staleness_score == 1.0
    assert (rec.rule_check, rec.rule_check_basis) == ("holds", "spans_unchanged")
    assert rec.rule_checked_commit == _git(repo, "rev-parse", "HEAD")
    assert await current_currency(session, rec) == "active"


async def test_a_comment_rule_that_went_away_reads_needs_review(session, repo: Path) -> None:
    quote = "Three tries: the upstream rate limit resets within that window."
    _commit(repo, {"src/retry.py": _SRC.replace(quote, "Retries.")}, _LATER)
    rec = await _accepted(session, decision=quote, files=["src/retry.py"], quote=quote)

    await recompute_decision_staleness(session, _REPO_ID, {}, repo)

    assert rec.rule_check == "drifted"
    assert await current_currency(session, rec) == "needs_review"


async def test_a_test_file_edit_never_moves_churn(session, repo: Path) -> None:
    _commit(repo, {"tests/test_retry.py": "def test_y():\n    pass\n"}, _LATER)
    rec = await _accepted(
        session,
        decision="retry three times",
        files=["src/retry.py", "tests/test_retry.py"],
        moved=("tests/test_retry.py",),
    )

    await recompute_decision_staleness(session, _REPO_ID, {}, repo)

    assert rec.staleness_score == 0.0
    assert rec.rule_check is None


async def test_no_new_commit_on_the_scope_means_no_recheck(session, repo: Path) -> None:
    _commit(repo, {"src/retry.py": _SRC.replace("return 1", "return 2")}, _LATER)
    rec = await _accepted(session, decision="`retry_limit` allows three tries", files=["src/retry.py"])
    await recompute_decision_staleness(session, _REPO_ID, {}, repo)
    rec.rule_check_basis = "kept"
    _commit(repo, {"other.py": "x = 1\n"}, _LATER + timedelta(hours=1))

    await recompute_decision_staleness(session, _REPO_ID, {}, repo)

    assert rec.rule_check_basis == "kept"


async def test_a_grant_clears_the_verdict_for_the_next_index(session, repo: Path) -> None:
    rec = await _accepted(session, decision="retry three times", files=["src/retry.py"])
    rec.rule_check, rec.rule_check_basis, rec.rule_checked_commit = "drifted", "quote_gone", "abc"
    rec.evidence_file = "src/retry.py"
    await session.flush()

    await reaffirm_decision(session, rec, accepter="tester")

    assert (rec.rule_check, rec.rule_check_basis, rec.rule_checked_commit) == (None, None, None)


@pytest.mark.parametrize(
    ("rule_check", "staleness", "expected"),
    [
        ("holds", 1.0, "active"),
        ("drifted", 0.0, "needs_review"),
        ("unknown", 1.0, "needs_review"),
        ("unknown", 0.2, "active"),
        (None, 1.0, "needs_review"),
        (None, 0.2, "active"),
    ],
)
def test_the_rule_verdict_outranks_churn_and_unknown_keeps_it(rule_check, staleness, expected):
    assert (
        effective_currency("active", has_scope=True, staleness=staleness, rule_check=rule_check)
        == expected
    )


def test_files_gone_still_read_stale_whatever_the_rule_verdict() -> None:
    assert (
        effective_currency(
            "active", has_scope=True, staleness=0.0, artifacts_gone=True, rule_check="holds"
        )
        == "stale"
    )


async def test_a_git_failure_leaves_the_churn_verdict_standing(session, repo: Path, monkeypatch):
    import repowise.core.analysis.decisions.rule_check as rule_check

    def boom(*_args, **_kwargs):
        raise TimeoutError("git took too long")

    monkeypatch.setattr(rule_check, "check_rules", boom)
    _commit(repo, {"src/retry.py": _SRC.replace("return 1", "return 2")}, _LATER)
    rec = await _accepted(session, decision="`retry_limit` allows three tries", files=["src/retry.py"])

    await recompute_decision_staleness(session, _REPO_ID, {}, repo)

    assert rec.staleness_score == 1.0
    assert rec.rule_check is None
    assert await current_currency(session, rec) == "needs_review"


async def test_a_verdict_on_a_record_whose_files_no_longer_moved_is_cleared(session, repo: Path):
    rec = await _accepted(session, decision="retry three times", files=["src/retry.py"], moved=("x",))
    rec.rule_check, rec.rule_check_basis = "drifted", "quote_gone"
    await session.flush()

    await recompute_decision_staleness(session, _REPO_ID, {}, repo)

    assert rec.staleness_score == 0.0
    assert rec.rule_check is None
    assert await current_currency(session, rec) == "active"


@pytest.mark.parametrize(
    ("basis", "reason"),
    [
        ("quote_gone", "the comment it came from was removed"),
        ("symbol_gone:retry_limit", "`retry_limit` no longer exists"),
        ("literal_gone:500_000", "`500_000` was removed from the code it names"),
    ],
)
def test_every_surface_names_what_left_the_rule_in_words(basis: str, reason: str) -> None:
    from types import SimpleNamespace

    from repowise.core.analysis.decisions.lifecycle import rule_drift_reason
    from repowise.server.mcp_server.tool_why.dashboard import _rule_changed

    assert rule_drift_reason(basis) == reason
    drifted = SimpleNamespace(rule_check="drifted", rule_check_basis=basis)
    assert _rule_changed(drifted) == {"rule_changed": reason}
    # An unflagged or churn-only row carries no extra key, so its bytes do not grow.
    assert _rule_changed(SimpleNamespace(rule_check="unknown", rule_check_basis="no_anchor")) == {}


async def test_an_undecided_record_is_not_read_again_until_its_files_move(
    session, repo: Path, monkeypatch
):
    import repowise.core.analysis.decisions.rule_check as rule_check

    _commit(repo, {"src/retry.py": _SRC.replace("return 1", "return 2")}, _LATER)
    rec = await _accepted(session, decision="`retry_limit` allows three tries", files=["src/retry.py"])
    rec.created_at = _T0 - timedelta(days=365)  # before any history: git cannot read it
    await session.flush()
    await recompute_decision_staleness(session, _REPO_ID, {}, repo)
    assert (rec.rule_check, rec.rule_check_basis) == ("unknown", "unread")
    assert rec.rule_checked_commit == _git(repo, "rev-parse", "HEAD")
    assert await current_currency(session, rec) == "needs_review"  # the churn reading stands

    calls = []
    real = rule_check.check_rules
    monkeypatch.setattr(
        rule_check, "check_rules", lambda *a, **k: calls.append(a) or real(*a, **k)
    )
    _commit(repo, {"other.py": "x = 1\n"}, _LATER + timedelta(hours=1))
    await recompute_decision_staleness(session, _REPO_ID, {}, repo)

    assert calls == []
