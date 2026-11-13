"""A bounded template refresh leaves a recoverable wiki between runs."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.commands.update_cmd import command, deterministic
from repowise.cli.helpers import run_async
from repowise.core.generation import ContextAssembler, GenerationConfig, PageGenerator
from repowise.core.generation.page_generator import PriorPage
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import RepoStructure
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.traverser import FileTraverser
from repowise.core.providers.llm.template import TemplateProvider


def _repository(tmp_path: Path):
    for name in ("a", "b", "c"):
        (tmp_path / f"{name}.py").write_text(
            f'"""Public {name} operations."""\n'
            f'def {name}(value):\n    """Transform the input. Return its new value."""\n'
            '    result = value + 1\n    return result\n'
        )
    builder, parser = GraphBuilder(), ASTParser()
    parsed, source = [], {}
    for info in FileTraverser(tmp_path).traverse():
        data = Path(info.abs_path).read_bytes()
        item = parser.parse_file(info, data)
        builder.add_file(item)
        parsed.append(item)
        source[info.path] = data
    builder.build()
    structure = RepoStructure(
        is_monorepo=False, packages=[], root_language_distribution={"python": 1.0},
        total_files=3, total_loc=15, entry_points=[],
    )
    return parsed, source, builder, structure


def _render(tmp_path: Path, prior: dict, *, budget: int, changed=(), stats=None, cap=None):
    parsed, source, builder, structure = _repository(tmp_path)
    config = GenerationConfig(deterministic=True, max_concurrency=1, enable_onboarding=False,
                              max_file_pages=cap)
    generator = PageGenerator(
        TemplateProvider(), ContextAssembler(config), config, prior_pages=prior,
    )
    return run_async(generator.generate_all(
        parsed, source, builder, structure, tmp_path.name,
        render_budget=budget, changed_paths=set(changed), render_stats=stats,
    ))


def _remember(prior: dict, pages: list) -> None:
    for page in pages:
        prior[page.page_id] = PriorPage(
            source_hash=page.source_hash, model_name="", content=page.content,
            metadata=dict(page.metadata), provider_name="template", freshness_status="fresh",
        )


def test_cli_accepts_positive_page_budget(monkeypatch):
    captured = {}
    monkeypatch.setattr(command, "run_update", lambda **kw: captured.update(kw))
    result = CliRunner().invoke(command.update_command, ["--render-budget", "2"])
    assert result.exit_code == 0, result.output
    assert captured["render_budget"] == 2
    assert CliRunner().invoke(command.update_command, ["--render-budget", "0"]).exit_code == 2


def test_budget_prioritizes_changed_sources_and_resumes_without_repeating(tmp_path):
    prior, stats = {}, {}
    first = _render(tmp_path, prior, budget=1, changed={"c.py"}, stats=stats)
    assert len(first) == 1
    assert first[0].target_path.split("::", 1)[0] == "c.py"
    assert stats["render_backlog"] > 0
    _remember(prior, first)
    seen = {first[0].page_id}
    for _ in range(12):
        batch = _render(tmp_path, prior, budget=1, stats=stats)
        assert len(batch) <= 1
        assert not seen.intersection(page.page_id for page in batch)
        seen.update(page.page_id for page in batch)
        _remember(prior, batch)
        if stats["render_backlog"] == 0:
            break
    assert stats["render_backlog"] == 0
    assert _render(tmp_path, prior, budget=1) == []


def test_saved_file_cap_still_applies(tmp_path):
    pages = _render(tmp_path, {}, budget=100, cap=1)
    assert sum(page.page_type == "file_page" for page in pages) == 1


def test_template_upgrade_does_not_reuse_old_revision(tmp_path, monkeypatch):
    prior = {}
    _remember(prior, _render(tmp_path, prior, budget=100))
    assert _render(tmp_path, prior, budget=1) == []
    from repowise.core.generation.page_generator import structural
    monkeypatch.setattr(structural, "STRUCTURAL_GENERATION_VERSION", "changed")
    stats = {}
    assert len(_render(tmp_path, prior, budget=1, stats=stats)) == 1
    assert stats["render_backlog"] > 0


def test_budget_preserves_model_written_page(tmp_path):
    prose = PriorPage(source_hash="paid", model_name="model", content="Reviewed prose",
                      provider_name="model", freshness_status="fresh")
    pages = _render(tmp_path, {"file_page:a.py": prose}, budget=100)
    assert "file_page:a.py" not in {page.page_id for page in pages}


def test_empty_modules_do_not_keep_the_backlog_alive(tmp_path, monkeypatch):
    from repowise.core.generation.page_generator import levels

    original = levels._is_empty_module
    monkeypatch.setattr(levels, "_is_empty_module", lambda *args: True)
    prior, stats = {}, {}
    for _ in range(12):
        batch = _render(tmp_path, prior, budget=1, stats=stats)
        _remember(prior, batch)
        if stats["render_backlog"] == 0:
            break
    assert stats["render_backlog"] == 0
    assert not any(pid.startswith("module_page:") for pid in stats["render_page_ids"])
    monkeypatch.setattr(levels, "_is_empty_module", original)


def test_failed_unbudgeted_continuation_retains_pending_state(tmp_path, monkeypatch):
    from repowise.cli.helpers import load_state, release_update_lock, save_state

    from ._real_call_guard import guard_real_calls
    from .test_update_reconciles_stale_pages import _repo_at_head, _seed, _state_at

    repo, head = _repo_at_head(tmp_path)
    run_async(_seed(repo, {"foo.py": "stale"}))
    _state_at(repo, head)
    state = load_state(repo)
    stamp = state["renderer_fingerprint"]
    state["pending_render_pages"] = 2
    save_state(repo, state)
    guard_real_calls(monkeypatch, allow_git=True)

    def fail_render(*args, **kwargs):
        kwargs["degraded"].append("Template rendering failed")
        return []

    monkeypatch.setattr(deterministic, "regenerate_deterministic_pages", fail_render)
    try:
        result = CliRunner().invoke(command.update_command, [
            str(repo), "--no-workspace", "--index-only",
        ])
    finally:
        release_update_lock(repo)
    assert result.exit_code == 0, result.output
    state = load_state(repo)
    assert state["pending_render_pages"] == 2
    assert state["renderer_fingerprint"] == stamp


def test_revision_planning_reads_metadata_without_page_bodies(tmp_path):
    from sqlalchemy import event

    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_repository_by_path,
        get_session,
    )
    from repowise.core.persistence.crud import load_prior_pages

    from .test_update_reconciles_stale_pages import _seed

    (tmp_path / ".repowise").mkdir()
    run_async(_seed(tmp_path, {"a.py": "fresh"}))

    async def read_revisions():
        engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / '.repowise' / 'wiki.db'}")
        queries = []
        event.listen(engine.sync_engine, "before_cursor_execute",
                     lambda conn, cursor, sql, params, context, many: queries.append(sql))
        try:
            async with get_session(create_session_factory(engine)) as session:
                repo = await get_repository_by_path(session, str(tmp_path))
                prior = await load_prior_pages(session, repo.id, include_content=False)
            return prior, queries
        finally:
            await engine.dispose()

    prior, queries = run_async(read_revisions())
    assert prior["file_page:a.py"].content == ""
    assert prior["file_page:a.py"].provider_name == "template"
    page_reads = [sql for sql in queries if "FROM wiki_pages" in sql]
    assert page_reads
    assert all("wiki_pages.content," not in sql and "wiki_pages.digest" not in sql
               for sql in page_reads)


def test_checkpoint_failure_keeps_old_rows_stale(tmp_path, monkeypatch):
    import sqlite3

    from repowise.core import persistence

    from .test_update_reconciles_stale_pages import _seed

    (tmp_path / ".repowise").mkdir()
    run_async(_seed(tmp_path, {"a.py": "fresh"}))

    async def fail_write(*args, **kwargs):
        raise OSError("checkpoint disk unavailable")

    monkeypatch.setattr(persistence, "upsert_page_from_generated", fail_write)
    parsed, source, builder, structure = _repository(tmp_path)
    degraded, stats = [], {}
    pages = deterministic.regenerate_deterministic_pages(
        repo_path=tmp_path, parsed_files=parsed, source_map=source,
        graph_builder=builder, repo_structure=structure, git_meta_map={},
        regenerate_paths=list(source), cfg={"embedder": "mock"}, concurrency=1,
        degraded=degraded, full_scope=True, render_budget=1, stats_out=stats,
        prior_page_ids=deterministic.load_prior_page_ids(tmp_path, load_revisions=True),
        only_page_ids={"file_page:a.py"},
    )
    assert pages == []
    assert any("checkpoint disk unavailable" in warning for warning in degraded)
    with sqlite3.connect(tmp_path / ".repowise" / "wiki.db") as db:
        row = db.execute("SELECT metadata_json, freshness_status FROM wiki_pages").fetchone()
    assert row[1] == "stale"
    assert '"render_revision"' not in row[0]


def test_checkpoint_survives_cancel_before_the_next_page(tmp_path, monkeypatch):
    import asyncio
    import sqlite3

    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.persistence import create_engine, init_db

    (tmp_path / ".repowise").mkdir()

    async def setup():
        engine = create_engine(get_db_url_for_repo(tmp_path))
        await init_db(engine)
        await engine.dispose()

    run_async(setup())
    original = PageGenerator._render_file_page
    calls = 0

    async def interrupted(self, parsed, ctx):
        nonlocal calls
        calls += 1
        if calls == 2:
            # Cancel only after the first transaction landed. Work still in
            # flight when cancellation arrives is allowed to be lost.
            for _ in range(100):
                with sqlite3.connect(tmp_path / ".repowise" / "wiki.db") as db:
                    if db.execute("SELECT count(*) FROM wiki_pages").fetchone()[0]:
                        break
                await asyncio.sleep(0.01)
            raise asyncio.CancelledError
        return await original(self, parsed, ctx)

    monkeypatch.setattr(PageGenerator, "_render_file_page", interrupted)
    parsed, source, builder, structure = _repository(tmp_path)
    with pytest.raises(asyncio.CancelledError):
        deterministic.regenerate_deterministic_pages(
            repo_path=tmp_path, parsed_files=parsed, source_map=source,
            graph_builder=builder, repo_structure=structure, git_meta_map={},
            regenerate_paths=[p.file_info.path for p in parsed], cfg={"embedder": "mock"},
            concurrency=1, degraded=[], full_scope=True, render_budget=3,
            only_page_ids={f"file_page:{name}.py" for name in ("a", "b", "c")},
        )
    with sqlite3.connect(tmp_path / ".repowise" / "wiki.db") as db:
        rows = db.execute("SELECT id, metadata_json FROM wiki_pages").fetchall()
    assert 1 <= len(rows) < 3
    assert all('"render_revision"' in metadata for _, metadata in rows)


@pytest.mark.parametrize("renderer_changed", [False, True])
def test_partial_batches_retain_stamp_and_previously_committed_pages(
    tmp_path, monkeypatch, renderer_changed,
):
    from repowise.cli.helpers import load_state, release_update_lock, save_state

    from ._real_call_guard import guard_real_calls
    from .test_update_progress_json import _split_output_runner
    from .test_update_reconciles_stale_pages import _git, _repo_at_head, _seed, _state_at

    repo, head = _repo_at_head(tmp_path)
    run_async(_seed(repo, {"foo.py": "stale"}))
    _state_at(repo, head)
    state = load_state(repo)
    initial_stamp = state["renderer_fingerprint"]
    if renderer_changed:
        state["renderer_fingerprint"] = "old-renderer"
    save_state(repo, state)
    guard_real_calls(monkeypatch, allow_git=True)
    seen = set()
    for _ in range(12):
        try:
            result = _split_output_runner().invoke(command.update_command, [
                str(repo), "--no-workspace", "--index-only", "--render-budget", "1",
                "--progress", "json",
            ])
        finally:
            release_update_lock(repo)
        assert result.exit_code == 0, result.output
        import sqlite3
        with sqlite3.connect(repo / ".repowise" / "wiki.db") as db:
            current = {pid for pid, freshness in db.execute(
                "SELECT id, freshness_status FROM wiki_pages"
            ) if freshness != "tombstone"}
        assert seen <= current
        seen = current
        state = load_state(repo)
        if not state.get("pending_render_pages"):
            break
        assert state["renderer_fingerprint"] == (
            "old-renderer" if renderer_changed else initial_stamp
        )
    assert state["pending_render_pages"] == 0
    assert state["renderer_fingerprint"] != "old-renderer"
    assert _git(repo, "rev-parse", "HEAD") == head


def test_killed_renderer_resumes_from_committed_rows(tmp_path):
    import os
    import sqlite3
    import subprocess
    import sys
    import time

    from .test_update_reconciles_stale_pages import _seed

    repo = tmp_path / "repo"
    repo.mkdir()
    _repository(repo)
    (repo / ".repowise").mkdir()
    run_async(_seed(repo, {f"{name}.py": "fresh" for name in ("a", "b", "c")}))
    code = '''
import asyncio, sys
from pathlib import Path
from tests.unit.cli.test_render_budget import _repository
from repowise.cli.commands.update_cmd import deterministic
from repowise.core.generation import PageGenerator
repo = Path(sys.argv[1])
original = PageGenerator._render_file_page
async def slow(self, parsed, ctx):
    await asyncio.sleep(0.2)
    return await original(self, parsed, ctx)
PageGenerator._render_file_page = slow
parsed, source, builder, structure = _repository(repo)
deterministic.regenerate_deterministic_pages(
    repo_path=repo, parsed_files=parsed, source_map=source,
    graph_builder=builder, repo_structure=structure, git_meta_map={},
    regenerate_paths=list(source), cfg={"embedder": "mock"}, concurrency=1,
    degraded=[], full_scope=True, render_budget=3, stats_out={},
    only_page_ids={f"file_page:{name}.py" for name in ("a", "b", "c")},
)
'''
    child = subprocess.Popen([sys.executable, "-c", code, str(repo)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, env=os.environ.copy())
    db_path = repo / ".repowise" / "wiki.db"
    committed = set()
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and child.poll() is None:
            with sqlite3.connect(db_path) as db:
                rows = db.execute("SELECT id, metadata_json FROM wiki_pages").fetchall()
            committed = {pid for pid, metadata in rows if '"render_revision"' in metadata}
            if committed:
                child.kill()
                break
            time.sleep(0.02)
        assert committed, "child never committed a rendered page"
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)
    with sqlite3.connect(db_path) as db:
        rows = db.execute("SELECT id, metadata_json, freshness_status FROM wiki_pages").fetchall()
    committed = {pid for pid, metadata, _ in rows if '"render_revision"' in metadata}
    assert 0 < len(committed) < 3
    assert any(freshness == "stale" for _, _, freshness in rows)
    parsed, source, builder, structure = _repository(repo)
    resumed = deterministic.regenerate_deterministic_pages(
        repo_path=repo, parsed_files=parsed, source_map=source,
        graph_builder=builder, repo_structure=structure, git_meta_map={},
        regenerate_paths=list(source), cfg={"embedder": "mock"}, concurrency=1,
        degraded=[], full_scope=True, render_budget=3, stats_out={},
        prior_page_ids=deterministic.load_prior_page_ids(repo, load_revisions=True),
        only_page_ids={f"file_page:{name}.py" for name in ("a", "b", "c")},
    )
    assert {page.page_id for page in resumed} == set(f"file_page:{name}.py" for name in ("a", "b", "c")) - committed
