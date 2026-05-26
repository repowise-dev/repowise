"""Unit tests for the decision provenance ladder, confidence formula, and the
anti-hallucination substring gate."""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.decision_extractor import DecisionExtractor, ExtractedDecision
from repowise.core.analysis.decision_provenance import (
    MAX_SOURCE_RANK,
    compute_confidence,
    rank_for_source,
    verify_quote,
)


# ---------------------------------------------------------------------------
# Source ranking ladder
# ---------------------------------------------------------------------------


def test_source_rank_ladder_ordering():
    # adr > pr > commit > changelog > inline_marker > comment > test_name > inferred
    order = ["adr", "pr", "commit", "changelog", "inline_marker", "comment", "test_name", "inferred"]
    ranks = [rank_for_source(s) for s in order]
    assert ranks == sorted(ranks, reverse=True)
    assert ranks == [8, 7, 6, 5, 4, 3, 2, 1]


def test_git_archaeology_aliases_commit_rank():
    assert rank_for_source("git_archaeology") == rank_for_source("commit") == 6


def test_unknown_source_lowest_rank():
    assert rank_for_source("something_new") == 1
    assert rank_for_source(None) == 1


def test_cli_outranks_adr():
    # Human-authored manual entry is the most authoritative source.
    assert rank_for_source("cli") > rank_for_source("adr")
    assert rank_for_source("cli") == MAX_SOURCE_RANK


# ---------------------------------------------------------------------------
# Confidence formula
# ---------------------------------------------------------------------------


def test_confidence_rises_with_rank():
    low = compute_confidence(rank_for_source("inferred"))
    high = compute_confidence(rank_for_source("adr"))
    assert high > low


def test_confidence_rises_with_corroboration():
    single = compute_confidence(rank_for_source("commit"), corroboration_count=1)
    triple = compute_confidence(rank_for_source("commit"), corroboration_count=3)
    assert triple > single


def test_confidence_decays_for_weak_verification():
    exact = compute_confidence(8, 2, "exact")
    fuzzy = compute_confidence(8, 2, "fuzzy")
    unverified = compute_confidence(8, 2, "unverified")
    assert exact > fuzzy > unverified


def test_confidence_bounded():
    assert 0.0 <= compute_confidence(MAX_SOURCE_RANK, 99, "exact") <= 0.99


# ---------------------------------------------------------------------------
# verify_quote
# ---------------------------------------------------------------------------


def test_verify_quote_exact_ignores_whitespace_and_case():
    src = "We   adopted Redis\nfor the shared cache layer."
    assert verify_quote("adopted redis for the shared cache layer.", src) == "exact"


def test_verify_quote_fuzzy_on_paraphrase():
    src = "We adopted Redis because the in-process cache could not share state across workers."
    # Reworded but high token overlap.
    assert verify_quote("adopted Redis: in-process cache could not share state workers", src) == (
        "fuzzy"
    )


def test_verify_quote_unverified_on_hallucination():
    src = "We adopted Redis for the shared cache."
    assert verify_quote("We migrated the billing service to Kafka for event sourcing", src) == (
        "unverified"
    )


def test_verify_quote_unverified_without_source():
    assert verify_quote("anything", "") == "unverified"


# ---------------------------------------------------------------------------
# Substring gate
# ---------------------------------------------------------------------------


def _extractor(tmp_path: Path) -> DecisionExtractor:
    return DecisionExtractor(repo_path=tmp_path)


def test_gate_keeps_grounded_decision(tmp_path):
    ex = _extractor(tmp_path)
    d = ExtractedDecision(
        title="Adopt Redis",
        decision="adopt Redis for caching",
        rationale="the in-process cache could not share state across workers",
        source="commit",
        source_text=(
            "feat: adopt Redis for caching\n\nThe in-process cache could not share "
            "state across workers, so we adopt Redis for caching."
        ),
    )
    kept, rejected = ex._apply_substring_gate([d])
    assert rejected == 0
    assert len(kept) == 1
    assert kept[0].verification in ("exact", "fuzzy")
    assert kept[0].decision == "adopt Redis for caching"
    # Transient source text is cleared before persistence.
    assert kept[0].source_text == ""


def test_gate_rejects_fully_hallucinated_decision(tmp_path):
    ex = _extractor(tmp_path)
    d = ExtractedDecision(
        title="Switch to Kafka",
        decision="migrate the billing pipeline to Kafka event streaming",
        rationale="to support exactly-once delivery semantics at scale",
        source="git_archaeology",
        # The source says nothing about Kafka — every produced field is ungrounded.
        source_text="chore: bump dependencies\n\nRoutine dependency upgrade, no behaviour change.",
    )
    kept, rejected = ex._apply_substring_gate([d])
    assert rejected == 1
    assert kept == []


def test_gate_drops_only_the_hallucinated_field(tmp_path):
    ex = _extractor(tmp_path)
    d = ExtractedDecision(
        title="Use SQLite",
        decision="use SQLite for local persistence",
        rationale="because it is web-scale and horizontally sharded",  # not in source
        source="inline_marker",
        source_text="# DECISION: use SQLite for local persistence — zero-config, single file.",
    )
    kept, _rejected = ex._apply_substring_gate([d])
    assert len(kept) == 1
    # Decision survives (grounded); hallucinated rationale is dropped.
    assert kept[0].decision == "use SQLite for local persistence"
    assert kept[0].rationale == ""


def test_gate_keeps_unverifiable_when_no_source_text(tmp_path):
    ex = _extractor(tmp_path)
    d = ExtractedDecision(title="Manual entry", decision="some decision", source="cli")
    kept, rejected = ex._apply_substring_gate([d])
    assert rejected == 0
    assert len(kept) == 1
    assert kept[0].verification == "unverified"
