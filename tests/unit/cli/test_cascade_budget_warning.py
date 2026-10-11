"""The cascade-budget warning splits skipped pages into model and structural."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

from repowise.cli.commands.update_cmd import reporting
from repowise.cli.commands.update_cmd.deterministic import (
    load_cascade_overflow_split,
    split_cascade_overflow,
)

_ROWS = [
    ("file_page", "src/a.py", "{}"),
    ("file_page", "src/b.py", "{}"),
    ("file_page", "src/kept.py", "{}"),
    ("module_page", "src", '{"file_paths": ["src/a.py", "src/kept.py"]}'),
    ("module_page", "lib", '{"file_paths": ["lib/x.py"]}'),
    ("scc_page", "scc-1", '{"files": ["src/b.py", "lib/x.py"]}'),
    ("repo_overview", "repo", "{}"),
    ("onboarding", "start", "{}"),
]


def _render(*args: object) -> str:
    rec = Console(record=True, width=300)
    with patch.object(reporting, "console", rec):
        reporting.render_cascade_budget_warning(*args)  # type: ignore[arg-type]
    return rec.export_text()


def test_split_counts_file_and_cycle_pages_as_structural() -> None:
    # model: the src module + overview + onboarding; structural: 2 files + 1 SCC.
    assert split_cascade_overflow(_ROWS, ["src/a.py", "src/b.py"]) == (3, 3)


def test_warning_points_model_pages_at_generate_stale() -> None:
    out = _render(50, 2752, (122, 1235))
    assert "Cascade budget of 50 pages was reached. 2752 dependent files were skipped" in out
    assert "1235 structural pages (file / cycle) refresh on the next repowise update" in out
    assert "122 model-written pages: run repowise generate --stale" in out
    assert "or pass `--cascade-budget 2802` to regenerate everything in this run." in out


def test_warning_without_model_pages_skips_generate_hint() -> None:
    out = _render(50, 10, (0, 10))
    assert "generate --stale" not in out
    assert "Pass `--cascade-budget 60` to regenerate them in this run." in out


def test_unreadable_store_falls_back_to_the_unsplit_hint() -> None:
    out = _render(50, 10, None)
    assert "Pass `--cascade-budget 60` to regenerate them all." in out
    assert "structural" not in out


def test_loader_reads_the_split_from_the_store(tmp_path: Path) -> None:
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.crud import upsert_page, upsert_repository
    from repowise.core.persistence.database import init_db

    repo_path = (tmp_path / "repo").resolve()
    (repo_path / ".repowise").mkdir(parents=True)

    async def build() -> None:
        engine = create_engine(f"sqlite+aiosqlite:///{repo_path / '.repowise' / 'wiki.db'}")
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            repo = await upsert_repository(session, name="repo", local_path=str(repo_path))
            for page_type, target, meta in _ROWS:
                await upsert_page(
                    session,
                    page_id=f"{page_type}:{target}",
                    repository_id=repo.id,
                    page_type=page_type,
                    title=target,
                    content="body",
                    target_path=target,
                    source_hash="",
                    model_name="mock",
                    provider_name="mock",
                    metadata=json.loads(meta),
                )
            await session.commit()
        await engine.dispose()

    asyncio.run(build())
    assert load_cascade_overflow_split(repo_path, ["src/a.py", "src/b.py"]) == (3, 3)
