"""MCP replies are the same whether the sidecar is current or from a newer Repowise."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from repowise.core.distill.store import OmissionStore
from repowise.core.savings.recorder import sidecar_path
from repowise.core.savings.schema import SAVINGS_SCHEMA_VERSION


def _stamp(db: Path, version: int) -> None:
    with sqlite3.connect(db) as conn:
        conn.execute(f"PRAGMA user_version={version}")


@pytest.mark.asyncio
async def test_get_why_and_get_symbol_reply_the_same_on_a_newer_sidecar(setup_mcp, tmp_path):
    from repowise.server.mcp_server import get_symbol, get_why

    db = sidecar_path(tmp_path)
    OmissionStore(db).close()

    async def replies() -> str:
        why = await get_why("src/auth/service.py")
        missing = await get_symbol("repowise#0123456789ab")
        return json.dumps([why, missing], sort_keys=True, default=str)

    current = await replies()
    _stamp(db, SAVINGS_SCHEMA_VERSION + 1)
    newer = await replies()

    assert newer == current
    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SAVINGS_SCHEMA_VERSION + 1
