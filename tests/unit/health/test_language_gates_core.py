"""Core surfaces built from rows in memory honour the layer x language gates.

They run on the audit's gated table (no shipped row is gated), so the gated
state stays covered."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.core.analysis.actions.build import build_dead
from repowise.core.analysis.change_health.service import _limits
from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.generation.page_generator.helpers import build_dead_code_map
from tests.unit.server.mcp.test_language_gates import AUDIT_GATED


@pytest.fixture(autouse=True)
def _audit_gated(monkeypatch):
    from repowise.core.analysis import finding_registry

    monkeypatch.setattr(finding_registry, "LANGUAGE_GATES", AUDIT_GATED)


def _dead(path: str) -> dict:
    return {"id": path, "kind": "unused_export", "file_path": path, "symbol_name": "old",
            "lines": 5, "confidence": 0.9, "safe_to_delete": True, "status": "open"}


def test_actions_never_offer_a_gated_deletion():
    rows = [_dead(p) for p in ("src/a.py", "src/A.cs", "src/a.cpp", "app/A.kt", "App/A.swift")]
    dead = build_dead(rows, {})
    assert [d.file_path for d in dead["dead"]] == ["src/a.py"]


def test_wiki_prompts_never_carry_gated_dead_code():
    finding = SimpleNamespace(
        kind="unreachable_file", reason="no imports", confidence=0.9, safe_to_delete=True,
        symbol_name=None, symbol_kind=None,
    )
    report = SimpleNamespace(
        findings=[
            SimpleNamespace(**vars(finding), file_path="src/Form.cs"),
            SimpleNamespace(**vars(finding), file_path="src/form.py"),
        ]
    )
    assert set(build_dead_code_map(report)) == {"src/form.py"}


def test_change_review_says_when_gated_findings_were_not_compared():
    def finding(path: str) -> HealthFindingData:
        return HealthFindingData("complex_method", Severity.HIGH, path, "f", 1, 9, {}, 1.0)

    assert len(_limits([finding("src/a.py")])) == 1
    note = _limits([finding("src/a.py"), finding("src/A.java"), finding("src/A.cs")])[-1]
    assert "C#, Java files" in note and "pending verification" in note
    assert "precision" not in note
    # Kotlin is gated for dead code only, so its health findings are compared.
    assert len(_limits([finding("app/A.kt")])) == 1
