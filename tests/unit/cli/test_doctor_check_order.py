"""Safety net for ``_run_repo_checks`` before it is split.

Pins the row list (names, order, statuses) for a healthy store and a few
broken ones, the table/advisory output order, and every ``--repair`` branch:
FTS and vector repair, the "nothing to repair" paths, agent refresh and the
Claude Code re-registration. Rows that depend on the machine (providers,
distill, editor registration, agents, hosted account) are stubbed with
marker rows so only their position is pinned.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from repowise.cli.commands.doctor_cmd import _render, repo_checks
from repowise.cli.commands.doctor_cmd._checks import core, integrations, providers
from repowise.cli.commands.doctor_cmd._types import _check

from ._real_call_guard import guard_real_calls


@pytest.fixture(autouse=True)
def _no_real_calls(monkeypatch) -> None:
    # git stays real: the fixtures build actual repositories.
    guard_real_calls(monkeypatch, allow_git=True)

KEPT = "file_page:kept.py"
ALSO = "file_page:also.py"
GHOST = "file_page:ghost.py"


async def _build(
    tmp_path: Path,
    *,
    pages: tuple[tuple[str, str, str], ...] = ((KEPT, "kept.py", "File: kept.py"),),
    fts: tuple[str, ...] = (KEPT,),
    vectors: tuple[str, ...] = (KEPT,),
) -> Path:
    """A git repo with SQL pages and chosen FTS / vector contents."""
    import git as gitpython

    from repowise.core.persistence import (
        FullTextSearch,
        create_engine,
        create_session_factory,
        get_session,
    )
    from repowise.core.persistence.crud import upsert_page, upsert_repository
    from repowise.core.persistence.database import init_db
    from repowise.core.persistence.vector_store import LanceDBVectorStore
    from repowise.core.providers.embedding.base import MockEmbedder

    repo_path = (tmp_path / "repo").resolve()
    repo_path.mkdir()
    gitpython.Repo.init(repo_path)
    rw = repo_path / ".repowise"
    rw.mkdir()
    (rw / "state.json").write_text(json.dumps({"last_sync_commit": "abcdef0123456789"}))

    engine = create_engine(f"sqlite+aiosqlite:///{rw / 'wiki.db'}")
    await init_db(engine)
    sf = create_session_factory(engine)
    async with get_session(sf) as session:
        repo = await upsert_repository(
            session, name="repo", local_path=str(repo_path), url="https://example.test/repo"
        )
        for page_id, path, title in pages:
            await upsert_page(
                session,
                page_id=page_id,
                repository_id=repo.id,
                page_type="file_page",
                title=title,
                content="Body.",
                summary="",
                target_path=path,
                source_hash="",
                model_name="mock",
                provider_name="mock",
            )
        await session.commit()

    index = FullTextSearch(engine)
    await index.ensure_index()
    for page_id in fts:
        await index.index(page_id, "File", "Body.", summary="", target_path="x.py")
    await engine.dispose()

    store = LanceDBVectorStore(str(rw / "lancedb"), embedder=MockEmbedder())
    for page_id in vectors:
        await store.embed_and_upsert(page_id, "Body.", {"title": "File"})
    await store.close()
    return repo_path


@pytest.fixture
def env(monkeypatch) -> dict:
    """Stub machine-dependent rows and record console output in order."""
    st: dict = {"printed": [], "wedged": False, "refresh": False}
    monkeypatch.setattr(
        core, "_autosync_hook_check", lambda _p: _check("<hook>", True, "", check_id="hook")
    )
    monkeypatch.setattr(core, "_attribution_notes_check", lambda _p: None)
    monkeypatch.setattr(core, "_events_config_check", lambda _p: None)
    monkeypatch.setattr(
        providers,
        "_provider_checks",
        lambda _p: [_check("<provider>", True, "", check_id="provider")],
    )
    monkeypatch.setattr(
        integrations,
        "_distill_checks",
        lambda _p: [_check("<distill>", True, "", check_id="distill")],
    )
    monkeypatch.setattr(
        integrations,
        "_claude_registration_check",
        lambda _p: (
            _check("<registration>", not st["wedged"], "", check_id="registration"),
            st["wedged"],
        ),
    )
    monkeypatch.setattr(
        integrations, "_mcp_responds_check", lambda: _check("<mcp>", True, "", check_id="mcp")
    )
    monkeypatch.setattr(
        integrations,
        "_agent_target_checks",
        lambda _p: ([_check("<agent>", not st["refresh"], "", check_id="agent")], st["refresh"]),
    )
    monkeypatch.setattr(_render, "_advise_claude_md_stamp", lambda *_a: st["printed"].append("<stamp>"))
    monkeypatch.setattr("repowise.cli.platform.credentials.load", lambda: None)
    monkeypatch.setattr(
        repo_checks.console, "print", lambda *a, **_k: st["printed"].append(" ".join(map(str, a)))
    )
    return st


def _rows(repo_path: Path, fmt: str = "json") -> list[tuple[str, bool, str]]:
    _ok, checks = repo_checks._run_repo_checks(repo_path, repair=False, fmt=fmt)
    return [(c.name, c.ok, c.detail) for c in checks]


HEALTHY_ORDER = [
    "Git repository",
    ".repowise/ directory",
    "<hook>",
    "Database",
    "state.json",
    "Store format",
    "<provider>",
    "Hosted account",
    "Stale pages",
    "SQL ↔ Vector Store",
    "SQL ↔ FTS Index",
    "Coordinator drift",
    "<distill>",
    "<registration>",
    "<mcp>",
    "<agent>",
]


def _by_name(rows: list[tuple[str, bool, str]]) -> dict[str, tuple[bool, str]]:
    return {n: (ok, d) for n, ok, d in rows}


def test_healthy_store_rows_and_order(env, tmp_path) -> None:
    repo = asyncio.run(_build(tmp_path))
    rows = _rows(repo)
    assert [r[0] for r in rows] == HEALTHY_ORDER
    assert all(ok for _n, ok, _d in rows)
    got = _by_name(rows)
    assert got["Git repository"][1] == str(repo)
    assert got["Database"] == (True, "1 pages")
    assert got["state.json"] == (True, "last_sync: abcdef01")
    assert got["Stale pages"] == (True, "0 stale")
    assert got["SQL ↔ Vector Store"] == (True, "in sync")
    assert got["SQL ↔ FTS Index"] == (True, "in sync")
    assert got["Coordinator drift"] == (True, "SQL=1, Vector=1, Drift=0.0%")
    assert got["Hosted account"][1].startswith("Not signed in")
    # JSON format prints nothing; the table and advisories are table-only.
    assert env["printed"] == []


def test_quick_skips_the_store_reconciliation_and_the_mcp_launch(
    env, monkeypatch, tmp_path
) -> None:
    from repowise.cli.commands.doctor_cmd._checks import stores

    def fail(*_a, **_k):
        raise AssertionError("--quick must not open the stores")

    monkeypatch.setattr(stores, "_reconcile", fail)
    monkeypatch.setattr(stores, "_coordinator_health", fail)
    repo = asyncio.run(_build(tmp_path))
    _ok, checks = repo_checks._run_repo_checks(repo, repair=False, fmt="json", quick=True)
    slow = {"SQL ↔ Vector Store", "SQL ↔ FTS Index", "Coordinator drift", "<mcp>"}
    assert [c.name for c in checks] == [n for n in HEALTHY_ORDER if n not in slow]


def test_quick_and_repair_is_a_usage_error(tmp_path) -> None:
    from click.testing import CliRunner

    from repowise.cli.commands.doctor_cmd import doctor_command

    result = CliRunner().invoke(doctor_command, [str(tmp_path), "--quick", "--repair"])
    assert result.exit_code == 2
    assert "--quick" in result.output


def test_optional_rows_slot_in_after_the_hook(env, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        core, "_attribution_notes_check", lambda _p: _check("<notes>", True, "", check_id="notes")
    )
    monkeypatch.setattr(
        core, "_events_config_check", lambda _p: _check("<events>", False, "", check_id="events")
    )
    repo = asyncio.run(_build(tmp_path))
    names = [r[0] for r in _rows(repo)]
    assert names[2:5] == ["<hook>", "<notes>", "<events>"]


def test_unindexed_repo_rows(env, monkeypatch, tmp_path) -> None:
    import git as gitpython

    monkeypatch.setattr(core, "db_configured", lambda: False)
    repo = tmp_path / "bare"
    repo.mkdir()
    gitpython.Repo.init(repo)
    rows = _rows(repo)
    assert [r[0] for r in rows] == [
        "Git repository",
        ".repowise/ directory",
        "<hook>",
        "Database",
        "state.json",
        "<provider>",
        "Hosted account",
        "<distill>",
        "<registration>",
        "<mcp>",
        "<agent>",
    ]
    got = _by_name(rows)
    assert got[".repowise/ directory"][0] is False
    assert got["Database"] == (False, "wiki.db not found")
    assert got["state.json"] == (False, "Not found or empty")


def test_missing_state_drops_store_format_row(env, tmp_path) -> None:
    repo = asyncio.run(_build(tmp_path))
    (repo / ".repowise" / "state.json").unlink()
    rows = _rows(repo)
    assert "Store format" not in [r[0] for r in rows]
    assert _by_name(rows)["state.json"] == (False, "Not found or empty")


def test_drift_rows_and_coordinator(env, tmp_path) -> None:
    repo = asyncio.run(
        _build(
            tmp_path,
            pages=((KEPT, "kept.py", "File: kept.py"), (ALSO, "also.py", "File: also.py")),
            fts=(KEPT, GHOST),
            vectors=(KEPT, GHOST),
        )
    )
    rows = _rows(repo)
    assert [r[0] for r in rows] == HEALTHY_ORDER
    got = _by_name(rows)
    assert got["Database"] == (True, "2 pages")
    assert got["SQL ↔ Vector Store"] == (False, "1 missing, 1 orphaned")
    assert got["SQL ↔ FTS Index"] == (False, "1 missing, 1 orphaned")
    # Same count on both sides: the coordinator reads 0% drift.
    assert got["Coordinator drift"] == (True, "SQL=2, Vector=2, Drift=0.0%")


def test_stale_pages_row(env, monkeypatch, tmp_path) -> None:
    async def _stale(_s, _r):
        return {"file_page": 2}

    monkeypatch.setattr(providers, "_stale_page_counts", _stale)
    repo = asyncio.run(_build(tmp_path))
    ok, detail = _by_name(_rows(repo))["Stale pages"]
    assert ok is False
    assert detail.startswith("2 stale")


def test_expired_and_cached_hosted_identity(env, monkeypatch, tmp_path) -> None:
    repo = asyncio.run(_build(tmp_path))
    monkeypatch.setattr("repowise.cli.platform.credentials.load", lambda: {"stale": True})
    assert _by_name(_rows(repo))["Hosted account"] == (
        False,
        "Session expired or revoked; run repowise login",
    )
    monkeypatch.setattr(
        "repowise.cli.platform.credentials.load",
        lambda: {"account": {"email": "a@b.test", "tier": "pro"}},
    )
    monkeypatch.setattr("repowise.cli.platform.auth.fetch_account", lambda: None)
    assert _by_name(_rows(repo))["Hosted account"] == (
        True,
        "a@b.test (pro) (offline, using cached identity)",
    )


# ---------------------------------------------------------------------------
# Table output and --repair
# ---------------------------------------------------------------------------


def _repair(repo: Path) -> bool:
    ok, _checks = repo_checks._run_repo_checks(repo, repair=True)
    return ok


def _texts(env: dict) -> list[str]:
    """Printed strings, with the rich Table collapsed to a marker."""
    return ["<table>" if p.startswith("<rich.table") else p for p in env["printed"]]


@pytest.fixture
def mock_embedder(monkeypatch) -> None:
    from repowise.core.providers.embedding.base import MockEmbedder

    monkeypatch.setattr("repowise.cli.providers.resolve_embedder_for_repo", lambda _p: "mock")
    monkeypatch.setattr(
        "repowise.cli.providers.build_embedder", lambda _n, _p=None: MockEmbedder()
    )


def test_table_healthy_prints_table_stamp_then_pass(env, tmp_path) -> None:
    repo = asyncio.run(_build(tmp_path))
    ok, _ = repo_checks._run_repo_checks(repo, repair=False)
    assert ok is True
    # The fixture's store predates a capability, so the store-format row warns;
    # a warn still passes and is counted on the summary line.
    texts = _texts(env)
    assert texts[:2] == ["<table>", "<stamp>"]
    assert texts[2].startswith("[bold green]All checks passed[/bold green]")
    assert "1 warning(s)" in texts[2]
    assert len(texts) == 3


def test_repair_with_nothing_to_do(env, tmp_path) -> None:
    repo = asyncio.run(_build(tmp_path))
    _repair(repo)
    assert _texts(env)[-1] == "[green]Nothing to repair.[/green]"


def test_repair_without_drift_names_stale_pages(env, monkeypatch, tmp_path) -> None:
    async def _stale(_s, _r):
        return {"file_page": 1}

    monkeypatch.setattr(providers, "_stale_page_counts", _stale)
    repo = asyncio.run(_build(tmp_path))
    _repair(repo)
    assert _texts(env)[-1].startswith("[yellow]No store drift to repair. 1 stale")


def test_repair_fixes_fts_and_vector_drift(env, mock_embedder, tmp_path) -> None:
    repo = asyncio.run(
        _build(
            tmp_path,
            pages=((KEPT, "kept.py", "File: kept.py"), (ALSO, "also.py", "File: also.py")),
            fts=(KEPT, GHOST),
            vectors=(KEPT, GHOST),
        )
    )
    assert _repair(repo) is False
    assert _texts(env) == [
        "<table>",
        "<stamp>",
        "[bold yellow]Some checks failed.[/bold yellow]",
        "\n[bold]Repairing store mismatches...[/bold]",
        # FTS: 1 orphan + 1 missing; vector: 1 missing + 1 orphan.
        "[bold green]Repaired 4 entries.[/bold green]",
    ]
    got = _by_name(_rows(repo))
    assert got["SQL ↔ Vector Store"] == (True, "in sync")
    assert got["SQL ↔ FTS Index"] == (True, "in sync")


def test_repair_reports_stale_after_store_repair(env, monkeypatch, tmp_path) -> None:
    async def _stale(_s, _r):
        return {"file_page": 1}

    monkeypatch.setattr(providers, "_stale_page_counts", _stale)
    repo = asyncio.run(_build(tmp_path, fts=(KEPT, GHOST)))
    _repair(repo)
    out = _texts(env)
    assert out[-2] == "[bold green]Repaired 1 entries.[/bold green]"
    assert out[-1].startswith("[yellow]1 stale")


def test_fts_upgrade_failure_does_not_stop_repair(env, monkeypatch, tmp_path) -> None:
    from repowise.core.persistence import FullTextSearch

    repo = asyncio.run(_build(tmp_path, fts=(KEPT, GHOST)))

    async def _refuse(self):
        raise RuntimeError("drifted columns")

    monkeypatch.setattr(FullTextSearch, "ensure_index", _refuse)
    _repair(repo)
    out = _texts(env)
    assert "  [yellow]Full-text index upgrade skipped: drifted columns[/yellow]" in out
    assert out[-1] == "[bold green]Repaired 1 entries.[/bold green]"


def test_vector_repair_skips_untitled_page(env, mock_embedder, tmp_path) -> None:
    repo = asyncio.run(
        _build(
            tmp_path,
            pages=((KEPT, "kept.py", "File: kept.py"), (ALSO, "also.py", "")),
            fts=(KEPT, ALSO),
        )
    )
    _repair(repo)
    out = _texts(env)
    assert f"  [yellow]Skipped {ALSO}: no title to index it by.[/yellow]" in out
    assert out[-1] == "[bold green]Repaired 0 entries.[/bold green]"


def test_vector_repair_on_paid_embedder_announces_cost(env, monkeypatch, tmp_path) -> None:
    from repowise.core.persistence.vector_store import LanceDBVectorStore
    from repowise.core.providers.embedding.base import MockEmbedder

    repo = asyncio.run(
        _build(
            tmp_path,
            pages=((KEPT, "kept.py", "File: kept.py"), (ALSO, "also.py", "File: also.py")),
            fts=(KEPT, ALSO),
        )
    )
    embedded: list[str] = []

    async def _record(self, page_id, *_a, **_k):
        embedded.append(page_id)

    monkeypatch.setattr(LanceDBVectorStore, "embed_and_upsert", _record)
    monkeypatch.setattr("repowise.cli.providers.resolve_embedder_for_repo", lambda _p: "openai")
    monkeypatch.setattr(
        "repowise.cli.providers.build_embedder", lambda _n, _p=None: MockEmbedder()
    )
    _repair(repo)
    assert embedded == [ALSO]
    assert "  [dim]Embedding 1 missing page(s) with openai.[/dim]" in _texts(env)


def test_vector_repair_without_usable_embedder(env, mock_embedder, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("repowise.cli.providers.build_vector_store", lambda _p, _e: None)
    repo = asyncio.run(_build(tmp_path, vectors=(KEPT, GHOST)))
    _repair(repo)
    out = _texts(env)
    assert any(o.startswith("[yellow]Vector repair skipped: no embedder available") for o in out)
    assert out[-1] == "[bold green]Repaired 0 entries.[/bold green]"


def test_repair_refreshes_agents_then_reregisters(env, monkeypatch, tmp_path) -> None:
    env["refresh"] = True
    env["wedged"] = True
    payload = {
        "changed": False,
        "agents": [
            {"id": "claude", "writes": {"repo": {"files": [{"action": "kept", "path": "a.json"}]}}}
        ],
    }
    monkeypatch.setattr(
        "repowise.cli.commands.agents_cmd.refresh_wired_agents", lambda _p: payload
    )
    monkeypatch.setattr(
        "repowise.cli.editor_integrations.claude_config.register_with_claude_code",
        lambda _p: "~/.claude.json",
    )
    repo = asyncio.run(_build(tmp_path))
    assert _repair(repo) is False
    out = _texts(env)
    start = out.index("\n[bold]Refreshing agent integrations...[/bold]")
    assert out[start + 1] == "  claude [repo]: kept a.json"
    assert out[start + 2].startswith("[yellow]Nothing moved.")
    assert out[start + 3:] == [
        "[bold green]Re-registered the Claude Code MCP entry (~/.claude.json).[/bold green]"
    ]
    # The "Nothing to repair" lines are skipped when agents or registration need work.
    assert not any("Nothing to repair" in o for o in out)


def test_failed_reregistration_points_at_init(env, monkeypatch, tmp_path) -> None:
    env["wedged"] = True
    monkeypatch.setattr(
        "repowise.cli.editor_integrations.claude_config.register_with_claude_code",
        lambda _p: None,
    )
    repo = asyncio.run(_build(tmp_path))
    _repair(repo)
    assert _texts(env)[-1].startswith("[yellow]Could not re-register the Claude Code MCP entry")


def test_coordinator_drift_is_colored_in_table(env, monkeypatch, tmp_path) -> None:
    from rich.table import Table

    rows: list[tuple] = []
    real_add_row = Table.add_row

    def _add_row(self, *cells, **kw):
        rows.append(cells)
        return real_add_row(self, *cells, **kw)

    monkeypatch.setattr(Table, "add_row", _add_row)
    repo = asyncio.run(
        _build(
            tmp_path,
            pages=((KEPT, "kept.py", "File: kept.py"), (ALSO, "also.py", "File: also.py")),
            fts=(KEPT, ALSO),
        )
    )
    repo_checks._run_repo_checks(repo, repair=False)
    coord = next(r for r in rows if r[0] == "Coordinator drift")
    assert coord[2] == "SQL=2, Vector=1, Drift=[red]50.0%[/red]"
