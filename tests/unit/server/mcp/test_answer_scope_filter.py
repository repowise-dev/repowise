"""A scoped get_answer never names a file outside its scope.

Hydration drops out-of-scope retrieval hits, but later stages (graph
expansion here) add files after it. Before the fix those files reached
``best_guesses``, ``fallback_targets``, ``candidate_files`` and the synthesis
prompt of a scoped call.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

_IN = "pkg/alpha/store.py"
# A sibling sharing the scope's spelling: ``pkg/alpha`` is a directory, not a prefix.
_OUT = "pkg/alphabet/retry.py"


def _patch_pipeline(monkeypatch, answer_mod) -> None:
    async def _fake_retrieve(question, ctx):
        return [{"page_id": f"file_page:{_IN}", "score": 5.0}]

    async def _fake_hydrate(hits, ctx, *, scope=None):
        for h in hits:
            h["target_path"] = h["page_id"].removeprefix("file_page:")
            h["title"] = h["target_path"]
            h["summary"] = "Session store."
            h["snippet"] = ""
            h["page_type"] = "file_page"
        return hits

    async def _fake_flow(session, repo_id, hits, question, question_ids):
        return hits, [[_IN, _OUT]]

    async def _fake_expand(hits, ctx, repo_id):
        # A 1-hop neighbour outside the scope, ranked above the scoped hit.
        neighbour = {
            "page_id": f"file_page:{_OUT}",
            "target_path": _OUT,
            "title": _OUT,
            "summary": "Retry with exponential backoff and jitter.",
            "snippet": "",
            "page_type": "file_page",
            "score": 9.0,
        }
        return [neighbour, *hits]

    monkeypatch.setattr(answer_mod, "_hybrid_retrieve", _fake_retrieve)
    monkeypatch.setattr(answer_mod, "_hydrate_hits", _fake_hydrate)
    monkeypatch.setattr(answer_mod, "_expand_via_graph", _fake_expand)
    monkeypatch.setattr(answer_mod, "_expand_via_flow_path", _fake_flow)


@pytest.mark.asyncio
async def test_keyless_scoped_answer_names_nothing_outside_the_scope(setup_mcp, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod)
    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: None)

    result = await get_answer("how does the retry backoff work?", scope="pkg/alpha")

    assert _OUT not in json.dumps(result)
    assert _IN in json.dumps(result)


@pytest.mark.asyncio
async def test_synthesized_scoped_answer_names_nothing_outside_the_scope(setup_mcp, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod)
    prompts: list[str] = []

    class _Provider:
        provider_name = "mock"
        model_name = "mock-1"

        async def generate(self, **kwargs):
            prompts.append(json.dumps(kwargs, default=str))
            return SimpleNamespace(content=f"The session store lives in `{_IN}`.")

    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: _Provider())

    result = await get_answer("where is the session store kept?", scope="pkg/alpha")

    assert _OUT not in json.dumps(result)
    assert prompts and all(_OUT not in p for p in prompts)


@pytest.mark.asyncio
async def test_unscoped_answer_keeps_the_neighbour(setup_mcp, monkeypatch):
    """Control: without a scope the expanded file is still served."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod)
    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: None)

    result = await get_answer("how does the retry backoff work?")

    assert _OUT in json.dumps(result)


@pytest.mark.asyncio
async def test_a_scope_holding_no_hit_says_so(setup_mcp, monkeypatch):
    """Nothing in scope: the existing no-hits reply, naming the scope it searched."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod)
    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: None)

    result = await get_answer("how does the retry backoff work?", scope="pkg/gamma")

    assert result["confidence"] == "low"
    assert "under pkg/gamma/" in result["note"]
    assert _OUT not in json.dumps(result)


def test_union_defs_and_unindexed_lines_outside_the_scope_are_dropped() -> None:
    from repowise.server.mcp_server.tool_answer.answer import _keep_in_scope

    homonyms = {
        "union": {
            "retry": [{"file_path": _IN}, {"file_path": _OUT}],
            "backoff": [{"file_path": _OUT}],
        },
        "unindexed_lines": [{"path": _OUT, "line": 3, "text": "retry = 1"}],
        "split": {"retry": [_IN, _OUT]},
        "split_at": {"retry": {_IN: 3, _OUT: 7}},
    }
    hits, flows = _keep_in_scope(
        [{"target_path": _OUT}, {"target_path": _IN}, {"page_id": "pathless"}],
        homonyms,
        [[_IN], [_IN, _OUT]],
        "pkg/alpha",
    )

    assert hits == [{"target_path": _IN}, {"page_id": "pathless"}]
    assert flows == [[_IN]]
    assert homonyms["union"] == {"retry": [{"file_path": _IN}]}
    assert homonyms["unindexed_lines"] == []
    assert homonyms["split_at"] == {"retry": {_IN: 3}}
    # Only lowers a grade: kept, so no label moves up under a scope.
    assert homonyms["split"] == {"retry": [_IN, _OUT]}


def test_held_at_medium_note_counts_definitions_outside_the_scope() -> None:
    from repowise.server.mcp_server.tool_answer.answer import _split_in_scope

    shown = _split_in_scope({"retry": [_IN, _OUT, "pkg/gamma/r.py"]}, "pkg/alpha")

    assert shown == {"retry": [_IN, "2 files outside the scope"]}
