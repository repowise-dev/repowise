"""Regression: ``repowise export --format json --full`` must not crash on dead code."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from repowise.core.persistence.models import DeadCodeFinding


def test_dead_code_finding_model_exposes_kind_not_finding_type():
    """ORM contract the full-JSON export must follow."""
    assert hasattr(DeadCodeFinding, "kind")
    assert not hasattr(DeadCodeFinding, "finding_type")


def test_full_export_dead_code_projection_uses_kind():
    """Projection mirrors export_cmd: read ``kind``, never ``finding_type``."""
    row = SimpleNamespace(
        file_path="pkg/orphan.py",
        symbol_name="unused",
        kind="unused_export",
        confidence=0.9,
        safe_to_delete=True,
    )
    out = {
        "file_path": row.file_path,
        "symbol_name": row.symbol_name,
        "kind": row.kind,
        "confidence": row.confidence,
        "safe_to_delete": row.safe_to_delete,
    }
    assert out["kind"] == "unused_export"


def test_export_cmd_source_does_not_reference_finding_type():
    src = (
        Path(__file__).resolve().parents[3]
        / "packages/cli/src/repowise/cli/commands/export_cmd.py"
    ).read_text(encoding="utf-8")
    assert "f.finding_type" not in src
    assert "f.kind" in src
