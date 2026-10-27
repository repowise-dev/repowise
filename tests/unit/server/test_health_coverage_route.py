"""``/health/coverage``: what ``limit`` caps, and what it must not.

``limit`` names the file list. It was also being applied to ``modules`` — a
repo-level aggregate — so the tab badge, which asks for one file row precisely
because it wants a cheap response, was told the repo had one module. The
``?file_path=`` branch had the same defect from the other direction: it
aggregated ``modules`` over the single-row read, so asking about one file
reported that file's directory as the whole repo.

The tests below enumerate ``limit`` x ``file_path`` rather than inspecting one
response: both bugs live in the interaction between the two parameters, and
neither is visible from a single call.
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.crud import save_coverage_files, save_health_metrics

from .conftest import create_test_repo

# Three files in three directories, so a truncated or subset-derived module
# list is distinguishable from a complete one at any limit under three.
_FILES = [
    {"file_path": "src/a.py", "line_coverage_pct": 10.0, "covered_lines": [1],
     "total_coverable_lines": 10, "branch_coverage_pct": 5.0},
    {"file_path": "lib/b.py", "line_coverage_pct": 50.0, "covered_lines": [1, 2],
     "total_coverable_lines": 20, "branch_coverage_pct": 40.0},
    {"file_path": "tools/c.py", "line_coverage_pct": 90.0, "covered_lines": [1, 2, 3],
     "total_coverable_lines": 30, "branch_coverage_pct": 80.0},
]


def _metric(path: str, score: float, nloc: int) -> dict:
    return {
        "file_path": path,
        "score": score,
        "max_ccn": 1,
        "max_nesting": 1,
        "nloc": nloc,
        "duplication_pct": 0.0,
        "has_test_file": True,
        "line_coverage_pct": 0.0,
        "branch_coverage_pct": 0.0,
        "module": path.split("/")[0],
    }


@pytest.fixture
async def seeded(client, session, tmp_path):
    repo = await create_test_repo(client, tmp_path)
    await save_coverage_files(session, repo["id"], _FILES, source_format="lcov")
    await save_health_metrics(
        session,
        repo["id"],
        [
            _metric("src/a.py", 3.0, 100),
            _metric("lib/b.py", 6.0, 200),
            _metric("tools/c.py", 9.0, 300),
        ],
    )
    await session.commit()
    return repo


async def _get(client, repo_id: str, **params) -> dict:
    resp = await client.get(f"/api/repos/{repo_id}/health/coverage", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.parametrize("limit", [1, 2])
async def test_the_file_limit_does_not_cap_the_module_rollup(
    client, seeded, limit
) -> None:
    """``limit`` caps ``files``; ``module_limit`` caps ``modules``.

    Only limits below the module count can show this, hence 1 and 2 against
    three modules — at 3 or 500 the old slice was a no-op and the assertion
    would hold with the bug present.
    """
    body = await _get(client, seeded["id"], limit=limit)

    assert len(body["files"]) == limit
    assert {m["module"] for m in body["modules"]} == {"src", "lib", "tools"}
    assert body["modules_total"] == 3


async def test_module_limit_caps_modules_without_touching_files(
    client, seeded
) -> None:
    body = await _get(client, seeded["id"], limit=3, module_limit=1)

    assert len(body["files"]) == 3
    assert len(body["modules"]) == 1
    # The truncation is never silent.
    assert body["modules_total"] == 3


async def test_module_limit_zero_declines_the_rollup_but_still_counts_it(
    client, seeded
) -> None:
    """What the tab badge sends: the summary, and none of the rest."""
    body = await _get(client, seeded["id"], limit=1, module_limit=0)

    assert body["modules"] == []
    assert body["modules_total"] == 3
    assert body["summary"]["file_count"] == 3


@pytest.mark.parametrize("limit", [1, 3, 500])
async def test_modules_cover_the_repo_when_one_file_is_requested(
    client, seeded, limit
) -> None:
    """``?file_path=`` scopes ``files``, and nothing else.

    The module aggregation used to run over the single-row read, so this
    returned one module — the requested file's directory — presented as the
    repo's module coverage.
    """
    body = await _get(client, seeded["id"], file_path="src/a.py", limit=limit)

    assert [f["file_path"] for f in body["files"]] == ["src/a.py"]
    assert body["files"][0]["covered_lines"] == [1]
    assert {m["module"] for m in body["modules"]} == {"src", "lib", "tools"}


async def test_summary_stays_repo_wide_for_a_single_file_request(
    client, seeded
) -> None:
    """Guards the shared read, not a past bug.

    ``summary`` is now computed from rows the route loaded rather than from a
    read of its own, so handing it the ``file_path=`` subset would silently
    report one file's coverage as the repo's.
    """
    everything = await _get(client, seeded["id"], limit=500)
    one_file = await _get(client, seeded["id"], file_path="src/a.py")

    assert one_file["summary"] == everything["summary"]
    assert one_file["summary"]["file_count"] == 3
    # 1 + 10 + 27 covered of 60 coverable.
    assert one_file["summary"]["covered_lines"] == 38


async def test_module_rollup_counts_every_file_not_just_the_returned_page(
    client, seeded
) -> None:
    body = await _get(client, seeded["id"], limit=1)

    by_name = {m["module"]: m for m in body["modules"]}
    assert by_name["src"]["files"] == 1
    assert by_name["src"]["total_lines"] == 10
    assert by_name["tools"]["line_coverage_pct"] == 90.0
    # Worst coverage first, so the bars read as a ranking.
    assert [m["module"] for m in body["modules"]] == ["src", "lib", "tools"]


async def test_health_scores_are_attached_to_the_returned_rows(client, seeded) -> None:
    """The metrics read is scoped to the page; the join must still land."""
    body = await _get(client, seeded["id"], limit=2)

    assert [(f["file_path"], f["health_score"], f["nloc"]) for f in body["files"]] == [
        ("src/a.py", 3.0, 100),
        ("lib/b.py", 6.0, 200),
    ]


async def test_a_file_with_no_health_metric_still_returns_its_coverage(
    client, session, tmp_path
) -> None:
    """A scoped metrics read must not turn a miss into a dropped row."""
    repo = await create_test_repo(client, tmp_path)
    await save_coverage_files(session, repo["id"], _FILES, source_format="lcov")
    await save_health_metrics(session, repo["id"], [_metric("lib/b.py", 6.0, 200)])
    await session.commit()

    body = await _get(client, repo["id"], limit=500)

    assert [f["file_path"] for f in body["files"]] == [
        "src/a.py",
        "lib/b.py",
        "tools/c.py",
    ]
    assert "health_score" not in body["files"][0]
    assert body["files"][1]["health_score"] == 6.0


async def test_covered_lines_are_withheld_from_the_list_response(
    client, seeded
) -> None:
    """The list response carries no covered-line arrays; the detail one does."""
    listed = await _get(client, seeded["id"], limit=500)
    detail = await _get(client, seeded["id"], file_path="tools/c.py")

    assert all("covered_lines" not in f for f in listed["files"])
    assert detail["files"][0]["covered_lines"] == [1, 2, 3]


async def test_summary_says_whether_coverage_was_measured_at_the_indexed_commit(
    client, session, tmp_path
) -> None:
    from repowise.core.persistence.models import Repository

    repo = await create_test_repo(client, tmp_path)
    await save_coverage_files(
        session, repo["id"], _FILES, source_format="lcov", ingested_commit_sha="aaa"
    )
    row = await session.get(Repository, repo["id"])
    row.head_commit = "bbb"
    await session.commit()

    summary = (await _get(client, repo["id"], include_inferred="false"))["summary"]

    assert summary["freshness"] == {"status": "stale", "indexed_commit": "bbb"}
    # Written without provenance: the counts are unknown, not zero.
    assert summary["report_paths"] is None
    assert summary["source_formats"] == ["lcov"]


async def test_history_carries_one_point_per_report_oldest_first(
    client, session, tmp_path
) -> None:
    from datetime import UTC, datetime

    repo = await create_test_repo(client, tmp_path)
    for minute, commit in enumerate(("aaa", "bbb")):
        files = _FILES if commit == "aaa" else _FILES[1:]
        await save_coverage_files(
            session,
            repo["id"],
            files,
            source_format="lcov",
            ingested_commit_sha=commit,
            ingested_at=datetime(2026, 9, 1, 12, minute, tzinfo=UTC),
        )
    await session.commit()

    body = await _get(client, repo["id"], include_inferred="false")

    # SQLite drops the offset, as it does for ``summary.ingested_at``.
    assert body["history"] == [
        {
            "ingested_at": "2026-09-01T12:00:00",
            "ingested_commit_sha": "aaa",
            "line_coverage_pct": 63.33,
            "branch_coverage_pct": 54.17,
        },
        {
            "ingested_at": "2026-09-01T12:01:00",
            "ingested_commit_sha": "bbb",
            "line_coverage_pct": 74.0,
            "branch_coverage_pct": 64.0,
        },
    ]
    assert body["history"][-1]["line_coverage_pct"] == body["summary"]["line_coverage_pct"]


@pytest.mark.parametrize(
    "params", [{"file_path": "src/a.py"}, {"limit": 1, "module_limit": 0}]
)
async def test_the_badge_reads_carry_no_history(client, seeded, params) -> None:
    """The one-file and no-module reads never draw a trend, so they skip its read."""
    body = await _get(client, seeded["id"], include_inferred="false", **params)

    assert body["basis"] == "measured"
    assert "history" not in body
