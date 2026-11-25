"""Concept questions lead with the file the index names, not the prose about it.

"where is bus factor computed" used to rank the UI panels that chart bus factor
above the function that computes it, because the panels' pages say the words
and the function's page does not. "main entry point" never reached the CLI's
``main.py`` that ``get_overview`` lists first.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from repowise.server.mcp_server import tool_search
from repowise.server.mcp_server.tool_search.concept_lift import definition_lifts


def _hit(path: str, names: tuple[str, ...] = ()) -> SimpleNamespace:
    return SimpleNamespace(
        page_id=f"file_page:{path}",
        title=path,
        score=5.0,
        snippet="",
        page_type="file_page",
        symbol_names=list(names),
    )


@pytest.mark.parametrize(
    ("query", "names", "lifted"),
    [
        ("where is bus factor computed", ("_bus_factor",), True),
        ("how is bus factor computed", ("compute_bus_factor",), True),
        ("where is bus factor computed", ("computeBusFactor",), True),
        # A word the question never said, leading or trailing, makes it a use.
        ("where is the dead code analysis implemented", ("get_dead_code",), False),
        ("where is bus factor computed", ("BusFactorPanel",), False),
        # One qualifier of the question is not what it asks for.
        ("where are decisions extracted from git history", ("git_history",), False),
        # One word is too generic to be a definition.
        ("where is main", ("main",), False),
        # Not a where/how question.
        ("bus factor", ("_bus_factor",), False),
    ],
)
def test_a_definition_is_a_name_the_question_spells(query, names, lifted) -> None:
    lifts = definition_lifts(query, [_hit("src/git/history.py", names)])
    assert bool(lifts) is lifted


def test_a_test_file_defining_the_name_is_not_lifted() -> None:
    hits = [
        _hit("tests/test_history.py", ("_bus_factor",)),
        _hit("src/history.py", ("_bus_factor",)),
    ]
    assert list(definition_lifts("where is bus factor computed", hits)) == [
        "file_page:src/history.py"
    ]


def _fuse(monkeypatch, query: str, fts_hits, sym_hits) -> list[str]:
    async def fts(*args, **kwargs):
        return fts_hits

    async def symbol(*args, **kwargs):
        return sym_hits

    async def nothing(*args, **kwargs):
        return []

    monkeypatch.setattr(tool_search, "_safe_fts", fts)
    monkeypatch.setattr(tool_search, "_safe_symbol_search", symbol)
    monkeypatch.setattr(tool_search, "_safe_filename_search", nothing)
    ctx = SimpleNamespace(fts=None, vector_store=None)
    out = asyncio.run(tool_search._fused_retrieve(ctx, query, 10, None, semantic=False))
    return [row["page_id"] for row in out]


def test_the_definition_outranks_pages_that_only_talk_about_it(monkeypatch) -> None:
    panels = [_hit("ui/bus-factor-panel.tsx"), _hit("ui/bus-factor-scatter.tsx")]
    definition = _hit("src/git/history.py", ("_bus_factor",))
    ranked = _fuse(monkeypatch, "where is bus factor computed", panels, [definition])
    assert ranked[0] == "file_page:src/git/history.py"


def test_a_plain_concept_query_keeps_the_page_order(monkeypatch) -> None:
    panels = [_hit("ui/bus-factor-panel.tsx"), _hit("ui/bus-factor-scatter.tsx")]
    definition = _hit("src/git/history.py", ("_bus_factor",))
    ranked = _fuse(monkeypatch, "bus factor", panels, [definition])
    assert ranked[0] == "file_page:ui/bus-factor-panel.tsx"


async def _curate(session, entry_points: list[str]) -> None:
    from repowise.core.persistence.models import KnowledgeGraphProjectMeta

    session.add(
        KnowledgeGraphProjectMeta(
            repository_id="repo1",
            entry_points_json=json.dumps(entry_points),
            entry_candidates_json=json.dumps(entry_points),
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_an_entry_point_question_leads_with_the_curated_entry_point(setup_mcp, session):
    from repowise.server.mcp_server import search_codebase

    # The fixture's models.py page is about the ORM; only the curated list says
    # it is where the program starts.
    await _curate(session, ["src/db/models.py"])
    result = await search_codebase(query="main entry point")
    assert result["results"][0]["path"] == "src/db/models.py"


@pytest.mark.asyncio
async def test_words_past_entry_point_narrow_the_entry_points(setup_mcp, session):
    from repowise.server.mcp_server.tool_search.concept_lift import entry_point_rows

    await _curate(session, ["src/db/models.py", "src/auth/service.py"])
    ctx = await tool_search._resolve_repo_context(None)
    rows = await entry_point_rows(ctx, "entry point of the auth service")
    assert [row["target_path"] for row in rows] == ["src/auth/service.py"]
    assert await entry_point_rows(ctx, "how is a user stored") == []
