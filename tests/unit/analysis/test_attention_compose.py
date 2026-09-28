"""The attention fold over plain records, with no session behind it."""

from __future__ import annotations

import pytest

from repowise.core.analysis.attention import (
    AREA_OF,
    AREA_ORDER,
    ATTENTION_ITEM_TYPES,
    PER_SOURCE_CAP,
    SOURCE_RANK,
    DecisionAttentionInput,
    SourceResult,
    compose_attention,
    decision_source,
    severity_of_file_score,
    silo_source,
)


def _health(path: str, severity: str, weight: float) -> dict:
    return {
        "id": f"health-{path}",
        "type": "health_finding",
        "title": path,
        "description": "",
        "severity": severity,
        "target_id": path,
        "subtype": "brain_method",
        "weight": weight,
    }


def test_composes_from_plain_records() -> None:
    view = compose_attention(
        {
            "health_finding": SourceResult(
                (_health("src/a.py", "critical", 9.0), _health("src/b.py", "low", 0.5)), 40
            ),
            "decisions": decision_source(
                [DecisionAttentionInput(id="d1", title="Stale one")], [], ["src/hot.py"]
            ),
            "knowledge_silo": silo_source(
                [{"file_path": "src/a.py", "owner_pct": 0.9, "commit_count_90d": 3}]
            ),
        }
    )

    assert [i["id"] for i in view["items"]] == [
        "health-src/a.py",
        "stale-d1",
        "ungoverned-src/hot.py",
        "silo-src/a.py",
        "health-src/b.py",
    ]
    assert view["by_source"] == {"health_finding": 40, "decisions": 2, "knowledge_silo": 1}
    assert view["total"] == 43
    decisions = next(a for a in view["areas"] if a["key"] == "decisions")
    assert decisions["detail"] == "1 drifting from the code · 1 hotspots ungoverned"


def test_total_comes_from_the_producer_not_the_items() -> None:
    """A producer that capped its rows still reports the whole store."""
    view = compose_attention({"dead_code": SourceResult((), 12)})
    assert view["by_source"] == {"dead_code": 12}
    assert view["areas"][0]["lead"] is None


def test_decision_lanes_truncate_without_reordering() -> None:
    n = PER_SOURCE_CAP + 3
    records = [DecisionAttentionInput(id=f"d{i}", title=f"t{i}") for i in range(n)]
    paths = [f"src/{i}.py" for i in range(n)]
    result = decision_source(records, records, paths)
    kept = [d.id for d in records[:PER_SOURCE_CAP]]
    by_type: dict[str, list[str]] = {}
    for item in result.items:
        by_type.setdefault(item["type"], []).append(item["target_id"])
    assert by_type == {
        "stale_decision": kept,
        "proposed_decision": kept,
        "ungoverned_hotspot": paths[:PER_SOURCE_CAP],
    }
    assert result.total == 3 * n


def test_every_item_type_is_ranked_and_rolled_up() -> None:
    """A type missing here would rank last or roll into an area never rendered."""
    assert set(SOURCE_RANK) == set(AREA_OF) == ATTENTION_ITEM_TYPES
    assert set(AREA_OF.values()) == set(AREA_ORDER)


@pytest.mark.parametrize(
    ("score", "severity"),
    [(1.0, "critical"), (4.5, "high"), (6.0, "medium"), (7.5, "low"), (9.0, "low")],
)
def test_file_score_maps_every_band(score: float, severity: str) -> None:
    assert severity_of_file_score(score) == severity
