"""``doctor`` does not report excluded files as missing from the indexes.

A page whose file the user excluded is absent from FTS and the vector store
because they asked for that. The reconciliation counted it as drift anyway, so
the row FAILed on every run and no action could clear it — ``--repair``'s only
remedy is to index the content they excluded.

``core/exclusion.py`` documents the contract this violated: rows outlive an
``exclude_patterns`` edit by design, so read paths filter at query time rather
than forcing a reindex. Every other reader does — ``filter_graph_nodes``,
``_node_id_is_excluded``, ``_prose_symbols`` — and this check did not.

The symbol case is the one that hides: a symbol page's ``target_path`` is
``path::Name``, which no file pattern matches, so filtering without splitting on
``::`` silently keeps every symbol of every excluded file.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from repowise.cli.commands.doctor_cmd import repo_checks

KEPT = "file_page:kept.py"
EXCLUDED_FILE = "file_page:lib/model.g.dart"
EXCLUDED_SYMBOL = "symbol_spotlight:lib/model.g.dart::GeneratedThing"

_BODY = "Body with enough words in it to clear the information floor."


async def _build_repo(tmp_path: Path) -> Path:
    """A repo excluding ``*.g.dart``, with pages for it left in the database.

    The pattern is extension-anchored on purpose. A directory prefix like
    ``build/**`` matches ``build/x.py::Name`` too, so it would let a fix that
    forgot to split on ``::`` pass; ``*.g.dart`` does not match
    ``model.g.dart::GeneratedThing`` and pins the symbol case.

    Only the kept page is indexed, which is the correct state: the excluded
    ones were skipped at ingest, and their rows predate the exclusion.
    """
    import git as gitpython

    from repowise.core.persistence import (
        FullTextSearch,
        create_engine,
        create_session_factory,
        get_session,
    )
    from repowise.core.persistence.crud import upsert_page, upsert_repository
    from repowise.core.persistence.database import init_db

    repo_path = (tmp_path / "repo").resolve()
    repo_path.mkdir()
    gitpython.Repo.init(repo_path)
    repowise_dir = repo_path / ".repowise"
    repowise_dir.mkdir()
    (repowise_dir / "config.yaml").write_text(
        "exclude_patterns:\n  - '**/*.g.dart'\n", encoding="utf-8"
    )

    engine = create_engine(f"sqlite+aiosqlite:///{repowise_dir / 'wiki.db'}")
    await init_db(engine)
    sf = create_session_factory(engine)
    async with get_session(sf) as session:
        repo = await upsert_repository(
            session, name="repo", local_path=str(repo_path), url="https://example.test/repo"
        )
        for page_id, page_type, target in (
            (KEPT, "file_page", "kept.py"),
            (EXCLUDED_FILE, "file_page", "lib/model.g.dart"),
            (EXCLUDED_SYMBOL, "symbol_spotlight", "lib/model.g.dart::GeneratedThing"),
        ):
            await upsert_page(
                session,
                page_id=page_id,
                repository_id=repo.id,
                page_type=page_type,
                title=f"Page: {target}",
                content=_BODY,
                summary="",
                target_path=target,
                source_hash="",
                model_name="mock",
                provider_name="mock",
            )
        await session.commit()

    fts = FullTextSearch(engine)
    await fts.ensure_index()
    await fts.index(KEPT, "Page: kept.py", _BODY, summary="", target_path="kept.py")
    await engine.dispose()
    return repo_path


def _rows(repo_path: Path) -> dict[str, tuple[bool, str]]:
    _all_ok, checks = repo_checks._run_repo_checks(repo_path, repair=False)
    return {c.name: (c.ok, c.detail) for c in checks}


def test_an_excluded_file_is_not_missing_from_the_index(tmp_path: Path) -> None:
    """Both excluded pages are absent on purpose, so neither is drift."""
    rows = _rows(asyncio.run(_build_repo(tmp_path)))

    ok, detail = rows["SQL ↔ FTS Index"]
    assert detail == "in sync"
    assert ok is True
