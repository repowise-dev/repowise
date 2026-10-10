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
from repowise.server.mcp_server._page_paths import add_row_paths
from repowise.server.mcp_server.tool_answer.answer import (
    _build_best_guesses,
    _drop_duplicated_guess_excerpts,
    _trim_served_payload,
)
from repowise.server.mcp_server.tool_answer.retrieval import _CANDIDATE_LIMIT
from repowise.server.mcp_server.tool_search import (
    _assign_confidence,
    _drop_derivable_confidence,
    _drop_derivable_page_ids,
    _slim_served_rows,
)

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


def test_page_id_dropped_after_add_row_paths_moved_target_path_to_path():
    """The shape every search response actually has at this point.

    ``add_row_paths`` runs first and deletes ``target_path`` where it equals
    ``path``, so an ordinary file row reaches the drop with only ``path``.
    """
    results = [
        {
            "page_id": "file_page:rich/ansi.py",
            "page_type": "file_page",
            "target_path": "rich/ansi.py",
            "title": "ansi",
        }
    ]
    add_row_paths(results)
    assert "target_path" not in results[0]
    _drop_derivable_page_ids(results)
    assert "page_id" not in results[0]
    assert f"{results[0]['page_type']}:{results[0]['path']}" == "file_page:rich/ansi.py"


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


# ---------------------------------------------------------------------------
# search_codebase.results[] — one location per row
# ---------------------------------------------------------------------------


def _row(page_type, target, title, **extra):
    row = {
        "page_id": f"{page_type}:{target}",
        "page_type": page_type,
        "target_path": target,
        "title": title,
        "sources": ["fts"],
        **extra,
    }
    return row


def _keyless(monkeypatch, value=False):
    monkeypatch.setattr(
        "repowise.server.mcp_server.tool_search.semantic_search_state", lambda: value
    )


def test_file_page_row_keeps_only_path(monkeypatch):
    _keyless(monkeypatch)
    (row,) = _slim_served_rows([_row("file_page", "src/a.py", "File: src/a.py")])
    assert row["path"] == "src/a.py"
    assert not {"page_id", "title", "file", "target_path", "sources"} & row.keys()


def test_symbol_page_row_keeps_path_and_symbol_id(monkeypatch):
    _keyless(monkeypatch)
    row = _row(
        "symbol_spotlight",
        "src/a.py::f",
        "Symbol: src/a.py::f",
        symbol_id="src/a.py::f",
        file="src/a.py",
    )
    row["target_path"] = "src/a.py"
    row["page_id"] = "symbol_spotlight:src/a.py::f"
    (row,) = _slim_served_rows([row])
    assert row["path"] == "src/a.py" and row["symbol_id"] == "src/a.py::f"
    assert not {"page_id", "title", "file"} & row.keys()


def test_prose_titles_and_unrebuildable_ids_stay(monkeypatch):
    _keyless(monkeypatch)
    module = _row("module_page", "pkg/cmd", "The command layer")
    edited = _row("file_page", "src/b.py", "Why b.py exists")
    unloaded = {"page_id": "file_page:x.py", "page_type": "file_page", "target_path": ""}
    _slim_served_rows([module, edited, unloaded])
    # A module page has no ``path``; its id rebuilds from ``target_path``, which stays.
    assert module["title"] == "The command layer" and module["target_path"] == "pkg/cmd"
    assert edited["title"] == "Why b.py exists" and "page_id" not in edited
    assert unloaded["page_id"] == "file_page:x.py"


def test_localized_structural_title_is_dropped_per_repo_language(monkeypatch):
    _keyless(monkeypatch)
    de = _row("file_page", "src/a.py", "Datei: src/a.py", repo="de-repo")
    en = _row("file_page", "src/a.py", "File: src/a.py", repo="en-repo")
    wrong = _row("file_page", "src/a.py", "Datei: src/a.py", repo="en-repo")
    _slim_served_rows([de, en, wrong], {"de-repo": "de", "en-repo": "en"})
    assert "title" not in de and "title" not in en
    assert wrong["title"] == "Datei: src/a.py"


def test_sources_dropped_only_when_fts_only_and_keyless(monkeypatch):
    _keyless(monkeypatch, False)
    fts, sym, both = (
        _row("file_page", "a.py", "A", sources=["fts"]),
        _row("file_page", "b.py", "B", sources=["symbol"]),
        _row("file_page", "c.py", "C", sources=["fts", "vector"]),
    )
    _slim_served_rows([fts, sym, both])
    assert "sources" not in fts
    assert sym["sources"] == ["symbol"] and both["sources"] == ["fts", "vector"]

    for state in (True, None):
        _keyless(monkeypatch, state)
        (kept,) = _slim_served_rows([_row("file_page", "d.py", "D", sources=["fts"])])
        assert kept["sources"] == ["fts"]


# -- confidence_score -------------------------------------------------------


def _scored(*scores: float) -> list[dict]:
    rows = [{"relevance_score": score} for score in scores]
    _assign_confidence(rows, "relevance_score", "confidence_score")
    return rows


def test_a_confidence_that_is_relevance_over_top_is_dropped() -> None:
    rows = _drop_derivable_confidence(_scored(18.08, 14.75, 0.43))
    assert all("confidence_score" not in row for row in rows)
    assert [row["relevance_score"] for row in rows] == [18.08, 14.75, 0.43]


def test_a_capped_row_keeps_its_confidence() -> None:
    capped, plain = _scored(0.45, 0.3)
    capped["relation"] = "related, not the named symbol"
    capped["confidence_score"] = 0.45
    _drop_derivable_confidence([capped, plain])
    assert capped["confidence_score"] == 0.45
    assert "confidence_score" not in plain


def test_a_confidence_that_differs_from_the_derivation_is_kept() -> None:
    rows = _scored(10.0, 5.0)
    rows[1]["confidence_score"] = 0.9
    _drop_derivable_confidence(rows)
    assert rows[1]["confidence_score"] == 0.9
    assert "confidence_score" not in rows[0]


def test_nothing_is_dropped_when_there_is_no_positive_top_score() -> None:
    rows = _scored(0, 0)
    _drop_derivable_confidence(rows)
    assert [row["confidence_score"] for row in rows] == [0.0, 0.0]
    assert _drop_derivable_confidence([]) == []
