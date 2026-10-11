"""Calibration of PRBlastRadiusAnalyzer._compute_overall_risk.

Regression: the old ``min(raw * 100, 10)`` normalisation scaled the 0-1
breadth bonus to 0-20 points, so any PR with >=20 transitive dependents â€”
i.e. any PR touching a hotspot â€” reported exactly 10.0. The score must
discriminate between small clean diffs, moderate diffs, and hotspot-heavy
diffs, and only approach 10 asymptotically.
"""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.analysis.pr_blast import PRBlastRadiusAnalyzer
from repowise.core.analysis.risk_semantics import (
    structural_impact_band,
    structural_impact_contract,
)

_CORPUS = Path(__file__).parents[2] / "fixtures" / "risk_scale_corpus.json"


def _direct(*scores: float) -> list[dict]:
    # Only the two keys _compute_overall_risk reads; _score_files emits five.
    return [{"structural_score": s, "risk_score": s} for s in scores]


def _transitive(n: int) -> list[dict]:
    return [{"path": f"f{i}.py"} for i in range(n)]


class TestOverallRiskCalibration:
    def test_empty_is_zero(self):
        assert PRBlastRadiusAnalyzer._compute_overall_risk([], []) == 0.0

    def test_small_clean_diff_scores_low(self):
        # Three low-centrality, low-churn files, narrow blast.
        score = PRBlastRadiusAnalyzer._compute_overall_risk(
            _direct(0.002, 0.001, 0.003), _transitive(2)
        )
        assert 0.0 < score < 2.0

    def test_hotspot_diff_does_not_saturate(self):
        # One genuine hotspot (pagerank*(1+temporal) ~ 0.2) plus wide blast:
        # previously this pinned at exactly 10.0.
        score = PRBlastRadiusAnalyzer._compute_overall_risk(
            _direct(0.2, 0.01, 0.005), _transitive(40)
        )
        assert score < 10.0
        assert score > 3.0

    def test_breadth_alone_cannot_max_the_score(self):
        # Regression core: >=20 transitive dependents alone used to add 20
        # points. Breadth now contributes at most 2.
        score = PRBlastRadiusAnalyzer._compute_overall_risk(_direct(0.001), _transitive(100))
        assert score < 4.0

    def test_monotonic_in_file_risk(self):
        low = PRBlastRadiusAnalyzer._compute_overall_risk(_direct(0.01), _transitive(5))
        mid = PRBlastRadiusAnalyzer._compute_overall_risk(_direct(0.05), _transitive(5))
        high = PRBlastRadiusAnalyzer._compute_overall_risk(_direct(0.3), _transitive(5))
        assert low < mid < high

    def test_monotonic_in_breadth(self):
        narrow = PRBlastRadiusAnalyzer._compute_overall_risk(_direct(0.05), _transitive(0))
        wide = PRBlastRadiusAnalyzer._compute_overall_risk(_direct(0.05), _transitive(40))
        assert narrow < wide
        assert round(wide - narrow, 6) <= 2.0  # breadth term is capped at 2 points

    def test_never_exceeds_ten(self):
        score = PRBlastRadiusAnalyzer._compute_overall_risk(
            _direct(5.0, 5.0, 5.0), _transitive(500)
        )
        assert score <= 10.0


def test_deterministic_corpus_preserves_separation_without_ordinary_saturation() -> None:
    """Band mapping and ordering over the heuristic's own input space.

    These are constructed inputs, not sampled commits: they prove the formula
    separates and never pins at 10, not that a real repo's changes land where
    they should. Live distribution evidence belongs in the dogfood record.
    """
    fixtures = json.loads(_CORPUS.read_text(encoding="utf-8"))
    scored: dict[str, float] = {}
    for fixture in fixtures:
        score = PRBlastRadiusAnalyzer._compute_overall_risk(
            _direct(*fixture["direct_scores"]),
            _transitive(fixture["transitive_dependents"]),
        )
        scored[fixture["name"]] = score
        assert structural_impact_band(score) == fixture["expected_band"]

    ordinary = [
        score
        for name, score in scored.items()
        if name
        in {
            "documentation_low_signal",
            "small_ordinary_source",
            "historical_fix_limited_reach",
            "cochange_only_relationship",
        }
    ]
    assert max(ordinary) < 4.0
    assert all(score < 10.0 for score in scored.values())
    assert scored["documentation_low_signal"] < scored["moderate_multi_file"]
    assert scored["moderate_multi_file"] < scored["structurally_broad_low_history"]
    assert scored["structurally_broad_low_history"] < scored["genuinely_broad_high_control"]


def test_cochange_only_relationships_are_not_structural_reach() -> None:
    """A co-change partner is temporal correlation, never a transitive dependent."""
    fixtures = {item["name"]: item for item in json.loads(_CORPUS.read_text(encoding="utf-8"))}
    cochange = fixtures["cochange_only_relationship"]
    assert cochange["cochange_only"] is True
    assert cochange["transitive_dependents"] == 0
    score = PRBlastRadiusAnalyzer._compute_overall_risk(
        _direct(*cochange["direct_scores"]), _transitive(cochange["transitive_dependents"])
    )
    assert structural_impact_band(score) == "localized"


def test_historical_evidence_does_not_change_structural_impact_score() -> None:
    fixtures = {item["name"]: item for item in json.loads(_CORPUS.read_text(encoding="utf-8"))}
    ordinary = fixtures["small_ordinary_source"]
    historical = fixtures["historical_fix_limited_reach"]
    assert ordinary["direct_scores"] == historical["direct_scores"]
    assert ordinary["transitive_dependents"] == historical["transitive_dependents"]
    assert ordinary["historical_fix_evidence"] is False
    assert historical["historical_fix_evidence"] is True
    assert PRBlastRadiusAnalyzer._compute_overall_risk(
        _direct(*ordinary["direct_scores"]), _transitive(ordinary["transitive_dependents"])
    ) == PRBlastRadiusAnalyzer._compute_overall_risk(
        _direct(*historical["direct_scores"]), _transitive(historical["transitive_dependents"])
    )


def test_default_contract_guards_the_reading_without_the_reference_tier() -> None:
    """The default ships what stops a misreading; include=["scales"] ships the rest."""
    default = structural_impact_contract(6.25)
    scale = default["structural_impact_scale"]
    for guard in ("unit", "range", "measures", "band_thresholds"):
        assert guard in scale
    assert scale["calibration"] == {"status": "uncalibrated"}
    assert scale["runtime_breakage_probability"] is False
    assert "formula" not in scale
    assert "component_fields" not in scale
    assert "deterministic" not in scale

    full = structural_impact_contract(6.25, full_scale=True)
    full_scale_block = full["structural_impact_scale"]
    assert full_scale_block["formula"] and full_scale_block["component_fields"]
    assert full_scale_block["deterministic"] is True
    assert full_scale_block["calibration"]["source"] is None

    # Ceiling, not a ratio: this block rides every PR-mode response, so a
    # future field cannot quietly reintroduce the reference tier by default.
    assert len(json.dumps(default)) < 1000
    assert len(json.dumps(full)) > 2000


def test_structural_contract_is_typed_and_legacy_alias_cannot_contradict() -> None:
    contract = structural_impact_contract(6.25, full_scale=True)
    assert contract["structural_impact_score"] == contract["overall_risk_score"] == 6.25
    assert contract["structural_impact_band"] == "moderate"
    scale = contract["structural_impact_scale"]
    assert scale["unit"] == "normalized_points"
    assert scale["calibration"]["status"] == "uncalibrated"
    assert scale["runtime_breakage_probability"] is False
    assert scale["authoritative_for_change_review"] is False
    direct_alias = scale["component_fields"]["direct_risks.risk_score"]
    assert direct_alias["deprecated"] is True
    assert direct_alias["replacement"] == "direct_risks.structural_score"
    assert direct_alias["equivalent_value"] is True
    assert contract["overall_risk_score_compatibility"]["replacement"] == (
        "structural_impact_score"
    )


def test_raw_churn_and_its_repo_relative_rank_are_both_published() -> None:
    """The velocity-coupled multiplier must ship its normalised form beside it.

    Folding the percentile into ``structural_score`` was measured and rejected
    (it collapses the 0-10 output), so the rank is published as its own field
    and the contract has to say which one travels across repositories.
    """
    fields = structural_impact_contract(6.25, full_scale=True)["structural_impact_scale"][
        "component_fields"
    ]

    raw = fields["direct_risks.temporal_hotspot"]
    assert raw["kind"] == "raw_decayed_churn"
    assert raw["range"]["maximum"] is None
    assert "churn_percentile" in raw["note"]

    rank = fields["direct_risks.churn_percentile"]
    assert rank["kind"] == "repo_relative_rank"
    assert rank["range"] == {"minimum": 0.0, "maximum": 1.0}

    # The score itself keeps the raw multiplier; the rank sits beside it.
    assert fields["direct_risks.structural_score"]["formula"] == (
        "pagerank * (1 + temporal_hotspot)"
    )


def test_score_file_keeps_the_unbounded_multiplier() -> None:
    # (1 + churn_percentile) would cap this term at 2x; (1 + temporal) does not.
    assert PRBlastRadiusAnalyzer._score_file(0.0, 0.01) == 0.01
    assert PRBlastRadiusAnalyzer._score_file(9.0, 0.01) == 0.1


class _Meta:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """Answers _score_files' two SELECTs in the order it issues them."""

    def __init__(self, metas, nodes):
        self._answers = [list(metas.values()), list(nodes.values())]

    async def execute(self, _stmt):
        return _Result(self._answers.pop(0))


class _Node:
    def __init__(self, node_id, pagerank):
        self.node_id = node_id
        self.pagerank = pagerank


async def test_score_files_emits_the_rank_and_the_index_verdict() -> None:
    """A row carries the raw churn sum, its repo-relative rank, and is_hotspot.

    A consumer that had only the raw sum was re-deriving a hotspot from it with
    an absolute cutoff, which is the velocity artifact this change removes.
    """
    analyzer = PRBlastRadiusAnalyzer.__new__(PRBlastRadiusAnalyzer)
    analyzer._repo_id = "r"
    metas = {
        "hot.py": _Meta(
            file_path="hot.py",
            temporal_hotspot_score=8.4,
            churn_percentile=0.93,
            is_hotspot=True,
        ),
    }
    nodes = {"hot.py": _Node("hot.py", 0.01), "unknown.py": _Node("unknown.py", 0.02)}
    analyzer._session = _FakeSession(metas, nodes)

    rows = {r["path"]: r for r in await analyzer._score_files(["hot.py", "unknown.py"])}

    hot = rows["hot.py"]
    assert hot["temporal_hotspot"] == 8.4
    assert hot["churn_percentile"] == 0.93
    assert hot["is_hotspot"] is True
    assert hot["structural_score"] == hot["risk_score"] == round(0.01 * 9.4, 4)

    # No git_metadata row: every history field degrades to a measured zero
    # rather than to None, so the marker stays off instead of rendering NaN.
    missing = rows["unknown.py"]
    assert missing["temporal_hotspot"] == 0.0
    assert missing["churn_percentile"] == 0.0
    assert missing["is_hotspot"] is False
    assert missing["centrality"] == 0.02
