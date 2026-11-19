"""The evidence freshness block doctor, status and health read: newest coverage, newest run."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from repowise.core.evidence.status import load_evidence_freshness
from repowise.core.persistence.models import CoverageIngest, EvidenceRun
from tests.unit.persistence.helpers import insert_repo


async def test_freshness_block_reads_the_newest_coverage_and_run(async_session) -> None:
    rid = (await insert_repo(async_session)).id
    now = datetime(2026, 11, 19, tzinfo=UTC)
    empty = await load_evidence_freshness(async_session, rid, "head", now=now)
    assert empty["coverage"] == {
        "present": False, "commit_sha": None, "ingested_at": None, "age_days": None,
        "freshness": "unknown",
    }
    assert (empty["runtime"]["present"], empty["runtime"]["runs"]) == (False, 0)

    async_session.add(CoverageIngest(repository_id=rid, ingested_commit_sha="head",
                                     ingested_at=now - timedelta(days=2)))
    for run_id, sha, days in (("r1", "head", 9), ("r2", "old", 3)):
        async_session.add(
            EvidenceRun(id=run_id, repository_id=rid, run_key=run_id, kind="profile",
                        format="repowise-evidence-v1", source="cli", workload_name="cli",
                        workload_class="entry_point", commit_sha=sha,
                        ingested_at=now - timedelta(days=days))
        )
    await async_session.flush()
    block = await load_evidence_freshness(async_session, rid, "head", now=now)
    assert block["reference_commit"] == "head"
    cov, rt = block["coverage"], block["runtime"]
    assert (cov["present"], cov["commit_sha"], cov["age_days"], cov["freshness"]) == (
        True, "head", 2, "current"
    )
    # A run at HEAD decides, even when another commit's run was ingested later.
    assert (rt["commit_sha"], rt["age_days"], rt["freshness"], rt["runs"]) == ("head", 9, "current", 2)
    other = (await load_evidence_freshness(async_session, rid, "newer", now=now))["runtime"]
    assert (other["commit_sha"], other["freshness"]) == ("old", "stale")
    assert rt["ingested_at"].endswith("+00:00")
