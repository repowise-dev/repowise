"""The opt-in caller options on ``update_single_repo_index``.

A products sink, a switch that reports "full index needed" instead of running
one, and a whole-repo health re-score. Defaults leave the update unchanged.
"""

from __future__ import annotations

import asyncio

import pytest

from repowise.core.workspace.update import (
    UpdateProducts,
    get_head_commit,
    update_single_repo_index,
)
from tests.unit.workspace.test_incremental_update import (
    _add_commit,
    _git,
    _make_git_repo,
    _mark_indexed,
    forbid_full_pipeline,  # noqa: F401 (fixture)
    stub_full_pipeline,  # noqa: F401 (fixture)
)


@pytest.fixture
def forbid_index_repo_full(monkeypatch):
    import repowise.core.pipeline.full_index as full_index_mod

    async def _boom(*args, **kwargs):  # pragma: no cover - failure path
        raise AssertionError("index_repo_full must not run without the fallback")

    monkeypatch.setattr(full_index_mod, "index_repo_full", _boom)


def _indexed_repo_with_new_commit(tmp_path, **state):
    repo = _make_git_repo(tmp_path)
    _mark_indexed(repo, get_head_commit(repo), **state)
    _add_commit(repo, "b.py")
    return repo


def _stored_metric_paths(repo) -> set[str]:
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.crud import get_health_metrics
    from repowise.core.persistence.database import resolve_db_url
    from repowise.core.persistence.models import Repository

    async def _read() -> set[str]:
        from sqlalchemy import select

        engine = create_engine(resolve_db_url(repo))
        try:
            async with get_session(create_session_factory(engine)) as session:
                repo_id = (await session.execute(select(Repository.id))).scalar_one()
                return {m.file_path for m in await get_health_metrics(session, repo_id)}
        finally:
            await engine.dispose()

    return asyncio.run(_read())


def test_sink_receives_the_update_products(tmp_path, forbid_full_pipeline):  # noqa: F811
    repo = _indexed_repo_with_new_commit(tmp_path)
    sink: list[UpdateProducts] = []

    result = asyncio.run(update_single_repo_index(repo, products_sink=sink))

    assert result.updated is True and result.error is None
    assert len(sink) == 1
    products = sink[0]
    paths = {pf.file_info.path for pf in products.parsed_files}
    # The whole repo, not only the changed file.
    assert {"a.py", "b.py"} <= paths
    assert {"a.py", "b.py"} <= set(products.source_map)
    assert products.file_count == result.file_count
    assert "a.py" in products.graph_builder.graph()
    assert products.repo_structure is not None
    assert products.dead_code_report is not None
    assert products.doc_drift_report is not None
    assert products.health_report is not None


def test_sink_stays_empty_when_nothing_changed(tmp_path, forbid_full_pipeline):  # noqa: F811
    repo = _make_git_repo(tmp_path)
    _mark_indexed(repo, get_head_commit(repo))
    _git(repo, "commit", "--allow-empty", "-m", "empty")
    sink: list[UpdateProducts] = []

    result = asyncio.run(update_single_repo_index(repo, products_sink=sink))

    assert result.updated is True
    assert sink == []


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        ("never_indexed", "not_indexed"),
        ("config_drift", "config_changed"),
        ("base_missing", "base_missing"),
        ("update_failed", "update_failed"),
    ],
)
def test_no_fallback_reports_full_index_needed(
    tmp_path, monkeypatch, forbid_index_repo_full, setup, reason
):
    repo = _make_git_repo(tmp_path)
    if setup == "config_drift":
        _mark_indexed(repo, get_head_commit(repo), config_fingerprint="0" * 64)
    elif setup == "base_missing":
        _mark_indexed(repo, "deadbeef" * 5)
    elif setup == "update_failed":
        import repowise.core.pipeline.incremental as incremental_mod

        async def _boom(*args, **kwargs):
            raise RuntimeError("incremental exploded")

        _mark_indexed(repo, get_head_commit(repo))
        monkeypatch.setattr(incremental_mod, "rebuild_graph_and_git", _boom)
    _add_commit(repo, "b.py")
    sink: list[UpdateProducts] = []

    result = asyncio.run(
        update_single_repo_index(repo, full_index_fallback=False, products_sink=sink)
    )

    assert result.updated is False
    assert result.skipped_reason == "full_index_required"
    assert result.full_index_reason == reason
    assert result.error is None
    assert sink == []


def test_rescore_covers_unchanged_files(tmp_path, forbid_full_pipeline):  # noqa: F811
    repo = _indexed_repo_with_new_commit(tmp_path)
    sink: list[UpdateProducts] = []

    result = asyncio.run(update_single_repo_index(repo, rescore_health=True, products_sink=sink))

    assert result.health_rescored is True
    # a.py did not change; only the whole-repo re-score writes its row.
    assert {"a.py", "b.py"} <= _stored_metric_paths(repo)
    assert {"a.py", "b.py"} <= {m.file_path for m in sink[0].health_report.metrics}


def test_without_rescore_unchanged_files_keep_their_rows(
    tmp_path,
    forbid_full_pipeline,  # noqa: F811
    monkeypatch,
):
    import repowise.core.pipeline.incremental.rescore as rescore_mod

    async def _boom(*args, **kwargs):  # pragma: no cover - failure path
        raise AssertionError("no re-score by default")

    monkeypatch.setattr(rescore_mod, "rescore_health_from_store", _boom)
    repo = _indexed_repo_with_new_commit(tmp_path)

    result = asyncio.run(update_single_repo_index(repo))

    assert result.health_rescored is False
    stored = _stored_metric_paths(repo)
    assert "b.py" in stored and "a.py" not in stored


def test_defaults_keep_the_full_index_fallback(tmp_path, stub_full_pipeline):  # noqa: F811
    repo = _make_git_repo(tmp_path)

    result = asyncio.run(update_single_repo_index(repo))

    assert len(stub_full_pipeline) == 1
    assert result.updated is True
    assert result.skipped_reason is None
    assert result.full_index_reason is None
    assert result.health_rescored is False
