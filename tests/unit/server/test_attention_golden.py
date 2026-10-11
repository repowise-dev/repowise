"""Golden output of ``build_attention`` with every source populated.

Pins items, order, totals, details and areas exactly, so moving the fold
between packages cannot change what Overview serves. Rewrite with
``REPOWISE_REWRITE_GOLDEN=1`` only for an intended behaviour change.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import (
    DeadCodeFinding,
    DocDriftFinding,
    RefactoringSuggestion,
    SecurityFinding,
)
from repowise.server.services import attention
from repowise.server.services.attention import build_attention
from tests.unit.server.conftest import create_test_repo

GOLDEN = Path(__file__).parent / "golden" / "attention_build.json"
_AT = datetime(2026, 9, 1, tzinfo=UTC)


async def _seed(session_factory, repo_id: str) -> None:
    async with get_session(session_factory) as session:
        await crud.save_health_metrics(
            session,
            repo_id,
            [
                {"file_path": "tests/test_core.py", "nloc": 50, "is_test": True},
                {"file_path": "src/core.py", "nloc": 400, "is_test": False},
            ],
        )
        await crud.save_health_findings(
            session,
            repo_id,
            [
                {
                    "file_path": path,
                    "biomarker_type": biomarker,
                    "severity": "high",
                    "health_impact": impact,
                    "dimension": "defect",
                }
                for path, biomarker, impact in [
                    ("src/core.py", "brain_method", 4.0),
                    ("src/core.py", "god_class", 5.5),
                    ("src/util.py", "nested_complexity", 3.0),
                    ("src/tiny.py", "long_method", 0.4),
                    ("tests/test_core.py", "mock_saturation", 9.0),
                ]
            ],
        )
        session.add_all(
            [
                SecurityFinding(
                    id=1,
                    repository_id=repo_id,
                    file_path="config/settings.py",
                    kind="aws_access_key",
                    severity="high",
                    snippet="AKIA****",
                    line_number=12,
                    commit_sha="",
                    detected_at=_AT,
                ),
                SecurityFinding(
                    id=2,
                    repository_id=repo_id,
                    file_path="old/keys.py",
                    kind="hardcoded_secret",
                    severity="high",
                    snippet="",
                    line_number=3,
                    commit_sha="abcdef1234567",
                    detected_at=_AT,
                ),
                SecurityFinding(
                    id=3,
                    repository_id=repo_id,
                    file_path="src/run.py",
                    kind="eval_call",
                    severity="low",
                    snippet="eval(x)",
                    line_number=7,
                    commit_sha="",
                    detected_at=_AT,
                ),
                DocDriftFinding(
                    id=1,
                    repository_id=repo_id,
                    file_path="README.md",
                    kind="missing_path",
                    line_number=3,
                    target="docs/gone.md",
                    confidence=0.95,
                    reason="names a file that is not in the tree",
                    detected_at=_AT,
                ),
                DocDriftFinding(
                    id=2,
                    repository_id=repo_id,
                    file_path="docs/setup.md",
                    kind="missing_symbol",
                    line_number=9,
                    target="old_fn",
                    confidence=0.5,
                    reason="",
                    detected_at=_AT,
                ),
                RefactoringSuggestion(
                    id="ref1",
                    repository_id=repo_id,
                    refactoring_type="extract_method",
                    file_path="src/core.py",
                    target_symbol="Engine.run",
                    impact_delta=1.8,
                    effort_bucket="M",
                ),
                RefactoringSuggestion(
                    id="ref2",
                    repository_id=repo_id,
                    refactoring_type="split_class",
                    file_path="src/util.py",
                    target_symbol="",
                    impact_delta=0.6,
                    effort_bucket="",
                ),
                DeadCodeFinding(
                    id="dead1",
                    repository_id=repo_id,
                    kind="unused_export",
                    file_path="src/legacy.py",
                    symbol_name="old_helper",
                    symbol_kind="function",
                    confidence=0.9,
                    lines=12,
                    safe_to_delete=True,
                ),
                DeadCodeFinding(
                    id="dead2",
                    repository_id=repo_id,
                    kind="unreachable_file",
                    file_path="src/orphan.py",
                    symbol_name=None,
                    symbol_kind=None,
                    confidence=0.7,
                    lines=40,
                    safe_to_delete=True,
                ),
            ]
        )
        await session.commit()


_DECISION_HEALTH = {
    "stale_decisions": [
        SimpleNamespace(id="d-stale-1", title="Use SQLite for the local store"),
        SimpleNamespace(id="d-stale-2", title="One worker per repo"),
    ],
    "proposed_awaiting_review": [SimpleNamespace(id="d-prop-1", title="Cache the graph")],
    "ungoverned_hotspots": ["src/core.py", "src/api.py"],
}

_SILOS = [
    {"file_path": "src/core.py", "owner_pct": 0.9, "commit_count_90d": 25},
    {"file_path": "docker/entry.sh", "owner_pct": 1.0, "commit_count_90d": 0},
]


@pytest.mark.anyio
async def test_build_attention_matches_the_golden(
    client: AsyncClient, session_factory
) -> None:
    repo = await create_test_repo(client)
    await _seed(session_factory, repo["id"])

    async with get_session(session_factory) as session:
        result = await build_attention(
            session,
            repo["id"],
            decision_health=_DECISION_HEALTH,
            knowledge_silos=_SILOS,
        )

    if os.environ.get("REPOWISE_REWRITE_GOLDEN"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
        pytest.skip("golden rewritten")
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert result == golden
    # Dict equality ignores order; the wire keeps it.
    assert list(result["by_source"]) == list(golden["by_source"])


@pytest.mark.anyio
async def test_a_source_that_raises_is_dropped_with_its_count(
    client: AsyncClient, session_factory, monkeypatch
) -> None:
    async def boom(session, repo_id):
        raise RuntimeError("table missing")

    fetchers = tuple(
        (key, boom if key == "security_finding" else fetch) for key, fetch in attention._FETCHERS
    )
    monkeypatch.setattr(attention, "_FETCHERS", fetchers)
    repo = await create_test_repo(client)
    await _seed(session_factory, repo["id"])

    async with get_session(session_factory) as session:
        result = await build_attention(
            session, repo["id"], decision_health=_DECISION_HEALTH, knowledge_silos=_SILOS
        )

    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert "security_finding" not in result["by_source"]
    assert result["by_source"] == {
        k: v for k, v in golden["by_source"].items() if k != "security_finding"
    }
    assert all(i["type"] != "security_finding" for i in result["items"])
