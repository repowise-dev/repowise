"""A keyless ``serve`` starts without loading lancedb or pyarrow.

Run in a fresh interpreter: the test process has usually imported both already.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SCRIPT = r"""
import asyncio, sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import Repository
from repowise.server.app import lifespan

repo = Path(sys.argv[1])
db = repo / ".repowise" / "wiki.db"
# A store an earlier keyless init left behind: still not opened.
(repo / ".repowise" / "lancedb").mkdir(parents=True)


async def main():
    engine = create_async_engine(f"sqlite+aiosqlite:///{db.as_posix()}")
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    now = datetime.now(UTC)
    async with factory() as session:
        session.add(Repository(id="r", name="repo", url="", local_path=str(repo.resolve()),
                               default_branch="main", settings_json="{}",
                               created_at=now, updated_at=now))
        await session.commit()
    await engine.dispose()
    with patch("repowise.server.app.setup_scheduler", return_value=MagicMock()):
        app = FastAPI()
        async with lifespan(app):
            print(type(app.state.vector_store).__name__)
    print(sorted(m for m in ("lancedb", "pyarrow") if m in sys.modules))


asyncio.run(main())
"""


def test_keyless_serve_startup_never_imports_lancedb(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.endswith("_API_KEY") and not k.startswith("REPOWISE_EMBEDD")
    }
    env["REPOWISE_DB_URL"] = f"sqlite+aiosqlite:///{(repo / '.repowise' / 'wiki.db').as_posix()}"
    env["HOME"] = env["USERPROFILE"] = str(tmp_path)
    out = subprocess.run(
        [sys.executable, "-c", _SCRIPT, str(repo)],
        capture_output=True,
        text=True,
        cwd=repo,
        env=env,
        timeout=180,
    )
    assert out.returncode == 0, out.stderr[-3000:]
    lines = out.stdout.strip().splitlines()
    assert lines[-2] == "InMemoryVectorStore"
    assert lines[-1] == "[]", out.stderr[-3000:]
