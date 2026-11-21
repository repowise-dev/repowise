"""An update follows the flow selection in both directions (#5677).

A repository with more flow candidates than the budget: when the ranking
shifts, one update renders the newly selected entry's page and retires the
template page of the entry that dropped out. The real pipeline, renderer and
sweep run against a real ``wiki.db``; only git is absent.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from pathlib import Path
from types import SimpleNamespace

import pytest
from structlog.testing import capture_logs

from repowise.cli.commands.update_cmd.deterministic import regenerate_deterministic_page_ids
from repowise.cli.commands.update_cmd.page_generation import (
    _load_prior_pages,
    render_stale_structure,
)
from repowise.cli.commands.update_cmd.stale_targets import structure_ids_to_render
from repowise.core.generation.flow_pages import plans_for_index
from repowise.core.generation.selection.selector import structure_ids_for_incremental_run

# Helper files per package: five undeclared roots, three undeclared slots.
_SIZES = {"alpha": 9, "bravo": 8, "charlie": 7, "delta": 6, "echo": 5}


def _flow_id(pkg: str) -> str:
    return f"flow_page:{pkg}/entry.py::run_{pkg}"


def _write_package(root: Path, pkg: str, helpers: int) -> None:
    """One root ``run_<pkg>`` whose reach grows with *helpers*."""
    folder = root / pkg
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "__init__.py").write_text("")
    for i in range(helpers):
        (folder / f"part{i}.py").write_text(
            f"def h_{pkg}_{i}():\n    return {i}\n\n\n"
            f"def g_{pkg}_{i}():\n    return h_{pkg}_{i}() + 1\n\n\n"
            f"def f_{pkg}_{i}():\n    return g_{pkg}_{i}() + h_{pkg}_{i}()\n"
        )
    imports = "\n".join(f"from {pkg}.part{i} import f_{pkg}_{i}" for i in range(helpers))
    body = "\n".join(f"    total += f_{pkg}_{i}()" for i in range(helpers))
    (folder / "entry.py").write_text(
        f"{imports}\n\n\ndef run_{pkg}():\n    total = 0\n{body}\n    return total\n"
    )


def _index(repo: Path):
    from repowise.core.pipeline.orchestrator import run_pipeline

    return asyncio.run(run_pipeline(repo, generate_docs=False))


def _render(repo: Path, index, page_ids: set[str], degraded: list[str]) -> list:
    return regenerate_deterministic_page_ids(
        repo_path=repo,
        parsed_files=index.parsed_files,
        source_map=index.source_map,
        graph_builder=index.graph_builder,
        repo_structure=index.repo_structure,
        git_meta_map={},
        page_ids=page_ids,
        cfg={},
        concurrency=1,
        degraded=degraded,
    )


async def _store(
    repo: Path, index, pages: list, stale: Iterable[str] = (), model_written: Iterable[str] = ()
) -> set[str]:
    """Persist *pages*, mark *stale* and *model_written*, run the update's
    retired-page sweep; return the flow ids."""
    from sqlalchemy import select, update

    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_pages_from_generated,
        upsert_repository,
    )
    from repowise.core.persistence.models import Page
    from repowise.core.pipeline.incremental.persist_index import IncrementalPersist, sweep_retired
    from repowise.core.pipeline.persist import mark_page_ids_stale

    (repo / ".repowise").mkdir(exist_ok=True)
    engine = create_engine(f"sqlite+aiosqlite:///{repo / '.repowise' / 'wiki.db'}")
    await init_db(engine)
    try:
        async with get_session(create_session_factory(engine)) as session:
            row = await upsert_repository(session, name="r", local_path=str(repo))
            await upsert_pages_from_generated(session, pages, row.id)
            await mark_page_ids_stale(session, row.id, sorted(stale))
            await session.execute(
                update(Page).where(Page.id.in_(list(model_written))).values(provider_name="mock")
            )
            await sweep_retired(
                IncrementalPersist(
                    repo_path=repo,
                    graph_builder=index.graph_builder,
                    git_meta_map={},
                    parsed_files=index.parsed_files,
                    session=session,
                    repo_id=row.id,
                )
            )
            found = await session.execute(select(Page.id).where(Page.page_type == "flow_page"))
            return set(found.scalars())
    finally:
        await engine.dispose()


async def _edit_page(repo: Path, page_id: str, **values: str) -> None:
    from sqlalchemy import update

    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.models import Page

    engine = create_engine(f"sqlite+aiosqlite:///{repo / '.repowise' / 'wiki.db'}")
    try:
        async with get_session(create_session_factory(engine)) as session:
            await session.execute(update(Page).where(Page.id == page_id).values(**values))
    finally:
        await engine.dispose()


def _run(repo: Path, index, stale: Iterable[str] = ()) -> SimpleNamespace:
    """What a rendering update holds once its graph is rebuilt."""
    return SimpleNamespace(
        repo_path=repo,
        affected=None,
        full_generation=False,
        stale_deterministic_ids=set(stale),
        parsed_files=index.parsed_files,
        source_map=index.source_map,
        graph_builder=index.graph_builder,
        repo_structure=index.repo_structure,
        render_git_meta={},
        cfg={},
        timings=None,
        degraded=[],
        dead_code_report=None,
    )


def _model_update_renders(run: SimpleNamespace) -> list[str]:
    """The structure pages a model-wiki update writes with the template renderer."""
    docs = SimpleNamespace(
        prior_pages=_load_prior_pages(run),
        generated_pages=[],
        decision_vector_store=None,
        full_stats={},
    )
    render_stale_structure(SimpleNamespace(concurrency=1), run, docs)
    return [page.page_id for page in docs.generated_pages]


def _flow_ids(index) -> set[str]:
    return {plan.page_id for plan in plans_for_index(index.graph_builder, index.parsed_files)}


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The first index: ``alpha``, ``bravo`` and ``charlie`` have a template flow page."""
    for var in ("REPOWISE_DB_URL", "REPOWISE_DATABASE_URL", "REPOWISE_EMBEDDER"):
        monkeypatch.delenv(var, raising=False)
    root = (tmp_path / "repo").resolve()
    for pkg, helpers in _SIZES.items():
        _write_package(root, pkg, helpers)
    index = _index(root)
    selected = {_flow_id(pkg) for pkg in ("alpha", "bravo", "charlie")}
    assert _flow_ids(index) == selected
    degraded: list[str] = []
    pages = _render(root, index, selected, degraded)
    assert degraded == []
    assert asyncio.run(_store(root, index, pages)) == selected
    return root


def _shift_ranking(repo: Path):
    """``echo`` outgrows the selected flows, so ``charlie`` no longer has a slot."""
    _write_package(repo, "echo", 12)
    index = _index(repo)
    assert _flow_ids(index) == {_flow_id(pkg) for pkg in ("alpha", "bravo", "echo")}
    return index


def test_one_update_renders_the_new_entry_and_retires_the_dropped_one(repo: Path) -> None:
    index = _shift_ranking(repo)
    stored = {_flow_id(pkg) for pkg in ("alpha", "bravo", "charlie")}
    # The dropped page went stale with its step file, so the update asks for it.
    wanted = structure_ids_to_render(_run(repo, index, stale={_flow_id("charlie")}), stored)
    # The server sync renders these ids with a model client: no flow id there.
    shared = structure_ids_for_incremental_run(index.parsed_files, index.graph_builder, stored, [])
    assert _flow_id("echo") in wanted
    assert _flow_id("echo") not in shared
    degraded: list[str] = []
    pages = _render(repo, index, wanted, degraded)

    assert degraded == []
    assert [page.page_id for page in pages] == [_flow_id("echo")]
    left = asyncio.run(_store(repo, index, pages, stale={_flow_id("charlie")}))
    assert left == {_flow_id(pkg) for pkg in ("alpha", "bravo", "echo")}


def test_an_update_that_renders_nothing_still_retires_the_dropped_page(repo: Path) -> None:
    index = _shift_ranking(repo)
    left = asyncio.run(_store(repo, index, []))
    assert left == {_flow_id("alpha"), _flow_id("bravo")}


def test_a_model_written_page_survives_its_entry_being_dropped(repo: Path) -> None:
    index = _shift_ranking(repo)
    left = asyncio.run(_store(repo, index, [], model_written={_flow_id("charlie")}))
    assert left == {_flow_id(pkg) for pkg in ("alpha", "bravo", "charlie")}


def test_a_model_wiki_update_leaves_the_flow_pages_it_already_has(repo: Path) -> None:
    from repowise.core.generation.models import STUB_FALLBACK_ERROR

    # One a model wrote, one whose model call failed: the reuse cache skips the stub.
    asyncio.run(_edit_page(repo, _flow_id("alpha"), provider_name="mock"))
    stub = json.dumps({STUB_FALLBACK_ERROR: "overloaded"})
    asyncio.run(_edit_page(repo, _flow_id("bravo"), metadata_json=stub))
    run = _run(repo, _index(repo))

    assert _model_update_renders(run) == []
    assert run.degraded == []


def test_a_failed_reuse_cache_read_overwrites_no_flow_page(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.core import persistence

    async def _raise(*_args, **_kwargs):
        raise RuntimeError("database is locked")

    asyncio.run(_edit_page(repo, _flow_id("alpha"), provider_name="mock"))
    monkeypatch.setattr(persistence, "load_prior_pages", _raise)
    run = _run(repo, _index(repo))

    assert _model_update_renders(run) == []
    assert run.degraded == ["Prior-page reuse: database is locked"]


def test_an_unreadable_store_adds_no_flow_page(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from repowise.cli.commands.update_cmd import stale_targets

    async def _raise(_repo_path):
        raise RuntimeError("database is locked")

    index = _shift_ranking(repo)
    monkeypatch.setattr(stale_targets, "_visible_flow_page_ids", _raise)
    run = _run(repo, index)

    assert structure_ids_to_render(run, {}) == set()
    assert run.degraded == ["New flow pages: database is locked"]


def test_an_entry_selected_again_gets_a_page_over_its_tombstone(repo: Path) -> None:
    from repowise.core.pipeline.persist import tombstone_pages_outside_generation

    async def _tombstone_charlie() -> None:
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_repository_by_path,
            get_session,
        )

        engine = create_engine(f"sqlite+aiosqlite:///{repo / '.repowise' / 'wiki.db'}")
        try:
            async with get_session(create_session_factory(engine)) as session:
                row = await get_repository_by_path(session, str(repo))
                kept = {_flow_id("alpha"), _flow_id("bravo")}
                assert await tombstone_pages_outside_generation(session, row.id, kept) == [
                    _flow_id("charlie")
                ]
        finally:
            await engine.dispose()

    # A full generation that had dropped ``charlie`` hid its page; it is selected again.
    asyncio.run(_tombstone_charlie())
    index = _index(repo)
    run = _run(repo, index)

    wanted = structure_ids_to_render(run, {})
    assert wanted == {_flow_id("charlie")}
    asyncio.run(_store(repo, index, _render(repo, index, wanted, run.degraded)))
    assert structure_ids_to_render(run, {}) == set()
    assert run.degraded == []


def test_a_sweep_given_no_parsed_files_retires_nothing(repo: Path) -> None:
    index = _shift_ranking(repo)
    index.parsed_files = []
    left = asyncio.run(_store(repo, index, []))
    assert left == {_flow_id(pkg) for pkg in ("alpha", "bravo", "charlie")}


def test_a_selection_that_raises_warns_and_retires_nothing(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.core.generation import flow_pages

    def _raise(*_args, **_kwargs):
        raise RuntimeError("selection broke")

    index = _shift_ranking(repo)
    monkeypatch.setattr(flow_pages, "plans_for_index", _raise)
    with capture_logs() as logs:
        left = asyncio.run(_store(repo, index, []))

    assert left == {_flow_id(pkg) for pkg in ("alpha", "bravo", "charlie")}
    assert ("flow_selection_failed", "selection broke") in [
        (entry["event"], entry.get("error")) for entry in logs
    ]


def test_a_failed_render_keeps_the_page_that_is_still_selected(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.core.generation import PageGenerator

    async def _raise(*_args, **_kwargs):
        raise RuntimeError("render failed")

    index = _shift_ranking(repo)
    monkeypatch.setattr(PageGenerator, "generate_flow_page", _raise)
    pages = _render(repo, index, {_flow_id("alpha"), _flow_id("charlie")}, [])

    assert pages == []
    left = asyncio.run(_store(repo, index, pages, stale={_flow_id("alpha"), _flow_id("charlie")}))
    # Requested and not produced, both: only the one the selection dropped goes.
    assert left == {_flow_id("alpha"), _flow_id("bravo")}


def test_one_run_reuses_its_flow_selection_until_the_files_or_graph_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.core.generation import flow_pages

    for var in ("REPOWISE_DB_URL", "REPOWISE_DATABASE_URL", "REPOWISE_EMBEDDER"):
        monkeypatch.delenv(var, raising=False)
    root = (tmp_path / "repo").resolve()
    _write_package(root, "alpha", 3)
    index = _index(root)
    calls: list[int] = []
    real = flow_pages._select_for_index
    monkeypatch.setattr(
        flow_pages,
        "_select_for_index",
        lambda builder, files: calls.append(len(files)) or real(builder, files),
    )
    files = index.parsed_files

    plans_for_index(index.graph_builder, files[1:])
    full = plans_for_index(index.graph_builder, files)
    assert plans_for_index(index.graph_builder, list(files)) == full  # same files, a new list
    assert calls == [len(files) - 1, len(files)]
    plans_for_index(index.graph_builder, files[1:])  # a changed file set selects again
    index.graph_builder.graph().add_node("alpha/new.py")  # so does a changed graph
    plans_for_index(index.graph_builder, files)
    assert calls == [len(files) - 1, len(files), len(files) - 1, len(files)]
