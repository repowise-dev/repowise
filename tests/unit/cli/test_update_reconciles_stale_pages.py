"""``repowise update`` reconciles stale file pages when HEAD has not moved (#1744).

The early exit gates on git alone, and the "no changed files" exit right after
it does too. A page marked stale by a budget-capped cascade, an interrupted
run or an expiry therefore had no command that could clear it. These drive
the real command over a one-file repository and read the page back.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from repowise.core.ingestion.git_indexer import GIT_HISTORY_VERSION, GIT_HISTORY_VERSION_KEY

# An index written by this build: its git rows follow the current rules.
CURRENT_GIT_HISTORY = {GIT_HISTORY_VERSION_KEY: GIT_HISTORY_VERSION}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True
    ).stdout.strip()


def _db_url(repo: Path) -> str:
    return f"sqlite+aiosqlite:///{repo / '.repowise' / 'wiki.db'}"


async def _seed(repo: Path, pages: dict[str, str]) -> None:
    """A repository row plus one file page per ``{path: freshness_status}``."""
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_page,
        upsert_repository,
    )

    engine = create_engine(_db_url(repo))
    await init_db(engine)
    sf = create_session_factory(engine)
    async with get_session(sf) as session:
        row = await upsert_repository(session, name=repo.name, local_path=str(repo))
        for path, status in pages.items():
            await upsert_page(
                session,
                page_id=f"file_page:{path}",
                repository_id=row.id,
                page_type="file_page",
                title=f"File: {path}",
                content=f"# {path}\n",
                summary="",
                target_path=path,
                source_hash="",
                model_name="template",
                provider_name="template",
                freshness_status=status,
            )
        await session.commit()
    await engine.dispose()


async def _freshness(repo: Path) -> dict[str, str]:
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.models import Page

    engine = create_engine(_db_url(repo))
    try:
        async with get_session(create_session_factory(engine)) as session:
            rows = await session.execute(select(Page.target_path, Page.freshness_status))
            return dict(rows.all())
    finally:
        await engine.dispose()


def _repo_at_head(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    (repo / "foo.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "c0")
    head = _git(repo, "rev-parse", "HEAD")
    (repo / ".repowise").mkdir()
    return repo, head


def _update(repo: Path) -> str:
    from click.testing import CliRunner

    from repowise.cli.main import cli

    result = CliRunner().invoke(cli, ["update", str(repo), "--no-workspace"])
    assert result.exit_code == 0, result.output
    return result.output


def _state_at(repo: Path, head: str) -> None:
    from repowise.cli.commands.update_cmd.command import _current_renderer_fingerprint
    from repowise.cli.helpers import save_state

    save_state(
        repo,
        {
            "last_sync_commit": head,
            "last_docs_commit": head,
            "docs_mode": "deterministic",
            # Written by this release, as init stamps it.
            "renderer_fingerprint": _current_renderer_fingerprint(repo),
            **CURRENT_GIT_HISTORY,
        },
        full_index=True,
    )


def test_stale_page_for_a_present_file_is_re_rendered(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("REPOWISE_SKIP_EDITOR_SETUP", "1")
    repo, head = _repo_at_head(tmp_path)
    asyncio.run(_seed(repo, {"foo.py": "stale"}))
    _state_at(repo, head)

    output = _update(repo)

    assert "Already up to date" not in output
    assert "Reconciling stale structural pages: 1" in output
    assert asyncio.run(_freshness(repo))["foo.py"] == "fresh"
    state = json.loads((repo / ".repowise" / "state.json").read_text(encoding="utf-8"))
    assert state["last_sync_commit"] == head


def test_stale_page_for_a_missing_file_is_tombstoned(tmp_path: Path, monkeypatch) -> None:
    """Re-rendering cannot clear it; the deletion path can."""
    monkeypatch.setenv("REPOWISE_SKIP_EDITOR_SETUP", "1")
    repo, head = _repo_at_head(tmp_path)
    asyncio.run(_seed(repo, {"foo.py": "fresh", "gone.py": "stale"}))
    _state_at(repo, head)

    output = _update(repo)

    assert "Stale pages for deleted files: 1" in output
    freshness = asyncio.run(_freshness(repo))
    assert freshness["gone.py"] == "tombstone"
    assert freshness["foo.py"] == "fresh"


def test_no_stale_pages_keeps_the_fast_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("REPOWISE_SKIP_EDITOR_SETUP", "1")
    repo, head = _repo_at_head(tmp_path)
    asyncio.run(_seed(repo, {"foo.py": "fresh"}))
    _state_at(repo, head)

    output = _update(repo)

    assert "Already up to date" in output
    assert "Reconciling" not in output


# --- A mode that cannot regenerate a page must not let it force a rebuild ---


class _PipelineEntered(Exception):
    pass


def _llm_state_at(repo: Path, head: str) -> None:
    from repowise.cli.commands.update_cmd.command import _current_renderer_fingerprint
    from repowise.cli.helpers import save_state

    save_state(
        repo,
        {
            "last_sync_commit": head,
            "last_docs_commit": head,
            "docs_mode": "llm",
            "renderer_fingerprint": _current_renderer_fingerprint(repo),
            **CURRENT_GIT_HISTORY,
        },
    )


def _guard_pipeline(monkeypatch, *, provider_ready: bool) -> None:
    """Make entering the rebuild observable, and pin whether a provider resolves."""
    from repowise.cli.commands.update_cmd import command

    def _entered(run):
        raise _PipelineEntered

    monkeypatch.setattr(command, "_rebuild_index", _entered)
    monkeypatch.setattr(
        "repowise.core.providers.llm.registry.provider_available_for_repo",
        lambda repo_path: provider_ready,
    )


def _invoke(repo: Path, *flags: str):
    from click.testing import CliRunner

    from repowise.cli.main import cli

    return CliRunner().invoke(cli, ["update", str(repo), "--no-workspace", *flags])


def _llm_store_with_stale_page(tmp_path: Path, monkeypatch, pages=None) -> tuple[Path, str]:
    monkeypatch.setenv("REPOWISE_SKIP_EDITOR_SETUP", "1")
    repo, head = _repo_at_head(tmp_path)
    asyncio.run(_seed(repo, pages or {"foo.py": "stale"}))
    _llm_state_at(repo, head)
    return repo, head


@pytest.mark.parametrize("flag", ["--index-only", "--no-docs"])
def test_index_only_noop_on_llm_store_with_stale_pages_is_up_to_date(
    tmp_path: Path, monkeypatch, flag: str
) -> None:
    repo, _ = _llm_store_with_stale_page(tmp_path, monkeypatch)
    _guard_pipeline(monkeypatch, provider_ready=True)

    result = _invoke(repo, flag)

    assert result.exit_code == 0, result.output
    assert "Already up to date" in result.output
    assert "1 stale page(s) wait for a model run" in result.output
    # Left for a model run, not silently marked fresh.
    assert asyncio.run(_freshness(repo))["foo.py"] == "stale"


def test_llm_store_without_provider_is_up_to_date(tmp_path: Path, monkeypatch) -> None:
    repo, _ = _llm_store_with_stale_page(tmp_path, monkeypatch)
    _guard_pipeline(monkeypatch, provider_ready=False)

    result = _invoke(repo)

    assert result.exit_code == 0, result.output
    assert "Already up to date" in result.output
    assert asyncio.run(_freshness(repo))["foo.py"] == "stale"


def test_llm_store_with_provider_still_heals_stale_pages(tmp_path: Path, monkeypatch) -> None:
    repo, _ = _llm_store_with_stale_page(tmp_path, monkeypatch)
    _guard_pipeline(monkeypatch, provider_ready=True)

    result = _invoke(repo)

    assert isinstance(result.exception, _PipelineEntered), result.output
    assert "Already up to date" not in result.output


def test_explicit_provider_still_heals_stale_pages(tmp_path: Path, monkeypatch) -> None:
    repo, _ = _llm_store_with_stale_page(tmp_path, monkeypatch)
    _guard_pipeline(monkeypatch, provider_ready=False)

    result = _invoke(repo, "--provider", "mock")

    assert isinstance(result.exception, _PipelineEntered), result.output


def test_index_only_on_llm_store_still_indexes_a_code_change(tmp_path: Path, monkeypatch) -> None:
    repo, _ = _llm_store_with_stale_page(tmp_path, monkeypatch)
    _guard_pipeline(monkeypatch, provider_ready=False)
    (repo / "foo.py").write_text("def foo():\n    return 2\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "c1")

    result = _invoke(repo, "--index-only")

    assert isinstance(result.exception, _PipelineEntered), result.output
    assert "Reconciling stale structural pages" not in result.output


def test_index_only_on_llm_store_still_tombstones_a_deleted_files_page(
    tmp_path: Path, monkeypatch
) -> None:
    """A stale page whose file is gone is index work, which every mode does."""
    repo, _ = _llm_store_with_stale_page(
        tmp_path, monkeypatch, pages={"foo.py": "stale", "gone.py": "stale"}
    )

    result = _invoke(repo, "--index-only")

    assert result.exit_code == 0, result.output
    output = result.output

    assert "Already up to date" not in output
    assert "Stale pages for deleted files: 1" in output
    freshness = asyncio.run(_freshness(repo))
    assert freshness["gone.py"] == "tombstone"
    assert freshness["foo.py"] == "stale"


def test_explicit_docs_flag_still_tries_to_heal(tmp_path: Path, monkeypatch) -> None:
    """--docs asked for a model, so the run proceeds to resolve (or prompt for) one."""
    repo, _ = _llm_store_with_stale_page(tmp_path, monkeypatch)
    _guard_pipeline(monkeypatch, provider_ready=False)

    result = _invoke(repo, "--docs")

    assert isinstance(result.exception, _PipelineEntered), result.output


def test_template_store_index_only_still_heals_stale_pages(tmp_path: Path, monkeypatch) -> None:
    """Index-only renders a template wiki, so its stale pages still wake the run."""
    monkeypatch.setenv("REPOWISE_SKIP_EDITOR_SETUP", "1")
    repo, head = _repo_at_head(tmp_path)
    asyncio.run(_seed(repo, {"foo.py": "stale"}))
    _state_at(repo, head)
    _guard_pipeline(monkeypatch, provider_ready=False)

    result = _invoke(repo, "--index-only")

    assert isinstance(result.exception, _PipelineEntered), result.output
