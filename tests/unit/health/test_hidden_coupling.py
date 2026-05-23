"""Tests for the ``hidden_coupling`` biomarker."""

from __future__ import annotations

import json

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.hidden_coupling import (
    HiddenCouplingDetector,
)
from repowise.core.analysis.health.models import Severity


class _FakeGraph:
    """Minimal ``HasEdge`` fake for unit tests."""

    def __init__(self, edges: set[tuple[str, str, str]] | None = None) -> None:
        # (src, dst, edge_type)
        self._edges = edges or set()

    def has_edge(self, src: str, dst: str, key: str = "imports") -> bool:
        return (src, dst, key) in self._edges


def _partners(d: dict[str, int]) -> str:
    return json.dumps([{"file_path": p, "co_change_count": c} for p, c in d.items()])


def _ctx(
    path: str,
    *,
    partners: dict[str, int],
    self_commits: int,
    repo_commits: dict[str, int],
    graph: _FakeGraph | None = None,
) -> FileContext:
    repo_commits = {**repo_commits, path: self_commits}
    return FileContext(
        file_path=path,
        language="python",
        nloc=120,
        has_test_file=False,
        module=None,
        function_metrics={},
        git_meta={
            "commit_count_total": self_commits,
            "co_change_partners_json": _partners(partners),
        },
        dependents_count=0,
        graph_view=graph,
        repo_commit_counts=repo_commits,
    )


def test_positive_python_pair_no_import_edge():
    ctx = _ctx(
        "src/payments.py",
        partners={"src/billing.py": 18},
        self_commits=20,
        repo_commits={"src/billing.py": 22},
    )
    out = HiddenCouplingDetector().detect(ctx)
    assert len(out) == 1
    assert out[0].details["partner"] == "src/billing.py"
    # 18 / min(20, 22) = 0.9 → CRITICAL
    assert out[0].severity == Severity.CRITICAL


def test_positive_ts_pair_at_medium():
    ctx = _ctx(
        "web/checkout.ts",
        partners={"web/cart.ts": 6},
        self_commits=10,
        repo_commits={"web/cart.ts": 12},
    )
    out = HiddenCouplingDetector().detect(ctx)
    assert len(out) == 1
    # 6 / min(10, 12) = 0.6 → HIGH (>= 0.5, < 0.65 is MEDIUM; 0.6 -> MEDIUM)
    assert out[0].severity == Severity.MEDIUM


def test_negative_explicit_import_edge_suppresses():
    graph = _FakeGraph({("src/payments.py", "src/billing.py", "imports")})
    ctx = _ctx(
        "src/payments.py",
        partners={"src/billing.py": 18},
        self_commits=20,
        repo_commits={"src/billing.py": 22},
        graph=graph,
    )
    assert HiddenCouplingDetector().detect(ctx) == []


def test_negative_low_commit_count_noise_floor():
    ctx = _ctx(
        "src/payments.py",
        partners={"src/billing.py": 3},
        self_commits=4,  # below MIN_COMMITS=5
        repo_commits={"src/billing.py": 22},
    )
    assert HiddenCouplingDetector().detect(ctx) == []


def test_negative_partner_below_noise_floor():
    ctx = _ctx(
        "src/payments.py",
        partners={"src/billing.py": 3},
        self_commits=20,
        repo_commits={"src/billing.py": 3},
    )
    assert HiddenCouplingDetector().detect(ctx) == []


def test_essential_tier_empty_partners_short_circuits():
    """Plan §1.2.1: ESSENTIAL git tier leaves co_change_partners_json empty."""
    ctx = FileContext(
        file_path="src/payments.py",
        language="python",
        nloc=120,
        has_test_file=False,
        module=None,
        function_metrics={},
        git_meta={"commit_count_total": 100, "co_change_partners_json": "[]"},
        dependents_count=0,
        repo_commit_counts={"src/payments.py": 100, "src/billing.py": 100},
    )
    assert HiddenCouplingDetector().detect(ctx) == []
    # Also defend the literal absence of the field.
    ctx.git_meta = {"commit_count_total": 100}
    assert HiddenCouplingDetector().detect(ctx) == []


def test_test_to_production_pair_is_filtered():
    ctx = _ctx(
        "src/__tests__/cart.test.ts",
        partners={"src/cart.ts": 18},
        self_commits=20,
        repo_commits={"src/cart.ts": 22},
    )
    assert HiddenCouplingDetector().detect(ctx) == []


def test_findings_capped_at_top_three_partners():
    partners = {
        "src/a.py": 18,
        "src/b.py": 17,
        "src/c.py": 16,
        "src/d.py": 15,
        "src/e.py": 14,
    }
    repo_commits = {p: 20 for p in partners}
    ctx = _ctx(
        "src/payments.py",
        partners=partners,
        self_commits=20,
        repo_commits=repo_commits,
    )
    out = HiddenCouplingDetector().detect(ctx)
    assert len(out) == 3
    # Sorted by correlation desc — top three are the highest counts.
    assert [f.details["partner"] for f in out] == ["src/a.py", "src/b.py", "src/c.py"]


def test_pair_dedupes_naturally_by_frozenset():
    """Each side of a pair emits independently; the union dedupes via
    ``frozenset({a, b})`` at the caller level. Verifies symmetry."""
    a = _ctx(
        "src/a.py",
        partners={"src/b.py": 18},
        self_commits=20,
        repo_commits={"src/b.py": 22},
    )
    b = _ctx(
        "src/b.py",
        partners={"src/a.py": 18},
        self_commits=22,
        repo_commits={"src/a.py": 20},
    )
    out_a = HiddenCouplingDetector().detect(a)
    out_b = HiddenCouplingDetector().detect(b)
    pairs = {
        frozenset({a.file_path, out_a[0].details["partner"]}),
        frozenset({b.file_path, out_b[0].details["partner"]}),
    }
    assert pairs == {frozenset({"src/a.py", "src/b.py"})}
