"""``repowise update --full`` leaves nothing stale, in every repo state."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from repowise.cli.commands.update_cmd import catch_up as catch_up_mod
from repowise.cli.commands.update_cmd import command as update_cmd
from repowise.cli.commands.update_cmd.options import UpdateOptions


def _repo(tmp_path: Path, **state: Any) -> Path:
    repo = tmp_path / "repo"
    (repo / ".repowise").mkdir(parents=True)
    (repo / ".repowise" / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return repo


@pytest.mark.parametrize(
    ("state", "full"),
    [
        ({"docs_mode": "llm", "git_tier": "full"}, True),
        ({"docs_mode": "none", "git_tier": "essential", "run_mode": "fast"}, False),
        ({"docs_mode": "deterministic", "git_tier": "full"}, False),
        ({"docs_mode": "llm", "git_tier": "essential"}, False),
    ],
    ids=["model-written", "index-only-fast", "built-from-code", "model-essential-git"],
)
def test_only_a_model_written_full_index_catches_up(tmp_path, state, full) -> None:
    assert catch_up_mod.is_full_index(_repo(tmp_path, **state)) is full


def _opts(**overrides: Any) -> UpdateOptions:
    base: dict[str, Any] = dict(
        path=None,
        provider_name="mock",
        model=None,
        since=None,
        reasoning=None,
        cascade_budget=None,
        dry_run=False,
        workspace=False,
        no_workspace=True,
        repo_alias=None,
        full=True,
        yes=True,
    )
    base.update(overrides)
    return UpdateOptions(**base)


@pytest.mark.parametrize("full_index", [True, False])
def test_full_routes_by_repo_state(monkeypatch, tmp_path, full_index) -> None:
    calls: list[str] = []
    monkeypatch.setattr(catch_up_mod, "is_full_index", lambda _p: full_index)
    monkeypatch.setattr(catch_up_mod, "catch_up", lambda *_a: calls.append("catch_up"))
    monkeypatch.setattr(
        "repowise.cli.commands.upgrade_flow.upgrade_to_full",
        lambda *_a, **_k: calls.append("upgrade"),
    )
    monkeypatch.setattr(update_cmd, "_emit_done", lambda run, outcome, **_k: outcome)
    run = SimpleNamespace(repo_path=tmp_path, emitter=None)
    update_cmd._full_upgrade(_opts(), run)
    assert calls == (["catch_up"] if full_index else ["upgrade"])


class _Recorder:
    """Stands in for the update, the stale counts and the generate run."""

    def __init__(self, monkeypatch, stale: dict[bool, list[int]], outcome: Any = None) -> None:
        self.events: list[tuple[str, dict]] = []
        self.stale = stale
        self.outcome = outcome or update_cmd.UpdateOutcome.REGENERATED
        monkeypatch.setattr(update_cmd, "run_update", self.update)
        monkeypatch.setattr(catch_up_mod, "_stale", self.count)
        monkeypatch.setattr(catch_up_mod, "_report_remaining", lambda _p: None)
        monkeypatch.setattr(
            "repowise.cli.commands.generate_cmd.command.run_generate", self.generate
        )

    def update(self, **kwargs: Any) -> Any:
        self.events.append(("update", kwargs))
        return self.outcome

    def count(self, _repo: Path, *, model_written: bool) -> int:
        return self.stale[model_written].pop(0)

    def generate(self, path: str, **kwargs: Any) -> None:
        self.events.append(("generate", kwargs))


def test_catch_up_runs_uncapped_then_rewrites_then_renders(monkeypatch, tmp_path) -> None:
    rec = _Recorder(monkeypatch, {True: [3], False: [5]})
    catch_up_mod.catch_up(_opts(), tmp_path)

    assert [name for name, _ in rec.events] == ["update", "generate", "update"]
    first = rec.events[0][1]
    assert first["cascade_budget"] == catch_up_mod.UNCAPPED_CASCADE
    assert first["no_workspace"] is True and first["repo_alias"] is None
    gen = rec.events[1][1]
    assert gen["stale"] is True and gen["cascade"] == "full"
    assert gen["yes"] is True and gen["dry_run"] is False


def test_nothing_stale_after_the_update_runs_nothing_else(monkeypatch, tmp_path) -> None:
    rec = _Recorder(monkeypatch, {True: [0], False: [0]})
    catch_up_mod.catch_up(_opts(), tmp_path)
    assert [name for name, _ in rec.events] == ["update"]


def test_dry_run_prices_the_rewrite_and_writes_nothing(monkeypatch, tmp_path) -> None:
    rec = _Recorder(monkeypatch, {True: [2], False: [7]}, update_cmd.UpdateOutcome.DRY_RUN)
    outcome = catch_up_mod.catch_up(_opts(dry_run=True), tmp_path)
    assert outcome is update_cmd.UpdateOutcome.DRY_RUN
    assert [name for name, _ in rec.events] == ["update", "generate"]
    assert rec.events[0][1]["dry_run"] is True
    assert rec.events[1][1]["dry_run"] is True


def test_a_held_lock_defers_the_whole_catch_up(monkeypatch, tmp_path) -> None:
    rec = _Recorder(monkeypatch, {True: [9], False: [9]}, update_cmd.UpdateOutcome.DEFERRED)
    assert catch_up_mod.catch_up(_opts(), tmp_path) is update_cmd.UpdateOutcome.DEFERRED
    assert [name for name, _ in rec.events] == ["update"]


def test_unproduced_stale_pages_are_retired(tmp_path) -> None:
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.crud import upsert_page, upsert_repository
    from repowise.core.persistence.database import init_db
    from repowise.core.pipeline.persist import mark_page_ids_stale

    repo_path = (tmp_path / "repo").resolve()
    (repo_path / ".repowise").mkdir(parents=True)
    url = f"sqlite+aiosqlite:///{repo_path / '.repowise' / 'wiki.db'}"

    async def build() -> None:
        engine = create_engine(url)
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            repo = await upsert_repository(session, name="repo", local_path=str(repo_path))
            for page_id in ("module_page:gone", "module_page:kept"):
                await upsert_page(
                    session,
                    page_id=page_id,
                    repository_id=repo.id,
                    page_type="module_page",
                    title=page_id,
                    content="body",
                    target_path=page_id.split(":")[1],
                    source_hash="",
                    model_name="mock",
                    provider_name="mock",
                )
            await mark_page_ids_stale(session, repo.id, {"module_page:gone"})
        await engine.dispose()

    async def statuses() -> dict[str, str]:
        from sqlalchemy import select

        from repowise.core.persistence.models import Page

        engine = create_engine(url)
        try:
            async with get_session(create_session_factory(engine)) as session:
                rows = await session.execute(select(Page.id, Page.freshness_status))
                return dict(rows.all())
        finally:
            await engine.dispose()

    asyncio.run(build())
    written = SimpleNamespace(
        generated_pages=[SimpleNamespace(page_id="module_page:kept")],
        stale_page_ids={"module_page:gone"},
        plan=SimpleNamespace(generate_ids={"module_page:gone", "module_page:kept"}),
    )
    catch_up_mod._retire_unproduced(repo_path, written)

    assert asyncio.run(statuses()) == {"module_page:gone": "tombstone", "module_page:kept": "fresh"}
