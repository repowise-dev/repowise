"""An update re-renders a changed file's neighbours from the same facts a full
index renders them from.

The run re-indexes git for the changed files only, so a neighbour page used to
render with no git facts and lose its history sections. The changed file's own
page could also be cut by the cascade budget and then marked stale by the
same run that rendered it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import networkx as nx

from repowise.cli.commands.update_cmd.command import _drop_from_decay
from repowise.cli.commands.update_cmd.deterministic import (
    load_prior_page_ids,
    with_stored_git_meta,
)
from repowise.core.ingestion.change_detector import AffectedPages, ChangeDetector, FileDiff
from repowise.core.persistence import create_engine, create_session_factory, get_session
from repowise.core.persistence.crud import (
    upsert_git_metadata_bulk,
    upsert_page,
    upsert_repository,
)
from repowise.core.persistence.database import init_db, resolve_db_url


def _seed(repo_path: Path, *, git_rows: list[dict], pages: list[tuple[str, str]]) -> None:
    async def _go() -> None:
        engine = create_engine(resolve_db_url(repo_path))
        try:
            await init_db(engine)
            async with get_session(create_session_factory(engine)) as session:
                repo = await upsert_repository(session, name="r", local_path=str(repo_path))
                await upsert_git_metadata_bulk(session, repo.id, git_rows)
                for page_id, target in pages:
                    await upsert_page(
                        session,
                        page_id=page_id,
                        repository_id=repo.id,
                        page_type=page_id.split(":", 1)[0],
                        title=target,
                        content=f"body of {target}",
                        target_path=target,
                        source_hash="h",
                        model_name="template",
                        provider_name="template",
                    )
        finally:
            await engine.dispose()

    (repo_path / ".repowise").mkdir(parents=True, exist_ok=True)
    asyncio.run(_go())


def test_neighbour_pages_get_their_stored_git_rows(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        git_rows=[
            {"file_path": "a.py", "commit_count_total": 3},
            {"file_path": "b.py", "commit_count_total": 47},
        ],
        pages=[],
    )
    fresh = {"a.py": {"file_path": "a.py", "commit_count_total": 4}}

    merged = with_stored_git_meta(tmp_path, fresh, ["a.py", "b.py", "gone.py"])

    # The changed file keeps this run's row; the neighbour gets its stored one.
    assert merged["a.py"]["commit_count_total"] == 4
    assert merged["b.py"]["commit_count_total"] == 47
    assert "gone.py" not in merged
    # The persisted map is not widened: only the render sees stored rows.
    assert set(fresh) == {"a.py"}


def test_no_store_read_when_every_path_is_fresh(tmp_path: Path) -> None:
    fresh = {"a.py": {"commit_count_total": 4}}
    # No .repowise here: a read would fail; the map comes back as given.
    assert with_stored_git_meta(tmp_path, fresh, ["a.py"]) is fresh


def test_prior_bodies_only_for_rerendered_files(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        git_rows=[],
        pages=[
            ("file_page:a.py", "a.py"),
            ("symbol_spotlight:a.py::f", "a.py::f"),
            ("file_page:b.py", "b.py"),
        ],
    )

    prior = load_prior_page_ids(tmp_path, content_paths=["a.py"])

    assert set(prior) == {"file_page:a.py", "symbol_spotlight:a.py::f", "file_page:b.py"}
    assert prior["file_page:a.py"].content == "body of a.py"
    assert prior["symbol_spotlight:a.py::f"].content == "body of a.py::f"
    assert prior["file_page:b.py"] is None


def test_rerendered_page_is_not_decayed() -> None:
    affected = AffectedPages(
        regenerate=["imp1.py"],
        rename_patch=[],
        decay_only=["changed.py", "imp2.py", "partner.py"],
        stale_due_to_budget=2,
    )

    _drop_from_decay(affected, {"changed.py"})

    assert affected.decay_only == ["imp2.py", "partner.py"]
    # The budget overflow still heads decay_only, and its count follows it.
    assert affected.stale_due_to_budget == 1


def test_changed_file_outranks_more_central_importers(tmp_path: Path) -> None:
    graph = nx.DiGraph()
    for i in range(1, 6):
        graph.add_edge(f"imp{i}.py", "utils.py")
    pagerank = {f"imp{i}.py": 0.5 + i / 10 for i in range(1, 6)}
    pagerank["utils.py"] = 0.01
    diff = FileDiff(
        path="utils.py",
        status="modified",
        old_path=None,
        old_parsed=None,
        new_parsed=SimpleNamespace(file_info=SimpleNamespace(path="utils.py")),
        symbol_diff=None,
    )

    result = ChangeDetector(tmp_path).get_affected_pages(
        [diff], graph, cascade_budget=2, pagerank=pagerank
    )

    assert result.regenerate == ["utils.py", "imp5.py"]
    assert "utils.py" not in result.decay_only
