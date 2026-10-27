"""Immutable oracles for the deterministic retrieval-precision corpus.

This file intentionally validates only the sealed inputs. Ranking assertions
consume this corpus in later tests, but production routing, generic-name, and
scoring helpers must never be used to derive these expected identities.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

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
    assert corpus["modes"] == ["full", "keyless"]
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
            assert candidate["legs"]["keyless"], "keyless candidates must retain a local leg"


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


def test_full_and_keyless_arms_share_queries_windows_and_oracles() -> None:
    corpus = load_retrieval_precision_corpus()

    assert corpus["keyless_mode"] == {
        "embedder": "keyless",
        "llm": "absent",
        "retained_legs": ["path", "symbol", "fts", "history"],
    }
    for case in corpus["cases"]:
        assert all(candidate["legs"]["full"] for candidate in case["candidates"])
        assert all(candidate["legs"]["keyless"] for candidate in case["candidates"])


def _rank_case(case: dict, mode: str, window_size: int) -> list[dict]:
    """Feed sealed candidates through the production ordering primitives."""
    from repowise.server.mcp_server._prose_symbols import _corroborated
    from repowise.server.mcp_server._query_terms import content_terms
    from repowise.server.mcp_server.tool_answer.retrieval import _rerank_by_coverage
    from repowise.server.mcp_server.tool_search import (
        _protect_exact_paths,
        _protect_exact_symbols,
    )

    hits: list[dict] = []
    query_terms = set(content_terms(case["query"]))
    generic_names = {"get", "run", "main"}
    for index, candidate in enumerate(case["candidates"]):
        member_name = candidate["member_name"]
        if case["shape"] == "ambiguous_generic_name" and member_name in generic_names:
            covered = {
                term: (0.25 if term == member_name else 1.0)
                for term in query_terms & ({member_name} | set(candidate["context"]))
            }
            row = SimpleNamespace(name=member_name)
            if not _corroborated(row, covered, saturated={member_name}):
                continue
        hits.append(
            {
                "target": candidate["target"],
                "symbol_id": candidate["target"] if candidate["kind"] == "symbol" else "",
                "name": member_name,
                "qualified_name": (
                    candidate["target"].split("::", 1)[-1]
                    if candidate["kind"] == "symbol"
                    else ""
                ),
                "file": candidate["path"],
                "target_path": candidate["path"],
                "title": candidate["target"].rsplit("/", 1)[-1],
                "summary": " ".join(candidate["context"]),
                "snippet": " ".join(candidate["context"]),
                "page_type": "decision_record" if candidate["kind"] == "decision" else "file_page",
                "score": 1.0 - index * 0.05,
                "_sources": set(candidate["legs"][mode]),
            }
        )

    if case["shape"] in {"identifier", "ambiguous_generic_name"}:
        hits = _protect_exact_symbols(case["query"], hits)
    elif case["shape"] == "path":
        hits = _protect_exact_paths(case["query"], hits)
    else:
        hits = _rerank_by_coverage(hits, case["query"])
    return hits[:window_size]


def _case_metrics(corpus: dict, case: dict, mode: str) -> dict:
    window = _rank_case(case, mode, corpus["window_size"])
    targets = [hit["target"] for hit in window]
    primary = case["known_correct_targets"][0]
    unsupported_generic = [
        hit
        for hit in window
        if hit["name"] in corpus["generic_member_names"]
        and hit["target"] not in case["supported_generic_targets"]
    ]
    return {
        "rank": targets.index(primary) + 1 if primary in targets else None,
        "protected": primary in case["protected_targets"] and targets[0] == primary,
        "generic_noise_share": len(unsupported_generic) / len(window),
        "targets": targets,
        "sources": [hit["_sources"] for hit in window],
    }


def test_sealed_ranking_corpus_in_full_and_keyless_modes() -> None:
    corpus = load_retrieval_precision_corpus()

    for mode in corpus["modes"]:
        for case in corpus["cases"]:
            metrics = _case_metrics(corpus, case, mode)
            assert metrics["rank"] is not None, (case["shape"], mode, metrics)
            assert metrics["generic_noise_share"] < 0.5, (case["shape"], mode, metrics)
            assert metrics["targets"], (case["shape"], mode)
            if case["protected_targets"]:
                assert metrics["protected"], (case["shape"], mode, metrics)
            for required in case["required_targets"]:
                assert required in metrics["targets"], (case["shape"], mode, metrics)
            if mode == "keyless":
                assert all("semantic" not in sources for sources in metrics["sources"])
