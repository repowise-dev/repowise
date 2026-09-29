"""The next-actions loader over a real (in-memory) store.

Seeds one small repository across the stores the rules read, then checks the
composed view, a missing store degrading to ``unavailable``, and the
per-person state round trip.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from repowise.core.persistence.crud.analysis.actions import (
    get_action_states,
    load_actions_view,
    set_action_state,
)
from repowise.core.persistence.models import (
    ActionState,
    DeadCodeFinding,
    DocDriftFinding,
    FixEvent,
    GitCommit,
    GitCommitFile,
    GitCommitHealthFinding,
    GitMetadata,
    HealthFileMetric,
    HealthFinding,
    PerformanceOpportunity,
    SecurityFinding,
)
from tests.unit.persistence.helpers import insert_repo

ANCHOR = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
NOW = ANCHOR + timedelta(days=1)


async def _seed(session) -> str:
    repo = await insert_repo(session)
    rid = repo.id
    add = session.add_all

    # Three active authors, the newest commit is the anchor.
    add(
        [
            GitCommit(repository_id=rid, sha="head", author_name="Ada", author_email="ada@x.io",
                      committed_at=ANCHOR, subject="chore: head"),
            GitCommit(repository_id=rid, sha="recent", author_name="Bob", author_email="bob@x.io",
                      committed_at=ANCHOR - timedelta(days=2), subject="feat: build step"),
            GitCommit(repository_id=rid, sha="older", author_name="Cy", author_email="cy@x.io",
                      committed_at=ANCHOR - timedelta(days=20), subject="fix: things"),
        ]
    )
    add([GitCommitFile(repository_id=rid, sha="older", file_path="src/old.py")])

    # A busy bug magnet with four fixes and a code-shape lead beside a
    # higher-impact history marker.
    add(
        [
            GitMetadata(repository_id=rid, file_path="src/core.py", commit_count_90d=12,
                        last_commit_at=ANCHOR - timedelta(days=1), bug_magnet=True),
            HealthFileMetric(repository_id=rid, file_path="src/core.py", max_ccn=12, nloc=300,
                             is_test=False),
            HealthFinding(repository_id=rid, file_path="src/core.py", biomarker_type="change_entropy",
                          severity="high", health_impact=3.0),
            HealthFinding(repository_id=rid, file_path="src/core.py", biomarker_type="complex_method",
                          severity="high", function_name="run", health_impact=1.0),
        ]
    )
    add(
        [
            FixEvent(repository_id=rid, fix_sha=f"fix{i}", file_path="src/core.py",
                     shape_kind="code_fix", attribution="exact",
                     committed_at=ANCHOR - timedelta(days=10 + i))
            for i in range(4)
        ]
    )

    # This week's commit wrote an open finding; two siblings must not count.
    add(
        [
            GitCommitHealthFinding(repository_id=rid, sha="recent", change_finding_id="c1",
                                   change_kind="introduced", dimension="defect",
                                   biomarker_type="complex_method", severity="high",
                                   file_path="src/new.py", symbol="build",
                                   attribution_basis="added_lines"),
            GitCommitHealthFinding(repository_id=rid, sha="recent", change_finding_id="c2",
                                   change_kind="introduced", dimension="defect",
                                   biomarker_type="long_method", severity="high",
                                   file_path="src/touched.py", symbol="x",
                                   attribution_basis="file_change"),
            GitCommitHealthFinding(repository_id=rid, sha="recent", change_finding_id="c3",
                                   change_kind="introduced", dimension="defect",
                                   biomarker_type="change_entropy", severity="high",
                                   file_path="src/hist.py", symbol=None,
                                   attribution_basis="added_lines"),
            HealthFinding(repository_id=rid, file_path="src/new.py", biomarker_type="complex_method",
                          severity="high", function_name="build", health_impact=1.0),
            HealthFinding(repository_id=rid, file_path="src/touched.py", biomarker_type="long_method",
                          severity="high", function_name="x", health_impact=1.0),
        ]
    )

    add(
        [
            PerformanceOpportunity(
                repository_id=rid, opportunity_id="perf1", status="open",
                execution_context="production", actionability_state="plan_ready",
                boundary_kind="db", biomarker_type="n_plus_one", file_path="src/repo.py",
                intervention_symbol="src/repo.py::Repo.load", affected_call_sites_total=4,
                affected_files_total=2,
                details_json=json.dumps(
                    {"facets": {"exposure": "entry_reachable", "loop_magnitude": "grows_with_data"},
                     "plan": {"effort_bucket": "S"}}
                ),
            ),
            PerformanceOpportunity(
                repository_id=rid, opportunity_id="perf2", status="open",
                execution_context="production", actionability_state="plan_ready",
                boundary_kind="db", file_path="src/other.py", affected_call_sites_total=9,
                details_json=json.dumps({"facets": {"exposure": "internal"}}),
            ),
        ]
    )

    add(
        [
            SecurityFinding(repository_id=rid, file_path="src/settings.py", kind="hardcoded_secret",
                            severity="high", snippet='TOKEN = "sk_live_123"', line_number=4,
                            commit_sha=""),
            SecurityFinding(repository_id=rid, file_path="tests/test_x.py", kind="hardcoded_secret",
                            severity="high", snippet='TOKEN = "fake"', line_number=4, commit_sha=""),
        ]
    )

    add(
        [
            DocDriftFinding(repository_id=rid, file_path="README.md", kind="anchor", line_number=3,
                            target="README.md#setup", raw="#setup", confidence=0.9),
            DocDriftFinding(repository_id=rid, file_path="docs/guide.md", kind="path", line_number=5,
                            target="src/old.py", raw="src/old.py", confidence=0.9),
            DocDriftFinding(repository_id=rid, file_path="docs/other.md", kind="path", line_number=5,
                            target="elsewhere/never.py", raw="elsewhere/never.py", confidence=0.9),
        ]
    )

    add(
        [
            DeadCodeFinding(repository_id=rid, kind="unused_export", file_path="src/dead.py",
                            symbol_name=f"f{i}", lines=10, safe_to_delete=True)
            for i in range(3)
        ]
    )
    await session.commit()
    return rid


def _quarter(view) -> list[dict]:
    return view["horizons"]["quarter"]["actions"]


def _by_rule(view, horizon: str = "quarter") -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for a in view["horizons"][horizon]["actions"]:
        out.setdefault(a["rule"], []).append(a)
    return out


async def test_load_actions_view_composes_every_store(async_session) -> None:
    rid = await _seed(async_session)
    view = await load_actions_view(async_session, rid, now=NOW)

    assert view["unavailable"] == {}
    assert view["context"]["active_authors_90d"] == 3
    assert view["context"]["fix_commits_90d"] == 4
    rules = _by_rule(view)

    (fragile,) = rules["fragile_file"]
    assert fragile["target"]["path"] == "src/core.py"
    assert fragile["title"].startswith("Add tests around `src/core.py`")
    # The code-shape finding leads, not the higher-impact history marker.
    assert fragile["marker"] == "complex_method"

    (perf,) = rules["hot_path_perf"]
    assert perf["title"] == "Batch the database calls loops make through `Repo.load`"
    assert perf["effort"] == "S"

    (secret,) = rules["live_secret"]
    assert secret["target"]["path"] == "src/settings.py"

    assert {a["target"]["path"] for a in rules["broken_doc_refs"]} == {"README.md", "docs/guide.md"}
    (dead,) = rules["dead_code_batch"]
    assert dead["evidence_total"] == 3

    week = _by_rule(view, "week")
    (fresh,) = week["fresh_regressions"]
    assert fresh["title"] == "Simplify `build` in `src/new.py` while the change is fresh"
    assert fresh["evidence_ids"] == ["recent"]


async def test_a_missing_store_is_reported_and_the_rest_still_load(async_session) -> None:
    rid = await _seed(async_session)
    await async_session.execute(text("DROP TABLE performance_opportunities"))
    await async_session.commit()

    view = await load_actions_view(async_session, rid, now=NOW)
    assert set(view["unavailable"]) == {"performance"}
    status = {r["rule"]: r["status"] for r in view["rules"]}
    assert status["hot_path_perf"] == "unavailable"
    assert status["live_secret"] == "evaluated"
    rules = _by_rule(view)
    assert "hot_path_perf" not in rules
    assert {"fragile_file", "live_secret", "broken_doc_refs"} <= set(rules)


async def test_action_state_round_trip(async_session) -> None:
    rid = await _seed(async_session)

    await set_action_state(async_session, rid, "act_x", state="dismissed", fingerprint="fp1")
    await async_session.commit()
    assert (await get_action_states(async_session, rid))["act_x"].state == "dismissed"

    until = NOW + timedelta(days=7)
    await set_action_state(async_session, rid, "act_x", state="snoozed", until=until)
    await async_session.commit()
    rec = (await get_action_states(async_session, rid))["act_x"]
    assert (rec.state, rec.fingerprint) == ("snoozed", "")
    assert rec.until.replace(tzinfo=None) == until.replace(tzinfo=None)
    rows = (await async_session.execute(select(ActionState))).scalars().all()
    assert len(rows) == 1

    await set_action_state(async_session, rid, "act_x", state=None)
    await async_session.commit()
    assert await get_action_states(async_session, rid) == {}


async def test_dismissed_action_returns_when_its_fingerprint_changes(async_session) -> None:
    rid = await _seed(async_session)
    view = await load_actions_view(async_session, rid, now=NOW)
    (secret,) = _by_rule(view)["live_secret"]

    await set_action_state(
        async_session, rid, secret["id"], state="dismissed", fingerprint=secret["fingerprint"]
    )
    await async_session.commit()
    view = await load_actions_view(async_session, rid, now=NOW)
    assert "live_secret" not in _by_rule(view)
    assert view["horizons"]["quarter"]["hidden"] == 1

    # A second secret in the same file changes the facts behind the action.
    async_session.add(
        SecurityFinding(repository_id=rid, file_path="src/settings.py", kind="hardcoded_secret",
                        severity="high", snippet='OTHER = "sk_live_456"', line_number=9,
                        commit_sha="")
    )
    await async_session.commit()
    view = await load_actions_view(async_session, rid, now=NOW)
    (back,) = _by_rule(view)["live_secret"]
    assert back["id"] == secret["id"]
    assert back["fingerprint"] != secret["fingerprint"]
