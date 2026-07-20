"""MCP coverage for live commit/range change-risk scoring."""

from __future__ import annotations

import importlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _commit(repo: Path, files: dict[str, str], message: str) -> None:
    for relative_path, content in files.items():
        path = repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        _git(["add", relative_path], repo)
    _git(["-c", "user.name=Dev", "-c", "user.email=dev@example.com", "commit", "-m", message], repo)


@pytest.mark.asyncio
async def test_get_change_risk_honors_riskignore_and_request_filters(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"README.md": "# seed\n"}, "chore: seed")
    _commit(
        repo,
        {
            "src/app.py": "value = 1\n",
            "tests/test_app.py": "def test_value():\n    assert True\n",
            "docs/notes.md": "notes\n",
        },
        "feat: app",
    )
    (repo / ".riskignore").write_text("tests/\n", encoding="utf-8")

    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo))

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    result = await module.get_change_risk(
        extensions=["py", "md"], exclude_patterns=["docs/"], baseline=0
    )

    assert result["features"] == {
        "la": 1,
        "ld": 0,
        "nf": 1,
        "nd": 1,
        "ns": 1,
        "entropy": 0.0,
        "exp": 1,
    }
    assert result["exclude_patterns"] == ["tests/", "docs/"]
    assert result["risk_percentile"] is None
    assert result["review_priority"] is None
    assert result["classification"] is None
    assert result["baseline_sample_size"] == 0
    # Live-git responses carry a _meta envelope flagged as index-independent.
    assert result["_meta"]["source"] == "live_git"
    assert "warning" not in result


@pytest.mark.asyncio
async def test_get_change_risk_bad_revspec_returns_error(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"README.md": "# seed\n"}, "chore: seed")

    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo))

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    result = await module.get_change_risk(revspec="does-not-exist", baseline=0)

    # A bogus revspec must surface an error, not a silent zero-risk score.
    assert "error" in result
    assert "does-not-exist" in result["error"]
    assert "score" not in result


@pytest.mark.asyncio
async def test_get_change_risk_empty_diff_warns(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"README.md": "# seed\n"}, "chore: seed")
    _commit(repo, {"app.py": "value = 1\n"}, "feat: app")

    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo))

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    # Only a .py change exists; restricting to .md counts zero files.
    result = await module.get_change_risk(extensions=["md"], baseline=0)

    assert result["features"]["nf"] == 0
    assert "warning" in result
    assert "no counted file changes" in result["warning"].lower()


@pytest.mark.asyncio
async def test_get_change_risk_rejects_repo_all() -> None:
    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")
    result = await module.get_change_risk(repo="all")

    assert "error" in result
    assert "get_change_risk" in result["error"]


# ---------------------------------------------------------------------------
# Line-level impacted tests (Phase 4B fold-in)
# ---------------------------------------------------------------------------


async def _factory_with_repo(coverage_records: list | None):
    """Build an in-memory session factory seeded with one repo + coverage map."""
    from datetime import UTC, datetime

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from repowise.core.persistence.crud import save_test_coverage
    from repowise.core.persistence.database import init_db
    from repowise.core.persistence.models import Repository

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    now = datetime(2026, 7, 18, tzinfo=UTC)
    async with factory() as s:
        s.add(
            Repository(
                id="repo1",
                name="repo",
                url="https://example.com/repo",
                local_path="/tmp/repo",
                default_branch="main",
                settings_json="{}",
                created_at=now,
                updated_at=now,
            )
        )
        await s.flush()
        if coverage_records:
            await save_test_coverage(s, "repo1", coverage_records, source_format="coverage.py")
        await s.commit()
    return factory


def _tc(test_id: str, source_file: str, covered_lines: list[int], test_file: str):
    from repowise.core.analysis.health.coverage import TestCoverage

    return TestCoverage(
        test_id=test_id,
        file_path=source_file,
        covered_lines=covered_lines,
        source_format="coverage.py",
        test_file=test_file,
    )


@pytest.mark.asyncio
async def test_impacted_tests_line_precise_hit_and_miss(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"src/app.py": "a\nb\nc\nd\ne\n"}, "chore: seed app")
    # The change: edit app.py line 3, add other.py and new.py.
    _commit(
        repo,
        {
            "src/app.py": "a\nb\nc2\nd\ne\n",
            "src/other.py": "o1\no2\no3\n",
            "src/new.py": "n1\n",
        },
        "feat: change",
    )

    factory = await _factory_with_repo(
        [
            _tc("tests/test_app.py::test_app", "src/app.py", [3], "tests/test_app.py"),
            _tc("tests/test_other.py::test_other", "src/other.py", [9], "tests/test_other.py"),
        ]
    )

    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo), session_factory=factory)

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    result = await module.get_change_risk(baseline=0)

    it = result["impacted_tests"]
    assert it["status"] == "map_present"
    assert it["map_present"] is True
    # app.py line 3 is covered -> its test is named; other/new are not covering.
    assert it["tests"] == ["tests/test_app.py::test_app"]
    assert it["total"] == 1
    assert it["truncated"] is False

    mt = it["missing_tests"]
    # other.py is in the map but its changed lines (1,2,3) are uncovered.
    assert mt["untested_changes"] == [
        {"source_file": "src/other.py", "uncovered_lines": [1, 2, 3], "changed_line_count": 3}
    ]
    # new.py has no coverage rows -> unknown, never "untested".
    assert mt["no_coverage_data"] == ["src/new.py"]
    # app.py is covered but its test file is absent from the diff -> stale candidate.
    assert [s["source_file"] for s in mt["stale_test_candidates"]] == ["src/app.py"]


@pytest.mark.asyncio
async def test_impacted_tests_no_map_is_unknown_not_untested(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"src/app.py": "a\nb\n"}, "chore: seed")
    _commit(repo, {"src/app.py": "a\nb\nc\n"}, "feat: add line")

    factory = await _factory_with_repo(None)  # repo present, no coverage rows

    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo), session_factory=factory)

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    result = await module.get_change_risk(baseline=0)

    it = result["impacted_tests"]
    assert it["status"] == "no_map"
    assert it["map_present"] is False
    assert it["tests"] == []
    # Honest degradation: no untested claim, a "run the suite" summary instead.
    assert it["missing_tests"]["untested_changes"] == []
    assert "run the full suite" in it["summary"]


@pytest.mark.asyncio
async def test_impacted_tests_overflow_cap_is_honest(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"src/hot.py": "seed\n"}, "chore: seed")
    _commit(repo, {"src/hot.py": "changed\n"}, "feat: touch line 1")

    # 12 distinct tests all cover the changed line 1 -> over the cap of 10.
    records = [
        _tc(f"tests/test_{i}.py::test_{i}", "src/hot.py", [1], f"tests/test_{i}.py")
        for i in range(12)
    ]
    factory = await _factory_with_repo(records)

    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo), session_factory=factory)

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    result = await module.get_change_risk(baseline=0)

    it = result["impacted_tests"]
    assert it["total"] == 12
    assert len(it["tests"]) == 10
    assert it["truncated"] is True


@pytest.mark.asyncio
async def test_impacted_tests_no_session_factory_degrades_to_no_index(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"src/app.py": "a\n"}, "chore: seed")
    _commit(repo, {"src/app.py": "a\nb\n"}, "feat: add")

    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo))  # no session_factory

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    result = await module.get_change_risk(baseline=0)

    assert result["impacted_tests"]["status"] == "no_index"
    assert result["impacted_tests"]["map_present"] is False
