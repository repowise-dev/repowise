"""Regression tests for decision-payload quality fixes:

- semantic snippet text must not double a title-only decision,
- a code-comment-derived decision must land below the real-ADR floor,
- decision titles must truncate on a word boundary with an ellipsis.
"""

from __future__ import annotations

from repowise.core.analysis.decisions.extractor import _truncate_title
from repowise.core.analysis.decisions.provenance import (
    compute_confidence,
    rank_for_source,
)
from repowise.core.analysis.decisions.semantic_match import decision_match_text

# --- snippet doubling ------------------------------------------------------


def test_snippet_not_doubled_when_decision_equals_title():
    text = decision_match_text("Use Redis for caching", "Use Redis for caching")
    assert text == "Use Redis for caching"
    assert text.count("Use Redis for caching") == 1


def test_snippet_not_doubled_for_trivial_variant():
    # Trailing punctuation / case difference is still "the same line".
    text = decision_match_text("Use Redis for caching", "use redis for caching.")
    assert text.count("\n") == 0
    assert text == "Use Redis for caching"


def test_snippet_keeps_both_lines_when_they_differ():
    text = decision_match_text("Use Redis for caching", "Chosen for sub-ms reads")
    assert "Use Redis for caching" in text
    assert "Chosen for sub-ms reads" in text
    assert "\n" in text


# --- comment-derived confidence sub-tier -----------------------------------


def test_code_comment_confidence_below_real_adr_floor():
    comment_conf = compute_confidence(rank_for_source("code_comment"))
    adr_conf = compute_confidence(rank_for_source("adr"))
    commit_conf = compute_confidence(rank_for_source("commit"))
    assert comment_conf < 0.5  # does not clear the confidence floor
    assert comment_conf < adr_conf
    assert comment_conf < commit_conf


def test_corroborating_adr_lifts_comment_out_of_subtier():
    # When a stronger source corroborates, top_rank rises and the sub-tier
    # penalty no longer applies.
    corroborated = compute_confidence(rank_for_source("adr"), corroboration_count=2)
    assert corroborated > 0.5


# --- boundary-aware title truncation ---------------------------------------


def test_title_truncates_on_word_boundary_with_ellipsis():
    title = "Adopt an event-driven architecture for the payment settlement pipeline " + ("x" * 200)
    out = _truncate_title(title, 60)
    assert out.endswith("…")
    body = out.rstrip("…")
    # Never splits a word: every emitted word appears verbatim in the source.
    for word in body.split():
        assert word in title
    assert len(out) <= 61


def test_short_title_unchanged():
    assert _truncate_title("Use Postgres", 100) == "Use Postgres"


def test_single_overlong_word_hard_cut_still_ellipsized():
    out = _truncate_title("a" * 200, 40)
    assert out.endswith("…")
    assert len(out) == 41
