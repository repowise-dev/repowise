"""Core surfaces built from rows in memory honour the layer x language gates."""

from __future__ import annotations

from types import SimpleNamespace

from repowise.core.analysis.actions.build import build_dead
from repowise.core.analysis.change_health.service import _limits
from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.generation.page_generator.helpers import build_dead_code_map


def _dead(path: str) -> dict:
    return {"id": path, "kind": "unused_export", "file_path": path, "symbol_name": "old",
            "lines": 5, "safe_to_delete": True, "status": "open"}


def test_actions_never_offer_a_gated_deletion():
    dead = build_dead([_dead("src/a.py"), _dead("src/A.cs"), _dead("src/a.cpp")], {})
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
    note = _limits([finding("src/a.py"), finding("src/A.java")])[-1]
    assert "java" in note
