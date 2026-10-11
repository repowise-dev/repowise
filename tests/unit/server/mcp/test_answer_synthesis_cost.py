"""get_answer meters what its own synthesis call costs.

The tool has never been able to report its own spend: ``synthesize`` threw the
provider's token counts away, so the only way to price a question was to run it
from outside and meter the provider. That makes every cost claim about the
answer path unverifiable in production, and it hides the two states that matter
— a provider that reports no usage at all, and a call that got expensive.

A completed call now writes one ``llm_costs`` row under its own operation
label, and a response carrying no usage warns instead of writing zeros (a zero
row reads as a free call, which is the more expensive mistake).
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import LlmCost, Repository
from repowise.core.providers.llm.base import GeneratedResponse
from repowise.server.mcp_server.tool_answer.synthesis import synthesize

# Asserted as a literal, not imported: the label is the bucket the costs report
# groups by, so renaming the constant must not quietly rename the bucket.
_COST_OPERATION = "answer_synthesis"


class _Provider:
    """Provider that returns a fixed response, recording nothing else."""

    provider_name = "testprov"
    model_name = "gemini-3.1-flash-lite-preview"
    interactive_timeout_s = 30.0

    def __init__(self, response: GeneratedResponse) -> None:
        self._response = response

    async def generate(self, **_kwargs) -> GeneratedResponse:
        return self._response


@pytest.fixture
async def factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(engine)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with sessionmaker() as session:
        session.add(
            Repository(
                id="repo1",
                name="test-repo",
                url="https://github.com/example/test-repo",
                local_path="/tmp/test-repo",
                default_branch="main",
                settings_json="{}",
            )
        )
        await session.commit()
    yield sessionmaker
    await engine.dispose()


async def _ledger_rows(factory) -> list[LlmCost]:
    async with factory() as session:
        rows = await session.execute(select(LlmCost))
        return list(rows.scalars().all())


async def test_a_completed_synthesis_writes_one_cost_row(factory):
    provider = _Provider(
        GeneratedResponse(content="the answer", input_tokens=61_741, output_tokens=402)
    )

    text, note = await synthesize(
        provider, "system", "user", session_factory=factory, repo_id="repo1"
    )

    assert (text, note) == ("the answer", None)
    rows = await _ledger_rows(factory)
    assert len(rows) == 1
    row = rows[0]
    assert row.operation == _COST_OPERATION
    assert row.model == "gemini-3.1-flash-lite-preview"
    assert (row.input_tokens, row.output_tokens) == (61_741, 402)
    assert row.cost_usd > 0.0


async def test_a_response_with_no_usage_warns_instead_of_writing_zeros(factory, caplog):
    """A provider adapter that forgets to normalise usage must not read as free."""
    provider = _Provider(GeneratedResponse(content="the answer", input_tokens=0, output_tokens=0))

    with caplog.at_level(logging.WARNING, logger="repowise.mcp.answer"):
        text, note = await synthesize(
            provider, "system", "user", session_factory=factory, repo_id="repo1"
        )

    assert (text, note) == ("the answer", None)
    assert await _ledger_rows(factory) == []
    assert any(
        "usage" in r.message.lower() and r.levelno == logging.WARNING for r in caplog.records
    ), caplog.text


async def test_the_cost_row_is_logged_even_with_nowhere_to_persist_it(factory, caplog):
    """The MCP server can run against a repo it has no writable ledger for."""
    provider = _Provider(
        GeneratedResponse(
            content="the answer", input_tokens=1_200, output_tokens=90, cached_tokens=800
        )
    )

    with caplog.at_level(logging.INFO, logger="repowise.mcp.answer"):
        text, _ = await synthesize(provider, "system", "user")

    assert text == "the answer"
    assert await _ledger_rows(factory) == []
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "cached_tokens=800" in logged, logged
    assert "input_tokens=1200" in logged, logged


async def test_a_failed_synthesis_writes_no_cost_row(factory):
    class _Failing(_Provider):
        async def generate(self, **_kwargs):
            raise RuntimeError("rate limited")

    provider = _Failing(GeneratedResponse(content="", input_tokens=0, output_tokens=0))

    text, note = await synthesize(
        provider, "system", "user", session_factory=factory, repo_id="repo1"
    )

    assert text == ""
    assert note is not None and "DEGRADED" in note
    assert await _ledger_rows(factory) == []
