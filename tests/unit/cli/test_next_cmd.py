"""``repowise next``, and the "Next" block ``repowise status`` prints.

Rendering is checked over a canned view; one test builds a real index so the
command's read path (store, repository lookup, loader) runs end to end.
"""

from __future__ import annotations

import asyncio
import datetime
import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from repowise.cli.commands import next_cmd, status_cmd
from repowise.cli.commands.next_cmd import next_command


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
    return {
        "status": "available",
        "anchor": "2026-09-28T12:00:00",
        "week_start": "2026-09-21T12:00:00",
        "context": {},
        "horizons": {"week": _horizon(week), "quarter": _horizon(quarter)},
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
    assert lines[0].endswith("1 thing is worth planning this quarter.")
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
                                file_path="src/dead.py", symbol_name=f"f{i}", lines=10,
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
