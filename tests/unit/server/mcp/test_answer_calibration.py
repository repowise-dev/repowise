"""Confidence calibration gates for get_answer.

Covers the three A-series gates that ground confidence in answer *content*
rather than retrieval scores alone:

  * expanded hedge markers ("do not include", "can't enumerate", …)
  * value-grounding gate — numbers asserted on value-shaped questions must
    appear in the retrieved material, else confidence caps at low
  * citation-source gate — high confidence requires ≥1 cited page that
    contributed actual source (hydrated symbols), not just summaries
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.server.mcp_server.tool_answer.confidence import (
    _answer_is_hedged,
    _distinctive_terms,
    _frame_term_grounding,
    _is_value_question,
    _ungrounded_numbers,
)

# ---------------------------------------------------------------------------
# Pure-predicate unit tests
# ---------------------------------------------------------------------------


class TestHedgeMarkers:
    def test_do_not_include_detected(self) -> None:
        assert _answer_is_hedged("The provided wiki excerpts do not include the source body.")

    def test_cannot_enumerate_detected(self) -> None:
        assert _answer_is_hedged("I can't enumerate the exact callers from the excerpts.")

    def test_unable_to_determine_detected(self) -> None:
        assert _answer_is_hedged("The exact default is unable to determine from this material.")

    def test_marker_late_in_answer_detected(self) -> None:
        long_preamble = "The module handles ingestion. " * 40
        assert _answer_is_hedged(long_preamble + "However, the excerpts do not include the value.")

    def test_direct_answer_not_hedged(self) -> None:
        assert not _answer_is_hedged("The default is 2, set in git_indexer/_constants.py.")

    def test_curly_apostrophe_hedge_detected(self) -> None:
        # LLMs routinely emit the curly U+2019; the markers use plain ASCII.
        # Without normalization this hedged answer rides through as high.
        assert _answer_is_hedged("I can\u2019t determine why the threshold is 1.2 from the excerpts.")


class TestValueQuestionShape:
    def test_default_question_is_value_shaped(self) -> None:
        assert _is_value_question("What is the default value of _MIN_COUNT?")

    def test_threshold_question_is_value_shaped(self) -> None:
        assert _is_value_question("what threshold gates the dominance ratio")

    def test_how_many_is_value_shaped(self) -> None:
        assert _is_value_question("How many retrieval hits are returned?")

    def test_mechanism_question_is_not(self) -> None:
        assert not _is_value_question("How does the ingestion pipeline parse imports?")


class TestUngroundedNumbers:
    HITS = [
        {
            "title": "constants",
            "summary": "Co-change tuning constants.",
            "snippet": "",
            "symbols": [
                {
                    "name": "_DEFAULT_CO_CHANGE_MIN_COUNT",
                    "signature": "_DEFAULT_CO_CHANGE_MIN_COUNT = 2",
                    "docstring": "",
                }
            ],
        }
    ]

    def test_invented_number_is_ungrounded(self) -> None:
        assert _ungrounded_numbers("The default minimum count is 3.", self.HITS) == ["3"]

    def test_number_present_in_signature_is_grounded(self) -> None:
        assert _ungrounded_numbers("The default minimum count is 2.", self.HITS) == []

    def test_file_line_citations_are_not_value_assertions(self) -> None:
        text = "The default is 2 (git_indexer/_constants.py:114)."
        assert _ungrounded_numbers(text, self.HITS) == []

    def test_no_numbers_in_answer_is_grounded(self) -> None:
        assert _ungrounded_numbers("It reads the tuning constant from config.", self.HITS) == []

    def test_underscore_separated_constant_is_grounded(self) -> None:
        # Source ``MAX = 100_000`` and an answer that says "100000" are the
        # same value — the digit-separator strip must keep this grounded.
        hits = [{"symbols": [{"name": "MAX", "signature": "MAX = 100_000", "docstring": ""}]}]
        assert _ungrounded_numbers("The cap is 100000 tokens.", hits) == []

    def test_comma_grouped_answer_is_grounded(self) -> None:
        # Prose commas (``100,000``) must not split into 100 and 000 and read
        # as ungrounded against ``100_000`` in source.
        hits = [{"symbols": [{"name": "MAX", "signature": "MAX = 100_000", "docstring": ""}]}]
        assert _ungrounded_numbers("The cap is 100,000 tokens.", hits) == []


class TestDistinctiveTerms:
    def test_camelcase_kept(self) -> None:
        assert "PageRank" in _distinctive_terms("ordered by the PageRank centrality score")

    def test_snake_case_kept(self) -> None:
        assert "apply_pagerank_bias" in _distinctive_terms("calls apply_pagerank_bias on the set")

    def test_plain_lowercase_dropped(self) -> None:
        # Common technical English is too broad to gate on.
        assert _distinctive_terms("the centrality computation caps neighbors") == set()

    def test_capitalized_prose_dropped(self) -> None:
        # Sentence-initial / markdown-header words are prose, not mechanisms.
        # A leading capital alone must not register as a frame term (the live
        # over-fire that flagged Because/Determine/Mechanism/Short/Since/What).
        assert _distinctive_terms(
            "## What happens. Because the Mechanism is Short, Determine it Since When."
        ) == set()

    def test_internal_caps_and_acronyms_kept(self) -> None:
        terms = _distinctive_terms("It wraps WikiSymbol over an HTTP transport.")
        assert "WikiSymbol" in terms
        assert "HTTP" in terms  # >=4-char all-caps acronym (internal upper)

    def test_short_tokens_dropped(self) -> None:
        assert _distinctive_terms("py id db v2") == set()  # all <4 chars

    def test_citation_path_yields_no_term(self) -> None:
        # A lowercase dotted filename is not a distinctive mechanism term.
        assert _distinctive_terms("see pkg/alpha/one.py for details") == set()


class TestFrameTermGrounding:
    HITS = [
        {
            "title": "enrichment",
            "summary": "Caller/callee rollup for symbol context.",
            "snippet": "",
            "target_path": "mcp/enrichment.py",
            "symbols": [
                {
                    "name": "_count_call_neighbors",
                    "signature": "def _count_call_neighbors(...)",
                    "docstring": "Counts distinct caller neighbors above confidence.",
                }
            ],
        }
    ]

    def test_foreign_frame_term_is_ungrounded(self) -> None:
        ungrounded, grounded = _frame_term_grounding(
            "The caller list is limited because the PageRank cap bounds neighbors.",
            "why is the caller list limited",
            self.HITS,
        )
        assert ungrounded == ["PageRank"]
        assert grounded == 0

    def test_grounded_frame_term_is_not_flagged(self) -> None:
        ungrounded, grounded = _frame_term_grounding(
            "The list is bounded by _count_call_neighbors (mcp/enrichment.py).",
            "why is the caller list limited",
            self.HITS,
        )
        assert ungrounded == []
        assert grounded == 1

    def test_question_named_term_is_excluded(self) -> None:
        # Echoing the user's own framing is not an invented frame.
        ungrounded, _ = _frame_term_grounding(
            "It uses PageRank ordering.",
            "why does the caller list use PageRank ordering",
            self.HITS,
        )
        assert ungrounded == []


# ---------------------------------------------------------------------------
# End-to-end gate behaviour through get_answer
# ---------------------------------------------------------------------------

_SYMBOL = {
    "name": "MIN_COUNT",
    "kind": "constant",
    "signature": "MIN_COUNT = 2",
    "docstring": "",
    "start_line": 10,
    "end_line": 10,
    "_matched": True,
}

# Function-kind variant: matched, but not extractable by the C1 fast path —
# used by tests that must reach the synthesis gates on value questions.
_FN_SYMBOL = {
    "name": "min_count_policy",
    "kind": "function",
    "signature": "def min_count_policy() -> int",
    "docstring": "Returns the default minimum count, 2.",
    "start_line": 10,
    "end_line": 14,
    "_matched": True,
}


def _patch_pipeline(monkeypatch, answer_mod, *, with_symbols: bool, symbol: dict | None = None):
    async def _fake_retrieve(question, ctx):
        return [
            {"page_id": "file_page:pkg/alpha/one.py", "score": 5.0},
            {"page_id": "file_page:pkg/alpha/two.py", "score": 4.0},
        ]

    async def _fake_hydrate(hits, ctx, *, scope=None):
        for i, h in enumerate(hits):
            h["target_path"] = h["page_id"].removeprefix("file_page:")
            h["title"] = h["target_path"]
            h["summary"] = "Auth service summary."
            h["snippet"] = ""
            h["page_type"] = "file_page"
            if with_symbols and i == 0:
                h["symbols"] = [dict(symbol or _SYMBOL)]
        return hits

    monkeypatch.setattr(answer_mod, "_hybrid_retrieve", _fake_retrieve)
    monkeypatch.setattr(answer_mod, "_hydrate_hits", _fake_hydrate)


def _patch_provider(monkeypatch, answer_mod, content: str):
    class _Provider:
        provider_name = "mock"
        model_name = "mock-1"

        async def generate(self, **kwargs):
            return SimpleNamespace(content=content)

    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: _Provider())


@pytest.mark.asyncio
async def test_ungrounded_value_caps_confidence_low(setup_mcp, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, with_symbols=True, symbol=_FN_SYMBOL)
    _patch_provider(
        monkeypatch,
        answer_mod,
        "The default of min_count_policy is 3 (pkg/alpha/one.py).",
    )

    result = await get_answer("What is the default value of min_count_policy?")
    assert result["confidence"] == "low"
    assert "3" in result["note"]
    assert "next_action_hint" in result


@pytest.mark.asyncio
async def test_grounded_value_keeps_high_confidence(setup_mcp, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, with_symbols=True, symbol=_FN_SYMBOL)
    _patch_provider(
        monkeypatch,
        answer_mod,
        "The default of min_count_policy is 2 (pkg/alpha/one.py).",
    )

    result = await get_answer("What is the default value of min_count_policy?")
    assert result["confidence"] == "high"
    assert "High confidence" in result["note"]


@pytest.mark.asyncio
async def test_high_confidence_requires_source_backed_citation(setup_mcp, monkeypatch):
    """No cited page contributed symbols → high is downgraded to medium."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, with_symbols=False)
    _patch_provider(
        monkeypatch,
        answer_mod,
        "Auth flows through middleware into the service (pkg/alpha/one.py).",
    )

    result = await get_answer("how does the auth flow work end to end")
    assert result["confidence"] == "medium"


@pytest.mark.asyncio
async def test_expanded_hedge_marker_downgrades_through_pipeline(setup_mcp, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, with_symbols=True, symbol=_FN_SYMBOL)
    _patch_provider(
        monkeypatch,
        answer_mod,
        "The provided excerpts do not include the body of min_count_policy.",
    )

    result = await get_answer("What is the default value of min_count_policy?")
    assert result["confidence"] == "low"
    assert result["retrieval"] == []


@pytest.mark.asyncio
async def test_value_question_uses_extraction_fast_path(setup_mcp, monkeypatch):
    """C1: matched constant + value question → verbatim line, no LLM call."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, with_symbols=True)

    def _no_provider(_p):
        raise AssertionError("fast path must not resolve a provider")

    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", _no_provider)

    result = await get_answer("What is the default value of MIN_COUNT?")
    assert result["grounding"] == "extracted"
    assert result["confidence"] == "high"
    assert "MIN_COUNT = 2" in result["answer"]
    assert "pkg/alpha/one.py:10" in result["answer"]
    assert result["citations"] == ["pkg/alpha/one.py"]
    assert result["retrieval"] == []


@pytest.mark.asyncio
async def test_mechanism_question_skips_fast_path(setup_mcp, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, with_symbols=True)
    _patch_provider(monkeypatch, answer_mod, "MIN_COUNT gates retries (pkg/alpha/one.py).")

    result = await get_answer("How does MIN_COUNT influence the retry loop?")
    assert "grounding" not in result


@pytest.mark.asyncio
async def test_synthesized_answer_carries_grounded_quotes(setup_mcp, monkeypatch):
    """C2: symbols named in the answer attach {path, lines, quote}."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, with_symbols=True)
    _patch_provider(monkeypatch, answer_mod, "Retries are gated by MIN_COUNT (pkg/alpha/one.py).")

    result = await get_answer("How does the retry gating mechanism work here?")
    assert result.get("quotes"), "answer naming MIN_COUNT must carry a quote"
    [q] = result["quotes"]
    assert q["path"] == "pkg/alpha/one.py"
    assert q["lines"][0] == 10
    assert "MIN_COUNT = 2" in q["quote"]
    # A constant's body IS its one-line assignment — it belongs in `quotes`,
    # never the `symbol_bodies` definition block.
    assert not result.get("symbol_bodies")


_FN_BODY_SYMBOL = {
    "name": "min_count_policy",
    "kind": "function",
    "signature": "def min_count_policy() -> int",
    "docstring": "Returns the default minimum count.",
    "start_line": 10,
    "end_line": 12,
    "_matched": True,
    "source_excerpt": "def min_count_policy() -> int:\n    # gate retries\n    return MIN_COUNT",
}


@pytest.mark.asyncio
async def test_named_function_carries_inline_body(setup_mcp, monkeypatch):
    """A named function symbol surfaces its full body in symbol_bodies so the
    agent skips the get_symbol follow-up."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, with_symbols=True, symbol=_FN_BODY_SYMBOL)
    _patch_provider(
        monkeypatch,
        answer_mod,
        "min_count_policy gates the retry loop (pkg/alpha/one.py).",
    )

    result = await get_answer("How does min_count_policy work?")
    bodies = result.get("symbol_bodies")
    assert bodies, "a named function symbol must carry an inline body"
    [b] = bodies
    assert b["path"] == "pkg/alpha/one.py"
    assert b["name"] == "min_count_policy"
    assert "return MIN_COUNT" in b["source"]
    assert b["lines"] == [10, 12]
    # Body served whole → no continuation pointer.
    assert "truncated" not in b


@pytest.mark.asyncio
async def test_inline_body_truncation_emits_continuation(setup_mcp, monkeypatch):
    """When the indexed body is longer than the hydrated excerpt, the body
    block names the exact range read for the remainder."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    big = dict(_FN_BODY_SYMBOL, end_line=60)  # excerpt covers 10-12, body ends at 60
    _patch_pipeline(monkeypatch, answer_mod, with_symbols=True, symbol=big)
    _patch_provider(
        monkeypatch,
        answer_mod,
        "min_count_policy gates the retry loop (pkg/alpha/one.py).",
    )

    result = await get_answer("How does min_count_policy work?")
    [b] = result["symbol_bodies"]
    assert b["truncated"] is True
    assert b["continuation"] == "pkg/alpha/one.py:13-60"


# ---------------------------------------------------------------------------
# Hedge + served named-symbol body — a hedge downgrades the PROSE, but when the
# exact symbol the question named is inlined as a live body, the answer's ground
# truth is already served. The response must not read "low, go Read" (which
# contradicts the payload); it holds at medium with grounding="symbol_body".
# ---------------------------------------------------------------------------


def _patch_anchor(monkeypatch, answer_mod, anchored: dict):
    """Force symbol anchoring to attach ``anchored`` to the top hit, as if the
    question named an indexed symbol whose defining file got promoted."""

    async def _fake_anchor(session, repo_id, question_ids, hits, **kwargs):
        if hits:
            hits[0]["_anchor_symbols"] = [anchored]
        return hits, {"union": {}, "qualified_miss": []}

    monkeypatch.setattr(answer_mod, "_anchor_symbol_hits", _fake_anchor)


@pytest.mark.asyncio
async def test_hedged_but_named_body_served_holds_medium(setup_mcp, monkeypatch, tmp_path):
    """Hedged synthesis + the exact question-named symbol inlined as a live body
    → medium with grounding='symbol_body', not low. The 'low → Read' hint the
    body makes unnecessary must not fire."""
    import repowise.server.mcp_server as mcp_mod
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    (tmp_path / "pkg" / "alpha").mkdir(parents=True)
    (tmp_path / "pkg" / "alpha" / "one.py").write_text(
        "def min_count_policy() -> int:\n"
        "    # gate retries at the floor\n"
        "    return MIN_COUNT\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mcp_mod, "_repo_path", str(tmp_path))

    _patch_pipeline(monkeypatch, answer_mod, with_symbols=True, symbol=_FN_SYMBOL)
    _patch_anchor(
        monkeypatch,
        answer_mod,
        {
            "name": "min_count_policy",
            "kind": "function",
            "start_line": 1,
            "end_line": 3,
        },
    )
    _patch_provider(
        monkeypatch,
        answer_mod,
        "The provided excerpts do not include the full body of min_count_policy.",
    )

    result = await get_answer("How does min_count_policy work?")
    assert result["confidence"] == "medium"
    assert result["grounding"] == "symbol_body"
    [b] = result["symbol_bodies"]
    assert b["name"] == "min_count_policy"
    assert "return MIN_COUNT" in b["source"]
    assert "cite that directly" in result["note"]
    # The low-confidence "Read the fallback_targets" hint must be gone.
    assert "Low confidence" not in (result["_meta"].get("hint") or "")


@pytest.mark.asyncio
async def test_hedged_without_named_body_stays_low(setup_mcp, monkeypatch):
    """The same hedge, but no anchored body is served → still low (the negative
    control for the grounding bump)."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, with_symbols=True, symbol=_FN_SYMBOL)
    _patch_provider(
        monkeypatch,
        answer_mod,
        "The provided excerpts do not include the full body of min_count_policy.",
    )

    result = await get_answer("How does min_count_policy work?")
    assert result["confidence"] == "low"
    assert "grounding" not in result


# ---------------------------------------------------------------------------
# Symbol anchoring — force the defining file of a question-named symbol into
# the candidate set when fuzzy retrieval missed it.
# ---------------------------------------------------------------------------


class _Sym:
    def __init__(
        self,
        name,
        file_path,
        kind="method",
        parent_name=None,
        qualified_name=None,
        start_line=1,
        end_line=10,
    ):
        self.name = name
        self.file_path = file_path
        self.kind = kind
        self.parent_name = parent_name
        self.qualified_name = qualified_name or name
        self.start_line = start_line
        self.end_line = end_line


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, *a, **k):
        return _FakeResult(self._rows)


@pytest.mark.asyncio
async def test_anchor_injects_defining_file_as_dominant_hit():
    from repowise.server.mcp_server.tool_answer.symbols import _anchor_symbol_hits

    rows = [
        _Sym(
            "extract_all",
            "core/decisions/extractor.py",
            parent_name="DecisionExtractor",
            qualified_name="DecisionExtractor.extract_all",
        )
    ]
    hits = [{"target_path": "core/pipeline/incremental.py", "score": 3.6}]
    out, homonyms = await _anchor_symbol_hits(
        _FakeSession(rows), "repo1", {"extract_all", "DecisionExtractor"}, hits
    )
    assert out[0]["target_path"] == "core/decisions/extractor.py"
    assert out[0]["_symbol_anchored"] is True
    assert out[0]["score"] > 3.6  # dominates the fuzzy top hit
    assert not homonyms["union"]  # single resolved def, no union


@pytest.mark.asyncio
async def test_anchor_unresolvable_homonym_becomes_union():
    """A bare homonym (no qualifier) is not injected as a hit — its full def
    set is returned in homonyms['union'] for the answer-by-union path."""
    from repowise.server.mcp_server.tool_answer.symbols import _anchor_symbol_hits

    rows = [
        _Sym("extract_all", "a/x.py", parent_name="A", qualified_name="A.extract_all"),
        _Sym("extract_all", "b/y.py", parent_name="B", qualified_name="B.extract_all"),
    ]
    hits = [{"target_path": "c/z.py", "score": 2.0}]
    out, homonyms = await _anchor_symbol_hits(_FakeSession(rows), "r", {"extract_all"}, hits)
    assert all(not h.get("_symbol_anchored") for h in out)
    assert out[0]["target_path"] == "c/z.py"  # nothing injected into hits
    assert {d["file_path"] for d in homonyms["union"]["extract_all"]} == {"a/x.py", "b/y.py"}
    assert not homonyms["qualified_miss"]


@pytest.mark.asyncio
async def test_anchor_qualified_miss_records_not_found():
    """The question qualifies the name (Foo.extract_all) but no def sits under
    Foo — record a qualified miss instead of guessing another Parent's def."""
    from repowise.server.mcp_server.tool_answer.symbols import _anchor_symbol_hits

    rows = [
        _Sym("extract_all", "a/x.py", parent_name="A", qualified_name="A.extract_all"),
        _Sym("extract_all", "b/y.py", parent_name="B", qualified_name="B.extract_all"),
    ]
    hits = [{"target_path": "c/z.py", "score": 2.0}]
    out, homonyms = await _anchor_symbol_hits(
        _FakeSession(rows), "r", {"extract_all", "Foo.extract_all", "Foo"}, hits
    )
    assert homonyms["qualified_miss"] == ["extract_all"]
    assert not homonyms["union"]  # a qualified miss must NOT fall back to a union
    assert all(not h.get("_symbol_anchored") for h in out)


@pytest.mark.asyncio
async def test_anchor_disambiguates_homonym_by_named_parent():
    from repowise.server.mcp_server.tool_answer.symbols import _anchor_symbol_hits

    rows = [
        _Sym("extract_all", "a/x.py", parent_name="Alpha", qualified_name="Alpha.extract_all"),
        _Sym(
            "extract_all",
            "b/y.py",
            parent_name="DecisionExtractor",
            qualified_name="DecisionExtractor.extract_all",
        ),
    ]
    hits = [{"target_path": "c/z.py", "score": 2.0}]
    out, homonyms = await _anchor_symbol_hits(
        _FakeSession(rows), "r", {"extract_all", "DecisionExtractor"}, hits
    )
    assert out[0]["target_path"] == "b/y.py"
    assert not homonyms["union"]


@pytest.mark.asyncio
async def test_anchor_boosts_existing_hit_without_duplicating():
    from repowise.server.mcp_server.tool_answer.symbols import _anchor_symbol_hits

    rows = [_Sym("get_symbol", "mcp/tool_symbol.py", kind="function", qualified_name="get_symbol")]
    hits = [
        {"target_path": "mcp/tool_symbol.py", "score": 9.4},
        {"target_path": "other.py", "score": 7.9},
    ]
    out, _homonyms = await _anchor_symbol_hits(_FakeSession(rows), "r", {"get_symbol"}, hits)
    paths = [h["target_path"] for h in out]
    assert paths.count("mcp/tool_symbol.py") == 1  # boosted, not duplicated
    anchored = next(h for h in out if h["target_path"] == "mcp/tool_symbol.py")
    assert anchored["_symbol_anchored"] is True
    assert anchored["score"] >= 9.4


def _write(tmp_path, rel, body):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return rel


def test_union_bodies_inlines_all_defs_under_budget(tmp_path):
    """Answer-by-union renders every def body (Read-parity) when they fit."""
    from repowise.server.mcp_server.tool_answer.symbols import build_homonym_union_bodies

    a = _write(tmp_path, "a/x.py", "def _sev(op):\n    return op > 3\n")
    b = _write(tmp_path, "b/y.py", "def _sev(corr):\n    return corr < 0.1\n")
    union = {
        "_sev": [
            {"name": "_sev", "kind": "function", "file_path": a, "start_line": 1, "end_line": 2,
             "qualified_name": "_sev", "parent_name": None},
            {"name": "_sev", "kind": "function", "file_path": b, "start_line": 1, "end_line": 2,
             "qualified_name": "_sev", "parent_name": None},
        ]
    }
    bodies, more = build_homonym_union_bodies(tmp_path, union)
    assert len(bodies) == 2 and not more
    assert {e["path"] for e in bodies} == {a, b}
    assert "op > 3" in bodies[0]["source"] and "corr < 0.1" in bodies[1]["source"]
    assert bodies[0]["lines"] == [1, 2]


def test_union_bodies_overflow_lists_remainder_as_pointers(tmp_path):
    """Under a tiny budget, first def always renders; the rest go to
    more_definitions with a do-NOT-Read get_symbol redirect."""
    from repowise.server.mcp_server.tool_answer.symbols import build_homonym_union_bodies

    a = _write(tmp_path, "a/x.py", "def _sev(op):\n    return op > 3\n")
    b = _write(tmp_path, "b/y.py", "def _sev(corr):\n    return corr < 0.1\n")
    union = {
        "_sev": [
            {"name": "_sev", "kind": "function", "file_path": a, "start_line": 1, "end_line": 2,
             "qualified_name": "_sev", "parent_name": None},
            {"name": "_sev", "kind": "function", "file_path": b, "start_line": 1, "end_line": 2,
             "qualified_name": "_sev", "parent_name": None},
        ]
    }
    bodies, more = build_homonym_union_bodies(tmp_path, union, char_budget=5)
    assert len(bodies) == 1  # first always renders even over budget
    assert len(more) == 1
    assert more[0]["symbol_id"] == f"{b}::_sev"
    assert "do NOT Read" in more[0]["hint"]


# ---------------------------------------------------------------------------
# code_rationale — the T4 lever: in-code rationale recovered when the wiki /
# decision corpus could not ground the question (low-confidence exits).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hedged_answer_surfaces_code_rationale(setup_mcp, monkeypatch, tmp_path):
    """Hedged synthesis → mine the cited source for the rationale comment."""
    import repowise.server.mcp_server as mcp_mod
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, with_symbols=True, symbol=_FN_SYMBOL)
    _patch_provider(
        monkeypatch,
        answer_mod,
        "The provided excerpts do not include the body of min_count_policy.",
    )
    (tmp_path / "pkg" / "alpha").mkdir(parents=True)
    (tmp_path / "pkg" / "alpha" / "one.py").write_text(
        "# min_count_policy defaults to 2 because the retry budget assumes\n"
        "# at least two attempts before giving up.\n"
        "MIN_COUNT = 2\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mcp_mod, "_repo_path", str(tmp_path))

    result = await get_answer("What is the default value of min_count_policy?")
    assert result["confidence"] == "low"
    assert "code_rationale" in result
    assert any("retry budget" in r["comment"] for r in result["code_rationale"])
    assert result["code_rationale"][0]["path"] == "pkg/alpha/one.py"


@pytest.mark.asyncio
async def test_gated_answer_surfaces_code_rationale(setup_mcp, monkeypatch, tmp_path):
    """Ambiguous retrieval (gated, no synthesis) → still mine source rationale."""
    import repowise.server.mcp_server as mcp_mod
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    # Two near-tied hits (4.0 vs 3.8) → dominance gate fails → best_guesses path.
    async def _fake_retrieve(question, ctx):
        return [
            {"page_id": "file_page:pkg/alpha/one.py", "score": 4.0},
            {"page_id": "file_page:pkg/alpha/two.py", "score": 3.8},
        ]

    async def _fake_hydrate(hits, ctx, *, scope=None):
        for h in hits:
            h["target_path"] = h["page_id"].removeprefix("file_page:")
            h["title"] = h["target_path"]
            h["summary"] = "Module summary."
            h["snippet"] = ""
            h["page_type"] = "file_page"
        return hits

    monkeypatch.setattr(answer_mod, "_hybrid_retrieve", _fake_retrieve)
    monkeypatch.setattr(answer_mod, "_hydrate_hits", _fake_hydrate)

    (tmp_path / "pkg" / "alpha").mkdir(parents=True)
    (tmp_path / "pkg" / "alpha" / "one.py").write_text(
        "# We chunk uploads at 8MB instead of streaming because the gateway\n"
        "# buffers the whole body and OOMs on larger payloads.\n"
        "CHUNK = 8\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mcp_mod, "_repo_path", str(tmp_path))

    result = await get_answer("why do uploads chunk at 8mb")
    assert result["confidence"] == "low"
    assert "best_guesses" in result  # confirms we hit the gated path
    assert "code_rationale" in result
    assert any("OOMs" in r["comment"] for r in result["code_rationale"])


# ---------------------------------------------------------------------------
# Frame-grounding gate — a why-answer naming a mechanism term absent from the
# retrieved source is downgraded from high to medium (conflated rationale).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_why_answer_with_unsupported_frame_downgrades_to_medium(setup_mcp, monkeypatch):
    """High-dominance why-answer whose 'because X' names a term retrieval never
    showed → medium, not high."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, with_symbols=True)
    _patch_provider(
        monkeypatch,
        answer_mod,
        "The caller list is limited because the PageRank centrality cap "
        "bounds neighbors (pkg/alpha/one.py).",
    )

    result = await get_answer("why is the caller list limited the way it is")
    assert result["confidence"] == "medium"
    assert "PageRank" in result["note"]
    assert "Frame-grounding" in result["note"]
    assert "next_action_hint" in result


@pytest.mark.asyncio
async def test_why_answer_with_grounded_frame_stays_high(setup_mcp, monkeypatch):
    """The same why-shape, but the named mechanism (MIN_COUNT) IS in the
    retrieved symbols → confidence stays high."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, with_symbols=True)
    _patch_provider(
        monkeypatch,
        answer_mod,
        "The caller list is limited because MIN_COUNT bounds the retries "
        "(pkg/alpha/one.py).",
    )

    result = await get_answer("why is the caller list limited the way it is")
    assert result["confidence"] == "high"
    assert "High confidence" in result["note"]


@pytest.mark.asyncio
async def test_frame_gate_scoped_to_why_questions(setup_mcp, monkeypatch):
    """A mechanism (non-why) question with the same ungrounded term is NOT
    gated — only rationale claims get the frame check."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, with_symbols=True)
    _patch_provider(
        monkeypatch,
        answer_mod,
        "The caller list is bounded by the PageRank centrality cap "
        "(pkg/alpha/one.py).",
    )

    result = await get_answer("how does the caller list get bounded")
    assert result["confidence"] == "high"


@pytest.mark.asyncio
async def test_frame_gated_answer_surfaces_code_rationale(setup_mcp, monkeypatch, tmp_path):
    """When the frame gate trips, mine the candidate source for the real
    rationale comment so the downgrade ships a lead."""
    import repowise.server.mcp_server as mcp_mod
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, with_symbols=True)
    _patch_provider(
        monkeypatch,
        answer_mod,
        "The caller list is limited because the PageRank cap applies "
        "(pkg/alpha/one.py).",
    )
    (tmp_path / "pkg" / "alpha").mkdir(parents=True)
    (tmp_path / "pkg" / "alpha" / "one.py").write_text(
        "# The caller list is limited to 50 because beyond that the rollup\n"
        "# floods the synthesis context and the agent loses the signal.\n"
        "CALLER_LIMIT = 50\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mcp_mod, "_repo_path", str(tmp_path))

    result = await get_answer("why is the caller list limited the way it is")
    assert result["confidence"] == "medium"
    assert "code_rationale" in result
    assert any("floods the synthesis context" in r["comment"] for r in result["code_rationale"])
