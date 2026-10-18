"""Better/worse history: commit and change rows led by their health delta, the
history filters, period headlines and /wins."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from repowise.core import attribution
from repowise.core.analysis.change_health.commit_scan import current_fingerprint
from repowise.core.analysis.history import (
    DELTA_FILTERS,
    MIN_SHOWN_ANALYZER_VERSION,
    DeltaStamp,
    matches_delta,
    summarize_delta,
)
from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import (
    GitCommitHealthDelta,
    HealthSnapshot,
    RefactoringPayoff,
    RefactoringSuggestion,
)
from tests.unit.server.conftest import create_test_repo

SHAS = {name: name * 40 for name in "abcde"}


def _when(day: int) -> datetime:
    return datetime(2026, 10, day, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _private_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    user_file = tmp_path / "home" / ".repowise" / "config.yaml"
    monkeypatch.setattr(attribution, "user_config_path", lambda: user_file)


def _commit(key: str, day: int, author: str) -> dict:
    return {
        "sha": SHAS[key],
        "author_name": author,
        "author_email": f"{author.lower()}@example.com",
        "committed_at": _when(day),
        "subject": f"commit {key}",
        "lines_added": 10,
        "lines_deleted": 2,
        "files_changed": 1,
        "dirs_changed": 1,
        "subsystems_changed": 1,
        "entropy": 0.5,
        "is_fix": False,
        "author_experience": 3,
        "change_risk_score": 3.0,
        "change_risk_level": "low",
    }


def _delta(key: str, introduced=0, worsened=0, resolved=0, status="available") -> dict:
    return {
        "sha": SHAS[key],
        "status": status,
        "introduced_count": introduced,
        "worsened_count": worsened,
        "resolved_count": resolved,
        "files_analyzed": 1,
        "files_skipped": 0,
        "findings_stored": introduced + worsened,
    }


def _finding(key: str, path: str, position: int = 0) -> dict:
    return {
        "sha": SHAS[key],
        "change_finding_id": f"{key}{position}",
        "position": position,
        "change_kind": "introduced",
        "dimension": "defect",
        "biomarker_type": "complex_method",
        "severity": "high",
        "file_path": path,
        "symbol": "run",
        "line_start": 10,
        "line_end": 40,
        "attribution_basis": "added_lines",
        "reason": "run has cyclomatic complexity 22",
    }


def _change(change_id: str, day: int, keys: str, paths: list[str], author="Ann") -> dict:
    return {
        "change_id": change_id,
        "identity_kind": "pr",
        "identity_value": change_id.removeprefix("pr-"),
        "shape": "merge" if len(keys) > 1 else "squash",
        "forge": "github",
        "forge_ref": "#" + change_id.removeprefix("pr-"),
        "title": f"change {change_id}",
        "merged_at": _when(day),
        "head_sha": SHAS[keys[-1]],
        "author_name": author,
        "author_email": f"{author.lower()}@example.com",
        "commit_shas_json": json.dumps([SHAS[k] for k in keys]),
        "files_json": json.dumps([{"path": p, "added": 1, "deleted": 0} for p in paths]),
        "files_changed": len(paths),
        "lines_added": len(paths),
        "lines_deleted": 0,
        "category": "feature",
    }


async def _current(session, repo_id: str) -> dict:
    """The stamp a delta computed now carries for this repository."""
    repo = await crud.get_repository(session, repo_id)
    return current_fingerprint(repo.local_path)


async def _seed(app, repo_id: str) -> None:
    """a: resolves in src/core (Ann); b: introduces in src/core/hot.py, a hotspot
    (Bob); c: neutral in docs (Ann); d: never scanned (Ann)."""
    async with get_session(app.state.session_factory) as session:
        await crud.upsert_git_commits_bulk(
            session,
            repo_id,
            [_commit("a", 1, "Ann"), _commit("b", 2, "Bob"), _commit("c", 3, "Ann"),
             _commit("d", 4, "Ann")],
        )
        await crud.upsert_git_commit_files_bulk(
            session,
            repo_id,
            [
                {"sha": SHAS["a"], "file_path": "src/core/a.py", "lines_added": 1, "lines_deleted": 0},
                {"sha": SHAS["b"], "file_path": "src/core/hot.py", "lines_added": 1, "lines_deleted": 0},
                {"sha": SHAS["c"], "file_path": "docs/x.md", "lines_added": 1, "lines_deleted": 0},
                {"sha": SHAS["d"], "file_path": "src/corex/d.py", "lines_added": 1, "lines_deleted": 0},
            ],
        )
        await crud.upsert_git_metadata(
            session, repository_id=repo_id, file_path="src/core/hot.py", is_hotspot=True
        )
        stamp = await _current(session, repo_id)
        await crud.upsert_commit_health_bulk(
            session,
            repo_id,
            [
                {**_delta("a", resolved=3), **stamp},
                {**_delta("b", introduced=2, worsened=1), **stamp},
                {**_delta("c"), **stamp},
            ],
            [_finding("b", "src/core/hot.py"), _finding("b", "src/core/hot.py", 1)],
        )
        await crud.upsert_changes(
            session,
            repo_id,
            [
                _change("pr-1", 2, "ab", ["src/core/a.py", "src/core/hot.py"]),
                _change("pr-2", 3, "c", ["docs/x.md"]),
                _change("pr-3", 4, "d", ["src/corex/d.py"]),
            ],
        )


def _shorts(page: dict) -> list[str]:
    return [item["sha"][0] for item in page["items"]]


@pytest.mark.asyncio
async def test_commit_rows_lead_with_their_health_delta(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    page = (await client.get(f"/api/repos/{repo['id']}/commits")).json()
    rows = {item["sha"][0]: item for item in page["items"]}

    assert rows["a"]["health_delta"]["kind"] == "improved"
    assert rows["b"]["health_delta"] == {
        "status": "available",
        "kind": "worsened",
        "introduced": 2,
        "worsened": 1,
        "resolved": 0,
        "improved": 0,
        "moved": 0,
        "stale": False,
        "files": [],
    }
    assert rows["c"]["health_delta"]["kind"] == "neutral"
    # Never scanned is absent, not neutral.
    assert rows["d"]["health_delta"] is None
    assert (rows["b"]["touches_hotspot"], rows["a"]["touches_hotspot"]) == (1, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"delta": "improved"}, ["a"]),
        ({"delta": "worsened"}, ["b"]),
        ({"delta": "neutral"}, ["c"]),
        ({"touches_hotspot": "true"}, ["b"]),
        ({"author": "ANN@example.com"}, ["d", "c", "a"]),
        ({"author": "Bob"}, ["b"]),
        ({"area": "src/core"}, ["b", "a"]),
        ({"area": "src/core/a.py"}, ["a"]),
        ({"since": "2026-10-02T00:00:00Z", "until": "2026-10-04T00:00:00Z"}, ["c", "b"]),
        # 13:00 at +05:30 is 07:30 UTC on the 2nd, before b's noon: b stays in.
        ({"since": "2026-10-02T13:00:00+05:30"}, ["d", "c", "b"]),
    ],
)
async def test_commit_history_filters_narrow_the_repository(
    client: AsyncClient, app, params: dict, expected: list[str]
) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    page = (await client.get(f"/api/repos/{repo['id']}/commits", params=params)).json()
    assert _shorts(page) == expected
    assert page["total"] == len(expected)


@pytest.mark.asyncio
async def test_an_unknown_delta_filter_is_rejected(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    resp = await client.get(f"/api/repos/{repo['id']}/commits", params={"delta": "better"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_the_sql_delta_filter_agrees_with_the_python_reading(
    client: AsyncClient, app
) -> None:
    """The commit filter runs in SQL and the change filter in Python; both must
    read a stored row the same way."""
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    async with get_session(app.state.session_factory) as session:
        rows = (await session.execute(select(GitCommitHealthDelta))).scalars().all()
        stamp = DeltaStamp(**await _current(session, repo["id"]))
        for wanted in DELTA_FILTERS:
            where = crud.CommitFilter(delta=wanted, stamp=stamp)
            by_sql = set(await crud.list_commit_shas(session, repo["id"], where))
            by_python = {r.sha for r in rows if matches_delta(summarize_delta(r).kind, wanted)}
            assert by_sql == by_python, wanted


@pytest.mark.asyncio
async def test_commit_detail_carries_the_delta_and_the_findings_a_prompt_needs(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    detail = (await client.get(f"/api/repos/{repo['id']}/commits/{SHAS['b'][:8]}")).json()
    assert detail["health_delta"]["kind"] == "worsened"
    finding = detail["health"]["findings"][0]
    assert (finding["path"], finding["symbol"], finding["biomarker_type"]) == (
        "src/core/hot.py",
        "run",
        "complex_method",
    )
    assert (finding["line_start"], finding["line_end"]) == (10, 40)


@pytest.mark.asyncio
async def test_commit_health_summary_counts_improvements_first(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    summary = (await client.get(f"/api/repos/{repo['id']}/commits/health-summary")).json()
    assert list(summary)[:4] == ["since", "until", "total", "improved"]
    assert (summary["improved"], summary["worsened"], summary["neutral"]) == (1, 1, 1)
    assert (summary["total"], summary["unscanned"]) == (4, 1)
    assert summary["net_findings"] == 3 - 3


@pytest.mark.asyncio
async def test_change_rows_roll_up_their_commits(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    page = (await client.get(f"/api/repos/{repo['id']}/changes")).json()
    rows = {item["id"]: item for item in page["items"]}

    merged = rows["pr-1"]["health"]
    assert merged["kind"] == "mixed"
    assert (merged["introduced"], merged["worsened"], merged["resolved"]) == (2, 1, 3)
    assert (merged["scanned_commits"], merged["files_worsened"]) == (2, 1)
    assert merged["files_improved"] is None
    assert rows["pr-1"]["touches_hotspot"] == 1
    assert rows["pr-2"]["health"]["kind"] == "neutral"
    assert rows["pr-3"]["health"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"delta": "improved"}, ["pr-1"]),
        ({"delta": "mixed"}, ["pr-1"]),
        ({"delta": "neutral"}, ["pr-2"]),
        ({"touches_hotspot": "true"}, ["pr-1"]),
        ({"area": "src/core"}, ["pr-1"]),
        ({"area": "src/corex/"}, ["pr-3"]),
        ({"since": "2026-10-03T00:00:00Z"}, ["pr-3", "pr-2"]),
        ({"author": "nobody"}, []),
    ],
)
async def test_change_history_filters(
    client: AsyncClient, app, params: dict, expected: list[str]
) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    page = (await client.get(f"/api/repos/{repo['id']}/changes", params=params)).json()
    assert [c["id"] for c in page["items"]] == expected
    assert page["total"] == len(expected)


@pytest.mark.asyncio
async def test_change_summary_is_the_period_headline(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    url = f"/api/repos/{repo['id']}/changes/summary"
    summary = (await client.get(url)).json()
    assert (summary["mixed"], summary["neutral"], summary["unscanned"]) == (1, 1, 1)
    assert summary["findings_resolved"] == 3 and summary["net_findings"] == 0
    narrowed = (await client.get(url, params={"delta": "neutral"})).json()
    assert (narrowed["total"], narrowed["neutral"]) == (1, 1)


@pytest.mark.asyncio
async def test_change_detail_carries_its_worse_findings_with_their_commit(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    detail = (await client.get(f"/api/repos/{repo['id']}/changes/pr-1")).json()
    health = detail["health"]
    assert health["kind"] == "mixed" and len(health["findings"]) == 2
    assert {f["sha"] for f in health["findings"]} == {SHAS["b"]}
    assert detail["title"] == "change pr-1" and detail["head_sha"] == SHAS["b"]


async def _seed_wins(app, repo_id: str) -> None:
    async with get_session(app.state.session_factory) as session:
        session.add_all(
            [
                HealthSnapshot(
                    repository_id=repo_id,
                    taken_at=datetime(2026, 9, 30, tzinfo=UTC),
                    per_file_scores_json=json.dumps(
                        {"src/core/a.py": 5.0, "src/core/hot.py": 6.0, "docs/x.md": 4.0}
                    ),
                ),
                HealthSnapshot(
                    repository_id=repo_id,
                    taken_at=datetime(2026, 10, 5, tzinfo=UTC),
                    per_file_scores_json=json.dumps(
                        {"src/core/a.py": 8.0, "src/core/hot.py": 7.0, "docs/x.md": 9.0}
                    ),
                ),
            ]
        )
        plan = RefactoringSuggestion(
            repository_id=repo_id,
            refactoring_type="extract_method",
            file_path="src/core/a.py",
            target_symbol="run",
            status="resolved",
        )
        session.add(plan)
        await session.flush()
        session.add(
            RefactoringPayoff(
                suggestion_id=plan.id,
                repository_id=repo_id,
                outcome="applied",
                resolved_at=_when(2),
                before_ccn=30,
                after_ccn=6,
            )
        )


@pytest.mark.asyncio
async def test_wins_name_what_got_better(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    await _seed_wins(app, repo["id"])
    wins = (
        await client.get(f"/api/repos/{repo['id']}/wins", params={"since": "2026-10-01T00:00:00Z"})
    ).json()

    assert wins["findings_resolved"]["count"] == 3
    assert wins["findings_resolved"]["top_commits"][0]["sha"] == SHAS["a"]
    # Every file here was touched in the window; biggest gain first.
    assert [f["path"] for f in wins["files_improved"]["top"]] == [
        "docs/x.md",
        "src/core/a.py",
        "src/core/hot.py",
    ]
    assert wins["refactorings_landed"]["top"][0]["after_ccn"] == 6
    assert wins["unavailable"]


@pytest.mark.asyncio
async def test_wins_for_an_author_credit_only_their_work(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    await _seed_wins(app, repo["id"])
    url = f"/api/repos/{repo['id']}/wins"
    params = {"since": "2026-10-01T00:00:00Z"}

    bob = (await client.get(url, params={**params, "author": "Bob"})).json()
    assert bob["findings_resolved"]["count"] == 0
    assert [f["path"] for f in bob["files_improved"]["top"]] == ["src/core/hot.py"]
    assert bob["refactorings_landed"]["count"] == 0

    ann = (await client.get(url, params={**params, "author": "ann@example.com"})).json()
    assert ann["findings_resolved"]["count"] == 3
    assert ann["refactorings_landed"]["count"] == 1


@pytest.mark.asyncio
async def test_wins_without_snapshots_or_history_are_empty_not_errors(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    wins = (await client.get(f"/api/repos/{repo['id']}/wins")).json()
    assert wins["findings_resolved"]["count"] == 0
    assert wins["files_improved"] == {
        "count": 0,
        "baseline_at": None,
        "current_at": None,
        "top": [],
    }


@pytest.mark.asyncio
async def test_wins_for_an_unknown_repo_is_404(client: AsyncClient) -> None:
    assert (await client.get("/api/repos/nope/wins")).status_code == 404


async def _add_old_deltas(app, repo_id: str) -> None:
    """d gets a row from an older readable analyzer (stale); a v21-era row for
    a fifth commit e stays below the shown floor."""
    async with get_session(app.state.session_factory) as session:
        stamp = await _current(session, repo_id)
        await crud.upsert_git_commits_bulk(session, repo_id, [_commit("e", 5, "Ann")])
        older = {**stamp, "analyzer_version": MIN_SHOWN_ANALYZER_VERSION, "rules_fingerprint": "old"}
        ancient = {**stamp, "analyzer_version": MIN_SHOWN_ANALYZER_VERSION - 1}
        await crud.upsert_commit_health_bulk(
            session,
            repo_id,
            [{**_delta("d", introduced=5), **older}, {**_delta("e", resolved=9), **ancient}],
            [],
        )


@pytest.mark.asyncio
async def test_an_older_analyzers_row_shows_stale_and_an_ancient_one_not_at_all(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    await _add_old_deltas(app, repo["id"])
    base = f"/api/repos/{repo['id']}"

    rows = {i["sha"][0]: i for i in (await client.get(f"{base}/commits")).json()["items"]}
    assert rows["d"]["health_delta"]["stale"] is True
    assert rows["d"]["health_delta"]["kind"] == "worsened"
    assert rows["a"]["health_delta"]["stale"] is False
    assert rows["e"]["health_delta"] is None
    detail = (await client.get(f"{base}/commits/{SHAS['d'][:8]}")).json()
    assert detail["health"]["stale"] is True
    assert (await client.get(f"{base}/commits/{SHAS['e'][:8]}")).json()["health"] is None


@pytest.mark.asyncio
async def test_aggregates_count_current_analyzer_rows_only(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    await _add_old_deltas(app, repo["id"])
    base = f"/api/repos/{repo['id']}"

    summary = (await client.get(f"{base}/commits/health-summary")).json()
    assert (summary["worsened"], summary["unscanned"]) == (1, 2)
    assert summary["findings_introduced"] == 2
    worse = (await client.get(f"{base}/commits", params={"delta": "worsened"})).json()
    assert _shorts(worse) == ["b"]
    changes = {c["id"]: c for c in (await client.get(f"{base}/changes")).json()["items"]}
    assert changes["pr-3"]["health"] is None
    wins = (await client.get(f"{base}/wins", params={"since": "2026-10-01T00:00:00Z"})).json()
    assert wins["findings_resolved"]["count"] == 3


@pytest.mark.asyncio
async def test_change_detail_findings_come_from_the_commits_its_counts_read(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    async with get_session(app.state.session_factory) as session:
        stamp = await _current(session, repo["id"])
        older = {**stamp, "analyzer_version": MIN_SHOWN_ANALYZER_VERSION, "rules_fingerprint": "old"}
        await crud.upsert_commit_health_bulk(
            session,
            repo["id"],
            [{**_delta("d", introduced=1), **older}],
            [_finding("d", "src/corex/d.py")],
        )
        await crud.upsert_changes(
            session, repo["id"], [_change("pr-4", 5, "bd", ["src/core/hot.py", "src/corex/d.py"])]
        )
    health = (await client.get(f"/api/repos/{repo['id']}/changes/pr-4")).json()["health"]
    assert (health["introduced"], health["scanned_commits"]) == (2, 1)
    assert {f["sha"] for f in health["findings"]} == {SHAS["b"]}


@pytest.mark.asyncio
async def test_improved_findings_make_a_commit_better_and_moved_ones_name_their_origin(
    client: AsyncClient, app
) -> None:
    """e only shrank a function (improved) and carried another to a new file,
    where it measures worse: worsened with its origin, never introduced."""
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    async with get_session(app.state.session_factory) as session:
        stamp = await _current(session, repo["id"])
        await crud.upsert_git_commits_bulk(session, repo["id"], [_commit("e", 5, "Ann")])
        await crud.upsert_changes(session, repo["id"], [_change("pr-5", 5, "e", ["src/pkg/b.py"])])
        moved = {
            **_finding("e", "src/pkg/b.py"),
            "change_kind": "worsened",
            "attribution_basis": "moved",
            "moved_from_path": "src/pkg/a.py",
            "moved_from_symbol": "run",
        }
        await crud.upsert_commit_health_bulk(
            session,
            repo["id"],
            [{**_delta("e", worsened=1), "improved_count": 2, "moved_count": 3, **stamp}],
            [moved],
        )
    base = f"/api/repos/{repo['id']}"

    row = next(
        i for i in (await client.get(f"{base}/commits")).json()["items"] if i["sha"] == SHAS["e"]
    )
    assert row["health_delta"]["improved"] == 2 and row["health_delta"]["moved"] == 3
    assert row["health_delta"]["introduced"] == 0
    assert row["health_delta"]["kind"] == "mixed"
    better = (await client.get(f"{base}/commits", params={"delta": "improved"})).json()
    assert "e" in _shorts(better)

    detail = (await client.get(f"{base}/changes/pr-5")).json()["health"]
    assert (detail["improved"], detail["moved"], detail["introduced"]) == (2, 3, 0)
    (finding,) = detail["findings"]
    assert finding["moved_from"] == {"path": "src/pkg/a.py", "symbol": "run"}
