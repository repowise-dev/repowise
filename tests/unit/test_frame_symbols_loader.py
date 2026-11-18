"""load_frame_symbols: exact paths always, case folded only when asked."""

from __future__ import annotations

import asyncio

from repowise.core.frame_resolver import load_frame_symbols
from repowise.core.persistence import create_session_factory, get_session
from repowise.core.persistence.database import create_engine, init_db
from repowise.core.persistence.models import HealthFileMetric, Repository, WikiSymbol


def _load(tmp_path, paths, **kw):
    async def run():
        engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'wiki.db'}")
        try:
            await init_db(engine)
            async with get_session(create_session_factory(engine)) as session:
                session.add(Repository(id="r1", name="t", local_path=str(tmp_path), url=""))
                await session.flush()
                session.add(WikiSymbol(
                    repository_id="r1", file_path="Pkg/App.py", symbol_id="Pkg/App.py::run",
                    name="run", qualified_name="run", kind="function", start_line=1, end_line=3,
                ))
                session.add(HealthFileMetric(repository_id="r1", file_path="pkg/empty.py",
                                             code_origin="production"))
                await session.flush()
                return await load_frame_symbols(session, "r1", paths, **kw)
        finally:
            await engine.dispose()

    return asyncio.run(run())


def test_exact_paths_load_symbols_and_files_without_symbols(tmp_path) -> None:
    symbols, origins = _load(tmp_path, ["Pkg/App.py", "pkg/empty.py", "pkg/app.py"])
    assert set(symbols) == {"Pkg/App.py", "pkg/empty.py"}
    assert [s.qualified_name for s in symbols["Pkg/App.py"]] == ["run"]
    assert symbols["pkg/empty.py"] == [] and origins == {"pkg/empty.py": "production"}


def test_fold_case_finds_a_path_spelled_in_another_case(tmp_path) -> None:
    symbols, _ = _load(tmp_path, ["pkg/app.py"], fold_case=True)
    assert set(symbols) == {"Pkg/App.py"}
