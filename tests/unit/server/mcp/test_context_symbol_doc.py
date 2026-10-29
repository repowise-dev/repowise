"""A symbol's full_doc is its own page, not the page of the file it lives in.

A spotlight page is written about one symbol; the file page is shared by every
symbol in the file. Without a spotlight the symbol's entry on the file page is
served, and a module's file list is capped so its page body survives the budget.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from repowise.core.persistence.models import GraphNode, Page, Repository, WikiSymbol
from repowise.server.mcp_server.tool_context.targets import (
    _SYMBOL_CAP,
    _file_page_entry,
    _resolve_one_target,
    _spotlight_page,
)

_NOW = datetime(2026, 10, 1, tzinfo=UTC)
_FILE = "src/shapes.py"
_FULL = {"docs", "freshness", "full_doc"}
_FILE_PAGE = """# src/shapes.py

## Public API

- `class Circle`: A round shape.
  - `def area(self) -> float`: Its area.
- `class Square`: A square shape.
- `def make(kind: str)`: Build a shape.

## Used by

- `src/app.py`
"""


def _page(rid: str, page_type: str, target: str, content: str) -> Page:
    return Page(
        id=f"{page_type}:{target}",
        repository_id=rid,
        page_type=page_type,
        title=target,
        content=content,
        target_path=target,
        source_hash=f"h-{target}",
        model_name="mock",
        provider_name="mock",
        generation_level=1,
        confidence=0.9,
        freshness_status="fresh",
        metadata_json="{}",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _symbol(rid: str, name: str, kind: str, line: int, tail: str | None = None) -> WikiSymbol:
    return WikiSymbol(
        id=f"shape-{line}",
        repository_id=rid,
        file_path=_FILE,
        symbol_id=f"{_FILE}::{tail or name}",
        name=name,
        qualified_name=name,
        kind=kind,
        signature=name,
        start_line=line,
        end_line=line + 3,
        visibility="public",
        is_async=False,
        complexity_estimate=1,
    )


@pytest.fixture
async def repository(session, populated_db) -> Repository:
    return await session.get(Repository, populated_db)


@pytest.fixture
async def shapes(session, populated_db) -> None:
    rid = populated_db
    session.add(_page(rid, "file_page", _FILE, _FILE_PAGE))
    session.add(_page(rid, "symbol_spotlight", f"{_FILE}::Circle", "# Circle\n\nNot thread safe."))
    session.add(_page(rid, "symbol_spotlight", f"{_FILE}::area", "# area\n\nCircle.area."))
    session.add(_symbol(rid, "Circle", "class", 1))
    session.add(_symbol(rid, "area", "method", 5, "Circle::area"))
    session.add(_symbol(rid, "Square", "class", 10))
    session.add(_symbol(rid, "area", "method", 14, "Square::area"))
    session.add(_symbol(rid, "make", "function", 20))
    session.add(_symbol(rid, "_helper", "function", 30))
    await session.flush()


async def _docs(session, repository, target: str) -> dict:
    card = await _resolve_one_target(session, repository, target, _FULL, True, exclude_spec=None)
    return card["docs"]


async def test_a_symbol_with_a_spotlight_gets_the_spotlight(session, repository, shapes) -> None:
    docs = await _docs(session, repository, f"{_FILE}::Circle")
    assert docs["documentation"].startswith("# Circle")


async def test_without_a_spotlight_the_file_page_entry_is_served(
    session, repository, shapes
) -> None:
    docs = await _docs(session, repository, f"{_FILE}::Square")
    assert docs["documentation"] == "- `class Square`: A square shape."


async def test_a_symbol_the_file_page_does_not_list_gets_no_file_page_copy(
    session, repository, shapes
) -> None:
    docs = await _docs(session, repository, f"{_FILE}::_helper")
    assert "documentation" not in docs
    assert "file_summary" in docs


async def test_a_later_same_named_symbol_does_not_take_the_spotlight(
    session, repository, populated_db, shapes
) -> None:
    card = SimpleNamespace(session=session, repo_id=populated_db)
    first = SimpleNamespace(file_path=_FILE, name="area", start_line=5)
    second = SimpleNamespace(file_path=_FILE, name="area", start_line=14)
    assert (await _spotlight_page(card, first)) is not None
    assert (await _spotlight_page(card, second)) is None


def test_the_entry_keeps_its_nested_rows_and_stops_at_a_sibling() -> None:
    assert _file_page_entry(_FILE_PAGE, "Circle") == (
        "- `class Circle`: A round shape.\n  - `def area(self) -> float`: Its area."
    )
    assert _file_page_entry(_FILE_PAGE, "Circ") is None
    assert _file_page_entry(_FILE_PAGE, "app") is None


async def test_a_large_module_caps_its_files_and_keeps_its_body(
    session, repository, populated_db
) -> None:
    rid = populated_db
    session.add(_page(rid, "module_page", "big", "# Big\n\nThe module body."))
    for i in range(_SYMBOL_CAP + 5):
        session.add(_page(rid, "file_page", f"big/f{i:02d}.py", f"# f{i}"))
    session.add(
        GraphNode(id="gn-big", repository_id=rid, node_id="big/f19.py", node_type="file",
                  pagerank=0.9)
    )
    await session.flush()

    docs = await _docs(session, repository, "big")

    assert docs["content_md"].startswith("# Big")
    assert len(docs["files"]) == _SYMBOL_CAP
    assert docs["files"][0]["path"] == "big/f19.py"  # highest PageRank first
    assert docs["files_omitted"] == 5
