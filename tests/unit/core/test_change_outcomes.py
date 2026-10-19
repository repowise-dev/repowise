"""Outcomes of a merged change: reverts, fixes that name it, its files' later fix rate."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from repowise.core.changes.evidence import ChangeEvidence, EvidenceInputs, build_change_evidence
from repowise.core.changes.outcomes import (
    FIX_REFERENCE,
    REVERT,
    OutcomeLink,
    OutcomeTarget,
    change_outcome_providers,
    file_fix_rate,
    outcome_links,
)

DAY = 86_400
T0 = 1_767_225_600  # 2026-01-01T00:00:00Z


def _git(root: Path, *args: str, at: int | None = None) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Ann",
        "GIT_AUTHOR_EMAIL": "ann@example.com",
        "GIT_COMMITTER_NAME": "Ann",
        "GIT_COMMITTER_EMAIL": "ann@example.com",
    }
    if at is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"@{at} +0000"
    return subprocess.run(
        ["git", *args], cwd=root, env=env, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(root: Path, files: dict[str, str], message: str, at: int) -> str:
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "--allow-empty", "-m", message, at=at)
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "commit.gpgsign", "false")
    _commit(root, {"src/a.py": "a = 0\n", "src/b.py": "b = 0\n"}, "chore: seed", T0)
    return root


def _target(root: Path, sha: str, number: str = "1", files=("src/a.py", "src/b.py")) -> OutcomeTarget:
    at = int(_git(root, "log", "-1", "--format=%ct", sha))
    return OutcomeTarget(
        change_id=f"pr-{number}",
        head_sha=sha,
        commit_shas=(sha,),
        identity_kind="pr",
        identity_value=number,
        forge="generic",
        forge_ref=f"#{number}",
        merged_at=datetime.fromtimestamp(at, tz=UTC),
        files=tuple(files),
    )


def _head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD")


def test_a_revert_that_names_the_change_links_to_it(root):
    change = _commit(root, {"src/a.py": "a = 1\n"}, "feat: a (#1)", T0 + DAY)
    revert = _commit(
        root, {"src/a.py": "a = 0\n"}, f'Revert "feat: a (#1)"\n\nThis reverts commit {change}.', T0 + 2 * DAY
    )
    links = outcome_links(root, _head(root), _target(root, change))
    assert [(link.kind, link.sha, link.target_sha, link.rule, link.in_force) for link in links] == [
        (REVERT, revert, change, "body", True)
    ]
    assert links[0].evidence == f"This reverts commit {change}"
    assert links[0].basis == "declared"


def test_a_partial_revert_is_not_a_revert(root):
    change = _commit(root, {"src/a.py": "a = 1\n"}, "feat: a (#1)", T0 + DAY)
    _commit(
        root,
        {"src/a.py": "a = 0\n"},
        f'Partially revert "feat: a (#1)"\n\nThis reverts commit {change}.',
        T0 + 2 * DAY,
    )
    assert outcome_links(root, _head(root), _target(root, change)) == []


@pytest.mark.parametrize(
    ("message", "rule"),
    [
        ("fix: guard empty input (regression from #1)", "regression"),
        ("fix: guard empty input\n\nA regression in {sha} left it unchecked.", "regression"),
        ("fix: guard empty input\n\nThe test failures were caused by\nPR #1 narrowing it.", "caused_by"),
        ("fix(tests): update tests broken by commit {short}", "broken_by"),
        ('fix: guard empty input\n\nFixes: {short} ("feat: a (#1)")', "fixes_trailer"),
    ],
)
def test_each_cause_phrase_links_in_its_own_words(root, message, rule):
    change = _commit(root, {"src/a.py": "a = 1\n"}, "feat: a (#1)", T0 + DAY)
    text = message.format(sha=change, short=change[:12])
    fix = _commit(root, {"src/a.py": "a = 2\n"}, text, T0 + 2 * DAY)
    links = outcome_links(root, _head(root), _target(root, change))
    assert [(link.kind, link.sha, link.target_sha, link.rule) for link in links] == [
        (FIX_REFERENCE, fix, change, rule)
    ]
    # The evidence is the author's line, verbatim.
    assert links[0].evidence in text


@pytest.mark.parametrize(
    "message",
    [
        "fix: crash\n\nFixes #1",  # mostly an issue number
        "fix: carry it forward\n\nThe option introduced in #1 is kept.",  # a feature, not a cause
        "fix: crash\n\nSame class of bug {short} fixed once already.",  # a quoted sha
        "test: the regression\n\nSince #1 a plain logger raises.",  # across a blank line
    ],
)
def test_forms_measured_below_the_gate_are_not_read(root, message):
    change = _commit(root, {"src/a.py": "a = 1\n"}, "feat: a (#1)", T0 + DAY)
    _commit(root, {"src/a.py": "a = 2\n"}, message.format(short=change[:12]), T0 + 2 * DAY)
    assert outcome_links(root, _head(root), _target(root, change)) == []


def test_a_reference_from_a_commit_the_change_is_not_an_ancestor_of_is_rejected(root):
    base = _head(root)
    change = _commit(root, {"src/a.py": "a = 1\n"}, "feat: a (#1)", T0 + DAY)
    _git(root, "checkout", "-q", "-b", "side", base)
    _commit(root, {"src/c.py": "c = 1\n"}, "fix: c (regression from #1)", T0 + 2 * DAY)
    _git(root, "checkout", "-q", "main")
    _git(root, "merge", "-q", "--no-ff", "-m", "Merge branch 'side'", "side", at=T0 + 3 * DAY)
    assert outcome_links(root, _head(root), _target(root, change)) == []


def test_a_sha_names_the_change_only_when_it_is_the_changes_own(root):
    change = _commit(root, {"src/a.py": "a = 1\n"}, "feat: a (#1)", T0 + DAY)
    other = _commit(root, {"src/b.py": "b = 1\n"}, "feat: b (#2)", T0 + 2 * DAY)
    _commit(root, {"src/b.py": "b = 2\n"}, f"fix: b (regression from {other[:10]})", T0 + 3 * DAY)
    assert outcome_links(root, _head(root), _target(root, change)) == []
    assert [link.target_sha for link in outcome_links(root, _head(root), _target(root, other, "2"))] == [
        other
    ]


def test_file_fix_rate_counts_fixes_within_the_window_with_its_basis(root):
    change = _commit(root, {"src/a.py": "a = 1\n", "src/b.py": "b = 1\n"}, "feat: a and b (#1)", T0 + DAY)
    inside = _commit(root, {"src/a.py": "a = 2\n"}, "fix: a crash", T0 + 5 * DAY)
    _commit(root, {"src/b.py": "b = 2\n"}, "feat: more b", T0 + 6 * DAY)  # not a fix
    _commit(root, {"src/b.py": "b = 3\n"}, "fix: b crash", T0 + 40 * DAY)  # after the window
    rate = file_fix_rate(root, _head(root), _target(root, change), 30)
    assert rate == {
        "files": 2,
        "files_fixed": 1,
        "days": 30,
        "fix_commits": 1,
        "window_complete": True,
        "fixed": [{"path": "src/a.py", "fixes": [inside]}],
    }
    # Fixes landed, not caused: the rate never names a link.
    assert outcome_links(root, _head(root), _target(root, change)) == []


def test_file_fix_rate_says_when_the_window_is_still_open(root):
    change = _commit(root, {"src/a.py": "a = 1\n"}, "feat: a (#1)", T0 + DAY)
    _commit(root, {"src/a.py": "a = 2\n"}, "fix: a crash", T0 + 3 * DAY)
    rate = file_fix_rate(root, _head(root), _target(root, change, files=("src/a.py",)), 30)
    assert rate["files_fixed"] == 1 and rate["window_complete"] is False


def test_a_registered_provider_plugs_in_after_the_local_ones(root):
    change = _commit(root, {"src/a.py": "a = 1\n"}, "feat: a (#1)", T0 + DAY)

    class ForgeLinks:
        def links(self, repo_path, head, target):
            return [OutcomeLink(FIX_REFERENCE, "f" * 40, target.head_sha, "linked follow-up", "forge")]

    assert change_outcome_providers.names[:2] == ["git-revert", "git-reference"]
    change_outcome_providers.register("forge-test", ForgeLinks())
    try:
        links = outcome_links(root, _head(root), _target(root, change))
    finally:
        change_outcome_providers._impls.pop("forge-test")
    assert [(link.sha, link.rule) for link in links] == [("f" * 40, "forge")]


def _outcomes_block(root: Path, change: str) -> dict:
    target = _target(root, change)
    head = _head(root)
    return {
        "as_of": head,
        "days": 30,
        "links": [link.to_dict() for link in outcome_links(root, head, target)],
        "file_fix_rate": file_fix_rate(root, head, target, 30),
    }


def test_the_outcomes_item_round_trips_and_leaves_policy_rules_as_they_were(root):
    from repowise.core.policy import evaluate, parse_policy
    from tests.unit.core.test_change_evidence import _bundle, _full_inputs

    change = _commit(root, {"src/a.py": "a = 1\n"}, "feat: a (#1)", T0 + DAY)
    _commit(
        root, {"src/a.py": "a = 0\n"}, f'Revert "feat: a (#1)"\n\nThis reverts commit {change}.', T0 + 2 * DAY
    )
    inputs = replace(_full_inputs(), outcomes=_outcomes_block(root, change))
    record = build_change_evidence(_bundle(["src/a.py"]), inputs, change_id="pr-1", revspec="r").to_dict()
    item = next(i for i in record["items"] if i["kind"] == "outcomes")
    assert item["status"] == "attention" and item["basis"] == "declared"
    assert item["value"]["reverted"] is True and item["value"]["reverts"] == 1
    assert [d["kind"] for d in item["details"]] == ["revert", "file_fix_rate"]
    assert item["details"][-1]["basis"] == "measured"
    data = json.loads(json.dumps(record))
    assert ChangeEvidence.from_dict(data).to_dict() == data
    policy = parse_policy(
        """\
rules:
  - name: decision
    when:
      decision_crossed: true
    action: warn
"""
    )
    assert evaluate(policy, data)[0].outcome == "triggered"


def test_no_link_and_no_rate_reads_ok_not_unknown():
    from tests.unit.core.test_change_evidence import _bundle

    inputs = EvidenceInputs(outcomes={"as_of": "h", "days": 30, "links": [], "file_fix_rate": None})
    item = build_change_evidence(_bundle(["src/a.py"]), inputs, change_id="c", revspec="r").item(
        "outcomes"
    )
    assert (item.status, item.basis) == ("ok", "declared")
    assert item.value["files"] is None and item.value["as_of"] == "h"


@pytest.mark.asyncio
async def test_kept_outcomes_follow_head(root):
    from types import SimpleNamespace

    from repowise.core.changes.collect import outcomes_stale, with_current_outcomes
    from repowise.core.changes.evidence import EvidenceItem, _outcomes

    change = _commit(root, {"src/a.py": "a = 1\n"}, "feat: a (#1)", T0 + DAY)
    block = _outcomes_block(root, change)

    kept = ChangeEvidence(
        change_id="pr-1",
        revspec=change,
        generated_at="2026-01-02T00:00:00+00:00",
        items=[EvidenceItem("health_delta", "Health delta", "ok", "measured"), _outcomes(EvidenceInputs(outcomes=block))],
    )
    assert not await outcomes_stale(root, kept)
    revert = _commit(
        root, {"src/a.py": "a = 0\n"}, f'Revert "feat: a (#1)"\n\nThis reverts commit {change}.', T0 + 2 * DAY
    )
    assert await outcomes_stale(root, kept)

    class _Session:
        async def execute(self, *_a, **_k):
            return SimpleNamespace(scalar_one_or_none=lambda: None, scalars=lambda: SimpleNamespace(all=list))

    row = SimpleNamespace(
        change_id="pr-1",
        head_sha=change,
        commit_shas_json=json.dumps([change]),
        identity_kind="pr",
        identity_value="1",
        forge="generic",
        forge_ref="#1",
        merged_at=datetime.fromtimestamp(T0 + DAY, tz=UTC),
        files_json=json.dumps([{"path": "src/a.py", "added": 1, "deleted": 1}]),
    )
    current = await with_current_outcomes(_Session(), "r", root, row, kept)
    assert current is not kept and current.item("health_delta") == kept.item("health_delta")
    outcomes = current.item("outcomes")
    assert outcomes.value["as_of"] == revert and outcomes.value["reverted"] is True
    assert await with_current_outcomes(_Session(), "r", root, row, current) is current


# -- number resolution -------------------------------------------------------


def test_a_number_two_commits_hold_names_neither(root):
    change = _commit(root, {"src/a.py": "a = 1\n"}, "feat: a (#1)", T0 + DAY)
    _commit(root, {"src/b.py": "b = 1\n"}, "feat: b, cherry-picked (#1)", T0 + 2 * DAY)
    _commit(root, {"src/a.py": "a = 2\n"}, "fix: a (regression from #1)", T0 + 3 * DAY)
    assert outcome_links(root, _head(root), _target(root, change)) == []


def test_a_number_held_by_another_commit_does_not_name_this_change(root):
    _commit(root, {"src/b.py": "b = 1\n"}, "feat: b (#1)", T0 + DAY)
    mine = _commit(root, {"src/a.py": "a = 1\n"}, "feat: a", T0 + 2 * DAY)
    _commit(root, {"src/a.py": "a = 2\n"}, "fix: a (regression from #1)", T0 + 3 * DAY)
    assert outcome_links(root, _head(root), _target(root, mine)) == []


def test_reverts_and_relands_do_not_make_a_number_ambiguous(root):
    change = _commit(root, {"src/a.py": "a = 1\n"}, "feat: a (#1)", T0 + DAY)
    _commit(
        root,
        {"src/a.py": "a = 0\n"},
        f'Revert "feat: a (#1)" (#2)\n\nThis reverts commit {change}.',
        T0 + 2 * DAY,
    )
    _commit(root, {"src/a.py": "a = 1\n"}, "Reland feat: a (#1)", T0 + 3 * DAY)
    fix = _commit(root, {"src/a.py": "a = 3\n"}, "fix: a (regression from #1)", T0 + 4 * DAY)
    links = outcome_links(root, _head(root), _target(root, change))
    assert [link.sha for link in links if link.kind == FIX_REFERENCE] == [fix]
    assert [link.kind for link in links].count(REVERT) == 1


def test_gitlab_reads_bang_numbers_and_not_hashes(root):
    change = _commit(root, {"src/a.py": "a = 1\n"}, "feat: a (!1)", T0 + DAY)
    _commit(root, {"src/a.py": "a = 2\n"}, "fix: a (regression from #1)", T0 + 2 * DAY)
    fix = _commit(root, {"src/a.py": "a = 3\n"}, "fix: a (regression from !1)", T0 + 3 * DAY)
    target = replace(_target(root, change), forge="gitlab", forge_ref="!1")
    assert [link.sha for link in outcome_links(root, _head(root), target)] == [fix]


def test_a_short_sha_two_commits_share_names_neither():
    from repowise.core.analysis.decisions.reverts import _History
    from repowise.core.changes import outcomes

    own, other, later = "abcdef1" + "0" * 33, "abcdef1" + "f" * 33, "1" * 40
    hist = _History()
    for sha, parents in ((later, own), (other, own), (own, "")):
        hist._add(sha, parents, "x", "")
    refs = outcomes._References(hist, sorted(hist.position))
    refs.by_token["abcdef1"] = [(later, "regression", "regression from abcdef1")]
    key = ("reference", "repo-x", "head-x", "generic")
    outcomes._index[key] = refs
    try:
        change = OutcomeTarget("commit-x", own, (own,), "commit", own, "generic", None, None, ())
        assert outcomes.GitReferenceOutcomes().links(Path("repo-x"), "head-x", change) == []
        refs.by_token["abcdef10"] = [(later, "regression", "regression from abcdef10")]
        found = outcomes.GitReferenceOutcomes().links(Path("repo-x"), "head-x", change)
        assert [link.target_sha for link in found] == [own]
    finally:
        outcomes._index.pop(key, None)


# -- what cannot be read reads unknown -----------------------------------------


def test_a_failed_git_read_is_unknown_and_not_kept(root, monkeypatch):
    from repowise.core.analysis.decisions import reverts
    from repowise.core.changes.outcomes import OutcomeUnknownError

    change = _commit(root, {"src/a.py": "a = 1\n"}, "feat: a (#1)", T0 + DAY)
    real = reverts._git_out
    monkeypatch.setattr(reverts, "_git_out", lambda *_a, **_k: None)
    with pytest.raises(OutcomeUnknownError):
        outcome_links(root, _head(root), _target(root, change))
    with pytest.raises(OutcomeUnknownError):
        file_fix_rate(root, _head(root), _target(root, change), 30)
    monkeypatch.setattr(reverts, "_git_out", real)
    assert outcome_links(root, _head(root), _target(root, change)) == []


def test_nothing_to_count_reads_unknown_with_a_reason(root):
    from repowise.core.changes.outcomes import OutcomeUnknownError

    change = _commit(root, {"src/a.py": "a = 1\n"}, "feat: a (#1)", T0 + DAY)
    with pytest.raises(OutcomeUnknownError, match="no files"):
        file_fix_rate(root, _head(root), _target(root, change, files=()), 30)
    with pytest.raises(OutcomeUnknownError, match="no merge time"):
        file_fix_rate(root, _head(root), replace(_target(root, change), merged_at=None), 30)


class _Session:
    """Enough of a session for ``get_change`` to find nothing."""

    async def execute(self, *_a, **_k):
        from types import SimpleNamespace

        return SimpleNamespace(
            scalar_one_or_none=lambda: None, scalars=lambda: SimpleNamespace(all=list)
        )


def _row(sha: str, *, files=("src/a.py",)):
    from types import SimpleNamespace

    return SimpleNamespace(
        change_id="pr-1",
        head_sha=sha,
        commit_shas_json=json.dumps([sha]),
        identity_kind="pr",
        identity_value="1",
        forge="generic",
        forge_ref="#1",
        merged_at=datetime.fromtimestamp(T0 + DAY, tz=UTC),
        files_json=json.dumps([{"path": p, "added": 1, "deleted": 0} for p in files]),
    )


@pytest.mark.asyncio
async def test_unknown_items_are_stamped_so_they_are_not_read_every_time(root, tmp_path):
    from repowise.core.changes.collect import outcomes_item, outcomes_key, outcomes_stale

    change = _commit(root, {"src/a.py": "a = 1\n"}, "feat: a (#1)", T0 + DAY)
    shallow = tmp_path / "shallow"
    _git(tmp_path, "clone", "-q", "--depth", "1", root.as_uri(), str(shallow))
    key = await outcomes_key(shallow)
    item = await outcomes_item(_Session(), "r", shallow, _row(_head(shallow)), key)
    assert (item.status, item.basis) == ("unknown", "unresolved")
    reason = "a shallow clone cuts off the history outcomes are read from"
    assert item.details == [{"reason": reason, "as_of": key[0], "days": 30}]
    kept = ChangeEvidence("pr-1", None, "2026-01-02T00:00:00+00:00", [item])
    assert not await outcomes_stale(shallow, kept)

    key = await outcomes_key(root)
    no_files = await outcomes_item(_Session(), "r", root, _row(change, files=()), key)
    rate = [d for d in no_files.details if d["kind"] == "file_fix_rate"]
    assert rate == [
        {"kind": "file_fix_rate", "basis": "unresolved", "reason": "the change lists no files"}
    ]
    assert no_files.value["files"] is None and no_files.status == "ok"


def test_fix_days_reads_the_config_within_bounds(tmp_path):
    from repowise.core.changes.outcomes import DEFAULT_FIX_DAYS, fix_days

    cfg = tmp_path / ".repowise" / "config.yaml"
    cfg.parent.mkdir()
    assert fix_days(tmp_path) == DEFAULT_FIX_DAYS == 30
    for raw, want in (("45", 45), ("1", 1), ("365", 365), ("0", 30), ("366", 30), ("soon", 30)):
        cfg.write_text(f"changes:\n  outcome_fix_days: {raw}\n", encoding="utf-8")
        assert fix_days(tmp_path) == want, raw
    cfg.write_text("changes: 12\n", encoding="utf-8")
    assert fix_days(tmp_path) == 30


# -- keeping ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_legacy_checklist_gains_outcomes_and_a_merge_keeps_a_newer_rebuild(
    tmp_path, root
):
    from repowise.core.changes.collect import (
        cached_evidence,
        change_evidence,
        keep_outcomes,
        outcomes_item,
        outcomes_key,
        save_evidence,
    )
    from repowise.core.changes.evidence import EvidenceItem
    from repowise.core.persistence.crud import get_change
    from repowise.core.persistence.database import get_session
    from tests.unit.core.test_change_evidence import _index

    head = _commit(root, {"src/a.py": "a = 1\n"}, "feat: a and web (#7)", T0 + DAY)
    engine, factory, repo_id = await _index(tmp_path, root, head)
    health = EvidenceItem("health_delta", "Health delta", "ok", "measured", {"introduced": 0})
    legacy = ChangeEvidence("pr-7", head, "2026-01-01T00:00:00+00:00", [health])
    try:
        async with get_session(factory) as session:
            await save_evidence(session, repo_id, "pr-7", legacy)
        async with get_session(factory) as session:
            row = await get_change(session, repo_id, "pr-7")
            got = await change_evidence(session, repo_id, root, row)
        assert [i.kind for i in got.items] == ["health_delta", "outcomes"]
        assert got.generated_at == legacy.generated_at
        async with get_session(factory) as session:
            stored = cached_evidence(await get_change(session, repo_id, "pr-7"))
        assert stored.to_dict() == got.to_dict()

        # An outcomes read that finishes after a full rebuild was kept merges
        # into the rebuild, and one read at the same HEAD is not written twice.
        key = await outcomes_key(root)
        async with get_session(factory) as session:
            row = await get_change(session, repo_id, "pr-7")
            item = await outcomes_item(session, repo_id, root, row, key)
        rebuilt = ChangeEvidence("pr-7", head, "2026-02-01T00:00:00+00:00", [health])
        async with get_session(factory) as session:
            await save_evidence(session, repo_id, "pr-7", rebuilt)
        async with get_session(factory) as session:
            merged = await keep_outcomes(session, repo_id, "pr-7", item)
        assert merged.generated_at == rebuilt.generated_at
        assert [i.kind for i in merged.items] == ["health_delta", "outcomes"]
        async with get_session(factory) as session:
            again = await keep_outcomes(session, repo_id, "pr-7", item)
        assert again.to_dict() == merged.to_dict()
    finally:
        await engine.dispose()
