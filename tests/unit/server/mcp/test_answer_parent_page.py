"""Unit tests for get_answer's subsystem-parent surfacing (_answer_pipeline).

A subsystem-shaped question ("overview of X", "what subsystem does Y belong to",
"where would I add a Z") should lead with the concept page for the whole
subsystem, not its member files or a more specific child. ``expand_via_parent_page``
picks that page from two signals: a tight structural cluster (>=2 surfaced hits
whose immediate parent has a concept page) or, when the file hits scatter, the
concept page a concept-restricted vector search ranks highest. It is a no-op on
non-subsystem questions, so file/implementation queries are untouched.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import Page, Repository
from repowise.server.mcp_server._answer_pipeline import (
    _common_ancestor,
    expand_via_parent_page,
    is_subsystem_query,
)

_NOW = __import__("datetime").datetime(2024, 1, 1)


# --------------------------------------------------------------------------- #
# Pure query-shape gate — the generality guard: matches natural-language
# subsystem phrasing, never implementation/flow questions (which carry file
# golds this stage must not touch).
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "q",
    [
        "Give me an overview of the ingestion subsystem.",
        "What are the main parts of the auth layer?",
        "What subsystem does the parser belong to?",
        "Where would I add a new payment provider?",
        "Walk me through the rendering pipeline.",
        "What are the main components of the scheduler?",
        "How is the auth layer structured?",
    ],
)
def test_subsystem_shapes_match(q):
    assert is_subsystem_query(q)


@pytest.mark.parametrize(
    "q",
    [
        "how does the parser compute a symbol's line range",
        "how does a request flow from the router to the handler",
        "verify_and_heal",
        "why is the cache invalidated on write",
        "what fields does the config blob contain",
        # Bare fragments that appear inside ordinary implementation questions and
        # must NOT trip the subsystem gate (would otherwise reorder file hits).
        "which module handles retries in this function",
        "how is data validated before it belongs to the batch",
        "structure of the JSON payload returned by this endpoint",
        "components of the response are validated where",
        "what parts of the code touch this variable",
        "how does data flow through parts of the pipeline",
    ],
)
def test_implementation_shapes_do_not_match(q):
    assert not is_subsystem_query(q)


def test_common_ancestor():
    assert _common_ancestor({"a/b/c", "a/b/d"}) == "a/b"
    assert _common_ancestor({"a/b/x", "a/c/y"}) == "a"
    assert _common_ancestor({"a/b"}) == "a/b"
    assert _common_ancestor({"x/y", "p/q"}) == ""


# --------------------------------------------------------------------------- #
# Fakes + helpers for the DB-backed stage.
# --------------------------------------------------------------------------- #
@dataclass
class _VecResult:
    page_id: str
    page_type: str
    target_path: str


class _FakeVectorStore:
    """Returns a fixed concept-page ranking regardless of the query."""

    def __init__(self, ranked_paths):
        self._ranked = ranked_paths

    async def search(self, query, limit=10):
        return [
            _VecResult(f"module_page:{p}", "module_page", p) for p in self._ranked[:limit]
        ]


class _Ctx:
    def __init__(self, factory, vector_store):
        self.session_factory = factory
        self.vector_store = vector_store


def _hit(page_type, target_path, score, sources=("vector",)):
    return {
        "page_id": f"{page_type}:{target_path}",
        "page_type": page_type,
        "target_path": target_path,
        "score": score,
        "_sources": set(sources),
    }


async def _seed(session: AsyncSession, module_paths):
    session.add(
        Repository(
            id="r1", name="r", url="u", local_path="/t", default_branch="main",
            settings_json="{}", created_at=_NOW, updated_at=_NOW,
        )
    )
    for p in module_paths:
        session.add(
            Page(
                id=f"module_page:{p}", repository_id="r1", page_type="module_page",
                title=f"{p.rsplit('/', 1)[-1]} overview", content="c", summary="s",
                target_path=p, source_hash="h", model_name="m", provider_name="m",
                generation_level=4, created_at=_NOW, updated_at=_NOW,
            )
        )
    await session.flush()


# --------------------------------------------------------------------------- #
# Behaviour of the stage.
# --------------------------------------------------------------------------- #
async def test_noop_on_non_subsystem_question(factory):
    async with factory() as s:
        await _seed(s, ["pkg/ingestion"])
        await s.commit()
    ctx = _Ctx(factory, _FakeVectorStore(["pkg/ingestion"]))
    hits = [_hit("file_page", "pkg/ingestion/parser.py", 5.0)]
    out = await expand_via_parent_page(list(hits), "how does the parser work", ctx)
    assert [h["target_path"] for h in out] == ["pkg/ingestion/parser.py"]


async def test_tight_cluster_injects_parent(factory):
    async with factory() as s:
        await _seed(s, ["pkg/ingestion"])
        await s.commit()
    ctx = _Ctx(factory, _FakeVectorStore(["pkg/somewhere/else"]))
    # Two files share the immediate parent pkg/ingestion -> tight cluster.
    hits = [
        _hit("file_page", "pkg/ingestion/parser.py", 5.0),
        _hit("file_page", "pkg/ingestion/walker.py", 4.9),
    ]
    out = await expand_via_parent_page(list(hits), "overview of the ingestion subsystem", ctx)
    assert out[0]["target_path"] == "pkg/ingestion"  # parent leads
    assert out[0]["score"] >= 5.0


async def test_dispersed_hits_use_semantic_concept_page(factory):
    async with factory() as s:
        await _seed(s, ["pkg/core/generation"])
        await s.commit()
    # File hits scatter across unrelated dirs: no tight cluster. The concept
    # page the query is about is recovered from the (fake) vector ranking.
    ctx = _Ctx(factory, _FakeVectorStore(["pkg/core/generation"]))
    hits = [
        _hit("module_page", "pkg/ui/wiki", 4.0),
        _hit("file_page", "pkg/types/overview.ts", 3.8),
        _hit("file_page", "pkg/api/overview.ts", 3.7),
    ]
    out = await expand_via_parent_page(list(hits), "overview of the wiki generation subsystem", ctx)
    assert out[0]["target_path"] == "pkg/core/generation"


async def test_sibling_clusters_roll_up_to_common_ancestor(factory):
    async with factory() as s:
        await _seed(s, ["pkg/health", "pkg/health/biomarkers", "pkg/health/complexity"])
        await s.commit()
    ctx = _Ctx(factory, _FakeVectorStore(["pkg/health/biomarkers"]))
    # biomarkers and complexity each cluster (>=2 immediate children); the answer
    # is their common parent subsystem, not one arbitrary half.
    hits = [
        _hit("file_page", "pkg/health/biomarkers/registry.py", 5.0),
        _hit("file_page", "pkg/health/biomarkers/base.py", 4.9),
        _hit("file_page", "pkg/health/complexity/walker.py", 4.8),
        _hit("file_page", "pkg/health/complexity/engine.py", 4.7),
    ]
    out = await expand_via_parent_page(list(hits), "where would I add a health metric", ctx)
    assert out[0]["target_path"] == "pkg/health"


async def test_present_parent_is_promoted_not_duplicated(factory):
    async with factory() as s:
        await _seed(s, ["pkg/ingestion"])
        await s.commit()
    ctx = _Ctx(factory, _FakeVectorStore(["pkg/ingestion"]))
    # The parent page is already retrieved but ranked below its children.
    hits = [
        _hit("module_page", "pkg/ingestion/parser", 5.0),
        _hit("module_page", "pkg/ingestion/walker", 4.9),
        _hit("module_page", "pkg/ingestion", 2.0),
    ]
    out = await expand_via_parent_page(list(hits), "overview of the ingestion subsystem", ctx)
    assert out[0]["target_path"] == "pkg/ingestion"  # promoted to lead
    assert [h["target_path"] for h in out].count("pkg/ingestion") == 1  # no duplicate
