"""``GitMetadataResponse.from_orm`` must survive a malformed partners cell.

The column is written by the indexer and read here with no validation in
between, so one bad row used to fail the whole response.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

from repowise.server.schemas.git import GitMetadataResponse


def _row(co_change_partners_json: str) -> SimpleNamespace:
    return SimpleNamespace(
        file_path="a.py",
        commit_count_total=3,
        commit_count_90d=2,
        commit_count_30d=1,
        first_commit_at=datetime(2026, 1, 1, tzinfo=UTC),
        last_commit_at=datetime(2026, 2, 1, tzinfo=UTC),
        primary_owner_name=None,
        primary_owner_email=None,
        primary_owner_commit_pct=None,
        recent_owner_name=None,
        recent_owner_commit_pct=None,
        top_authors_json="[]",
        significant_commits_json="[]",
        co_change_partners_json=co_change_partners_json,
        is_hotspot=False,
        is_stable=True,
        churn_percentile=0.5,
        age_days=10,
        bus_factor=1,
        contributor_count=1,
        lines_added_90d=0,
        lines_deleted_90d=0,
        avg_commit_size=1.0,
        commit_categories_json="{}",
        merge_commit_count_90d=0,
        change_entropy=0.0,
        change_entropy_pct=0.0,
        prior_defect_count=0,
        fix_symbol_counts_json="{}",
        bug_magnet=False,
        last_fix_at=None,
        temporal_hotspot_score=None,
        commit_count_capped=False,
        original_path=None,
        test_gap=None,
        agent_commit_count=0,
        agent_authored_pct=None,
        agent_tier_counts_json="{}",
    )


def test_well_formed_partners_pass_through() -> None:
    raw = json.dumps([{"file_path": "b.py", "co_change_count": 4.25}])
    out = GitMetadataResponse.from_orm(_row(raw))
    assert out.co_change_partners == [{"file_path": "b.py", "co_change_count": 4.25}]


def test_malformed_partners_yield_an_empty_list_not_an_error() -> None:
    assert GitMetadataResponse.from_orm(_row("not json")).co_change_partners == []


def test_non_record_elements_are_dropped() -> None:
    raw = json.dumps(["b.py", {"file_path": "c.py"}])
    assert GitMetadataResponse.from_orm(_row(raw)).co_change_partners == [{"file_path": "c.py"}]
