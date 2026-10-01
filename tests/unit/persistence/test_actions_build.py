"""The pure facts builder over plain rows agrees with the SQL loader.

The rows below restate the loader test's seed as dicts, unfiltered and with ISO
timestamps, the way a caller holding its rows in memory would pass them.
"""

from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import select

from repowise.core.analysis.actions.build import ABSENT, build_repo_facts
from repowise.core.persistence.crud.analysis.actions import load_repo_facts
from repowise.core.persistence.models import DeadCodeFinding
from tests.unit.persistence.test_actions_loader import ANCHOR, DEEP, _seed

# SQLite hands datetimes back naive; the rows match it, and the column defaults.
_T = ANCHOR.replace(tzinfo=None)


def _iso(delta: timedelta = timedelta()) -> str:
    return (_T - delta).isoformat()


COMMITS = [
    {"sha": "head", "author_name": "Ada", "author_email": "ada@x.io",
     "committed_at": _iso(), "subject": "chore: head"},
    {"sha": "recent", "author_name": "Bob", "author_email": "bob@x.io",
     "committed_at": _iso(timedelta(days=2)), "subject": "feat: build step"},
    {"sha": "older", "author_name": "Cy", "author_email": "cy@x.io",
     "committed_at": _iso(timedelta(days=20)), "subject": "fix: things"},
]
_BY_SHA = {c["sha"]: c for c in COMMITS}

FILES = [
    {"file_path": "src/core.py", "commit_count_90d": 12, "bug_magnet": True,
     "last_commit_at": _iso(timedelta(days=1)), "bus_factor": 0,
     "score": 10.0, "max_ccn": 12, "nloc": 300, "is_test": False},
]

FIX_EVENTS = [
    {"file_path": "src/core.py", "fix_sha": f"fix{i}", "shape_kind": "code_fix",
     "attribution": "exact", "committed_at": _iso(timedelta(days=10 + i))}
    for i in range(4)
]

HEALTH_FINDINGS = [
    {"file_path": "src/core.py", "biomarker_type": "change_entropy", "severity": "high",
     "health_impact": 3.0},
    {"file_path": "src/core.py", "biomarker_type": "complex_method", "severity": "high",
     "function_name": "run", "health_impact": 1.0, "line_start": 10, "line_end": 60,
     "details_json": DEEP},
    {"file_path": "src/new.py", "biomarker_type": "complex_method", "severity": "high",
     "function_name": "build", "health_impact": 1.0, "line_start": 10, "line_end": 60,
     "details_json": DEEP},
    {"file_path": "src/touched.py", "biomarker_type": "long_method", "severity": "high",
     "function_name": "x", "health_impact": 1.0},
]

COMMIT_HEALTH = [
    {**_BY_SHA[sha], "sha": sha, "change_kind": "introduced", "biomarker_type": biomarker,
     "severity": "high", "file_path": path, "symbol": symbol, "attribution_basis": basis}
    for sha, biomarker, path, symbol, basis in [
        ("recent", "complex_method", "src/new.py", "build", "added_lines"),
        ("recent", "long_method", "src/touched.py", "x", "file_change"),
        ("recent", "change_entropy", "src/hist.py", None, "added_lines"),
    ]
]

PERFORMANCE = [
    {"opportunity_id": "perf1", "execution_context": "production",
     "actionability_state": "plan_ready", "plan_state": "available",
     "fix_strategy": "batch_or_prefetch_io", "boundary_kind": "db",
     "biomarker_type": "n_plus_one", "rank_position": 0,
     "file_path": "src/repo.py", "intervention_symbol": "src/repo.py::Repo.load",
     "affected_call_sites_total": 4, "affected_files_total": 2,
     "details": {"facets": {"exposure": "entry_reachable", "loop_magnitude": "grows_with_data",
                            "actionability_confidence": "medium"},
                 "plan": {"effort_bucket": "S"}}},
    {"opportunity_id": "perf2", "execution_context": "production",
     "actionability_state": "plan_ready", "boundary_kind": "db", "biomarker_type": "",
     "file_path": "src/other.py", "rank_position": 0,
     "affected_call_sites_total": 9,
     "details_json": json.dumps({"facets": {"exposure": "internal"}})},
]

SECURITY = [
    {"file_path": "src/settings.py", "kind": "hardcoded_secret", "severity": "high",
     "snippet": 'TOKEN = "sk_live_123"', "line_number": 4, "commit_sha": ""},
    {"file_path": "tests/test_x.py", "kind": "hardcoded_secret", "severity": "high",
     "snippet": 'TOKEN = "fake"', "line_number": 4, "commit_sha": ""},
]

DOC_DRIFT = [
    {"file_path": "README.md", "kind": "anchor", "line_number": 3, "target": "README.md#setup",
     "raw": "#setup", "confidence": 0.9},
    {"file_path": "docs/guide.md", "kind": "path", "line_number": 5, "target": "src/old.py",
     "raw": "src/old.py", "confidence": 0.9},
    {"file_path": "docs/other.md", "kind": "path", "line_number": 5,
     "target": "elsewhere/never.py", "raw": "elsewhere/never.py", "confidence": 0.9},
]


def _rows(dead_ids: list[str], **overrides):
    rows = {
        "anchor": _iso(),
        "head_sha": "head",
        "files": FILES,
        "fix_events": FIX_EVENTS,
        "health_findings": HEALTH_FINDINGS,
        "authors": COMMITS,
        "commit_health": COMMIT_HEALTH,
        "fix_first": {
            "metrics": FILES,
            "findings": HEALTH_FINDINGS,
            "refactoring": [],
            "performance": PERFORMANCE,
            "plans": [],
        },
        "security": SECURITY,
        "doc_drift": DOC_DRIFT,
        "known_paths": ["src/old.py"],
        "dead_code": [
            {"id": i, "kind": "unused_export", "file_path": "src/dead.py",
             "symbol_name": f"f{n}", "lines": 10, "safe_to_delete": True}
            for n, i in enumerate(dead_ids)
        ],
        "decisions": {"stale_decisions": [], "summary": {}},
        "coverage": {"files_measured": 0},
    }
    return {**rows, **overrides}


async def _dead_ids(session, rid: str) -> list[str]:
    return list(
        (
            await session.execute(
                select(DeadCodeFinding.id)
                .where(DeadCodeFinding.repository_id == rid)
                .order_by(DeadCodeFinding.symbol_name)
            )
        ).scalars()
    )


async def test_builder_on_rows_matches_the_sql_loader(async_session) -> None:
    rid = await _seed(async_session)
    loaded = await load_repo_facts(async_session, rid)
    rows = _rows(await _dead_ids(async_session, rid))
    # Rows the loader's reads never return: each builder drops them itself.
    rows["fix_events"] = [
        *FIX_EVENTS,
        {"file_path": "src/core.py", "fix_sha": "doc", "shape_kind": "docs",
         "attribution": "exact", "committed_at": _iso(timedelta(days=1))},
        {"file_path": "src/core.py", "fix_sha": "old", "shape_kind": "code_fix",
         "attribution": "exact", "committed_at": _iso(timedelta(days=200))},
    ]
    rows["health_findings"] = [
        *HEALTH_FINDINGS,
        {"file_path": "src/core.py", "biomarker_type": "long_method", "severity": "critical",
         "function_name": "gone", "health_impact": 9.0, "status": "resolved"},
    ]
    rows["fix_first"] = {
        **rows["fix_first"],
        "findings": rows["health_findings"],
        "performance": [
            *PERFORMANCE,
            {**PERFORMANCE[0], "opportunity_id": "perf4", "status": "resolved"},
        ],
    }
    rows["security"] = [*SECURITY, {**SECURITY[0], "commit_sha": "abc"},
                        {**SECURITY[0], "severity": "medium"}]
    rows["dead_code"] = [
        *rows["dead_code"],
        {"id": "unsafe", "kind": "unused_export", "file_path": "src/dead.py",
         "symbol_name": "g", "lines": 5, "safe_to_delete": False},
        {"id": "resolved", "kind": "unused_export", "file_path": "src/dead.py",
         "symbol_name": "h", "lines": 5, "safe_to_delete": True, "status": "resolved"},
        {"id": "in_tests", "kind": "unused_export", "file_path": "tests/helpers.py",
         "symbol_name": "k", "lines": 5, "safe_to_delete": True},
    ]
    built = build_repo_facts(**rows)

    assert built == loaded
    # The seed exercises every store, so equality is not over empty facts.
    assert built.files["src/core.py"].lead.biomarker == "complex_method"
    assert [f.file_path for f in built.recent_findings] == ["src/new.py"]
    assert [s.file_path for s in built.secrets] == ["src/settings.py"]
    assert [i.target.file_path for i in built.fix_first] == ["src/repo.py", "src/core.py", "src/new.py"]
    assert (built.fix_commits_90d, built.active_authors_90d, len(built.dead)) == (4, 3, 3)


def test_a_store_passed_as_none_is_unavailable() -> None:
    facts = build_repo_facts(**_rows([], fix_first=None, security=None))
    assert facts.unavailable == {"fix_first": ABSENT, "security": ABSENT}
    assert facts.fix_first == () and facts.secrets == ()
    assert facts.drift


def test_a_withheld_store_is_still_built_and_named() -> None:
    facts = build_repo_facts(**_rows([], unavailable={"files": "No fix history here."}))
    assert facts.unavailable == {"files": "No fix history here."}
    # Its facts still keep a test file out of the other stores.
    assert "src/core.py" in facts.files


def test_a_store_that_fails_to_build_costs_only_itself() -> None:
    facts = build_repo_facts(
        **_rows([], doc_drift=[{"confidence": "not a number"}]), absent_reason="Not here."
    )
    assert facts.unavailable == {"doc_drift": "Not here."}
    assert facts.secrets
