"""Incremental updates must refresh ``wiki_symbols`` for changed files.

The incremental update path re-parses changed files but historically never
persisted their symbols, so symbol start/end bounds fossilized at the last
full index. The get_answer hydrator then read signatures/bodies from the live
file at those stale bounds and served garbled text. These tests drive the real
parser over a file whose symbol moves (lines inserted above it) and assert the
persisted bounds and ``updated_at`` follow, that a symbol deleted from a
still-existing file is pruned, and that unchanged files are left alone.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from repowise.core.ingestion import ASTParser, FileTraverser
from repowise.core.persistence.models import WikiSymbol
from repowise.core.pipeline.persist import persist_incremental_symbols
from tests.unit.persistence.helpers import insert_repo


def _parse(repo_dir: Path) -> list:
    """Parse every file under *repo_dir* into ParsedFile objects."""
    traverser = FileTraverser(repo_dir)
    parser = ASTParser()
    return [parser.parse_file(fi, Path(fi.abs_path).read_bytes()) for fi in traverser.traverse()]


async def _symbol_row(session, repo_id: str, symbol_id: str) -> WikiSymbol | None:
    return (
        await session.execute(
            select(WikiSymbol).where(
                WikiSymbol.repository_id == repo_id,
                WikiSymbol.symbol_id == symbol_id,
            )
        )
    ).scalar_one_or_none()


async def test_incremental_update_refreshes_moved_symbol(async_session, tmp_path):
    repo = await insert_repo(async_session)

    # v1: foo near the top, bar below it.
    (tmp_path / "m.py").write_text(
        "import os\n\n\ndef foo():\n    return 1\n\n\ndef bar():\n    return 2\n"
    )
    parsed_v1 = _parse(tmp_path)
    await persist_incremental_symbols(async_session, repo.id, parsed_v1, ["m.py"])
    await async_session.commit()

    foo_v1 = await _symbol_row(async_session, repo.id, "m.py::foo")
    assert foo_v1 is not None
    v1_start = foo_v1.start_line
    assert await _symbol_row(async_session, repo.id, "m.py::bar") is not None

    # Backdate updated_at so the refresh is detectable as a real re-write.
    old_ts = datetime.now(UTC) - timedelta(days=7)
    foo_v1.updated_at = old_ts
    await async_session.commit()

    # v2: five blank lines inserted above the code moves foo down; bar removed.
    (tmp_path / "m.py").write_text("import os\n" + "\n" * 5 + "\n\ndef foo():\n    return 1\n")
    parsed_v2 = _parse(tmp_path)
    from repowise.core.persistence.crud import reconcile_symbols_for_files

    # Sanity: the fresh parse really moved foo before we assert the DB followed.
    v2_foo = next(s for pf in parsed_v2 for s in pf.symbols if s.name == "foo")
    assert v2_foo.start_line > v1_start

    await persist_incremental_symbols(async_session, repo.id, parsed_v2, ["m.py"])
    await async_session.commit()

    foo_v2 = await _symbol_row(async_session, repo.id, "m.py::foo")
    assert foo_v2.start_line == v2_foo.start_line
    assert foo_v2.start_line > v1_start
    assert foo_v2.updated_at > old_ts
    # bar was deleted from a still-existing file, so its row must be pruned.
    assert await _symbol_row(async_session, repo.id, "m.py::bar") is None

    # reconcile returns the prune count directly for the changed-file scope.
    _reparse = _parse(tmp_path)
    syms = [s for pf in _reparse for s in pf.symbols]
    for s in syms:
        s.file_path = "m.py"
    pruned = await reconcile_symbols_for_files(async_session, repo.id, ["m.py"], syms)
    assert pruned == 0  # nothing new to prune on a no-op re-run


async def test_incremental_update_leaves_unchanged_files_untouched(async_session, tmp_path):
    """Only files in the changed set are re-written; others keep their rows."""
    repo = await insert_repo(async_session)

    # Seed an unchanged file's symbol directly with an old timestamp.
    old_ts = datetime.now(UTC) - timedelta(days=30)
    async_session.add(
        WikiSymbol(
            repository_id=repo.id,
            file_path="other.py",
            symbol_id="other.py::helper",
            name="helper",
            qualified_name="helper",
            kind="function",
            start_line=10,
            end_line=12,
            updated_at=old_ts,
        )
    )
    await async_session.commit()

    (tmp_path / "m.py").write_text("def foo():\n    return 1\n")
    parsed = _parse(tmp_path)
    # changed set names only m.py, even though other.py exists in the DB.
    await persist_incremental_symbols(async_session, repo.id, parsed, ["m.py"])
    await async_session.commit()

    other = await _symbol_row(async_session, repo.id, "other.py::helper")
    assert other is not None
    # SQLite drops tzinfo on read; compare against the naive UTC wall clock.
    assert other.updated_at.replace(tzinfo=None) == old_ts.replace(tzinfo=None)
    assert other.start_line == 10  # untouched by the scoped refresh
