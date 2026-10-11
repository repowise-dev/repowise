"""Delete a file, run ``repowise update``, and every table forgets it.

The deleted-file prune (#1377) and the tombstone step each cover a set of
tables, and a table missed by both keeps serving a file that is gone: that is
how a deleted file's ``git_metadata`` row came to rank first in "files that
need care" (#1929). This drives the real command and checks every file-keyed
table, so a table added later without a prune shows up here first.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from sqlalchemy import or_, select

from repowise.core.persistence.models import (
    DeadCodeFinding,
    GitMetadata,
    GraphEdge,
    GraphMetric,
    GraphNode,
    HealthFileMetric,
    HealthFinding,
    Page,
    WikiSymbol,
)

GONE = "pkg/gone.py"

# Every table that keys rows by file, and how a row names its file.
_KEYED = [
    (GraphNode, lambda m: or_(m.node_id == GONE, m.node_id.like(f"{GONE}::%"))),
    (GraphEdge, lambda m: or_(m.source_node_id.like(f"{GONE}%"), m.target_node_id.like(f"{GONE}%"))),
    (GraphMetric, lambda m: m.node_id == GONE),
    (WikiSymbol, lambda m: m.file_path == GONE),
    (GitMetadata, lambda m: m.file_path == GONE),
    (HealthFileMetric, lambda m: m.file_path == GONE),
    (HealthFinding, lambda m: m.file_path == GONE),
    (DeadCodeFinding, lambda m: m.file_path == GONE),
]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True
    ).stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    (repo / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "pkg" / "keep.py").write_text(
        "from pkg.gone import helper\n\n\ndef keep():\n    return helper()\n", encoding="utf-8"
    )
    (repo / "pkg" / "gone.py").write_text(
        "def helper():\n    return 1\n\n\ndef unused():\n    return 2\n", encoding="utf-8"
    )
    (repo / "pkg" / "other.py").write_text("def other():\n    return 3\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "c0")
    return repo, _git(repo, "rev-parse", "HEAD")


async def _rows(repo: Path) -> dict[str, int]:
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.persistence import create_engine, create_session_factory, get_session

    engine = create_engine(get_db_url_for_repo(repo))
    try:
        async with get_session(create_session_factory(engine)) as session:
            out: dict[str, int] = {}
            for model, where in _KEYED:
                rows = await session.execute(select(model).where(where(model)))
                out[model.__tablename__] = len(rows.scalars().all())
            page = await session.execute(select(Page).where(Page.id == f"file_page:{GONE}"))
            row = page.scalar_one_or_none()
            out["page"] = row.freshness_status if row is not None else None
            return out
    finally:
        await engine.dispose()


def test_every_table_forgets_a_deleted_file(tmp_path: Path, monkeypatch) -> None:
    from click.testing import CliRunner

    from repowise.cli.helpers import save_state
    from repowise.cli.main import cli
    from repowise.core.pipeline.full_index import index_repo_full

    monkeypatch.setenv("REPOWISE_SKIP_EDITOR_SETUP", "1")
    repo, c0 = _repo(tmp_path)
    asyncio.run(index_repo_full(repo))
    save_state(repo, {"last_sync_commit": c0, "last_docs_commit": c0, "docs_mode": "none"})

    before = asyncio.run(_rows(repo))
    # The index knew the file: a table with nothing to forget proves nothing.
    assert before["graph_nodes"] > 0
    assert before["wiki_symbols"] > 0
    assert before["git_metadata"] > 0

    _git(repo, "rm", "-q", GONE)
    (repo / "pkg" / "keep.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "c1: drop gone.py")

    result = CliRunner().invoke(cli, ["update", str(repo), "--no-workspace"])
    assert result.exit_code == 0, result.output

    after = asyncio.run(_rows(repo))
    leftovers = {t: n for t, n in after.items() if t != "page" and n}
    assert leftovers == {}, f"rows still name {GONE}: {leftovers}"
    assert after["page"] in (None, "tombstone")
    state = json.loads((repo / ".repowise" / "state.json").read_text(encoding="utf-8"))
    assert state["last_sync_commit"] == _git(repo, "rev-parse", "HEAD")
