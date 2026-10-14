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
