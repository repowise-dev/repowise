"""Unit tests for the governance findings pure function.

``build_governance_findings`` is a pure function over in-memory data — no
database needed.  Tests use simple mock dataclasses that duck-type
``DecisionRecord`` attributes accessed by the function.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from repowise.core.analysis.health.governance import build_governance_findings
from repowise.core.analysis.health.models import Severity

# ---------------------------------------------------------------------------
# Minimal stub for DecisionRecord (duck-typing the attributes we read)
# ---------------------------------------------------------------------------


@dataclass
class _Decision:
    id: str
    title: str
    status: str = "active"
    affected_files_json: str = "[]"
    staleness_score: float = 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _summary(
    *,
    ungoverned_hotspots: list[str] | None = None,
    stale_decisions: list | None = None,
    conflicts: list[dict] | None = None,
) -> dict:
    return {
        "ungoverned_hotspots": ungoverned_hotspots or [],
        "stale_decisions": stale_decisions or [],
        "conflicts": conflicts or [],
    }


# ---------------------------------------------------------------------------
# ungoverned_hotspot findings
# ---------------------------------------------------------------------------


def test_ungoverned_hotspot_one_per_path():
    summary = _summary(ungoverned_hotspots=["src/app.py", "src/db.py"])
    findings = build_governance_findings(health_summary=summary, decisions=[])

    types = [f.biomarker_type for f in findings]
    assert types.count("ungoverned_hotspot") == 2
    paths = {f.file_path for f in findings if f.biomarker_type == "ungoverned_hotspot"}
    assert paths == {"src/app.py", "src/db.py"}


def test_ungoverned_hotspot_severity_is_medium():
    summary = _summary(ungoverned_hotspots=["src/main.py"])
    findings = build_governance_findings(health_summary=summary, decisions=[])
    hit = next(f for f in findings if f.biomarker_type == "ungoverned_hotspot")
    assert hit.severity == Severity.MEDIUM


def test_ungoverned_hotspot_details_has_is_hotspot():
    summary = _summary(ungoverned_hotspots=["src/main.py"])
    findings = build_governance_findings(health_summary=summary, decisions=[])
    hit = next(f for f in findings if f.biomarker_type == "ungoverned_hotspot")
    assert hit.details.get("is_hotspot") is True


def test_ungoverned_hotspot_health_impact_nonzero():
    summary = _summary(ungoverned_hotspots=["src/main.py"])
    findings = build_governance_findings(health_summary=summary, decisions=[])
    hit = next(f for f in findings if f.biomarker_type == "ungoverned_hotspot")
    assert hit.health_impact > 0


def test_ungoverned_hotspot_empty():
    summary = _summary(ungoverned_hotspots=[])
    findings = build_governance_findings(health_summary=summary, decisions=[])
    assert not any(f.biomarker_type == "ungoverned_hotspot" for f in findings)


# ---------------------------------------------------------------------------
# stale_governance findings
# ---------------------------------------------------------------------------


def test_stale_governance_emits_finding_per_file():
    d = _Decision(
        id="d1",
        title="Use JWT for auth",
        staleness_score=0.8,
        affected_files_json=json.dumps(["src/auth.py", "src/middleware.py"]),
    )
    summary = _summary(stale_decisions=[d])
    findings = build_governance_findings(health_summary=summary, decisions=[d])

    sg = [f for f in findings if f.biomarker_type == "stale_governance"]
    paths = {f.file_path for f in sg}
    assert "src/auth.py" in paths
    assert "src/middleware.py" in paths


def test_stale_governance_severity_is_high():
    d = _Decision(
        id="d1",
        title="JWT",
        staleness_score=0.9,
        affected_files_json=json.dumps(["src/auth.py"]),
    )
    summary = _summary(stale_decisions=[d])
    findings = build_governance_findings(health_summary=summary, decisions=[d])
    hit = next(f for f in findings if f.biomarker_type == "stale_governance")
    assert hit.severity == Severity.HIGH


def test_stale_governance_dedup_keeps_highest_staleness():
    """When two stale decisions govern the same file, keep the higher-staleness one."""
    d_low = _Decision(
        id="d_low",
        title="Decision Low",
        staleness_score=0.5,
        affected_files_json=json.dumps(["src/shared.py"]),
    )
    d_high = _Decision(
        id="d_high",
        title="Decision High",
        staleness_score=0.95,
        affected_files_json=json.dumps(["src/shared.py"]),
    )
    summary = _summary(stale_decisions=[d_low, d_high])
    findings = build_governance_findings(health_summary=summary, decisions=[d_low, d_high])

    sg = [f for f in findings if f.biomarker_type == "stale_governance"]
    # Only one finding for the file (deduped)
    file_hits = [f for f in sg if f.file_path == "src/shared.py"]
    assert len(file_hits) == 1
    assert file_hits[0].details["decision_id"] == "d_high"


def test_stale_governance_details_has_expected_keys():
    d = _Decision(
        id="d42",
        title="Microservices split",
        staleness_score=0.7,
        affected_files_json=json.dumps(["svc/orders.py"]),
    )
    summary = _summary(stale_decisions=[d])
    findings = build_governance_findings(health_summary=summary, decisions=[d])
    hit = next(f for f in findings if f.biomarker_type == "stale_governance")
    assert hit.details["decision_id"] == "d42"
    assert hit.details["decision_title"] == "Microservices split"
    assert "staleness_score" in hit.details


def test_stale_governance_caps_files():
    """No more than _MAX_FILES_PER_DECISION=10 findings per decision."""
    files = [f"src/file_{i}.py" for i in range(25)]
    d = _Decision(
        id="d_big",
        title="Big Decision",
        staleness_score=0.8,
        affected_files_json=json.dumps(files),
    )
    summary = _summary(stale_decisions=[d])
    findings = build_governance_findings(health_summary=summary, decisions=[d])
    sg = [f for f in findings if f.biomarker_type == "stale_governance"]
    # Capped at 10
    assert len(sg) <= 10


# ---------------------------------------------------------------------------
# contradictory_decision findings
# ---------------------------------------------------------------------------


def test_contradictory_decision_emits_for_affected_files():
    d_src = _Decision(
        id="src1",
        title="Use REST",
        affected_files_json=json.dumps(["api/rest.py"]),
    )
    d_dst = _Decision(
        id="dst1",
        title="Use GraphQL",
        affected_files_json=json.dumps(["api/graphql.py"]),
    )
    conflict = {
        "src": {"id": "src1", "title": "Use REST", "status": "active"},
        "dst": {"id": "dst1", "title": "Use GraphQL", "status": "active"},
        "confidence": 0.8,
        "evidence": "",
    }
    summary = _summary(conflicts=[conflict])
    findings = build_governance_findings(health_summary=summary, decisions=[d_src, d_dst])
    cd = [f for f in findings if f.biomarker_type == "contradictory_decision"]
    paths = {f.file_path for f in cd}
    assert "api/rest.py" in paths
    assert "api/graphql.py" in paths


def test_contradictory_decision_severity_is_high():
    d_src = _Decision(id="s1", title="A", affected_files_json=json.dumps(["x.py"]))
    d_dst = _Decision(id="d1", title="B", affected_files_json=json.dumps(["y.py"]))
    conflict = {
        "src": {"id": "s1", "title": "A", "status": "active"},
        "dst": {"id": "d1", "title": "B", "status": "active"},
        "confidence": 0.9,
        "evidence": "",
    }
    summary = _summary(conflicts=[conflict])
    findings = build_governance_findings(health_summary=summary, decisions=[d_src, d_dst])
    cd = [f for f in findings if f.biomarker_type == "contradictory_decision"]
    assert all(f.severity == Severity.HIGH for f in cd)


def test_contradictory_decision_no_duplicates_same_pair():
    """Same (file, src_id, dst_id) triple must not be emitted twice."""
    shared_file = "shared/utils.py"
    d_src = _Decision(id="s1", title="A", affected_files_json=json.dumps([shared_file]))
    d_dst = _Decision(id="d1", title="B", affected_files_json=json.dumps([shared_file]))
    conflict = {
        "src": {"id": "s1", "title": "A", "status": "active"},
        "dst": {"id": "d1", "title": "B", "status": "active"},
        "confidence": 0.9,
        "evidence": "",
    }
    summary = _summary(conflicts=[conflict])
    findings = build_governance_findings(health_summary=summary, decisions=[d_src, d_dst])
    cd = [
        f
        for f in findings
        if f.biomarker_type == "contradictory_decision" and f.file_path == shared_file
    ]
    assert len(cd) == 1


def test_contradictory_decision_details_has_expected_keys():
    d_src = _Decision(id="s1", title="Use REST", affected_files_json=json.dumps(["api/main.py"]))
    d_dst = _Decision(id="d1", title="Use GraphQL", affected_files_json=json.dumps([]))
    conflict = {
        "src": {"id": "s1", "title": "Use REST", "status": "active"},
        "dst": {"id": "d1", "title": "Use GraphQL", "status": "active"},
        "confidence": 0.7,
        "evidence": "",
    }
    summary = _summary(conflicts=[conflict])
    findings = build_governance_findings(health_summary=summary, decisions=[d_src, d_dst])
    cd = next(f for f in findings if f.biomarker_type == "contradictory_decision")
    for key in ("src_decision_id", "dst_decision_id", "src_title", "dst_title"):
        assert key in cd.details, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# Empty / all-clear
# ---------------------------------------------------------------------------


def test_empty_summary_produces_no_findings():
    findings = build_governance_findings(health_summary=_summary(), decisions=[])
    assert findings == []


def test_deterministic_output_same_inputs():
    """Same inputs → same output (order and count)."""
    d = _Decision(
        id="d1",
        title="JWT",
        staleness_score=0.8,
        affected_files_json=json.dumps(["src/auth.py"]),
    )
    summary = _summary(
        ungoverned_hotspots=["src/main.py"],
        stale_decisions=[d],
    )
    a = build_governance_findings(health_summary=summary, decisions=[d])
    b = build_governance_findings(health_summary=summary, decisions=[d])
    assert len(a) == len(b)
    assert [f.biomarker_type for f in a] == [f.biomarker_type for f in b]
