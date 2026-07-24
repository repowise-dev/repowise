"""Unit tests for get_answer's retrieval-noise demotion (_answer_pipeline).

get_answer historically applied no decision/test demotion of its own, so
decision records and test file pages could win RRF and occupy top-5 slots that
feed synthesis. ``demote_noise_hits`` stable-partitions that noise below real
pages before the cap. These are pure-function tests (no fixture, no DB).
"""

from __future__ import annotations

from repowise.server.mcp_server._answer_pipeline import demote_noise_hits


def _hit(page_type, target_path, title="t"):
    return {"page_type": page_type, "target_path": target_path, "title": title}


def test_decision_demoted_on_non_why_question():
    hits = [
        _hit("decision_record", ""),
        _hit("file_page", "src/pipeline/incremental.py"),
    ]
    out = demote_noise_hits(hits, "how does incremental update work", is_why=False)
    assert out[0]["target_path"] == "src/pipeline/incremental.py"
    assert out[1]["page_type"] == "decision_record"  # demoted, not dropped


def test_decision_kept_on_why_question():
    hits = [
        _hit("decision_record", ""),
        _hit("file_page", "src/pipeline/incremental.py"),
    ]
    out = demote_noise_hits(hits, "why does update only regenerate affected pages", is_why=True)
    assert out[0]["page_type"] == "decision_record"  # ranked naturally on a why question


def test_test_page_demoted_on_impl_question():
    hits = [
        _hit("file_page", "tests/unit/test_incremental.py"),
        _hit("file_page", "src/pipeline/incremental.py"),
    ]
    out = demote_noise_hits(hits, "how does incremental update work", is_why=False)
    assert out[0]["target_path"] == "src/pipeline/incremental.py"
    assert out[1]["target_path"] == "tests/unit/test_incremental.py"  # demoted, not dropped


def test_test_page_kept_on_test_question():
    hits = [
        _hit("file_page", "tests/unit/test_incremental.py"),
        _hit("file_page", "src/pipeline/incremental.py"),
    ]
    out = demote_noise_hits(hits, "how is incremental update tested", is_why=False)
    assert out[0]["target_path"] == "tests/unit/test_incremental.py"  # test-focused: no demotion


def test_stable_order_preserved_among_real_hits():
    hits = [
        _hit("file_page", "src/a.py"),
        _hit("decision_record", ""),
        _hit("file_page", "src/b.py"),
        _hit("module_page", "src/pkg"),
    ]
    out = demote_noise_hits(hits, "how does a work", is_why=False)
    # Real hits keep their relative order; the decision falls to the tail.
    assert [h.get("target_path") for h in out] == ["src/a.py", "src/b.py", "src/pkg", ""]


def test_empty_hits_noop():
    assert demote_noise_hits([], "anything", is_why=False) == []
