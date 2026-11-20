"""Marking first-look items: each member store through its own writer, read back.

The repository is the collector test's (a secret in two files, dead exports,
a performance cause, a cycle), plus stored items for the stores whose rows the
Fix-first queue builds on (health findings, refactoring plans and
opportunities).
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from repowise.core.persistence.crud.analysis.actions import get_action_states
from repowise.core.persistence.models import (
    DeadCodeFinding,
    HealthFileMetric,
    HealthFinding,
    RefactoringOpportunity,
    RefactoringSuggestion,
)
from repowise.core.report.first_look.collect import collect_first_look
from repowise.core.report.first_look.marks import (
    DEFAULT_SNOOZE_DAYS,
    MarkError,
    item_statuses,
    mark_item,
)
from repowise.core.report.first_look.model import (
    FirstLookReport,
    Item,
    Location,
    Member,
    PromptRef,
    SectionBlock,
    dead_code_ref,
    file_link,
    item_id,
)
from repowise.core.report.first_look.store import store_report
from tests.unit.persistence.test_first_look_collect import NOW, _seed


async def _report(session) -> tuple[str, FirstLookReport]:
    rid = await _seed(session)
    return rid, await collect_first_look(session, rid, now=NOW)


def _one(report: FirstLookReport, section: str, rule: str) -> Item:
    return next(i for i in report.section(section).items if i.rule == rule)


async def _status(session, rid: str, item: Item, *, gone: bool = False) -> tuple:
    found = (await item_statuses(session, rid, [item], gone=[item.id] if gone else ()))[item.id]
    return found.status, found.reason


async def test_a_group_mark_goes_to_every_member_action(async_session) -> None:
    rid, report = await _report(async_session)
    group = _one(report, "security", "hardcoded_password")

    out = await mark_item(async_session, rid, group.id, "done")
    await async_session.commit()

    assert out == {"id": group.id, "status": "done", "store": "action", "reason": None, "until": None}
    states = await get_action_states(async_session, rid)
    assert {m.ref for m in group.members} <= set(states)
    assert {states[m.ref].state for m in group.members} == {"done"}
    assert await _status(async_session, rid, group) == ("done", None)


async def test_a_security_false_positive_is_sent_to_the_ignore_comment(async_session) -> None:
    rid, report = await _report(async_session)
    group = _one(report, "security", "hardcoded_password")
    with pytest.raises(MarkError) as raised:
        await mark_item(async_session, rid, group.id, "false-positive", reason="test value")
    assert raised.value.code == "security_use_ignore"
    assert "repowise-security-ignore" in raised.value.message
    assert "--write-baseline" in raised.value.message
    assert await get_action_states(async_session, rid) == {}


@pytest.mark.parametrize(
    ("status", "kwargs", "code"),
    [
        ("ack", {}, "reason_required"),
        ("false_positive", {"reason": "  "}, "reason_required"),
        ("later", {}, "bad_status"),
        ("snooze", {"days": 0}, "bad_status"),
    ],
)
async def test_bad_marks_are_refused(async_session, status, kwargs, code) -> None:
    rid, report = await _report(async_session)
    cycle = _one(report, "hygiene", "import_cycle")
    with pytest.raises(MarkError) as raised:
        await mark_item(async_session, rid, cycle.id, status, **kwargs)
    assert raised.value.code == code
    assert raised.value.to_json()["error"]["code"] == code


async def test_an_unknown_id_is_refused(async_session) -> None:
    rid, _ = await _report(async_session)
    with pytest.raises(MarkError) as raised:
        await mark_item(async_session, rid, "fl_000000000000", "done")
    assert raised.value.code == "unknown_item"
    assert "fl_ followed by 12 hex characters" in raised.value.message


async def test_dead_code_keeps_the_reason_as_its_note(async_session) -> None:
    rid, report = await _report(async_session)
    exports = _one(report, "dead_code", "safe_to_delete")
    await store_report(async_session, rid, report)

    out = await mark_item(async_session, rid, exports.id, "ack", reason="plugin API")
    await async_session.commit()

    assert out["status"] == "acknowledged" and out["store"] == "dead_code"
    rows = (
        await async_session.execute(
            select(DeadCodeFinding.status, DeadCodeFinding.note).where(
                DeadCodeFinding.file_path == "src/dead.py"
            )
        )
    ).all()
    assert set(rows) == {("acknowledged", "plugin API")}
    # Answered rows leave the collector's open set; the stored item reads them.
    assert exports.id not in {i.id for i in (await collect_first_look(async_session, rid)).items()}
    assert await _status(async_session, rid, exports, gone=True) == ("acknowledged", "plugin API")


async def test_a_performance_cause_resolves_its_health_findings(async_session) -> None:
    rid, report = await _report(async_session)
    cause = _one(report, "performance", "io_in_loop")
    async_session.add_all(
        HealthFinding(repository_id=rid, file_path="src/repo.py", biomarker_type="io_in_loop",
                      severity="high", dimension="performance", opportunity_id="perf1")
        for _ in range(2)
    )
    await async_session.commit()

    out = await mark_item(async_session, rid, cause.id, "false_positive", reason="cached upstream")
    await async_session.commit()

    assert out == {
        "id": cause.id, "status": "false_positive", "store": "performance",
        "reason": "cached upstream", "until": None,
    }
    statuses = (
        await async_session.execute(
            select(HealthFinding.status).where(HealthFinding.opportunity_id == "perf1")
        )
    ).scalars()
    assert set(statuses) == {"false_positive"}
    assert (await _status(async_session, rid, cause))[0] == "false_positive"


def _stored_item(rule: str, *members: Member) -> Item:
    return Item(
        id=item_id("health", rule, "src/core.py"),
        section="health",
        rule=rule,
        title_facts={},
        locations=(Location("src/core.py", 10, "run"),),
        confidence="medium",
        basis="inferred",
        severity="plan",
        effort="M",
        link=file_link("src/core.py", 10),
        prompt=PromptRef("first_look_item"),
        fingerprint="fp",
        members=members,
    )


async def _store_items(session, rid: str, report: FirstLookReport, *items: Item) -> None:
    health = report.section("health") or SectionBlock("health", ())
    block = dataclasses.replace(health, items=(*health.items, *items))
    others = tuple(s for s in report.sections if s.key != "health")
    await store_report(session, rid, dataclasses.replace(report, sections=(*others, block)))
    await session.commit()


async def test_fix_first_rows_go_through_their_writers(async_session) -> None:
    rid, report = await _report(async_session)
    finding = _stored_item("finding", Member("health_finding", "hf_pub"))
    plan = _stored_item("refactor", Member("refactoring_plan", "plan_pub"))
    opportunity = _stored_item("refactor_opp", Member("refactoring_opportunity", "opp1"))
    async_session.add_all(
        [
            HealthFinding(repository_id=rid, file_path="src/core.py", biomarker_type="long_method",
                          severity="medium", public_id="hf_pub"),
            RefactoringSuggestion(repository_id=rid, refactoring_type="extract_method",
                                  file_path="src/core.py", public_id="plan_pub"),
            RefactoringSuggestion(repository_id=rid, refactoring_type="extract_method",
                                  file_path="src/core.py", public_id="step_pub"),
            RefactoringOpportunity(repository_id=rid, opportunity_id="opp1",
                                   details_json=json.dumps({"steps": [{"plan_id": "step_pub"}]})),
        ]
    )
    await _store_items(async_session, rid, report, finding, plan, opportunity)

    assert (await mark_item(async_session, rid, finding.id, "done"))["store"] == "health_finding"
    assert (await mark_item(async_session, rid, plan.id, "ack", reason="next sprint"))[
        "store"
    ] == "refactoring_plan"
    assert (await mark_item(async_session, rid, opportunity.id, "done"))[
        "store"
    ] == "refactoring_opportunity"
    await async_session.commit()

    def status(model, column, value):
        return select(model.status).where(column == value)

    assert (await async_session.execute(status(HealthFinding, HealthFinding.public_id, "hf_pub"))).scalar() == "resolved"
    plans = dict(
        (await async_session.execute(select(RefactoringSuggestion.public_id, RefactoringSuggestion.status))).all()
    )
    assert plans == {"plan_pub": "acknowledged", "step_pub": "resolved"}
    read = await item_statuses(async_session, rid, [finding, plan, opportunity], gone=[finding.id])
    assert [read[i.id].status for i in (finding, plan, opportunity)] == ["done", "acknowledged", "done"]


async def test_a_plan_the_analysis_dropped_reads_resolved_not_done(async_session) -> None:
    rid, _ = await _report(async_session)
    plan = _stored_item("refactor", Member("refactoring_plan", "plan_pub"))
    async_session.add(
        RefactoringSuggestion(repository_id=rid, refactoring_type="extract_method",
                              file_path="src/core.py", public_id="plan_pub", status="resolved",
                              status_reason="no_longer_detected")
    )
    await async_session.commit()
    assert await _status(async_session, rid, plan, gone=True) == ("resolved", None)


async def test_an_item_no_store_holds_keeps_its_answer_under_its_id(async_session) -> None:
    rid, report = await _report(async_session)
    cycle = _one(report, "hygiene", "import_cycle")

    out = await mark_item(async_session, rid, cycle.id, "snooze")
    await async_session.commit()
    until = datetime.fromisoformat(out["until"])
    assert out["status"] == "snoozed" and out["store"] == "item"
    assert timedelta(days=DEFAULT_SNOOZE_DAYS - 1) < until - datetime.now(UTC) <= timedelta(
        days=DEFAULT_SNOOZE_DAYS
    )
    found = (await item_statuses(async_session, rid, [cycle]))[cycle.id]
    assert found.status == "snoozed" and found.to_json()["status_until"] == out["until"]

    out = await mark_item(async_session, rid, cycle.id, "false_positive", reason="generated pair")
    await async_session.commit()
    # The next-action store has one "not now" state, so it reads back acknowledged.
    assert out == {
        "id": cycle.id, "status": "acknowledged", "store": "item",
        "reason": "generated pair", "until": None,
    }
    assert (await get_action_states(async_session, rid))[cycle.id].state == "dismissed"
    # A changed item (new facts) is open again.
    moved = dataclasses.replace(cycle, fingerprint="other")
    assert await _status(async_session, rid, moved) == ("open", None)


async def test_a_finding_snooze_is_the_items_own_until_the_next_answer(async_session) -> None:
    rid, report = await _report(async_session)
    cause = _one(report, "performance", "io_in_loop")
    async_session.add(
        HealthFinding(repository_id=rid, file_path="src/repo.py", biomarker_type="io_in_loop",
                      severity="high", dimension="performance", opportunity_id="perf1")
    )
    await async_session.commit()

    out = await mark_item(async_session, rid, cause.id, "snooze", days=3)
    assert out["store"] == "item"
    assert (await _status(async_session, rid, cause))[0] == "snoozed"
    await mark_item(async_session, rid, cause.id, "done")
    await async_session.commit()
    assert cause.id not in await get_action_states(async_session, rid)
    assert (await _status(async_session, rid, cause))[0] == "done"


async def test_an_unanswered_item_is_open_while_listed_and_resolved_once_gone(async_session) -> None:
    rid, report = await _report(async_session)
    cause = _one(report, "performance", "io_in_loop")
    cycle = _one(report, "hygiene", "import_cycle")
    assert await _status(async_session, rid, cause) == ("open", None)
    assert await _status(async_session, rid, cause, gone=True) == ("resolved", None)
    assert await _status(async_session, rid, cycle, gone=True) == ("resolved", None)


async def test_health_items_carry_their_numbers(async_session) -> None:
    rid = await _seed(async_session)
    row = (
        await async_session.execute(
            select(HealthFileMetric).where(HealthFileMetric.file_path == "src/settings.py")
        )
    ).scalar_one()
    row.score, row.max_ccn = 1.0, 48
    await async_session.commit()
    report = await collect_first_look(async_session, rid, now=NOW)
    lowest = next(
        i for i in report.section("health").items if i.locations[0].path == "src/settings.py"
    )
    facts = lowest.title_facts
    assert (facts["ccn"], facts["nloc"]) == (48, 100)
    assert set(facts) >= {"importers", "changes_90d"}


async def test_a_dead_code_symbol_holding_a_bar_still_resolves(async_session) -> None:
    rid, report = await _report(async_session)
    async_session.add(
        DeadCodeFinding(repository_id=rid, kind="unused_export", file_path="src/ops.py",
                        symbol_name="operator|", lines=3, confidence=0.9, safe_to_delete=True)
    )
    await async_session.commit()
    item = _stored_item(
        "safe_to_delete", Member("dead_code", dead_code_ref("unused_export", "operator|", "src/ops.py"))
    )
    await _store_items(async_session, rid, report, item)
    assert (await mark_item(async_session, rid, item.id, "done"))["store"] == "dead_code"
    await async_session.commit()
    assert await _status(async_session, rid, item, gone=True) == ("done", None)


async def test_a_member_that_joins_a_snoozed_group_opens_it(async_session) -> None:
    rid, report = await _report(async_session)
    group = _one(report, "security", "hardcoded_password")
    await mark_item(async_session, rid, group.id, "snooze")
    await async_session.commit()
    assert (await _status(async_session, rid, group))[0] == "snoozed"
    grown = dataclasses.replace(group, members=(*group.members, Member("action", "act_new", "fp")))
    assert (await _status(async_session, rid, grown))[0] == "open"


async def test_every_store_keeps_the_reason_and_reads_it_back(async_session) -> None:
    rid, report = await _report(async_session)
    group = _one(report, "security", "hardcoded_password")  # next actions
    cycle = _one(report, "hygiene", "import_cycle")  # no store: the item's own answer
    cause = _one(report, "performance", "io_in_loop")
    finding = _stored_item("finding", Member("health_finding", "hf_pub"))
    plan = _stored_item("refactor", Member("refactoring_plan", "plan_pub"))
    opportunity = _stored_item("refactor_opp", Member("refactoring_opportunity", "opp1"))
    async_session.add_all(
        [
            HealthFinding(repository_id=rid, file_path="src/repo.py", biomarker_type="io_in_loop",
                          severity="high", dimension="performance", opportunity_id="perf1"),
            HealthFinding(repository_id=rid, file_path="src/core.py", biomarker_type="long_method",
                          severity="medium", public_id="hf_pub"),
            RefactoringSuggestion(repository_id=rid, refactoring_type="extract_method",
                                  file_path="src/core.py", public_id="plan_pub"),
            RefactoringSuggestion(repository_id=rid, refactoring_type="extract_method",
                                  file_path="src/core.py", public_id="step_pub"),
            RefactoringOpportunity(repository_id=rid, opportunity_id="opp1",
                                   details_json=json.dumps({"steps": [{"plan_id": "step_pub"}]})),
        ]
    )
    await _store_items(async_session, rid, report, finding, plan, opportunity)

    items = (group, cycle, cause, finding, plan, opportunity)
    for item in items:
        await mark_item(async_session, rid, item.id, "ack", reason=f"why {item.rule}")
    await async_session.commit()

    read = await item_statuses(async_session, rid, items)
    assert {i.id: (read[i.id].status, read[i.id].reason) for i in items} == {
        i.id: ("acknowledged", f"why {i.rule}") for i in items
    }
    assert read[plan.id].to_json()["status_reason"] == "why refactor"
    # A later answer without a reason drops the old one.
    await mark_item(async_session, rid, finding.id, "done")
    await async_session.commit()
    assert await _status(async_session, rid, finding) == ("done", None)


async def test_a_writer_without_a_reason_keeps_it_for_the_same_status_only(async_session) -> None:
    from repowise.core.persistence.crud.analysis.actions import set_action_state
    from repowise.core.persistence.crud.analysis.health.findings import (
        update_health_finding_status,
    )

    rid, _ = await _report(async_session)
    row = HealthFinding(repository_id=rid, file_path="src/core.py", biomarker_type="long_method",
                        severity="medium", public_id="hf_pub")
    async_session.add(row)
    await async_session.flush()
    await update_health_finding_status(async_session, row.id, "acknowledged", note="by design")
    # A surface that knows no reason (the web triage) re-sends the same status.
    await update_health_finding_status(async_session, row.id, "acknowledged")
    assert row.status_note == "by design"
    await update_health_finding_status(async_session, row.id, "resolved")
    assert row.status_note is None

    await set_action_state(async_session, rid, "act_1", state="dismissed", note="later")
    await set_action_state(async_session, rid, "act_1", state="dismissed")
    assert (await get_action_states(async_session, rid))["act_1"].note == "later"
    await set_action_state(async_session, rid, "act_1", state="done")
    assert (await get_action_states(async_session, rid))["act_1"].note is None
