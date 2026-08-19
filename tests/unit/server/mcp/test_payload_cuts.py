"""Payload cuts: each one is lossless, and each proof is the loss test.

A tool result is new text entering a long, already-cached conversation, so it
is billed at the cache-*write* rate — far above the rate at which the rest of
the prompt is re-read, and paid again for every result. A field the agent does
not act on is therefore not free; it is some of the most expensive text in the
session, and it costs more the later in a session it arrives.

Every test here pins the same shape: **the cut fires where the information is
redundant, and does NOT fire where it is the only copy.** A one-directional
test passes just as happily on a cut that silently ate the payload, which is
the failure mode each of these changes was designed around.
"""

from __future__ import annotations

from repowise.server.mcp_server._budget.budgeter import truncate_to_budget
from repowise.server.mcp_server.tool_answer.answer import (
    _build_best_guesses,
    _drop_duplicated_guess_excerpts,
    _trim_served_payload,
)
from repowise.server.mcp_server.tool_answer.retrieval import _CANDIDATE_LIMIT
from repowise.server.mcp_server.tool_search import _drop_derivable_page_ids

# ---------------------------------------------------------------------------
# search_codebase.results[].page_id — derivable from two of its own siblings
# ---------------------------------------------------------------------------


def test_page_id_dropped_when_page_type_and_target_path_rebuild_it():
    results = [
        {
            "page_id": "file_page:rich/ansi.py",
            "page_type": "file_page",
            "target_path": "rich/ansi.py",
            "title": "ansi",
        }
    ]
    _drop_derivable_page_ids(results)
    assert "page_id" not in results[0]
    # Lossless: the consumer rebuilds it from what is still there.
    assert f"{results[0]['page_type']}:{results[0]['target_path']}" == "file_page:rich/ansi.py"


def test_page_id_kept_when_it_cannot_be_rebuilt():
    """The other direction, and it is a real case, not a hypothetical.

    ``_attach_paths`` writes ``target_path: ""`` for a hit whose Page row did
    not load. Dropping the id there would lose the only handle the row has.
    """
    orphan = {"page_id": "file_page:rich/gone.py", "page_type": "file_page", "target_path": ""}
    renamed = {"page_id": "module_page:rich", "page_type": "file_page", "target_path": "rich"}
    _drop_derivable_page_ids([orphan, renamed])
    assert orphan["page_id"] == "file_page:rich/gone.py"
    assert renamed["page_id"] == "module_page:rich"


def test_symbol_qualified_page_ids_still_rebuild():
    """A symbol_spotlight's target_path is ``file.py::Symbol`` — still derivable."""
    results = [
        {
            "page_id": "symbol_spotlight:rich/ansi.py::AnsiDecoder",
            "page_type": "symbol_spotlight",
            "target_path": "rich/ansi.py::AnsiDecoder",
        }
    ]
    _drop_derivable_page_ids(results)
    assert "page_id" not in results[0]


# ---------------------------------------------------------------------------
# get_answer.best_guesses[].excerpt — the same slab retrieval[] already carries
# ---------------------------------------------------------------------------


def test_guess_excerpt_dropped_when_retrieval_carries_it():
    payload = {
        "retrieval": [{"path": "a.py", "excerpt": "line one\nline two\nline three"}],
        "best_guesses": [{"file": "a.py", "score": 1.0, "excerpt": "line two\nline three"}],
    }
    _drop_duplicated_guess_excerpts(payload)
    assert "excerpt" not in payload["best_guesses"][0]
    # Still in the response, exactly once.
    assert "line two" in payload["retrieval"][0]["excerpt"]


def test_guess_excerpt_kept_when_retrieval_is_empty():
    """The abstain path ships ``retrieval: []``, so the guess IS the content.

    A measured payload carried 4,667 characters of guess excerpt with an empty
    retrieval block. An unconditional drop would have deleted the answer.
    """
    payload = {
        "retrieval": [],
        "best_guesses": [{"file": "a.py", "score": 1.0, "excerpt": "the only copy"}],
    }
    _drop_duplicated_guess_excerpts(payload)
    assert payload["best_guesses"][0]["excerpt"] == "the only copy"


def test_guess_excerpt_kept_when_retrieval_carries_a_different_file():
    payload = {
        "retrieval": [{"path": "b.py", "excerpt": "unrelated content"}],
        "best_guesses": [{"file": "a.py", "score": 1.0, "excerpt": "a.py content"}],
    }
    _drop_duplicated_guess_excerpts(payload)
    assert payload["best_guesses"][0]["excerpt"] == "a.py content"


def test_drop_is_a_noop_without_best_guesses():
    payload = {"answer": "prose", "retrieval": [{"path": "a.py", "excerpt": "x"}]}
    assert _drop_duplicated_guess_excerpts(payload) == payload


def test_null_domain_penalty_is_absent_not_null():
    plain = _build_best_guesses([{"target_path": "a.py", "score": 0.5}])
    assert "domain_penalty" not in plain[0]
    penalised = _build_best_guesses(
        [{"target_path": "a.py", "score": 0.5, "_domain_penalty": "ui question; cross-domain"}]
    )
    assert penalised[0]["domain_penalty"] == "ui question; cross-domain"


# ---------------------------------------------------------------------------
# get_answer.candidates — 20 rows measured at up to 39.9% of the payload
# ---------------------------------------------------------------------------


def test_candidate_limit_is_capped():
    # Not an equality assert on 5: the number is a judgement call and may move
    # again. What must not come back is the 20 that made this block 3,107-3,279
    # characters, up to 39.9% of a measured get_answer payload.
    assert _CANDIDATE_LIMIT <= 8


def test_cached_payload_is_capped_on_the_way_out():
    """A cache row written at 20 rows must not serve 20 rows.

    Capping only where the block is built left the cap unreachable for every
    already-cached answer, and the tree used to re-measure this change came
    back byte-identical because of exactly that.
    """
    payload = {"candidates": [{"path": f"f{i}.py"} for i in range(20)]}
    _trim_served_payload(payload)
    assert len(payload["candidates"]) == _CANDIDATE_LIMIT
    # Head kept, so the best-ranked file is still the one described.
    assert payload["candidates"][0]["path"] == "f0.py"


def test_trim_leaves_a_short_candidate_list_alone():
    payload = {"candidates": [{"path": "a.py"}, {"path": "b.py"}]}
    _trim_served_payload(payload)
    assert len(payload["candidates"]) == 2


# ---------------------------------------------------------------------------
# The empty truncation keys
# ---------------------------------------------------------------------------


def test_truncation_keys_absent_when_nothing_was_dropped():
    out = truncate_to_budget({"targets": {"a.py": {"target": "a.py"}}, "_meta": {}})
    for key in ("truncated", "dropped_targets", "dropped_symbols"):
        assert key not in out


# ---------------------------------------------------------------------------
# get_risk.impact_surface — the same call must give the same answer
# ---------------------------------------------------------------------------


def test_impact_surface_is_stable_across_equal_pagerank():
    """Ties broke on set-iteration order, so the "top 3" changed between calls.

    Found by a payload-parity run: two identical `get_risk` calls minutes apart
    on the same tree named `tests/test_progress.py` and `examples/fullscreen.py`
    in the same slot. `visited` is a set and the sort is stable, so wherever
    pagerank ties — which is most of the graph, at 0.0 — the order was whatever
    hashing produced that process.
    """
    from repowise.server.mcp_server.tool_risk.assessment import _compute_impact_surface

    deps = {"t.py": {"b.py", "a.py", "c.py", "d.py"}}
    first = _compute_impact_surface("t.py", deps, {})
    # Same inputs, a set built in a different insertion order.
    deps2 = {"t.py": {"d.py", "c.py", "a.py", "b.py"}}
    second = _compute_impact_surface("t.py", deps2, {})

    assert [r["file_path"] for r in first] == ["a.py", "b.py", "c.py"]
    assert first == second


def test_truncation_keys_present_when_something_was_dropped():
    """The other direction. A silent drop is the one failure this must not have."""
    targets = {
        f"file_{i}.py": {
            "target": f"file_{i}.py",
            "docs": {"symbols": [{"name": f"sym_{n}", "doc": "x" * 400} for n in range(40)]},
        }
        for i in range(30)
    }
    out = truncate_to_budget({"targets": targets, "_meta": {}}, char_budget=2_000)
    assert out["truncated"] is True
    assert out["dropped_targets"] or out["dropped_symbols"]
