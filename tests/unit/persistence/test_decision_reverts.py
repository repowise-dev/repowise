"""Revert-based supersession against a real synthetic git history.

A decision mined from a commit that was later reverted must stop reading as
current, and one whose revert was itself reverted must read as current again.
Anything the three rules cannot pin to exactly one ancestor stays unmatched.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from repowise.core.analysis.decisions.reverts import (
    _resolve,
    apply_revert_supersession,
    find_revert_links,
    reverted_at_head,
)
from repowise.core.persistence.crud import get_decision, unretire_auto_superseded
from repowise.core.persistence.models import DecisionEvidence, DecisionRecord
from tests.unit.persistence.helpers import insert_repo


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, message: str, name: str | None = None) -> str:
    path = repo / (name or f"f{len(list(repo.iterdir()))}.txt")
    path.write_text(f"{message}\n{path.read_text() if path.exists() else ''}")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _revert(repo: Path, sha: str) -> str:
    _git(repo, "revert", "--no-edit", sha)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def history(tmp_path: Path) -> dict[str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "commit.gpgsign", "false")
    c: dict[str, str] = {"root": str(repo)}
    # Every file the cases below edit exists from the start, so each target is
    # a modification; the add/delete cases at the end use files of their own.
    for name in ("pool", "cache", "retry", "strict", "tpl", "net", "save", "sort", "parser"):
        (repo / f"{name}.txt").write_text("start\n")
    for name in ("legacy", "ci", "cfg", "misc", "metrics", "other", "settings", "log", "docs"):
        (repo / f"{name}.txt").write_text("start\n")
    c["base"] = _commit(repo, "Initial commit")

    # A commit on a branch that never reaches main, reverted by sha from main.
    _git(repo, "checkout", "-q", "-b", "side")
    c["side"] = _commit(repo, "Side experiment", "side.txt")
    _git(repo, "checkout", "-q", "main")

    # body rule: what ``git revert`` writes.
    c["body_target"] = _commit(repo, "Use a connection pool", "pool.txt")
    c["body_revert"] = _revert(repo, c["body_target"])

    # subject rule: hand-written, squash-merge suffixes on both sides.
    c["subject_target"] = _commit(repo, "Add response caching (#12)", "cache.txt")
    c["subject_revert"] = _commit(repo, 'Revert "Add response caching (#12)" (#15)', "cache.txt")

    # pr rule: conventional revert naming the pull request.
    c["pr_target"] = _commit(repo, "feat: retry failed uploads (#21)", "retry.txt")
    c["pr_revert"] = _commit(repo, "revert(upload): back out #21, it double-sends", "retry.txt")

    # revert of a revert: the original holds again.
    c["rr_target"] = _commit(repo, "Enable strict mode", "strict.txt")
    c["rr_revert"] = _revert(repo, c["rr_target"])
    c["rr_reapply"] = _revert(repo, c["rr_revert"])

    # re-landed after the revert under the same subject.
    c["reland_target"] = _commit(repo, "Cache compiled templates (#40)", "tpl.txt")
    c["reland_revert"] = _revert(repo, c["reland_target"])
    c["reland"] = _commit(repo, "Cache compiled templates (#44)", "tpl.txt")
    c["marked_target"] = _commit(repo, "net: pool sockets", "net.txt")
    c["marked_revert"] = _revert(repo, c["marked_target"])
    _commit(repo, "net: reland pool sockets", "net.txt")
    # ... or closing the same issue again, reworded.
    c["reissue_target"] = _commit(repo, "Fixed #50 -- Debounced autosave", "save.txt")
    c["reissue_revert"] = _revert(repo, c["reissue_target"])
    c["reissue"] = _commit(repo, "Fixed #50 -- Queued autosave writes", "save.txt")
    # ... or re-worded only by a ticket prefix and tense.
    c["tense_target"] = _commit(repo, "Use a topological sort for ordering", "sort.txt")
    _revert(repo, c["tense_target"])
    _commit(repo, "Fixed #70 -- Used a topological sort for ordering.", "sort.txt")
    # ... or a second attempt under the same issue key, editing the same files;
    c["retry_target"] = _commit(repo, "gh-80: Speed up the parser", "parser.txt")
    _revert(repo, c["retry_target"])
    _commit(repo, "gh-80: Speed up the parser without the regression", "parser.txt")
    # an issue key's later commits elsewhere are not a second attempt.
    c["umbrella_target"] = _commit(repo, "gh-90: Drop the legacy cache", "legacy.txt")
    c["umbrella_revert"] = _revert(repo, c["umbrella_target"])
    _commit(repo, "gh-90: Tidy the docs", "docs.txt")
    # ... or closing it in the body of a message that also talks about the revert.
    c["rework_target"] = _commit(repo, "Fixed #60 -- Added a coverage job", "ci.txt")
    c["rework_revert"] = _revert(repo, c["rework_target"])
    _commit(
        repo,
        f"Reworked the coverage job\n\nreverted in {c['rework_revert']}, now split in two.\n"
        "Fixes #60.",
        "ci.txt",
    )

    # ambiguous: two commits share the reverted subject.
    c["dup_a"] = _commit(repo, "Tweak config", "cfg.txt")
    c["dup_b"] = _commit(repo, "Tweak config (#30)", "cfg.txt")
    c["dup_revert"] = _commit(repo, 'Revert "Tweak config"', "cfg.txt")

    # not reverts: the bare word, and a conventional revert with no reference.
    c["bare"] = _commit(repo, "Stop reverting the cache on error", "misc.txt")
    c["noref"] = _commit(repo, "revert: undo the flag flip", "misc.txt")

    # a body citing a commit that is not an ancestor.
    _commit(repo, f"Drop the experiment\n\nThis reverts commit {c['side']}.", "misc.txt")

    # a squash carrying a branch's revert line whose diff touches none of it.
    c["untouched"] = _commit(repo, "Add metrics endpoint", "metrics.txt")
    _commit(repo, f"Squash a branch\n\nThis reverts commit {c['untouched']}.", "other.txt")

    # a revert that says it is partial.
    c["partial_target"] = _commit(repo, "Split the settings module", "settings.txt")
    _commit(
        repo,
        f'Partially revert "Split the settings module"\n\nThis reverts commit {c["partial_target"]}.',
        "settings.txt",
    )

    # A deletion undone by its revert and later done again by another change.
    (repo / "legacy.cfg").write_text("x\n")
    _commit(repo, "Add a legacy config", "legacy.cfg")
    _git(repo, "rm", "-q", "legacy.cfg")
    _git(repo, "commit", "-q", "-m", "Remove the legacy config")
    c["deleted_target"] = _git(repo, "rev-parse", "HEAD")
    _revert(repo, c["deleted_target"])
    _git(repo, "rm", "-q", "legacy.cfg")
    _git(repo, "commit", "-q", "-m", "Clean up configuration files")
    # A file added, removed by the revert, and added back by another change.
    c["added_target"] = _commit(repo, "Add a health endpoint", "health.txt")
    _revert(repo, c["added_target"])
    _commit(repo, "Serve status pages", "health.txt")

    # A revert of a revert whose own file change a later commit repeats: the
    # original holds, however the middle revert's effect reads at HEAD.
    c["chain_target"] = _commit(repo, "Add a release note", "note.txt")
    c["chain_revert"] = _revert(repo, c["chain_target"])
    c["chain_reapply"] = _revert(repo, c["chain_revert"])
    _git(repo, "rm", "-q", "note.txt")
    _git(repo, "commit", "-q", "-m", "Fold release notes into the changelog")

    c["kept"] = _commit(repo, "Adopt structured logging", "log.txt")
    return c


def test_each_rule_links_its_target_and_nothing_else(history):
    found = find_revert_links(history["root"])
    links = {(link.revert, link.target): link.rule for link in found if not link.relanded}
    assert {link.target for link in found if link.relanded} >= {
        history["retry_target"],
        history["deleted_target"],
        history["added_target"],
        history["chain_revert"],
    }
    assert links == {
        (history["body_revert"], history["body_target"]): "body",
        (history["subject_revert"], history["subject_target"]): "subject",
        (history["pr_revert"], history["pr_target"]): "pr",
        (history["rr_revert"], history["rr_target"]): "body",
        (history["rr_reapply"], history["rr_revert"]): "body",
        (history["umbrella_revert"], history["umbrella_target"]): "body",
        (history["chain_revert"], history["chain_target"]): "body",
    }


def test_revert_of_a_revert_restores_the_original(history):
    reverted = reverted_at_head(find_revert_links(history["root"]))
    assert set(reverted) == {
        history["body_target"],
        history["subject_target"],
        history["pr_target"],
        history["rr_revert"],
        history["umbrella_target"],
    }
    assert reverted[history["body_target"]].revert == history["body_revert"]


async def _record(session, repo_id: str, title: str, commits: list[str], **kw) -> DecisionRecord:
    rec = DecisionRecord(
        repository_id=repo_id,
        title=title,
        source="git_archaeology",
        evidence_commits_json=json.dumps(commits),
        **kw,
    )
    session.add(rec)
    await session.flush()
    return rec


async def test_only_fully_reverted_decisions_are_superseded(async_session, history):
    repo = await insert_repo(async_session)
    h = history
    # Abbreviated shas, as a model sometimes reports them.
    body = await _record(async_session, repo.id, "Pool connections", [h["body_target"][:10]])
    subject = await _record(async_session, repo.id, "Cache responses", [h["subject_target"]])
    pr = await _record(async_session, repo.id, "Retry uploads", [h["pr_target"]])
    umbrella = await _record(async_session, repo.id, "Drop legacy cache", [h["umbrella_target"]])
    retried = await _record(async_session, repo.id, "Faster parser", [h["retry_target"]])
    deleted = await _record(async_session, repo.id, "No legacy config", [h["deleted_target"]])
    added = await _record(async_session, repo.id, "Health endpoint", [h["added_target"]])
    reapplied = await _record(async_session, repo.id, "Strict mode", [h["rr_target"]])
    relanded = await _record(async_session, repo.id, "Cache templates", [h["reland_target"]])
    marked = await _record(async_session, repo.id, "Pool sockets", [h["marked_target"]])
    reissued = await _record(async_session, repo.id, "Debounce autosave", [h["reissue_target"]])
    reworded = await _record(async_session, repo.id, "Topological sort", [h["tense_target"]])
    reworked = await _record(async_session, repo.id, "Coverage job", [h["rework_target"]])
    ambiguous = await _record(async_session, repo.id, "Tweak config", [h["dup_b"]])
    named_file = await _record(
        async_session, repo.id, "Pool (file)", [h["body_target"]], evidence_file="docs/pool.md"
    )
    partial = await _record(async_session, repo.id, "Pool + logging", [h["body_target"], h["kept"]])
    # Reverted commit, but an ADR file still states the decision.
    with_file = await _record(async_session, repo.id, "Pool (ADR)", [h["body_target"]])
    async_session.add(
        DecisionEvidence(decision_id=with_file.id, source="adr", evidence_file="docs/adr/1.md")
    )
    no_commits = await _record(async_session, repo.id, "From a comment", [])
    await async_session.flush()

    result = await apply_revert_supersession(async_session, repo.id, h["root"])
    assert result == {"superseded": 4, "restored": 0}

    for rec, revert in (
        (body, "body_revert"),
        (subject, "subject_revert"),
        (pr, "pr_revert"),
        (umbrella, "umbrella_revert"),
    ):
        got = await get_decision(async_session, rec.id)
        assert got.status == "superseded"
        assert got.superseded_by == f"revert:{h[revert]}"[:32]
    for rec in (
        reapplied,
        relanded,
        marked,
        reissued,
        reworded,
        retried,
        deleted,
        added,
        reworked,
        ambiguous,
        named_file,
        partial,
        with_file,
        no_commits,
    ):
        got = await get_decision(async_session, rec.id)
        assert got.status == "proposed", rec.title
        assert got.superseded_by is None

    # Idempotent, and the auto-supersession repair does not undo it.
    assert await apply_revert_supersession(async_session, repo.id, h["root"]) == {
        "superseded": 0,
        "restored": 0,
    }
    await unretire_auto_superseded(async_session)
    assert (await get_decision(async_session, body.id)).status == "superseded"


async def test_a_later_revert_of_the_revert_restores_the_decision(async_session, history):
    repo = await insert_repo(async_session)
    rec = await _record(async_session, repo.id, "Pool connections", [history["body_target"]])
    await apply_revert_supersession(async_session, repo.id, history["root"])
    assert (await get_decision(async_session, rec.id)).status == "superseded"

    _revert(Path(history["root"]), history["body_revert"])
    result = await apply_revert_supersession(async_session, repo.id, history["root"])
    assert result == {"superseded": 0, "restored": 1}
    got = await get_decision(async_session, rec.id)
    assert got.status == "proposed"
    assert got.superseded_by is None


async def test_a_human_retirement_is_never_restored(async_session, history):
    repo = await insert_repo(async_session)
    rec = await _record(
        async_session,
        repo.id,
        "Strict mode",
        [history["rr_target"]],
        status="superseded",
        superseded_by="a" * 32,
    )
    await apply_revert_supersession(async_session, repo.id, history["root"])
    got = await get_decision(async_session, rec.id)
    assert got.status == "superseded"
    assert got.superseded_by == "a" * 32


def test_an_abbreviated_sha_resolves_only_when_unique():
    shas = sorted(["abc1234000" + "0" * 30, "abc1234fff" + "f" * 30, "def5678" + "0" * 33])
    assert _resolve("def5678", shas) == shas[2]
    assert _resolve("abc1234", shas) is None
    assert _resolve("abc1234f", shas) == shas[1]
    assert _resolve("not-a-sha", shas) is None
