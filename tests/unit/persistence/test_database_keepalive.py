"""Opt-in Postgres settings must never leak into SQLite or other drivers."""

from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from repowise.core.persistence.database import create_engine

_POSTGRES_URL = "postgresql+asyncpg://user:password@localhost/repowise"
_SETTING_VARS = (
    "REPOWISE_POSTGRES_KEEPALIVES_IDLE",
    "REPOWISE_POSTGRES_KEEPALIVES_INTERVAL",
    "REPOWISE_POSTGRES_KEEPALIVES_COUNT",
    "REPOWISE_POSTGRES_COMMAND_TIMEOUT",
)


@pytest.fixture(autouse=True)
def _clear_keepalive_env(monkeypatch):
    for name in ("REPOWISE_POSTGRES_KEEPALIVE", *_SETTING_VARS):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("short_lived", [True, False])
@pytest.mark.parametrize("enabled", [None, "", "0", "false", "off", "no"])
def test_postgres_defaults_do_not_send_startup_parameters(monkeypatch, enabled, short_lived):
    if enabled is not None:
        monkeypatch.setenv("REPOWISE_POSTGRES_KEEPALIVE", enabled)
    for name in _SETTING_VARS:
        monkeypatch.setenv(name, "invalid-but-disabled")

    with patch("repowise.core.persistence.database.create_async_engine") as factory:
        create_engine(_POSTGRES_URL, short_lived=short_lived)

    options = factory.call_args.kwargs
    assert not options.get("connect_args")
    assert options["pool_pre_ping"] is True
    assert options.get("poolclass") is (NullPool if short_lived else None)


@pytest.mark.parametrize("url", [_POSTGRES_URL, "postgresql://user:password@localhost/repowise"])
@pytest.mark.parametrize("short_lived", [True, False])
@pytest.mark.parametrize("enabled", ["1", "true", "yes", "on", " TRUE "])
def test_opt_in_sets_asyncpg_defaults(monkeypatch, url, short_lived, enabled):
    monkeypatch.setenv("REPOWISE_POSTGRES_KEEPALIVE", enabled)
    with patch("repowise.core.persistence.database.create_async_engine") as factory:
        create_engine(url, short_lived=short_lived)

    assert factory.call_args.args == (_POSTGRES_URL,)
    options = factory.call_args.kwargs
    assert options["connect_args"] == {
        "server_settings": {
            "tcp_keepalives_idle": "60",
            "tcp_keepalives_interval": "15",
            "tcp_keepalives_count": "4",
        },
        "command_timeout": 300,
    }
    assert options["pool_pre_ping"] is True
    assert options.get("poolclass") is (NullPool if short_lived else None)


def test_overrides_are_read_for_each_engine(monkeypatch):
    monkeypatch.setenv("REPOWISE_POSTGRES_KEEPALIVE", "1")
    for name, value in zip(_SETTING_VARS, (90, 20, 6, 600), strict=True):
        monkeypatch.setenv(name, str(value))

    with patch("repowise.core.persistence.database.create_async_engine") as factory:
        create_engine(_POSTGRES_URL)
        assert factory.call_args.kwargs["connect_args"] == {
            "server_settings": {
                "tcp_keepalives_idle": "90",
                "tcp_keepalives_interval": "20",
                "tcp_keepalives_count": "6",
            },
            "command_timeout": 600,
        }
        monkeypatch.setenv("REPOWISE_POSTGRES_KEEPALIVES_IDLE", "120")
        create_engine(_POSTGRES_URL)
        assert factory.call_args.kwargs["connect_args"]["server_settings"]["tcp_keepalives_idle"] == "120"


@pytest.mark.parametrize("name", _SETTING_VARS)
@pytest.mark.parametrize("value", ["", "invalid", "0", "-1", "1.5"])
def test_invalid_enabled_setting_fails_before_connecting(monkeypatch, name, value):
    monkeypatch.setenv("REPOWISE_POSTGRES_KEEPALIVE", "1")
    monkeypatch.setenv(name, value)
    with patch("repowise.core.persistence.database.create_async_engine") as factory:
        with pytest.raises(ValueError, match=f"{name} must be a positive integer"):
            create_engine(_POSTGRES_URL)
        factory.assert_not_called()


async def test_sqlite_ignores_postgres_settings(monkeypatch):
    monkeypatch.setenv("REPOWISE_POSTGRES_KEEPALIVE", "1")
    for name in _SETTING_VARS:
        monkeypatch.setenv(name, "invalid-but-not-postgres")

    engine = create_engine("sqlite:///:memory:")
    try:
        async with engine.connect() as connection:
            assert (await connection.execute(text("SELECT 1"))).scalar_one() == 1
            assert (await connection.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1
    finally:
        await engine.dispose()


def test_explicit_other_driver_ignores_asyncpg_settings(monkeypatch):
    monkeypatch.setenv("REPOWISE_POSTGRES_KEEPALIVE", "1")
    for name in _SETTING_VARS:
        monkeypatch.setenv(name, "invalid-but-not-asyncpg")

    with patch("repowise.core.persistence.database.create_async_engine") as factory:
        create_engine("postgresql+psycopg://user:password@localhost/repowise")

    assert "connect_args" not in factory.call_args.kwargs
    assert factory.call_args.kwargs["pool_pre_ping"] is True
