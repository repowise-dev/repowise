"""Tests for the ``hidden_coupling`` biomarker."""

from __future__ import annotations

import json

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.hidden_coupling import (
    HiddenCouplingDetector,
)
from repowise.core.analysis.health.models import Severity
from repowise.core.co_change import (
    STRUCTURAL_CORROBORATED,
    STRUCTURAL_NOT_APPLICABLE,
    STRUCTURAL_UNEXPLAINED,
)


def _partners(
    d: dict[str, int],
    *,
    self_commits: int,
    repo_commits: dict[str, int],
    structural: str = STRUCTURAL_UNEXPLAINED,
) -> str:
    return json.dumps(
        [
            {
                "file_path": p,
                "co_change_count": float(support),
                "frequency": support,
                "self_commits": self_commits,
                "partner_commits": repo_commits.get(p, 0),
                "structural": structural,
            }
            for p, support in d.items()
        ]
    )


def _ctx(
    path: str,
    *,
    partners: dict[str, int],
    self_commits: int,
    repo_commits: dict[str, int],
    structural: str = STRUCTURAL_UNEXPLAINED,
) -> FileContext:
    return FileContext(
        file_path=path,
        language="python",
        nloc=120,
        has_test_file=False,
        module=None,
        function_metrics={},
        git_meta={
            "commit_count_total": self_commits,
            "co_change_partners_json": _partners(
                partners,
                self_commits=self_commits,
                repo_commits=repo_commits,
                structural=structural,
            ),
        },
        dependents_count=0,
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
    # 18 / min(20, 22) = 0.9 -> CRITICAL
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
    # 6 / min(10, 12) = 0.6, below the HIGH band.
    assert out[0].severity == Severity.MEDIUM


def test_negative_corroborated_pair_suppresses():
    """A pair the dependency graph already accounts for is not a finding."""
    ctx = _ctx(
        "src/payments.py",
        partners={"src/billing.py": 18},
        self_commits=20,
        repo_commits={"src/billing.py": 22},
        structural=STRUCTURAL_CORROBORATED,
    )
    assert HiddenCouplingDetector().detect(ctx) == []


def test_pair_outside_the_graph_is_not_a_finding():
    """A lockfile against a manifest co-changes constantly and imports nothing.

    Neither is a graph node, so there is no edge to look for and the absence of
    one says nothing. Scoring it would put release plumbing at the top of the
    list ahead of every real pair.
    """
    ctx = _ctx(
        "pyproject.toml",
        partners={"uv.lock": 40},
        self_commits=42,
        repo_commits={"uv.lock": 41},
        structural=STRUCTURAL_NOT_APPLICABLE,
    )
    assert HiddenCouplingDetector().detect(ctx) == []


def test_unlabelled_partner_is_not_a_finding():
    """An index written before the label existed cannot claim a pair is hidden."""
    ctx = FileContext(
        file_path="src/payments.py",
        language="python",
        nloc=120,
        has_test_file=False,
        module=None,
        function_metrics={},
        git_meta={
            "commit_count_total": 20,
            "co_change_partners_json": json.dumps(
                [
                    {
                        "file_path": "src/billing.py",
                        "co_change_count": 18.0,
                        "frequency": 18,
                        "self_commits": 20,
                        "partner_commits": 22,
                    }
                ]
            ),
        },
        dependents_count=0,
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
    """The ESSENTIAL git tier leaves co_change_partners_json empty."""
    ctx = FileContext(
        file_path="src/payments.py",
        language="python",
        nloc=120,
        has_test_file=False,
        module=None,
        function_metrics={},
        git_meta={"commit_count_total": 100, "co_change_partners_json": "[]"},
        dependents_count=0,
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


def test_test_support_is_test_material_when_paired_with_production():
    """``conftest.py`` against a production file is an expected pairing.

    Both ends go through the one shared classifier, which counts fixture plugins
    as test material. Two *test-material* files are a different case and still
    score -- the rule only skips the asymmetric pairing.
    """
    ctx = _ctx(
        "tests/conftest.py",
        partners={"src/cart.py": 18},
        self_commits=20,
        repo_commits={"src/cart.py": 22},
    )
    assert HiddenCouplingDetector().detect(ctx) == []


def test_production_path_containing_the_word_test_is_not_test_material():
    """``src/latest/`` is production on both ends, so the pair is still scored.

    An unanchored test-directory match would classify ``src/latest/api.py`` as a
    test and filter this pair away as an expected test/production pairing.
    """
    ctx = _ctx(
        "src/latest/api.py",
        partners={"src/billing.py": 18},
        self_commits=20,
        repo_commits={"src/billing.py": 22},
    )
    assert HiddenCouplingDetector().detect(ctx) != []


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
    # Sorted by correlation desc -- top three are the highest counts.
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


def test_ratio_reads_the_record_not_the_repo_wide_commit_total():
    """The denominator comes from the co-change walk, not ``commit_count_total``.

    That column is collected over a shorter window and only for files with a
    code extension, so a pair whose partner is missing from it used to score
    zero. Here the repo-wide map disagrees with the record and the record wins.
    """
    ctx = _ctx(
        "src/payments.py",
        partners={"src/billing.py": 9},
        self_commits=10,
        repo_commits={"src/billing.py": 10},
    )
    ctx.git_meta["commit_count_total"] = 0  # as if it never reached that column
    (finding,) = HiddenCouplingDetector().detect(ctx)
    assert finding.details["self_commits"] == 10
    assert finding.details["partner_commits"] == 10
    assert finding.details["correlation"] == 0.9
