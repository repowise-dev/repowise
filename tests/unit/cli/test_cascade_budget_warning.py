"""The cascade-budget warning splits skipped pages into model and structural."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
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


def test_warning_points_every_stale_page_at_update_full() -> None:
    out = _render(50, 2752, (122, 1235), "repowise update --full", "llm")
    assert "Cascade budget of 50 pages was reached. 2752 dependent files were skipped" in out
    assert "Run repowise update --full to refresh them" in out
    assert "1235 structural pages (file / cycle), no model needed" in out
    assert "122 pages written by a model, priced before they are rewritten" in out
    assert "generate --stale" not in out
    assert "--cascade-budget" not in out


def test_warning_names_the_workspace_repo() -> None:
    out = _render(50, 10, (0, 10), "repowise update --repo api --full", "llm")
    assert "Run repowise update --repo api --full to refresh them" in out
    assert "written by a model" not in out


def test_unreadable_store_falls_back_to_the_unsplit_hint() -> None:
    out = _render(50, 10, None, "repowise update --full", "llm")
    assert "Run repowise update --full to refresh them." in out
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


def test_loader_counts_only_this_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.crud import upsert_page, upsert_repository
    from repowise.core.persistence.database import init_db

    repo_path = (tmp_path / "repo").resolve()
    url = f"sqlite+aiosqlite:///{tmp_path / 'shared.db'}"
    monkeypatch.setenv("REPOWISE_DB_URL", url)

    async def build() -> None:
        engine = create_engine(url)
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            for name in ("repo", "other"):
                repo = await upsert_repository(session, name=name, local_path=str(tmp_path / name))
                await upsert_page(
                    session,
                    page_id=f"repo_overview:{name}",
                    repository_id=repo.id,
                    page_type="repo_overview",
                    title=name,
                    content="body",
                    target_path=name,
                    source_hash="",
                    model_name="mock",
                    provider_name="mock",
                )
            await session.commit()
        await engine.dispose()

    asyncio.run(build())
    assert load_cascade_overflow_split(repo_path, []) == (1, 0)


def test_remedy_with_a_named_provider_and_no_key_is_never_a_code_rebuild(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # `update --full` builds from code only when nothing names a provider; a
    # named one without its key makes the upgrade stop on the provider error.
    import click

    from repowise.cli.commands._page_freshness import stale_remedy
    from repowise.cli.commands.upgrade_flow import _resolve_upgrade_provider

    state = {"docs_mode": "deterministic"}
    monkeypatch.setenv("REPOWISE_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(click.ClickException, match="No API key provided"):
        _resolve_upgrade_provider(None, None, tmp_path, state)
    assert stale_remedy(state, tmp_path) == "model"


def _update_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    docs_mode: str,
    step: str,
    provider: bool = True,
    index_only: bool = False,
) -> str:
    from repowise.cli.commands.update_cmd import command, deterministic
    from repowise.core.providers.llm import resolve

    named = SimpleNamespace(provider="mock" if provider else None)
    monkeypatch.setattr(resolve, "resolve_llm", lambda path: named)
    monkeypatch.setattr(deterministic, "load_cascade_overflow_split", lambda path, paths: (7, 4))
    affected = SimpleNamespace(regenerate=["a.py"], decay_only=["b.py"], stale_due_to_budget=1)
    run = SimpleNamespace(
        affected=affected,
        cascade_budget=50,
        full_generation=False,
        index_only=index_only,
        parsed_files=[],
        renderer_changed=True,
        repo_path=tmp_path,
        state={"docs_mode": docs_mode},
    )
    rec = Console(record=True, width=300)
    with patch.object(reporting, "console", rec), patch.object(command, "console", rec):
        getattr(command, step)(run)
    return rec.export_text()


@pytest.mark.parametrize(
    ("docs_mode", "provider", "index_only", "detail"),
    [
        pytest.param(
            "deterministic",
            False,
            True,
            "4 structural pages (file / cycle), no model needed; 7 pages of the types a model "
            "writes, rebuilt from code with no model calls.",
            id="template-wiki-no-provider",
        ),
        # `update --docs` asks for a model: one remedy, the renderer notice's.
        pytest.param(
            "deterministic",
            False,
            False,
            "4 structural pages (file / cycle); 7 pages of the types a model writes. "
            "It writes the whole wiki with a model, and prices it first.",
            id="template-wiki-no-provider-docs-run",
        ),
        pytest.param(
            "deterministic",
            True,
            True,
            "4 structural pages (file / cycle); 7 pages of the types a model writes. "
            "It writes the whole wiki with a model, and prices it first.",
            id="template-wiki-provider",
        ),
        pytest.param(
            "llm",
            True,
            False,
            "4 structural pages (file / cycle), no model needed; 7 pages written by a model, "
            "priced before they are rewritten.",
            id="llm-wiki",
        ),
    ],
)
def test_cascade_warning_remedy_matches_the_wiki(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    docs_mode: str,
    provider: bool,
    index_only: bool,
    detail: str,
) -> None:
    out = _update_output(
        monkeypatch, tmp_path, docs_mode, "_report_page_plan", provider, index_only
    )
    assert f"Run repowise update --full to refresh them: {detail}\n" in out


@pytest.mark.parametrize(
    ("docs_mode", "notice"),
    [
        # Reached only by `update --docs`, which asks for a model.
        pytest.param(
            "deterministic",
            "7 pages of the types a model writes are marked stale: run repowise update --full "
            "to write the whole wiki with a model (it estimates the cost first).",
            id="template-wiki",
        ),
        pytest.param(
            "llm",
            "7 pages written by a model are marked stale: run repowise update --full "
            "to rewrite them (it estimates the cost first).",
            id="llm-wiki",
        ),
    ],
)
def test_renderer_notice_remedy_matches_the_wiki(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, docs_mode: str, notice: str
) -> None:
    out = _update_output(monkeypatch, tmp_path, docs_mode, "_widen_for_full_render")
    assert notice in out
