"""``repowise next``, and the "Next" block ``repowise status`` prints.

Rendering is checked over a canned view; one test builds a real index so the
command's read path (store, repository lookup, loader) runs end to end.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from repowise.cli.commands import next_cmd, status_cmd
from repowise.cli.commands.next_cmd import next_command
from repowise.core.analysis.actions.summary import summarize


def _action(n: int, tier: str, title: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": f"act_{n}",
        "rule": "fragile_file",
        "tier": tier,
        "horizons": ["week", "quarter"],
        "severity": "high",
        "title": title,
        "impact": f"Impact {n}.",
        "why": [{"label": "bug-fix commits", "value": str(n), "basis": "measured"}],
        "target": {"kind": "file", "path": f"src/f{n}.py", "symbol": None},
        "surface": "file",
        "effort": "M",
        "confidence": "high",
        "done_when": f"Done {n}.",
        "command": None,
        **extra,
    }


def _horizon(actions: list[dict[str, Any]], total: int | None = None) -> dict[str, Any]:
    by_tier: dict[str, int] = {}
    for a in actions:
        by_tier[a["tier"]] = by_tier.get(a["tier"], 0) + 1
    return {
        "actions": actions,
        "total": len(actions) if total is None else total,
        "hidden": 0,
        "by_tier": by_tier,
    }


def _view(week: list[dict], quarter: list[dict], **extra: Any) -> dict[str, Any]:
    horizons = {"week": _horizon(week), "quarter": _horizon(quarter)}
    return {
        "status": "available",
        "anchor": "2026-09-28T12:00:00",
        "week_start": "2026-09-21T12:00:00",
        "context": {},
        "horizons": horizons,
        "summary": summarize(horizons, datetime.datetime(2026, 9, 28, 12)),
        "rules": [],
        "unavailable": {},
        **extra,
    }


@pytest.fixture
def canned(monkeypatch: pytest.MonkeyPatch):
    def install(view: dict[str, Any] | None) -> None:
        monkeypatch.setattr(next_cmd, "load_view", lambda root: view)

    return install


def _run(*args: str) -> Any:
    return CliRunner().invoke(next_command, [*args, "--no-workspace"], catch_exceptions=False)


def test_prints_the_sentence_the_view_carries(canned, tmp_path: Path) -> None:
    view = _view([_action(1, "plan", "Do it")], [])
    view["summary"] = {"week": "Core wording, week.", "quarter": "Core wording, quarter."}
    canned(view)

    assert _run(str(tmp_path)).output.startswith("Core wording, week.")
    assert _run(str(tmp_path), "--horizon", "quarter").output.startswith("Core wording, quarter.")
    assert status_cmd._next_actions_lines(view)[0].endswith("Core wording, week.")


def test_a_view_without_a_summary_is_worded_by_core(canned, tmp_path: Path) -> None:
    view = _view([_action(1, "act_now", "Fix `a`")], [])
    del view["summary"]
    canned(view)

    out = _run(str(tmp_path)).output
    assert out.startswith("1 thing worth doing in the week to Sep 28, the last indexed commit, 1 of them now.")
    assert status_cmd._next_actions_lines(view)[0].endswith("1 of them now.")


def test_groups_by_tier_under_the_status_sentence(canned, tmp_path: Path) -> None:
    week = [
        _action(1, "act_now", "Rotate the key in `src/settings.py`"),
        _action(2, "plan", "Add tests around `src/core.py`", command="repowise coverage add x"),
        _action(3, "improve_signal", "Add a test coverage report"),
    ]
    canned(_view(week, week))

    out = _run(str(tmp_path)).output

    assert out.startswith("2 things worth doing in the week to Sep 28, the last indexed commit")
    assert "1 of them now" in out
    assert out.index("Now") < out.index("Worth planning") < out.index("Improve what Repowise")
    # Backticks are markup, not text.
    assert "Rotate the key in src/settings.py" in out
    assert "`" not in out
    assert "bug-fix commits: 2" in out
    assert "Done when: Done 2." in out
    assert "$ repowise coverage add x" in out


def test_falls_back_to_the_quarter_when_the_week_holds_no_work(canned, tmp_path: Path) -> None:
    canned(
        _view(
            [_action(1, "improve_signal", "Add a test coverage report")],
            [_action(2, "plan", "Delete 9 unused symbols")],
        )
    )

    out = _run(str(tmp_path)).output
    assert out.startswith("1 thing worth doing this quarter.")
    assert "Delete 9 unused symbols" in out

    # Asking for the week by name keeps it, and the sentence points at the quarter.
    out = _run(str(tmp_path), "--horizon", "week").output
    assert out.startswith("Nothing needs you in the week to Sep 28")
    assert "1 thing is worth planning this quarter." in out
    assert "Delete 9 unused symbols" not in out


def test_all_widens_the_preview(canned, tmp_path: Path) -> None:
    rows = [_action(i, "plan", f"Task number {i}") for i in range(8)]
    canned(_view(rows, rows))

    out = _run(str(tmp_path)).output
    assert "Task number 4" in out and "Task number 5" not in out
    assert "Showing 5 of 8. --all shows up to 20." in out

    out = _run(str(tmp_path), "--all").output
    assert "Task number 7" in out
    assert "Showing" not in out


def test_json_dumps_the_raw_view(canned, tmp_path: Path) -> None:
    view = _view([_action(1, "act_now", "Fix `a`")], [], unavailable={"performance": "x"})
    canned(view)

    for flag in (("--json",), ("--format", "json")):
        result = _run(str(tmp_path), *flag)
        assert json.loads(result.stdout) == view


def test_names_stores_the_index_predates(canned, tmp_path: Path) -> None:
    canned(_view([_action(1, "plan", "Do it")], [], unavailable={"security": "x", "performance": "y"}))

    out = _run(str(tmp_path)).output
    assert "Not in this index yet: performance, security." in out


def test_no_index_is_a_message_not_a_crash(tmp_path: Path) -> None:
    result = _run(str(tmp_path))
    assert result.exit_code == 0
    assert "No readable index here" in result.output

    result = _run(str(tmp_path), "--format", "json")
    assert json.loads(result.stdout) == {"repo": str(tmp_path.resolve()), "status": "unavailable"}


def test_status_carries_a_next_block_and_json_counts() -> None:
    view = _view(
        [_action(1, "improve_signal", "Add a report")],
        [_action(2, "plan", "Split `src/big.py`"), _action(3, "improve_signal", "Add a report")],
    )

    lines = status_cmd._next_actions_lines(view)
    assert lines[0].endswith("1 thing is worth planning this quarter. 1 setup step to finish.")
    assert "src/big.py" in lines[1]
    assert status_cmd._next_actions_lines(None) == []

    summary = status_cmd._next_actions_summary(view)
    assert summary["week"] == {"total": 1, "by_tier": {"improve_signal": 1}}
    assert summary["quarter"]["by_tier"] == {"plan": 1, "improve_signal": 1}
    assert "actions" not in summary["quarter"]
    assert status_cmd._next_actions_summary(None) is None


def test_status_never_fails_on_the_actions_read(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(root: Path) -> None:
        raise RuntimeError("no such table")

    monkeypatch.setattr(next_cmd, "load_view", boom)
    assert status_cmd._load_actions(Path("/nowhere")) is None


async def _build_index(repo_path: Path) -> None:
    import git as gitpython

    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.crud import upsert_repository
    from repowise.core.persistence.database import init_db
    from repowise.core.persistence.models import DeadCodeFinding, GitCommit

    gitpython.Repo.init(repo_path)
    repowise_dir = repo_path / ".repowise"
    repowise_dir.mkdir()
    engine = create_engine(f"sqlite+aiosqlite:///{repowise_dir / 'wiki.db'}")
    await init_db(engine)
    sf = create_session_factory(engine)
    try:
        async with get_session(sf) as session:
            repo = await upsert_repository(
                session, name="repo", local_path=str(repo_path), url="https://example.test/r"
            )
            anchor = datetime.datetime(2026, 9, 28, 12, 0, tzinfo=datetime.UTC)
            session.add(
                GitCommit(repository_id=repo.id, sha="head", author_name="Ada",
                          author_email="ada@x.io", committed_at=anchor, subject="chore: head")
            )  # fmt: skip
            session.add_all(
                DeadCodeFinding(repository_id=repo.id, kind="unused_export",
                                file_path="src/dead.py", symbol_name=f"f{i}", lines=10, confidence=0.9,
                                safe_to_delete=True)
                for i in range(3)
            )  # fmt: skip
            await session.commit()
    finally:
        await engine.dispose()


def test_reads_a_real_index(tmp_path: Path) -> None:
    repo_path = (tmp_path / "repo").resolve()
    repo_path.mkdir()
    asyncio.run(_build_index(repo_path))

    result = _run(str(repo_path), "--format", "json")
    view = json.loads(result.stdout)
    assert view["status"] == "available"
    rules = {a["rule"] for a in view["horizons"]["quarter"]["actions"]}
    assert "dead_code_batch" in rules

    out = _run(str(repo_path), "--horizon", "quarter").output
    assert "Worth planning" in out
    assert "unused" in out


@pytest.mark.parametrize("outdated_schema", [False, True])
def test_reads_a_repo_local_index_after_the_folder_is_moved(
    tmp_path: Path, outdated_schema: bool
) -> None:
    original = tmp_path / "original"
    original.mkdir()
    asyncio.run(_build_index(original))
    if outdated_schema:
        # Reconciliation must finish before the relocated repository is read.
        with closing(sqlite3.connect(original / ".repowise" / "wiki.db")) as conn:
            conn.execute("ALTER TABLE health_file_metrics DROP COLUMN analyzed_commit")
    moved = tmp_path / "moved"
    original.rename(moved)

    result = _run(str(moved), "--format", "json")
    assert result.exit_code == 0, result.output
    view = json.loads(result.stdout)
    assert view["status"] == "available"
    assert "dead_code_batch" in {
        a["rule"] for a in view["horizons"]["quarter"]["actions"]
    }


@pytest.mark.parametrize("env_name", ["REPOWISE_DB_URL", "REPOWISE_DATABASE_URL"])
def test_a_configured_store_does_not_fall_back_to_another_repo(
    tmp_path: Path, monkeypatch, env_name: str
) -> None:
    indexed = tmp_path / "indexed"
    indexed.mkdir()
    asyncio.run(_build_index(indexed))
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv(env_name, f"sqlite:///{indexed}/.repowise/wiki.db")

    result = _run(str(other), "--format", "json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "unavailable"


def test_a_local_store_with_multiple_repositories_requires_an_exact_path(tmp_path: Path):
    from repowise.cli.helpers import repo_index_session
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.crud import upsert_repository

    indexed = tmp_path / "indexed"
    indexed.mkdir()
    asyncio.run(_build_index(indexed))

    async def check():
        engine = create_engine(f"sqlite+aiosqlite:///{indexed}/.repowise/wiki.db")
        try:
            async with get_session(create_session_factory(engine)) as session:
                await upsert_repository(session, name="other", local_path=str(tmp_path / "other"))
                await session.commit()
        finally:
            await engine.dispose()
        # An exact match still wins in a multi-repository local store.
        async with repo_index_session(indexed) as opened:
            assert opened is not None
        moved = tmp_path / "moved"
        indexed.rename(moved)
        async with repo_index_session(moved) as opened:
            assert opened is None

    asyncio.run(check())


# -- setup steps -------------------------------------------------------------------


def test_prompt_setup_prints_the_finish_setup_prompt(tmp_path: Path) -> None:
    from repowise.core.agent_prompts import render_finish_setup

    result = _run(str(tmp_path), "--prompt", "setup")
    assert result.exit_code == 0
    assert result.output == render_finish_setup() + "\n"


def test_the_list_points_at_the_one_prompt_while_steps_are_shown(canned, tmp_path: Path) -> None:
    step = _action(1, "improve_signal", "Keep the index in sync on commit")
    canned(_view([step], [step], setup={"open": 1, "offer": None, "dismissed": False}))
    assert "repowise next --prompt setup" in _run(str(tmp_path)).output

    canned(_view([step], [step], setup={"open": 1, "offer": None, "dismissed": True}))
    assert "--prompt setup" not in _run(str(tmp_path)).output

    work = _action(2, "act_now", "Rotate the key")
    canned(_view([work], [work]))
    assert "--prompt setup" not in _run(str(tmp_path)).output


def test_a_real_index_lists_live_setup_steps_and_dismiss_stops_the_offer(tmp_path: Path) -> None:
    repo_path = (tmp_path / "repo").resolve()
    repo_path.mkdir()
    asyncio.run(_build_index(repo_path))

    view = json.loads(_run(str(repo_path), "--format", "json").stdout)
    week = view["horizons"]["week"]["actions"]
    sync = next(a for a in week if a["rule"] == "sync_hook_missing")
    assert sync["tier"] == "improve_signal" and sync["command"] == "repowise hook install"
    setup = view["setup"]
    assert setup["open"] == view["horizons"]["week"]["by_tier"]["improve_signal"]
    # No agent has called this index yet, so the pass is not offered.
    assert setup["offer"] is None and setup["dismissed"] is False
    stored = json.loads((repo_path / ".repowise" / "setup.json").read_text(encoding="utf-8"))
    assert stored["open"] == setup["open"] and stored["offer"] is None

    result = _run(str(repo_path), "--dismiss", "setup")
    assert result.exit_code == 0 and "stop offering" in result.output
    view = json.loads(_run(str(repo_path), "--format", "json").stdout)
    assert view["setup"]["dismissed"] is True
    # The steps stay; only the offer goes.
    assert any(a["rule"] == "sync_hook_missing" for a in view["horizons"]["week"]["actions"])
    stored = json.loads((repo_path / ".repowise" / "setup.json").read_text(encoding="utf-8"))
    assert stored["dismissed"] is True


def test_dismiss_without_an_index_says_so(tmp_path: Path) -> None:
    result = _run(str(tmp_path), "--dismiss", "setup")
    assert result.exit_code == 0 and "No readable index" in result.output


def test_status_offers_the_agent_pass_once(tmp_path: Path) -> None:
    (tmp_path / ".repowise").mkdir()
    offered = _view([], [], setup={"open": 4, "offer": "finish_setup", "dismissed": False})

    line = status_cmd._setup_tail(tmp_path, offered)
    assert line is not None
    assert "4 setup steps left" in line and "repowise next --prompt setup" in line
    assert status_cmd._setup_tail(tmp_path, offered) is None

    fresh = tmp_path / "fresh"
    (fresh / ".repowise").mkdir(parents=True)
    quiet = _view([], [], setup={"open": 4, "offer": None, "dismissed": True})
    assert status_cmd._setup_tail(fresh, quiet) is None
    assert status_cmd._setup_tail(fresh, None) is None
