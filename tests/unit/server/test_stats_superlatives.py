"""The Stats page's records and scale, from plain rows and through the route.

``biggest_commit`` skips the repo's very first commit (every initial import
would win otherwise), ``most_central_file`` names the most-imported file that
is not a test, and the function records leave test code out.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from repowise.core.persistence.crud import upsert_git_commits_bulk
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphMetric, GraphNode
from repowise.core.stats_highlights import (
    build_commit_pass,
    build_file_records,
    build_function_records,
    build_scale,
    is_test_checker,
    most_imported_file,
    most_patched_function,
)
from repowise.server.routers.stats import stats_highlights
from tests.unit.server.conftest import create_test_repo

_DAY = 86400


def _commit(sha: str, ts: int, added: int, deleted: int = 0, files: int = 1) -> dict:
    return {
        "sha": sha,
        "author_name": "Jane Doe",
        "author_email": "jane@company.com",
        "committed_at": datetime.fromtimestamp(ts, tz=UTC),
        "subject": f"commit {sha}",
        "lines_added": added,
        "lines_deleted": deleted,
        "files_changed": files,
        "dirs_changed": 1,
        "subsystems_changed": 1,
        "entropy": 0.1,
        "is_fix": False,
        "change_risk_score": 1.0,
        "change_risk_level": "low",
    }


def test_commit_awards_skip_the_root_commit_and_streak_counts_days() -> None:
    rows = [
        # The root commit is the largest by churn and by deletion, and wins neither.
        _commit("a1", 1000, added=50_000, deleted=60_000, files=300),
        _commit("b1", 1000 + _DAY, added=900, deleted=100, files=12),
        _commit("c1", 1000 + 2 * _DAY, added=10, deleted=400),
        _commit("d1", 1000 + 3 * _DAY, added=10),
        _commit("e1", 1000 + 10 * _DAY, added=10),
    ]
    out = build_commit_pass(rows)
    assert out["biggest_commit"]["sha"] == "b1"
    assert out["biggest_commit"]["lines_changed"] == 1000
    assert out["biggest_purge"] == {
        "sha": "c1",
        "subject": "commit c1",
        "lines_deleted": 400,
        "lines_added": 10,
    }
    assert out["rhythm"]["longest_streak"]["days"] == 4
    assert out["rhythm"]["longest_silence"]["hours"] == 7 * 24


def test_a_partial_sample_keeps_its_oldest_commit_eligible() -> None:
    """Only the repo's root commit is barred; the oldest commit of a newer
    sample is ordinary and may win."""
    rows = [
        _commit("old", 1000 + 50 * _DAY, added=9_000, files=40),
        _commit("new", 1000 + 51 * _DAY, added=10, files=1),
    ]
    totals = {"first_commit_at": datetime.fromtimestamp(1000, tz=UTC), "total_commit_count": 90}
    out = build_commit_pass(rows, totals)
    assert out["rhythm"]["window"]["complete"] is False
    assert out["biggest_commit"]["sha"] == "old"


def test_merge_commits_do_not_make_a_full_sample_look_partial() -> None:
    """The sample skips merges and the total counts them; the root decides."""
    rows = [_commit("root", 1000, added=5), _commit("b", 1000 + _DAY, added=5)]
    totals = {"first_commit_at": datetime.fromtimestamp(1000, tz=UTC), "total_commit_count": 3}
    assert build_commit_pass(rows, totals)["rhythm"]["window"]["complete"] is True


def test_most_imported_file_rechecks_stale_test_flags() -> None:
    """Stale ``is_test`` lets conftest through the SQL filter; the path rules
    catch it, without reading ``src/latest/api.py`` as a test (#1103)."""
    candidates = [
        {"path": "tests/conftest.py", "in_degree": 700, "pagerank": 0.1},
        {"path": "src/latest/api.py", "in_degree": 90, "pagerank": 0.1},
    ]
    assert most_imported_file(candidates) == {
        "path": "src/latest/api.py",
        "pagerank": 0.1,
        "import_count": 90,
    }


def test_scale_counts_code_languages_and_skips_external_nodes() -> None:
    nodes = [
        {"node_id": "a.py", "language": "python", "symbol_count": 3},
        {"node_id": "b.ts", "language": "typescript", "symbol_count": 2},
        {"node_id": "c.json", "language": "json", "symbol_count": 0},
        {"node_id": "external:requests", "language": "external", "symbol_count": 0},
    ]
    metrics = [
        {"nloc": 100, "module": "a", "is_test": False},
        {"nloc": 40, "module": "tests", "is_test": True},
    ]
    scale = build_scale(nodes, metrics)
    assert scale["file_count"] == 3
    assert scale["symbol_count"] == 5
    assert scale["language_count"] == 2
    assert {lang["language"] for lang in scale["languages"]} == {"python", "typescript"}
    assert scale["total_nloc"] == 140
    assert scale["test_nloc"] == 40


def test_day_one_files_replace_an_arbitrary_oldest_file() -> None:
    root = datetime(2024, 1, 1, 9, 0)
    meta = [
        {"file_path": "a.py", "first_commit_at": root, "commit_count_total": 3},
        {"file_path": "b.py", "first_commit_at": root, "commit_count_total": 9},
        {"file_path": "c.py", "first_commit_at": datetime(2024, 2, 1), "commit_count_total": 1},
    ]
    # A capped history can lose its root commit, so it counts on neither side.
    meta.append(
        {"file_path": "d.py", "first_commit_at": datetime(2024, 3, 1), "commit_count_total": 5,
         "commit_count_capped": True}
    )
    out = build_file_records([], meta, "2024-01-01T09:00:00+00:00")
    assert out["day_one_files"] == {"count": 2, "of": 3}
    assert out["most_changed_file"] == {"path": "b.py", "commit_count": 9}
    assert "oldest_file" not in out


def test_function_records_leave_test_code_out() -> None:
    def fn(name: str, path: str, end: int, kind: str = "function") -> dict:
        return {"name": name, "kind": kind, "file_path": path, "start_line": 1, "end_line": end}

    fns = [
        fn("run", "src/app.py", 300),
        fn("run", "src/other.py", 5, kind="method"),
        fn("a_rather_descriptive_name", "src/app.py", 2),
        fn("test_a_name_long_enough_to_win_everything_if_tests_counted", "tests/test_app.py", 900),
        fn("Widget", "src/app.py", 2000, kind="class"),
    ]
    # The stored flag is NULL here, as on an index written before the column:
    # the path rules have to catch the test file anyway.
    is_test = is_test_checker([{"file_path": "tests/test_app.py", "is_test": None}])
    out = build_function_records(fns, is_test)
    assert out["longest_function"] == {"name": "run", "file_path": "src/app.py", "lines": 300}
    assert out["longest_name"]["name"] == "a_rather_descriptive_name"
    assert out["most_common_name"] == {"name": "run", "count": 2}

    blame = [
        {"function_name": "test_x", "file_path": "tests/test_app.py", "mod_count": 40},
        {"function_name": "", "file_path": "src/app.py", "mod_count": 30},
        {"function_name": "run", "file_path": "src/app.py", "mod_count": 12},
    ]
    assert most_patched_function(blame, is_test) == {
        "name": "run",
        "file_path": "src/app.py",
        "mod_count": 12,
    }


@pytest.mark.asyncio
async def test_route_serves_records_from_the_database(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    async with get_session(app.state.session_factory) as session:
        for path, in_degree in (("tests/conftest.py", 700), ("src/latest/api.py", 90)):
            session.add(
                GraphNode(
                    repository_id=repo["id"],
                    node_id=path,
                    node_type="file",
                    language="python",
                    is_test=False,
                    pagerank=0.1,
                )
            )
            session.add(
                GraphMetric(
                    repository_id=repo["id"], node_id=path, in_degree=in_degree, pagerank=0.1
                )
            )
        await session.commit()
        await upsert_git_commits_bulk(
            session, repo["id"], [_commit("a1", 1000, 5), _commit("b1", 1000 + _DAY, 7, files=3)]
        )

    async with get_session(app.state.session_factory) as session:
        body = await stats_highlights(repo["id"], session)
    assert body["records"]["most_central_file"]["path"] == "src/latest/api.py"
    assert body["records"]["biggest_commit"]["sha"] == "b1"
    assert body["scale"]["file_count"] == 2
    assert body["rhythm"]["window"]["commits"] == 2
