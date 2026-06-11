"""Payload diet for get_answer (B1/B2).

* serialize_hits strips internal scoring fields — zero plumbing reaches
  the agent (path/title/summary/snippet/excerpt/score/key_symbols only)
* the retrieval block is confidence-conditional: high → none, medium →
  top-2 truncated, low/gated → full serialized block
* regression guard: no response key may start with "_" except _meta
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.server.mcp_server.tool_answer.retrieval import serialize_hits

# ---------------------------------------------------------------------------
# serialize_hits unit tests
# ---------------------------------------------------------------------------

RAW_HIT = {
    "page_id": "file_page:pkg/mod.py",
    "target_path": "pkg/mod.py",
    "title": "mod",
    "summary": "A module that does things. " * 20,
    "snippet": "def f(): ...",
    "score": 4.321987,
    "_coverage": 0.4,
    "_raw_score": 3.9,
    "_intersection": True,
    "_pagerank": 0.002,
    "_pagerank_bias": 1.01,
    "_sources": {"fts", "vector"},
    "_domain_penalty": None,
    "symbols": [
        {"name": "f", "kind": "function", "signature": "def f()", "_matched": True},
    ],
}


class TestSerializeHits:
    def test_internal_fields_stripped(self) -> None:
        [entry] = serialize_hits([dict(RAW_HIT)])
        assert not [k for k in entry if k.startswith("_")]
        assert "page_id" not in entry
        assert entry["path"] == "pkg/mod.py"
        assert entry["score"] == 4.322

    def test_symbol_internal_fields_stripped(self) -> None:
        [entry] = serialize_hits([dict(RAW_HIT)])
        [sym] = entry["key_symbols"]
        assert "_matched" not in sym
        assert sym["signature"] == "def f()"

    def test_summary_truncation(self) -> None:
        [entry] = serialize_hits([dict(RAW_HIT)], summary_chars=160)
        assert len(entry["summary"]) <= 160

    def test_limit(self) -> None:
        hits = [dict(RAW_HIT), dict(RAW_HIT), dict(RAW_HIT)]
        assert len(serialize_hits(hits, limit=2)) == 2

    def test_graph_expanded_hit_loses_symbols_when_asked(self) -> None:
        expanded = dict(RAW_HIT, _sources={"graph_expand"})
        [entry] = serialize_hits([expanded], symbols_for_expanded=False)
        assert "key_symbols" not in entry
        [kept] = serialize_hits([expanded], symbols_for_expanded=True)
        assert "key_symbols" in kept


# ---------------------------------------------------------------------------
# Confidence-conditional retrieval through get_answer
# ---------------------------------------------------------------------------


def _assert_no_underscore_keys(obj, path="root"):
    if isinstance(obj, dict):
        for k, v in obj.items():
            assert not (str(k).startswith("_") and k != "_meta"), (
                f"internal key {k!r} leaked at {path}"
            )
            if k != "_meta":
                _assert_no_underscore_keys(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _assert_no_underscore_keys(v, f"{path}[{i}]")


def _patch_pipeline(monkeypatch, answer_mod, scores=(5.0, 4.0)):
    async def _fake_retrieve(question, ctx):
        return [
            {"page_id": "file_page:pkg/beta/one.py", "score": scores[0], "_sources": {"fts"}},
            {"page_id": "file_page:pkg/beta/two.py", "score": scores[1], "_sources": {"fts"}},
        ]

    async def _fake_hydrate(hits, ctx, *, scope=None):
        for i, h in enumerate(hits):
            h["target_path"] = h["page_id"].removeprefix("file_page:")
            h["title"] = h["target_path"]
            h["summary"] = "Summary text for the page. " * 12
            h["snippet"] = ""
            h["page_type"] = "file_page"
            if i == 0:
                h["symbols"] = [
                    {
                        "name": "go",
                        "kind": "function",
                        "signature": "def go()",
                        "docstring": "",
                        "start_line": 3,
                        "end_line": 9,
                        "_matched": True,
                    }
                ]
        return hits

    monkeypatch.setattr(answer_mod, "_hybrid_retrieve", _fake_retrieve)
    monkeypatch.setattr(answer_mod, "_hydrate_hits", _fake_hydrate)


def _patch_provider(monkeypatch, answer_mod, content):
    class _Provider:
        provider_name = "mock"
        model_name = "mock-1"

        async def generate(self, **kwargs):
            return SimpleNamespace(content=content)

    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: _Provider())


@pytest.mark.asyncio
async def test_high_confidence_drops_retrieval_block(setup_mcp, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, scores=(5.0, 4.0))
    _patch_provider(monkeypatch, answer_mod, "The go() function drives it (pkg/beta/one.py).")

    result = await get_answer("how does the beta module go function work")
    assert result["confidence"] == "high"
    assert result["retrieval"] == []
    assert result["fallback_targets"], "routing targets survive the diet"
    _assert_no_underscore_keys(result)


@pytest.mark.asyncio
async def test_medium_confidence_keeps_two_truncated_hits(setup_mcp, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    # ratio 1.27 >= 1.2 but top score < 1.5 floor → medium.
    _patch_pipeline(monkeypatch, answer_mod, scores=(1.4, 1.1))
    _patch_provider(monkeypatch, answer_mod, "The go() function drives it (pkg/beta/one.py).")

    result = await get_answer("how does the beta module go function work")
    assert result["confidence"] == "medium"
    assert 0 < len(result["retrieval"]) <= 2
    for entry in result["retrieval"]:
        assert len(entry.get("summary", "")) <= 160
    _assert_no_underscore_keys(result)


@pytest.mark.asyncio
async def test_gated_path_serves_clean_retrieval(setup_mcp, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    # 2.0 vs 1.9: top < 3.0 and ratio 1.05 < 1.2 → gated, no synthesis.
    _patch_pipeline(monkeypatch, answer_mod, scores=(2.0, 1.9))
    _patch_provider(monkeypatch, answer_mod, "unused")

    result = await get_answer("how does the beta module go function work")
    assert result["confidence"] == "low"
    assert result["best_guesses"]
    for entry in result["retrieval"]:
        assert "page_id" not in entry
        assert entry.get("path")
    _assert_no_underscore_keys(result)


@pytest.mark.asyncio
async def test_cached_response_carries_no_internal_keys(setup_mcp, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, scores=(5.0, 4.0))
    _patch_provider(monkeypatch, answer_mod, "The go() function drives it (pkg/beta/one.py).")

    await get_answer("how does the beta module go function work")
    cached = await get_answer("how does the beta module go function work")
    assert cached["_meta"].get("cached") is True
    _assert_no_underscore_keys(cached)
