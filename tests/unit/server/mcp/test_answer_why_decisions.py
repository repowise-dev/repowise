"""A why-shaped get_answer reads the decisions in force that answer the question.

Before, only decisions naming one of the top retrieval hits' files reached the
prompt, so a recorded reason for a question whose files retrieval missed was
never seen and the answer said it could not be established. The question lane
ranks as get_why does; a mined record that nobody accepted does not enter it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from repowise.core.persistence.crud import bulk_upsert_decisions
from repowise.core.persistence.crud.authority import accept_decision
from repowise.core.persistence.models import DecisionRecord
from repowise.server.mcp_server._answer_context import (
    _format_decisions_block,
    fetch_relevant_decisions,
)

_QUESTION = "why is there no MCP write tool for decisions"


async def _record(session, repo_id: str, title: str, *, accept: bool) -> None:
    await bulk_upsert_decisions(
        session,
        repo_id,
        [
            {
                "title": title,
                "decision": "Decision writes go through the CLI, not an MCP write tool.",
                "rationale": "The MCP surface stays read-only so every write is signed.",
                "source": "cli",
                "status": "proposed",
                "affected_files": ["docs/decisions.md"],
                "evidence_file": "docs/decisions.md",
                "confidence": 0.9,
                "verification": "exact",
                "source_quote": title,
            }
        ],
    )
    if accept:
        record = (
            await session.execute(select(DecisionRecord).where(DecisionRecord.title == title))
        ).scalar_one()
        await accept_decision(session, record, accepter="tester", scope=["docs/decisions.md"])
    await session.commit()


@pytest.mark.asyncio
async def test_an_accepted_decision_answering_the_question_is_read_without_a_path_hit(
    session, factory, repo_id
):
    await _record(session, repo_id, "No MCP write tool for decisions", accept=True)
    ctx = SimpleNamespace(session_factory=factory)

    decisions = await fetch_relevant_decisions(ctx, repo_id, [], question=_QUESTION)

    assert [d["title"] for d in decisions] == ["No MCP write tool for decisions"]
    assert decisions[0]["lane"] == "accepted"


@pytest.mark.asyncio
async def test_a_record_nobody_accepted_does_not_enter_the_question_lane(
    session, factory, repo_id
):
    await _record(session, repo_id, "No MCP write tool for decisions", accept=False)
    ctx = SimpleNamespace(session_factory=factory)

    assert await fetch_relevant_decisions(ctx, repo_id, [], question=_QUESTION) == []


@pytest.mark.asyncio
async def test_an_off_topic_question_reads_no_decision(session, factory, repo_id):
    await _record(session, repo_id, "No MCP write tool for decisions", accept=True)
    ctx = SimpleNamespace(session_factory=factory)

    decisions = await fetch_relevant_decisions(
        ctx, repo_id, [], question="why does the parser pool stay warm between files"
    )

    assert decisions == []


def test_the_prompt_says_when_a_rule_needs_review():
    block = _format_decisions_block(
        [{"title": "T", "lane": "accepted", "currency": "needs_review", "decision": "d"}]
    )

    assert "[D1] T (accepted, needs review)" in block
