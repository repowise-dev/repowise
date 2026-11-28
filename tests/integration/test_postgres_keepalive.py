"""Verify asyncpg startup parameters and timeouts against a real PostgreSQL server.

Set REPOWISE_TEST_PG_URL to run; no schema changes or pgvector are needed.
"""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from repowise.core.persistence.database import create_engine

PG_URL = os.environ.get("REPOWISE_TEST_PG_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="REPOWISE_TEST_PG_URL not set")
_KEEPALIVE_SETTINGS = (
    "tcp_keepalives_idle",
    "tcp_keepalives_interval",
    "tcp_keepalives_count",
)


@pytest.fixture(autouse=True)
def _clear_keepalive_env(monkeypatch):
    for suffix in (
        "KEEPALIVE", "KEEPALIVES_IDLE", "KEEPALIVES_INTERVAL", "KEEPALIVES_COUNT", "COMMAND_TIMEOUT",
    ):
        monkeypatch.delenv(f"REPOWISE_POSTGRES_{suffix}", raising=False)


async def _read_settings(engine):
    try:
        async with engine.connect() as connection:
            return [
                (await connection.execute(text(f"SHOW {name}"))).scalar_one()
                for name in _KEEPALIVE_SETTINGS
            ]
    finally:
        await engine.dispose()


async def test_disabled_preserves_server_defaults():
    expected = await _read_settings(create_async_engine(PG_URL))
    assert await _read_settings(create_engine(PG_URL)) == expected


@pytest.mark.parametrize("overrides", [None, (90, 20, 6)])
async def test_enabled_session_uses_keepalive_settings(monkeypatch, overrides):
    monkeypatch.setenv("REPOWISE_POSTGRES_KEEPALIVE", "1")
    if overrides is not None:
        for suffix, value in zip(("IDLE", "INTERVAL", "COUNT"), overrides, strict=True):
            monkeypatch.setenv(f"REPOWISE_POSTGRES_KEEPALIVES_{suffix}", str(value))

    actual = await _read_settings(create_engine(PG_URL))
    assert actual == [str(value) for value in (overrides or (60, 15, 4))]


@pytest.mark.parametrize("short_lived", [True, False])
async def test_command_timeout_interrupts_a_statement(monkeypatch, short_lived):
    monkeypatch.setenv("REPOWISE_POSTGRES_KEEPALIVE", "1")
    monkeypatch.setenv("REPOWISE_POSTGRES_COMMAND_TIMEOUT", "1")
    engine = create_engine(PG_URL, short_lived=short_lived)
    try:
        async with engine.connect() as connection:
            with pytest.raises(TimeoutError):
                await connection.execute(text("SELECT pg_sleep(5)"))
    finally:
        await engine.dispose()
