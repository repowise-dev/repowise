"""get_change_risk and get_risk name the tests ``repowise impacted-tests`` selects.

Each case is a change the agent tools used to answer from their own shallow
walk, and got wrong: tests that run with every subset were missing, a conftest
named two tests instead of every test under it, a manifest read as "no map",
a test-package ``__init__.py`` was listed as a test, runner wiring read as an
import in ``tests_to_update``, and a map measured at the base was matched on
the head's line numbers.
"""

from __future__ import annotations

import importlib
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

_NOW = datetime(2026, 7, 18, tzinfo=UTC)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    for relative_path, content in files.items():
        path = repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        _git(repo, "add", relative_path)
    _git(repo, "-c", "user.name=Dev", "-c", "user.email=dev@example.com", "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path, seed: dict[str, str]) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    return repo, _commit(repo, seed, "chore: seed")


async def _index(
    base: str,
    nodes: dict[str, bool | str],
    edges: list[tuple[str, str, str]] = (),
    coverage: list | None = None,
    measured_at: str | None = None,
):
    """An index built at *base*: file nodes (``True`` test, ``str`` always-run reason), edges."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from repowise.core.persistence.crud import save_test_coverage
    from repowise.core.persistence.database import init_db
    from repowise.core.persistence.models import GraphEdge, GraphNode, Repository

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        s.add(
            Repository(
                id="repo1",
                name="repo",
                url="https://example.com/repo",
                local_path="/tmp/repo",
                default_branch="main",
                settings_json="{}",
                head_commit=base,
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        for path, kind in nodes.items():
            s.add(
                GraphNode(
                    repository_id="repo1",
                    node_id=path,
                    node_type="file",
                    is_test=kind is not False,
                    # Stamped by the indexer: "" for an ordinary test.
                    always_run_reason=kind if isinstance(kind, str) else ("" if kind else None),
                )
            )
        for source, target, edge_type in edges:
            s.add(
                GraphEdge(
                    repository_id="repo1",
                    source_node_id=source,
                    target_node_id=target,
                    edge_type=edge_type,
                )
            )
        await s.flush()
        if coverage:
            await save_test_coverage(
                s, "repo1", coverage, source_format="coverage.py", ingested_commit_sha=measured_at
            )
        await s.commit()
    return factory


def _tc(test_id: str, source_file: str, covered_lines: list[int]):
    from repowise.core.analysis.health.coverage import TestCoverage

    return TestCoverage(
        test_id=test_id,
        file_path=source_file,
        covered_lines=covered_lines,
        source_format="coverage.py",
        test_file=test_id.split("::", 1)[0],
    )


async def _change_risk(monkeypatch, repo: Path, factory) -> dict:
    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo), session_factory=factory)

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    return await module.get_change_risk("HEAD", baseline=0)


async def _risk_directive(monkeypatch, repo: Path, factory, changed: list[str]) -> dict:
    import repowise.server.mcp_server as mcp_mod
    from repowise.server.mcp_server.tool_risk.get_risk import get_risk

    monkeypatch.setattr(mcp_mod, "_session_factory", factory)
    monkeypatch.setattr(mcp_mod, "_repo_path", str(repo))
    return (await get_risk(changed_files=changed))["directive"]


_STATUS = {
    "src/status.py": "def status():\n    return 1\n",
    "tests/test_status.py": "from src.status import status\n",
    "tests/test_walks.py": "import pathlib\n",
}


@pytest.mark.asyncio
async def test_a_test_that_runs_with_every_subset_is_named(tmp_path, monkeypatch) -> None:
    repo, base = _repo(tmp_path, _STATUS)
    _commit(repo, {"src/status.py": "def status():\n    return 2\n"}, "fix: status")
    factory = await _index(
        base,
        {
            "src/status.py": False,
            "tests/test_status.py": True,
            "tests/test_walks.py": "it lists and reads files under a source directory",
        },
        [("tests/test_status.py", "src/status.py", "imports")],
    )

    it = (await _change_risk(monkeypatch, repo, factory))["impacted_tests"]

    assert it["status"] == "selected"
    assert it["run_all"] is False
    assert it["tests_to_run"] == ["tests/test_status.py", "tests/test_walks.py"]
    assert it["why"]["tests/test_walks.py"].startswith("runs with every subset: it lists")
    assert it["always_run_total"] == 1
    assert it["basis_by_file"] == {"src/status.py": "import-graph"}


@pytest.mark.asyncio
async def test_a_conftest_change_runs_every_test_under_it(tmp_path, monkeypatch) -> None:
    files = {
        "tests/conftest.py": "import pytest\n",
        "tests/test_a.py": "def test_a():\n    pass\n",
        "tests/sub/test_b.py": "def test_b():\n    pass\n",
        "src/a.py": "A = 1\n",
    }
    repo, base = _repo(tmp_path, files)
    _commit(repo, {"tests/conftest.py": "import pytest\n\nX = 1\n"}, "test: fixture")
    factory = await _index(
        base,
        {
            "tests/conftest.py": True,
            "tests/test_a.py": True,
            "tests/sub/test_b.py": True,
            "src/a.py": False,
        },
        [("tests/test_a.py", "src/a.py", "imports"), ("tests/sub/test_b.py", "src/a.py", "imports")],
    )

    it = (await _change_risk(monkeypatch, repo, factory))["impacted_tests"]

    assert it["run_all"] is False
    assert sorted(it["tests_to_run"]) == ["tests/sub/test_b.py", "tests/test_a.py"]
    assert it["basis_by_file"] == {"tests/conftest.py": "conftest"}


@pytest.mark.asyncio
async def test_a_manifest_change_runs_everything_on_both_tools(tmp_path, monkeypatch) -> None:
    repo, base = _repo(tmp_path, {**_STATUS, "package.json": "{}\n"})
    _commit(repo, {"package.json": '{"name": "x"}\n'}, "chore: manifest")
    nodes = {"src/status.py": False, "tests/test_status.py": True, "tests/test_walks.py": True}
    edges = [
        ("tests/test_status.py", "src/status.py", "imports"),
        ("tests/test_walks.py", "src/status.py", "imports"),
    ]
    factory = await _index(base, nodes, edges)

    result = await _change_risk(monkeypatch, repo, factory)
    it = result["impacted_tests"]
    assert it["status"] == "run_all"
    assert it["reasons"][0].startswith("package.json changed:")
    assert it["tests_to_run"] == []
    assert result["directive"]["next_actions"][-1].startswith("Run every test: package.json")

    directive = await _risk_directive(monkeypatch, repo, factory, ["package.json"])
    assert directive["tests_run_all"] is True
    assert directive["tests_run_all_reasons"][0].startswith("package.json changed:")


@pytest.mark.asyncio
async def test_get_risk_never_lists_a_test_package_init_as_a_test(tmp_path, monkeypatch) -> None:
    files = {
        "src/a.py": "A = 1\n",
        "tests/__init__.py": "from src import a\n",
        "tests/test_a.py": "def test_a():\n    pass\n",
    }
    repo, base = _repo(tmp_path, files)
    factory = await _index(
        base,
        {"src/a.py": False, "tests/__init__.py": True, "tests/test_a.py": True},
        [("tests/__init__.py", "src/a.py", "imports"), ("tests/test_a.py", "src/a.py", "imports")],
    )

    directive = await _risk_directive(monkeypatch, repo, factory, ["src/a.py"])

    assert directive["tests_to_run"] == ["tests/test_a.py"]
    assert directive["tests_run_all"] is False
    assert directive["tests_to_run_why"] == {"tests/test_a.py": "src/a.py changed (import-graph)"}


@pytest.mark.asyncio
async def test_tests_to_update_reads_imports_not_runner_wiring(tmp_path, monkeypatch) -> None:
    files = {
        "src/core.py": "C = 1\n",
        "tests/test_direct.py": "from src import core\n",
        "tests/test_wired.py": "def test_w():\n    pass\n",
    }
    repo, base = _repo(tmp_path, files)
    factory = await _index(
        base,
        {"src/core.py": False, "tests/test_direct.py": True, "tests/test_wired.py": True},
        [
            ("tests/test_direct.py", "src/core.py", "imports"),
            # A setup file's wiring: every test it runs for, no import of its own.
            ("tests/test_wired.py", "src/core.py", "framework"),
        ],
    )

    directive = await _risk_directive(monkeypatch, repo, factory, ["src/core.py"])

    assert sorted(directive["tests_to_run"]) == ["tests/test_direct.py", "tests/test_wired.py"]
    assert directive["tests_to_update"] == [{"path": "tests/test_direct.py", "reason": "imports"}]


@pytest.mark.asyncio
async def test_a_map_measured_at_the_base_matches_the_old_lines(tmp_path, monkeypatch) -> None:
    files = {
        "src/app.py": "a\nb\nc\nd\ne\nf\n",
        "tests/test_line2.py": "def test_2():\n    pass\n",
        "tests/test_line6.py": "def test_6():\n    pass\n",
        "tests/test_graph.py": "from src import app\n",
        "src/other.py": "O = 1\n",
    }
    repo, base = _repo(tmp_path, files)
    # Two lines inserted at the top and the old line 6 replaced: the head's
    # numbers (1, 2, 8) would hit the test of the untouched old line 2.
    _commit(repo, {"src/app.py": "n1\nn2\na\nb\nc\nd\ne\nF\n"}, "feat: app")
    factory = await _index(
        base,
        {
            "src/app.py": False,
            "tests/test_line2.py": True,
            "tests/test_line6.py": True,
            "tests/test_graph.py": True,
            "src/other.py": False,
        },
        [
            # The covering tests reach the app only at run time.
            ("tests/test_line2.py", "src/other.py", "imports"),
            ("tests/test_line6.py", "src/other.py", "imports"),
            ("tests/test_graph.py", "src/app.py", "imports"),
        ],
        coverage=[
            _tc("tests/test_line2.py::test_2", "src/app.py", [2]),
            _tc("tests/test_line6.py::test_6", "src/app.py", [6]),
        ],
        measured_at=base,
    )

    it = (await _change_risk(monkeypatch, repo, factory))["impacted_tests"]

    assert it["basis"] == "measured"
    assert it["map_present"] is True
    assert "tests/test_line6.py::test_6" in it["tests_to_run"]
    assert "tests/test_line2.py::test_2" not in it["tests_to_run"]
    # Coverage adds to the graph's answer; it never replaces it.
    assert "tests/test_graph.py" in it["tests_to_run"]
    assert it["line_coverage"]["untested_changes"] == []
    assert [c["source_file"] for c in it["line_coverage"]["stale_test_candidates"]] == [
        "src/app.py"
    ]


@pytest.mark.asyncio
async def test_a_selection_over_its_time_budget_says_so(tmp_path, monkeypatch) -> None:
    import asyncio

    from repowise.server.mcp_server import _test_selection

    async def _slow(*_a, **_k):
        await asyncio.sleep(1)

    monkeypatch.setattr(_test_selection, "SELECTION_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(_test_selection, "_select", _slow)
    repo, base = _repo(tmp_path, _STATUS)
    factory = await _index(base, {"src/status.py": False})

    directive = await _risk_directive(monkeypatch, repo, factory, ["src/status.py"])

    assert directive["tests_to_run"] == []
    assert directive["tests_status"] == "timeout"
    assert "repowise impacted-tests" in directive["tests_status_reason"]
