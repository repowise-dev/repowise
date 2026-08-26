"""Immutable oracles for the deterministic retrieval-precision corpus.

This file intentionally validates only the sealed inputs. Ranking assertions
consume this corpus in later tests, but production routing, generic-name, and
scoring helpers must never be used to derive these expected identities.
"""

from __future__ import annotations

import json
from pathlib import Path


CORPUS_PATH = (
    Path(__file__).resolve().parents[3] / "fixtures" / "mcp" / "retrieval_precision_corpus.json"
)
EXPECTED_SHAPES = {
    "identifier",
    "path",
    "concept",
    "cross_module_flow",
    "history_why",
    "ambiguous_generic_name",
}


def load_retrieval_precision_corpus() -> dict:
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def test_sealed_corpus_has_six_independent_query_shapes() -> None:
    corpus = load_retrieval_precision_corpus()
    cases = corpus["cases"]

    assert corpus["sealed_at_commit"] == "c2f915d07c11ca3f451309054dfb610e75b8ae26"
    assert corpus["window_size"] == 5
    assert corpus["modes"] == ["full", "degraded"]
    assert {case["shape"] for case in cases} == EXPECTED_SHAPES
    assert len(cases) == len(EXPECTED_SHAPES)

    for case in cases:
        assert case["query"]
        assert case["known_correct_targets"]
        assert case["oracle_basis"]
        assert len(case["candidates"]) > corpus["window_size"]
        candidate_targets = {candidate["target"] for candidate in case["candidates"]}
        assert set(case["known_correct_targets"]) <= candidate_targets
        assert set(case["required_targets"]) <= candidate_targets
        assert set(case["protected_targets"]) <= set(case["known_correct_targets"])
        assert set(case["supported_generic_targets"]) <= candidate_targets
        for candidate in case["candidates"]:
            assert set(candidate["legs"]) == set(corpus["modes"])
            assert candidate["legs"]["degraded"], "keyless candidates must retain a local leg"


def test_generic_support_labels_are_fixture_owned_and_include_positive_controls() -> None:
    corpus = load_retrieval_precision_corpus()
    generic_names = set(corpus["generic_member_names"])
    generic_case = next(case for case in corpus["cases"] if case["shape"] == "ambiguous_generic_name")
    generic_candidates = {
        candidate["target"]
        for candidate in generic_case["candidates"]
        if candidate["member_name"] in generic_names
    }

    assert {"get", "run", "main"} <= {
        candidate["member_name"] for candidate in generic_case["candidates"]
    }
    assert set(generic_case["supported_generic_targets"])
    assert set(generic_case["supported_generic_targets"]) < generic_candidates
    assert set(generic_case["known_correct_targets"]) == set(
        generic_case["supported_generic_targets"]
    )


def test_full_and_degraded_arms_share_queries_windows_and_oracles() -> None:
    corpus = load_retrieval_precision_corpus()

    assert corpus["degraded_mode"] == {
        "embedder": "keyless",
        "llm": "absent",
        "retained_legs": ["path", "symbol", "fts", "history"],
    }
    for case in corpus["cases"]:
        assert all(candidate["legs"]["full"] for candidate in case["candidates"])
        assert all(candidate["legs"]["degraded"] for candidate in case["candidates"])

