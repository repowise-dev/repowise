"""Setup steps: live rules in the improve_signal tier, ranked with the stored ones."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from repowise.core.agent_prompts.setup import EVIDENCE_RULES, SETUP_STEP_RULES
from repowise.core.analysis.actions import ACTION_RULES, ActionStateRecord, RepoFacts
from repowise.core.analysis.actions.engine import (
    find_action,
    rank_actions,
    rule_actions,
    with_setup,
)
from repowise.core.analysis.actions.rules import setup as rules
from repowise.core.analysis.actions.setup import (
    AGENT_CALLS_UNLOCK,
    NAMED_FILES,
    SETUP_STATE_ID,
    SYNCED_COMMITS_UNLOCK,
    FoundFile,
    SetupFacts,
)

NOW = datetime(2026, 11, 19, tzinfo=UTC)

#: A fresh index on a machine with nothing set up yet, and one agent call.
FRESH = SetupFacts(
    root="",
    sync_hook=False,
    unsynced_commits=38,
    coverage_ingested=False,
    coverage_reports=(FoundFile("coverage.xml", 2),),
    runtime_stored=False,
    evidence_files=(FoundFile(".benchmarks/Linux/0001_x.json", 1, "pytest-benchmark"),),
    background_records=24,
    agent_calls=1,
    claude_code_calls=1,
    first_call_at="2026-11-19T10:42:07Z",
    synced_commits=0,
    always_load=False,
    rewrite_hook=False,
)


def _fired(facts: SetupFacts) -> dict[str, object]:
    return {o.rule: o.actions[0] for r in rules.SETUP_RULES if (o := r(facts)).actions}


def test_the_screen_rows_fire_from_their_facts() -> None:
    fired = _fired(FRESH)
    assert set(fired) == {"sync_hook_missing", "coverage_found", "evidence_found", "decisions_unchecked"}
    sync = fired["sync_hook_missing"]
    assert sync.command == "repowise hook install"
    assert [(w.label, w.value) for w in sync.why] == [("commits since last sync", "38")]
    assert fired["coverage_found"].title == "Add the coverage report at `coverage.xml`"
    assert fired["coverage_found"].command == "repowise coverage add coverage.xml"
    assert fired["evidence_found"].title == "Connect the benchmark results in `.benchmarks/`"
    assert fired["evidence_found"].command == "repowise evidence setup"
    assert fired["decisions_unchecked"].title == "Check 24 background records on the busiest files"
    for action in fired.values():
        assert action.tier == "improve_signal" and action.command


def test_a_step_leaves_when_its_fact_is_met() -> None:
    done = replace(
        FRESH, sync_hook=True, coverage_ingested=True, runtime_stored=True, background_records=0
    )
    assert _fired(done) == {}


def test_an_unread_fact_is_unavailable_never_clear() -> None:
    unknown = SetupFacts(unavailable={"sync_hook": "Could not be read on this machine."})
    outcomes = {r(unknown).rule: r(unknown) for r in rules.SETUP_RULES}
    assert outcomes["sync_hook_missing"].status == "unavailable"
    assert outcomes["sync_hook_missing"].reason == "Could not be read on this machine."
    assert outcomes["coverage_found"].status == "unavailable"
    assert outcomes["decisions_unchecked"].status == "unavailable"
    assert not any(o.actions for o in outcomes.values())


@pytest.mark.parametrize(
    ("calls", "fires"), [(AGENT_CALLS_UNLOCK - 1, False), (AGENT_CALLS_UNLOCK, True)]
)
def test_always_load_and_distill_unlock_on_claude_code_calls(calls: int, fires: bool) -> None:
    facts = replace(FRESH, claude_code_calls=calls)
    fired = _fired(facts)
    assert ("always_load_suggested" in fired) is fires
    assert ("distill_suggested" in fired) is fires
    assert facts.unlocks()["always_load"] is fires and facts.unlocks()["distill"] is fires
    if fires:
        always = fired["always_load_suggested"]
        assert always.command == "repowise config set mcp.always_load true"
        assert "about 2.5k more tokens a turn" in always.impact
        assert fired["distill_suggested"].command == "repowise hook rewrite install"


def test_distill_waits_for_a_known_rewrite_hook() -> None:
    facts = replace(FRESH, claude_code_calls=AGENT_CALLS_UNLOCK, rewrite_hook=None)
    assert rules.distill_suggested(facts).status == "unavailable"


@pytest.mark.parametrize(
    ("commits", "fires"), [(SYNCED_COMMITS_UNLOCK - 1, False), (SYNCED_COMMITS_UNLOCK, True)]
)
def test_suggested_settings_unlock_on_synced_commits(commits: int, fires: bool) -> None:
    facts = replace(FRESH, synced_commits=commits, suggested_settings=("indexing.exclude",))
    assert ("settings_suggested" in _fired(facts)) is fires
    assert facts.unlocks()["suggested_settings"] is fires
    assert "dead_code_batch" not in facts.unlocks()


def test_a_model_row_names_the_source_and_asks_before_spending() -> None:
    facts = replace(
        FRESH,
        model_source=("anthropic", "Uses your Anthropic key", "repowise generate --provider anthropic"),
    )
    action = _fired(facts)["docs_model_ready"]
    assert action.command == "repowise generate --provider anthropic"
    assert "ask first" in action.impact
    assert [(w.label, w.value) for w in action.why] == [("model found", "Uses your Anthropic key")]


def test_a_found_report_or_profile_replaces_the_stored_ask() -> None:
    stored = rule_actions(RepoFacts(anchor=None))
    stored["actions"] += [
        (0.0, {"rule": "coverage_missing", "id": "act_cov"}),
        (0.0, {"rule": "runtime_missing", "id": "act_rt"}),
    ]
    merged = with_setup(stored, FRESH)
    rules_left = {a["rule"] for _, a in merged["actions"]}
    assert "coverage_missing" not in rules_left and "runtime_missing" not in rules_left
    assert {"coverage_found", "evidence_found"} <= rules_left

    nothing_found = replace(FRESH, coverage_reports=(), evidence_files=())
    kept = {a["rule"] for _, a in with_setup(stored, nothing_found)["actions"]}
    assert {"coverage_missing", "runtime_missing"} <= kept


def test_the_view_ranks_setup_steps_in_their_tier_and_carries_the_offer() -> None:
    ruled = rule_actions(RepoFacts(anchor=None))
    view = rank_actions(ruled, {}, now=NOW, setup=FRESH)
    week = view["horizons"]["week"]
    assert [a["rule"] for a in week["actions"]][:4] == [
        "sync_hook_missing",
        "coverage_found",
        "evidence_found",
        "decisions_unchecked",
    ]
    setup = view["setup"]
    assert setup["open"] == week["by_tier"]["improve_signal"] == 4
    assert setup["offer"] == "finish_setup"
    assert setup["first_call_at"] == "2026-11-19T10:42:07Z"
    assert "setup" not in rank_actions(ruled, {}, now=NOW)


def test_the_offer_waits_for_an_agent_call_and_stops_when_dismissed() -> None:
    ruled = rule_actions(RepoFacts(anchor=None))
    no_calls = rank_actions(ruled, {}, now=NOW, setup=replace(FRESH, agent_calls=0))["setup"]
    assert no_calls["open"] == 4 and no_calls["offer"] is None

    declined = {SETUP_STATE_ID: ActionStateRecord("dismissed", "")}
    setup = rank_actions(ruled, declined, now=NOW, setup=FRESH)["setup"]
    assert setup["dismissed"] is True and setup["offer"] is None and setup["open"] == 4

    done = replace(
        FRESH, sync_hook=True, coverage_ingested=True, runtime_stored=True, background_records=0
    )
    finished = rank_actions(ruled, {}, now=NOW, setup=done)["setup"]
    assert finished["open"] == 0 and finished["offer"] is None


def test_a_dismissed_report_row_returns_when_another_report_appears() -> None:
    ruled = rule_actions(RepoFacts(anchor=None))
    row = rules.coverage_found(FRESH).actions[0]
    states = {row.action_id: ActionStateRecord("dismissed", row.fingerprint)}
    week = rank_actions(ruled, states, now=NOW, setup=FRESH)["horizons"]["week"]
    assert "coverage_found" not in {a["rule"] for a in week["actions"]}

    more = replace(FRESH, coverage_reports=(*FRESH.coverage_reports, FoundFile("web/lcov.info", 0)))
    week = rank_actions(ruled, states, now=NOW, setup=more)["horizons"]["week"]
    assert "coverage_found" in {a["rule"] for a in week["actions"]}


def test_a_setup_step_opens_by_id() -> None:
    row = rules.sync_hook_missing(FRESH).actions[0]
    assert find_action(RepoFacts(anchor=None), row.action_id, FRESH) == row
    assert find_action(RepoFacts(anchor=None), row.action_id) is None


def test_every_setup_rule_is_in_the_vocabulary_and_has_a_setup_prompt() -> None:
    names = {r.__name__ for r in rules.SETUP_RULES}
    assert names <= set(ACTION_RULES)
    assert names | EVIDENCE_RULES == SETUP_STEP_RULES
    # Ahead of the stored signal rules, so a found report outranks "add a report".
    assert max(ACTION_RULES.index(n) for n in names) < ACTION_RULES.index("coverage_missing")


def test_an_unread_history_reports_the_settings_step_unavailable() -> None:
    outcome = rules.settings_suggested(replace(FRESH, synced_commits=None))
    assert outcome.status == "unavailable" and not outcome.actions


def test_once_completed_later_rows_appear_quietly_without_the_offer() -> None:
    ruled = rule_actions(RepoFacts(anchor=None))
    done = replace(
        FRESH, sync_hook=True, coverage_ingested=True, runtime_stored=True, background_records=0
    )
    first = rank_actions(ruled, {}, now=NOW, setup=done)["setup"]
    assert first["open"] == 0 and first["completed"] is True

    later = replace(done, claude_code_calls=AGENT_CALLS_UNLOCK, completed=True)
    view = rank_actions(ruled, {}, now=NOW, setup=later)
    rows = {a["rule"] for a in view["horizons"]["week"]["actions"]}
    assert {"always_load_suggested", "distill_suggested"} <= rows
    assert view["setup"]["open"] == 2 and view["setup"]["offer"] is None


@pytest.mark.parametrize(
    "unread",
    [
        {"unavailable": {"sync_hook": "Could not be read on this machine."}, "sync_hook": None},
        {"background_records": None},
        {"coverage_ingested": None},
        {"synced_commits": None},
    ],
)
def test_zero_steps_with_an_unread_fact_is_not_completed(unread: dict) -> None:
    ruled = rule_actions(RepoFacts(anchor=None))
    done = replace(
        FRESH, sync_hook=True, coverage_ingested=True, runtime_stored=True, background_records=0
    )
    assert rank_actions(ruled, {}, now=NOW, setup=done)["setup"]["completed"] is True
    partial = rank_actions(ruled, {}, now=NOW, setup=replace(done, **unread))["setup"]
    assert partial["open"] == 0 and partial["completed"] is False


def test_a_snoozed_step_keeps_setup_from_completing() -> None:
    ruled = rule_actions(RepoFacts(anchor=None))
    one_left = replace(
        FRESH, sync_hook=False, coverage_ingested=True, runtime_stored=True, background_records=0
    )
    sync = _fired(one_left)["sync_hook_missing"]
    snoozed = ActionStateRecord("snoozed", sync.fingerprint, until=datetime(2026, 12, 3, tzinfo=UTC))
    block = rank_actions(ruled, {sync.action_id: snoozed}, now=NOW, setup=one_left)["setup"]
    assert block["open"] == 0 and block["completed"] is False

    dismissed = ActionStateRecord("dismissed", sync.fingerprint)
    block = rank_actions(ruled, {sync.action_id: dismissed}, now=NOW, setup=one_left)["setup"]
    assert block["open"] == 0 and block["completed"] is True


def test_many_coverage_reports_are_counted_and_ingested_in_full() -> None:
    reports = tuple(FoundFile(f"pkg{i}/coverage.xml", i) for i in range(NAMED_FILES + 2))
    step = _fired(replace(FRESH, coverage_reports=reports))["coverage_found"]
    assert step.title == f"Add the {NAMED_FILES + 2} coverage reports found on disk"
    assert step.command == "repowise coverage add"
    assert len(step.details) == NAMED_FILES
