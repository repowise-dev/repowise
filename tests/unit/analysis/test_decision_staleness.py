from __future__ import annotations

from datetime import UTC, datetime, timedelta

from repowise.core.analysis.decision_extractor import DecisionExtractor


def test_compute_staleness_accepts_sqlite_naive_created_at() -> None:
    created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=30)
    last_commit_at = datetime.now(UTC) - timedelta(days=1)

    score = DecisionExtractor.compute_staleness(
        decision_created_at=created_at,
        affected_files=["src/app.py"],
        git_meta_map={
            "src/app.py": {
                "last_commit_at": last_commit_at,
                "commit_count_90d": 6,
            }
        },
    )

    assert score > 0
