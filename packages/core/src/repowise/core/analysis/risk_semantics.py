"""Shared public vocabulary for risk and structural-impact scalars.

The codebase exposes two deliberately different 0-10 values:

* live change risk scores the *shape* of a git diff with an offline-calibrated
  model and ranks it against recent commits; and
* PR blast radius summarizes indexed centrality and structural reach with an
  uncalibrated heuristic.

Keeping their labels, thresholds, and machine-readable descriptions here makes
it impossible for MCP, REST, CLI, and UI adapters to invent competing meanings.

It ships in two tiers. The guard tier (unit, range, calibration status,
authority) travels with the value and is never omitted. The reference tier
(fitting corpus, formula, component breakdown) is identical on every call and
is returned only for ``include=["scales"]``. :func:`compact_scale` is the one
projection between them, so the tiers cannot disagree.
"""

from __future__ import annotations

from typing import Any

REVIEW_PRIORITY_MODERATE_PERCENTILE = 100.0 / 3.0
REVIEW_PRIORITY_HIGH_PERCENTILE = 200.0 / 3.0

ABSOLUTE_CHANGE_SCORE_MODERATE = 4.0
ABSOLUTE_CHANGE_SCORE_HIGH = 7.0

STRUCTURAL_IMPACT_MODERATE = 4.0
STRUCTURAL_IMPACT_BROAD = 7.0

CHANGE_RISK_SCORE_MEASURES = "diff size and spread; not where the change lands"
CHANGE_RISK_SCORE_UNIT = "per-commit"
STRUCTURAL_IMPACT_MEASURES = (
    "indexed structural exposure: centrality of the changed files plus bounded "
    "direct and transitive dependent breadth"
)
STRUCTURAL_IMPACT_FORMULA = (
    "combined = 0.5 * mean(pagerank * (1 + temporal_hotspot)) + "
    "0.5 * max(pagerank * (1 + temporal_hotspot)); score = min(8 * "
    "(1 - exp(-10 * combined)) + 2 * min(transitive_dependents / 20, 1), 10)"
)
WORKSPACE_IMPACT_SCORE_FORMULA = (
    "maximum path product of edge confidence * edge-kind weight * 0.6 per hop; "
    "structural edge weight = 1.0 and historical co-change edge weight = 0.5"
)


#: Reference-tier keys: they document a scalar rather than guard a reading.
_REFERENCE_KEYS = frozenset({"deterministic", "formula", "component_fields"})
_CALIBRATION_REFERENCE_KEYS = frozenset(
    {"source", "population", "calibrated_at", "granularity"}
)


def compact_scale(row: dict[str, Any]) -> dict[str, Any]:
    """Project one scale row onto the facts that prevent a misreading."""
    compact = {key: value for key, value in row.items() if key not in _REFERENCE_KEYS}
    calibration = compact.get("calibration")
    if isinstance(calibration, dict):
        compact["calibration"] = {
            key: value
            for key, value in calibration.items()
            if key not in _CALIBRATION_REFERENCE_KEYS
        }
    return compact


def structural_impact_band(score: float) -> str:
    """Classify the uncalibrated 0-10 structural heuristic."""
    if score >= STRUCTURAL_IMPACT_BROAD:
        return "broad"
    if score >= STRUCTURAL_IMPACT_MODERATE:
        return "moderate"
    return "localized"


def change_risk_authority() -> dict[str, Any]:
    """Authority and scalar metadata shared by live-diff public surfaces."""
    return {
        "authoritative_for": "live_change_review",
        "primary_fields": ["risk_percentile", "classification"],
        "primary_basis": "benchmarked_population_relative",
        "fallback_field": "fallback_band",
        "fallback_basis": "absolute_model_score_band",
        "score_role": "supporting_diff_shape_signal",
    }


def change_risk_scales() -> list[dict[str, Any]]:
    """Describe every scalar or categorical change-risk value and its units."""
    return [
        {
            "field": "score",
            "kind": "benchmarked_model_score",
            "unit": "normalized_points",
            "range": {"minimum": 0.0, "maximum": 10.0},
            "measures": CHANGE_RISK_SCORE_MEASURES,
            "deterministic": True,
            "calibration": {
                "status": "benchmarked",
                "source": "repowise-bench/health-defect/jit_calibration.py",
                "calibrated_at": "2026-05-30",
                "population": "4102 commits across 7 repositories using AG-SZZ labels",
                "granularity": "single_commit",
            },
            "authoritative": False,
        },
        {
            "field": "risk_percentile",
            "kind": "percentile",
            "unit": "percentile_rank",
            "range": {"minimum": 0.0, "maximum": 100.0},
            "measures": "rank of the diff-shape score among sampled recent commits",
            "deterministic": True,
            "calibration": {
                "status": "benchmarked",
                "source": "live repository baseline",
                "population": "sampled recent commits after the same filters",
            },
            "authoritative": True,
        },
        {
            "field": "review_priority",
            "kind": "classification",
            "unit": "category",
            "range": None,
            "measures": "repo-relative review-priority tercile",
            "deterministic": True,
            "thresholds": {
                "moderate_percentile": REVIEW_PRIORITY_MODERATE_PERCENTILE,
                "high_percentile": REVIEW_PRIORITY_HIGH_PERCENTILE,
            },
            "authoritative": True,
        },
        {
            "field": "classification",
            "kind": "classification_label",
            "unit": "category",
            "range": None,
            "measures": "human-facing label for review_priority",
            "deterministic": True,
            "authoritative": True,
        },
        {
            "field": "fallback_band",
            "kind": "absolute_classification",
            "unit": "category",
            "range": None,
            "measures": "absolute model-score band when no baseline can rank the change",
            "deterministic": True,
            "thresholds": {
                "moderate_score": ABSOLUTE_CHANGE_SCORE_MODERATE,
                "high_score": ABSOLUTE_CHANGE_SCORE_HIGH,
            },
            "calibration": {
                "status": "heuristic_thresholds",
                "source": "shared 4.0 and 7.0 model-score thresholds",
                "granularity": "single_commit",
            },
            "authoritative": False,
        },
        {
            "field": "fix_history.density",
            "kind": "historical_heuristic",
            "unit": "recency_weighted_prior_fixes",
            "range": {"minimum": 0.0, "maximum": None},
            "measures": "churn-weighted prior bug-fix pressure of touched files",
            "deterministic": True,
            "calibration": {"status": "uncalibrated", "source": None},
            "authoritative": False,
        },
        {
            "field": "fix_history.percentile",
            "kind": "percentile",
            "unit": "percentile_rank",
            "range": {"minimum": 0.0, "maximum": 100.0},
            "measures": "rank of fix-history density among sampled recent commits",
            "deterministic": True,
            "calibration": {
                "status": "benchmarked",
                "source": "live repository baseline",
                "population": "sampled recent commits after the same filters",
            },
            "authoritative": False,
        },
        {
            "field": "features.la|features.ld",
            "kind": "raw_count",
            "unit": "lines",
            "range": {"minimum": 0.0, "maximum": None},
            "measures": "added or deleted lines in the filtered live diff",
            "deterministic": True,
            "calibration": {"status": "not_applicable", "source": None},
            "authoritative": False,
        },
        {
            "field": "features.nf|features.nd|features.ns",
            "kind": "raw_count",
            "unit": "items",
            "range": {"minimum": 0.0, "maximum": None},
            "measures": "changed files, directories, or top-level subsystems",
            "deterministic": True,
            "calibration": {"status": "not_applicable", "source": None},
            "authoritative": False,
        },
        {
            "field": "features.entropy",
            "kind": "distribution_statistic",
            "unit": "shannon_bits",
            "range": {"minimum": 0.0, "maximum": None},
            "measures": "Shannon entropy of changed-line distribution across files",
            "deterministic": True,
            "calibration": {"status": "not_applicable", "source": None},
            "authoritative": False,
        },
        {
            "field": "features.exp",
            "kind": "raw_count",
            "unit": "prior_commits",
            "range": {"minimum": 0.0, "maximum": None},
            "measures": "author commits before the assessed change; null when unknown",
            "deterministic": True,
            "calibration": {"status": "not_applicable", "source": None},
            "authoritative": False,
        },
        {
            "field": "drivers[].contribution",
            "kind": "model_component",
            "unit": "logit_points",
            "range": {"minimum": None, "maximum": None},
            "measures": "signed exact feature contribution to the model logit",
            "deterministic": True,
            "calibration": {
                "status": "benchmarked",
                "source": "repowise-bench/health-defect/jit_calibration.py",
            },
            "authoritative": False,
        },
    ]


def structural_impact_contract(score: float, *, full_scale: bool = False) -> dict[str, Any]:
    """Typed additive contract plus exact legacy alias for PR blast responses."""
    contract: dict[str, Any] = {
        "structural_impact_score": score,
        "structural_impact_band": structural_impact_band(score),
        "structural_impact_scale": {
            "field": "structural_impact_score",
            "kind": "heuristic_structural_score",
            "unit": "normalized_points",
            "range": {"minimum": 0.0, "maximum": 10.0},
            "measures": STRUCTURAL_IMPACT_MEASURES,
            "formula": STRUCTURAL_IMPACT_FORMULA,
            "deterministic": True,
            "calibration": {"status": "uncalibrated", "source": None},
            "authoritative_for_change_review": False,
            "runtime_breakage_probability": False,
            "band_thresholds": {
                "moderate": STRUCTURAL_IMPACT_MODERATE,
                "broad": STRUCTURAL_IMPACT_BROAD,
            },
            "component_fields": {
                "direct_risks.structural_score": {
                    "kind": "raw_structural_heuristic",
                    "unit": "pagerank_weighted_hotspot",
                    "range": {"minimum": 0.0, "maximum": None},
                    "formula": "pagerank * (1 + temporal_hotspot)",
                },
                "direct_risks.risk_score": {
                    "kind": "compatibility_alias",
                    "unit": "pagerank_weighted_hotspot",
                    "range": {"minimum": 0.0, "maximum": None},
                    "deprecated": True,
                    "replacement": "direct_risks.structural_score",
                    "equivalent_value": True,
                },
                "direct_risks.temporal_hotspot": {
                    "kind": "normalized_component_score",
                    "unit": "ratio",
                    "range": {"minimum": 0.0, "maximum": 1.0},
                },
                "direct_risks.centrality": {
                    "kind": "graph_centrality",
                    "unit": "pagerank",
                    "range": {"minimum": 0.0, "maximum": 1.0},
                },
                "transitive_affected.depth": {
                    "kind": "raw_count",
                    "unit": "dependency_hops",
                    "range": {"minimum": 1.0, "maximum": None},
                },
                "cochange_warnings.score": {
                    "kind": "raw_count",
                    "unit": "historical_cochange_commits",
                    "range": {"minimum": 0.0, "maximum": None},
                },
                "recommended_reviewers.ownership_pct": {
                    "kind": "ownership_fraction",
                    "unit": "ratio",
                    "range": {"minimum": 0.0, "maximum": 1.0},
                },
            },
        },
        # Public compatibility field. It is deliberately an exact alias, never
        # repurposed, so old clients cannot interpret a new unit as the old one.
        "overall_risk_score": score,
        "overall_risk_score_compatibility": {
            "deprecated": True,
            "replacement": "structural_impact_score",
            "equivalent_value": True,
            "historical_meaning": "uncalibrated 0-10 structural blast-radius heuristic",
        },
    }
    if not full_scale:
        contract["structural_impact_scale"] = compact_scale(contract["structural_impact_scale"])
    return contract


def file_risk_scales() -> list[dict[str, Any]]:
    """Metadata for indexed-file risk cards returned by ``get_risk``."""
    return [
        {
            "field": "targets.*.hotspot_score",
            "kind": "normalized_component_score",
            "unit": "ratio",
            "range": {"minimum": 0.0, "maximum": 1.0},
            "measures": "repository-relative churn percentile",
            "calibration": {"status": "uncalibrated", "source": None},
        },
        {
            "field": "targets.*.health_score",
            "kind": "code_health_score",
            "unit": "health_points",
            "range": {"minimum": 0.0, "maximum": 10.0},
            "measures": "overall code health; higher is healthier",
            "calibration": {"status": "benchmarked", "source": "code-health model"},
        },
        {
            "field": "targets.*.owner_pct",
            "kind": "ownership_fraction",
            "unit": "ratio",
            "range": {"minimum": 0.0, "maximum": 1.0},
            "measures": "share of commits attributed to the primary owner",
            "calibration": {"status": "not_applicable", "source": None},
        },
        {
            "field": "targets.*.recent_owner_pct",
            "kind": "ownership_fraction",
            "unit": "ratio",
            "range": {"minimum": 0.0, "maximum": 1.0},
            "measures": "share of recent commits attributed to the recent owner",
            "calibration": {"status": "not_applicable", "source": None},
        },
        {
            "field": "targets.*.risk_type",
            "kind": "heuristic_classification",
            "unit": "category",
            "range": None,
            "measures": "dominant indexed warning type from fixes, churn, coupling, or ownership",
            "calibration": {"status": "uncalibrated", "source": None},
        },
    ]


def workspace_impact_score_semantics(*, full: bool = False) -> dict[str, Any]:
    """Metadata for the workspace blast-radius path-ranking score."""
    scale: dict[str, Any] = {
        "field": "impacted[].score",
        "kind": "heuristic_path_rank",
        "unit": "relative_weight",
        "range": {"minimum": 0.0, "maximum": 1.0},
        "measures": "strongest bounded structural or historical path from a target service",
        "formula": WORKSPACE_IMPACT_SCORE_FORMULA,
        "deterministic": True,
        "calibration": {"status": "uncalibrated", "source": None},
        "authoritative_for_change_review": False,
        "runtime_breakage_probability": False,
    }
    return scale if full else compact_scale(scale)
