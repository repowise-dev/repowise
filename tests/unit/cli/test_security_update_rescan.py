"""Every update path rescans the files it changed for security findings.

Before this, only ``init`` and a scanner version bump wrote working-tree
findings, so an update left new findings in changed files unseen and old
rows pointing at lines that had moved.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

_HASH = "import hashlib\n\n\ndef f(x):\n    return hashlib.md5(x)\n"


async def _seed(repo_path: Path) -> None:
    """Rows as the last full index wrote them: ``a.py`` before an edit moved
    its finding, and a stale row on untouched ``c.py`` that must stay put."""
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )

    engine = create_engine(get_db_url_for_repo(repo_path))
    await init_db(engine)
    async with get_session(create_session_factory(engine)) as session:
        repo = await upsert_repository(session, name=repo_path.name, local_path=str(repo_path))
        for path, line in (("a.py", 2), ("c.py", 9)):
            await session.execute(
                text(
                    "INSERT INTO security_findings (repository_id, file_path, kind, "
                    "severity, snippet, line_number, commit_sha, detected_at) "
                    "VALUES (:r, :p, 'weak_hash', 'low', 'x', :l, '', datetime('now'))"
                ),
                {"r": repo.id, "p": path, "l": line},
            )
    await engine.dispose()


async def _rows(repo_path: Path) -> set[tuple]:
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.persistence import create_engine, create_session_factory, get_session

    engine = create_engine(get_db_url_for_repo(repo_path))
    async with get_session(create_session_factory(engine)) as session:
        res = await session.execute(
            text("SELECT file_path, kind, line_number FROM security_findings")
        )
        rows = {tuple(r) for r in res.fetchall()}
    await engine.dispose()
    return rows


def _repo(tmp_path: Path):
    from repowise.cli.commands.update_cmd.incremental import _build_repo_graph

    (tmp_path / ".repowise").mkdir()
    (tmp_path / "c.py").write_text("def c():\n    return 3\n", encoding="utf-8")
    asyncio.run(_seed(tmp_path))
    # The edit: a.py's finding moves to line 5, and b.py is new.
    (tmp_path / "a.py").write_text(_HASH, encoding="utf-8")
    (tmp_path / "b.py").write_text(_HASH, encoding="utf-8")
    parsed_files, source_map, graph_builder, _structure, _count = _build_repo_graph(
        tmp_path, [], collect_sources=True
    )
    return parsed_files, source_map, graph_builder


def _persist_index_only(tmp_path, parsed_files, source_map, graph_builder, changed):
    from repowise.core.pipeline.incremental import persist_incremental_index

    asyncio.run(
        persist_incremental_index(
            tmp_path,
            graph_builder,
            {},
            None,
            None,
            changed,
            parsed_files=parsed_files,
            source_map=source_map,
            log=lambda *_a, **_k: None,
        )
    )


def _persist_docs_mode(tmp_path, parsed_files, source_map, graph_builder, changed):
    from repowise.cli.commands.update_cmd.persistence import _persist_full_update_async

    asyncio.run(
        _persist_full_update_async(
            repo_path=tmp_path,
            repo_name=tmp_path.name,
            generated_pages=[],
            file_diffs=[SimpleNamespace(path=p, old_path=None, status="modified") for p in changed],
            git_meta_map={},
            new_decision_markers=[],
            decision_vector_store=None,
            provider=None,
            partial_health_report=None,
            dead_code_report=None,
            graph_builder=graph_builder,
            knowledge_graph_result=None,
            degraded=[],
            parsed_files=parsed_files,
            source_map=source_map,
        )
    )


@pytest.mark.parametrize("persist", [_persist_index_only, _persist_docs_mode])
def test_update_rescans_changed_and_added_files(tmp_path: Path, persist) -> None:
    parsed_files, source_map, graph_builder = _repo(tmp_path)

    persist(tmp_path, parsed_files, source_map, graph_builder, ["a.py", "b.py"])

    rows = asyncio.run(_rows(tmp_path))
    assert ("a.py", "weak_hash", 5) in rows
    assert ("a.py", "weak_hash", 2) not in rows
    assert ("b.py", "weak_hash", 5) in rows
    # Scoped to the change: an untouched file's rows are not this run's to rewrite.
    assert ("c.py", "weak_hash", 9) in rows
