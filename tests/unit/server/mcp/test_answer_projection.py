"""Lean, cache-invariant external projections for ``get_answer``."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from repowise.server.mcp_server.tool_answer.projection import project_answer_payload

_HITS = [
    {"page_id": "file_page:src/auth/service.py", "score": 6.0},
    {"page_id": "file_page:src/auth/middleware.py", "score": 1.0},
]


class _Provider:
    provider_name = "mock"
    model_name = "mock-1"

    def __init__(self, content: str, *, fail: bool = False) -> None:
        self.content = content
        self.fail = fail
        self.calls = 0

    async def generate(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("offline synthesis")
        return SimpleNamespace(content=self.content)


def _patch_retrieval(monkeypatch, answer_mod, scores=(6.0, 1.0)):
    async def _fake_retrieve(question, ctx):
        return [dict(hit, score=score) for hit, score in zip(_HITS, scores, strict=True)]

    async def _fake_hydrate(hits, ctx, *, scope=None):
        for hit in hits:
            hit["target_path"] = hit["page_id"].removeprefix("file_page:")
            hit["title"] = hit["target_path"]
            hit["summary"] = "Authentication evidence"
            hit["snippet"] = "AuthService validates a request and middleware calls it."
            hit["excerpt"] = "class AuthService:\n    def check(self): ..."
            hit["page_type"] = "file_page"
        return hits

    monkeypatch.setattr(answer_mod, "_hybrid_retrieve", _fake_retrieve)
    monkeypatch.setattr(answer_mod, "_hydrate_hits", _fake_hydrate)


def _without_meta(payload: dict) -> dict:
    result = copy.deepcopy(payload)
    result.pop("_meta", None)
    return result


@pytest.mark.asyncio
async def test_fresh_and_cached_answers_share_one_external_contract(setup_mcp, monkeypatch):
    import repowise.server.mcp_server._meta as meta_mod
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer, tool_middleware

    _patch_retrieval(monkeypatch, answer_mod)
    provider = _Provider("Authentication is implemented in src/auth/service.py.")
    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _path: provider)

    def freshness(_repository, targets=None):
        result = {"index_behind": True}
        if "src/auth/middleware.py" in (targets or []):
            result["stale_warning"] = "dropped evidence changed"
        return result

    monkeypatch.setattr(meta_mod, "freshness_from_repo", freshness)
    call = tool_middleware(get_answer)

    fresh = await call("where is authentication implemented")
    cached = await call("where is authentication implemented")

    assert fresh["confidence"] == "high"
    assert cached["_meta"]["cached"] is True
    assert _without_meta(fresh) == _without_meta(cached)
    assert "stale_warning" not in fresh["_meta"]
    assert "stale_warning" not in cached["_meta"]
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_scoped_cache_identity_isolated_and_projected_the_same(setup_mcp, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer, tool_middleware

    _patch_retrieval(monkeypatch, answer_mod)
    provider = _Provider("Authentication is implemented in src/auth/service.py.")
    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _path: provider)
    call = tool_middleware(get_answer)

    unscoped = await call("where is scoped authentication implemented")
    scoped = await call("where is scoped authentication implemented", scope="src/auth")
    scoped_cached = await call("where is scoped authentication implemented", scope="src/auth")

    assert provider.calls == 2
    assert scoped_cached["_meta"]["cached"] is True
    assert _without_meta(scoped) == _without_meta(scoped_cached)
    assert unscoped["_meta"].get("cached") is not True


def _raw(confidence: str) -> dict:
    return {
        "answer": "Use AuthService.check.",
        "citations": ["src/auth/service.py"],
        "confidence": confidence,
        "retrieval_quality": "high" if confidence == "high" else "partial",
        "fallback_targets": ["src/auth/service.py", "src/auth/middleware.py"],
        "retrieval": [
            {"path": "src/auth/service.py", "excerpt": "def check(): return True"},
            {"path": "src/auth/middleware.py", "excerpt": "check()"},
        ],
        "quotes": [
            {"path": "src/auth/service.py", "lines": [2, 2], "quote": "return True"}
        ],
        "symbol_bodies": [
            {
                "path": "src/auth/service.py",
                "name": "check",
                "lines": [1, 2],
                "source": "def check():\n    return True",
            }
        ],
        "best_guesses": [
            {
                "file": "src/auth/service.py",
                "why_relevant": "defines check",
                "excerpt": "def check(): return True",
            }
        ],
        "candidates": [
            {"path": "src/auth/service.py"},
            {"path": "src/auth/middleware.py"},
            {"path": "src/auth/routes.py"},
        ],
        "code_rationale": [
            {"path": "src/auth/service.py", "comment": "return True"}
        ],
        "_meta": {"contract_version": 1},
    }


def test_high_medium_and_low_are_intentionally_different_shapes():
    high = project_answer_payload(_raw("high"), question="how does auth work")
    medium = project_answer_payload(_raw("medium"), question="how does auth work")
    low = project_answer_payload(_raw("low"), question="how does auth work")

    assert "retrieval" not in high and "best_guesses" not in high
    assert "retrieval" not in medium and len(medium["best_guesses"]) == 1
    assert "best_guesses" in low and "retrieval" not in low
    assert high["next_action_hint"] == "Use the answer and citations directly."
    assert medium["next_action_hint"] != high["next_action_hint"]
    assert low["next_action_hint"] != high["next_action_hint"]


def test_adversarial_duplicate_evidence_survives_once_with_exact_recovery():
    raw = _raw("low")
    raw["retrieval"] *= 3
    raw["quotes"] *= 3
    raw["symbol_bodies"] *= 3
    raw["best_guesses"] *= 3
    raw["code_rationale"] *= 3
    raw["candidates"] *= 3
    raw["fallback_targets"] *= 3
    raw["citations"] *= 3

    compact = project_answer_payload(raw, question="how does auth work", scope="src/auth")
    expanded = project_answer_payload(
        raw,
        question="how does auth work",
        scope="src/auth",
        include=["evidence"],
    )

    serialized = json.dumps(expanded, separators=(",", ":"), default=str)
    assert serialized.count("def check():\\n    return True") == 1
    assert "quotes" not in expanded and "code_rationale" not in expanded
    assert expanded["citations"] == ["src/auth/service.py"]
    assert "fallback_targets" not in expanded
    assert expanded["candidates"] == [{"path": "src/auth/routes.py"}]
    assert expanded["citations_total"] == 3
    assert expanded["citations_emitted"] == 1
    assert compact["retrieval_total"] == 6
    assert compact["retrieval_emitted"] == 0
    assert compact["retrieval_reduced_reason"] == "confidence_projection_and_deduplication"
    assert compact["_meta"]["projection"]["recovery"] == {
        "tool": "get_answer",
        "arguments": {
            "question": "how does auth work",
            "include": ["evidence"],
            "scope": "src/auth",
        },
    }
    assert expanded["retrieval"], "the advertised one-call expansion must recover evidence"


def test_expanded_projection_preserves_distinct_evidence_from_the_same_path():
    raw = _raw("low")
    for key in ("symbol_bodies", "quotes", "code_rationale", "best_guesses"):
        raw[key] = []
    raw["retrieval"].append(
        {
            "path": "src/auth/middleware.py",
            "excerpt": "middleware rejects a missing token",
        }
    )

    expanded = project_answer_payload(
        raw, question="how does auth work", include=["evidence"]
    )

    middleware = [
        row for row in expanded["retrieval"] if row["path"] == "src/auth/middleware.py"
    ]
    assert len(middleware) == 2
    assert {row["excerpt"] for row in middleware} == {
        "check()",
        "middleware rejects a missing token",
    }


def test_symbol_qualified_evidence_deduplicates_file_navigation():
    raw = _raw("low")
    raw["citations"] = []
    raw["symbol_bodies"] = []
    raw["quotes"] = []
    raw["code_rationale"] = []
    raw["best_guesses"] = []
    raw["retrieval"] = [
        {
            "path": "src/auth/middleware.py::check",
            "file": "src/auth/middleware.py",
            "excerpt": "def check(): ...",
        }
    ]
    raw["fallback_targets"] = ["src/auth/middleware.py"]
    raw["candidates"] = [{"path": "src/auth/middleware.py"}]

    expanded = project_answer_payload(
        raw, question="where is check", include=["evidence"]
    )

    assert "fallback_targets" not in expanded
    assert "candidates" not in expanded


def test_expanded_degraded_answer_names_only_retained_blocks():
    raw = _raw("low")
    raw.update(
        {
            "degraded": "no-llm-provider",
            "answer": "raw summary names retrieval, fallback_targets, and candidates",
        }
    )

    expanded = project_answer_payload(
        raw, question="how does auth work", include=["evidence"]
    )

    assert "fallback_targets" not in expanded
    assert "symbol_bodies" in expanded
    assert "fallback_targets" not in expanded["answer"]
    assert "candidates" not in expanded["answer"]


def test_rationale_only_degraded_answer_leads_with_the_local_conclusion():
    raw = {
        "answer": "generic fallback",
        "citations": ["src/cache.py"],
        "confidence": "low",
        "degraded": "no-llm-provider",
        "code_rationale": [
            {
                "path": "src/cache.py",
                "lines": [4, 5],
                "comment": "The TTL matches the upstream feed's five-minute refresh.",
            }
        ],
        "_meta": {"contract_version": 1},
    }

    result = project_answer_payload(raw, question="why is the TTL five minutes")

    assert "upstream feed" in result["answer"]
    assert "src/cache.py" in result["answer"]
    assert result["confidence"] == "low"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "legs", "expected_degraded"),
    [
        (None, {"fts": "ok", "vector": "keyless"}, "no-llm-provider"),
        (_Provider("", fail=True), {"fts": "ok", "vector": "ok"}, "synthesis-failed"),
        (None, {"fts": "ok", "vector": "error"}, "no-llm-provider"),
    ],
)
async def test_no_llm_and_failed_legs_remain_compact_and_actionable(
    setup_mcp, monkeypatch, provider, legs, expected_degraded
):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer, tool_middleware

    _patch_retrieval(monkeypatch, answer_mod)
    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _path: provider)
    monkeypatch.setattr(answer_mod, "_retrieval_legs", lambda: legs)
    monkeypatch.setattr(
        answer_mod,
        "_degraded_legs",
        lambda state: [name for name, status in state.items() if status == "error"],
    )

    result = await tool_middleware(get_answer)(
        f"how does auth work in {expected_degraded} {legs['vector']} mode"
    )

    assert result["degraded"] == expected_degraded
    # The grade, through the real tool rather than the payload builder: derived
    # from the retrieval on the no-provider path, and pinned low when a
    # configured provider failed and a retry could still answer properly.
    if expected_degraded == "synthesis-failed":
        assert result["confidence"] == "low"
    else:
        expected = "low" if result["retrieval_quality"] == "weak" else "medium"
        assert result["confidence"] == expected
    assert result["confidence"] != "high"
    assert result["answer"]
    assert result["next_action_hint"]
    assert result.get("best_guesses") or result.get("symbol_bodies") or result.get("retrieval")
    assert len(json.dumps(result, separators=(",", ":"), default=str)) < 24_000
    if legs["vector"] == "error":
        assert result["_meta"]["retrieval_degraded"] == ["vector"]


@pytest.mark.asyncio
async def test_real_embedding_failure_keeps_fts_evidence_in_final_projection(
    setup_mcp, monkeypatch
):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import _answer_pipeline, get_answer, tool_middleware

    class Fts:
        async def search(self, _query, limit=15):
            return [
                SimpleNamespace(
                    page_id=hit["page_id"],
                    title=hit["page_id"],
                    snippet="lexical authentication evidence",
                    page_type="file_page",
                )
                for hit in _HITS[:limit]
            ]

    class FailingVectorStore:
        async def embed_texts(self, _texts):
            raise RuntimeError("embedding provider unavailable")

        async def search(self, _query, limit=15):
            raise RuntimeError("embedding provider unavailable")

    ctx = await answer_mod._resolve_repo_context()
    ctx.fts = Fts()
    ctx.vector_store = FailingVectorStore()
    ctx.vector_store_ready = None

    async def resolve(_repo=None):
        return ctx

    monkeypatch.setattr(answer_mod, "_resolve_repo_context", resolve)
    monkeypatch.setattr(answer_mod, "_hybrid_retrieve", _answer_pipeline.hybrid_retrieve)
    _patch_retrieval(monkeypatch, answer_mod)
    monkeypatch.setattr(answer_mod, "_hybrid_retrieve", _answer_pipeline.hybrid_retrieve)
    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _path: None)

    result = await tool_middleware(get_answer)("how does auth work if embeddings fail")

    assert result["answer"]
    assert result.get("best_guesses") or result.get("retrieval")
    assert "embed" in result["_meta"]["retrieval_degraded"]
    assert "vector" in result["_meta"]["retrieval_degraded"]
    assert len(json.dumps(result, separators=(",", ":"), default=str)) < 24_000


@pytest.mark.asyncio
async def test_missing_generated_docs_can_fall_back_to_local_symbol_evidence(
    setup_mcp, monkeypatch
):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer, tool_middleware

    async def no_pages(question, ctx):
        return []

    async def hydrate_empty(hits, ctx, *, scope=None):
        return hits

    async def anchor_symbol(session, repo_id, names, hits, **kwargs):
        return (
            [
                {
                    "page_id": "symbol:AuthService",
                    "target_path": "src/auth/service.py",
                    "title": "AuthService",
                    "summary": "Local indexed symbol",
                    "snippet": "class AuthService",
                    "excerpt": "class AuthService",
                    "page_type": "symbol_spotlight",
                    "score": 6.0,
                }
            ],
            {"union": {}, "qualified_miss": []},
        )

    monkeypatch.setattr(answer_mod, "_hybrid_retrieve", no_pages)
    monkeypatch.setattr(answer_mod, "_hydrate_hits", hydrate_empty)
    monkeypatch.setattr(answer_mod, "_anchor_symbol_hits", anchor_symbol)
    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _path: None)

    result = await tool_middleware(get_answer)("where is AuthService defined")

    assert result["answer"]
    assert result["best_guesses"][0]["file"] == "src/auth/service.py"
    assert result["next_action_hint"]
