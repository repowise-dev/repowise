"""Calibration: the per-file symbol budget must go to the question, not the top.

A file with more symbols than its budget used to serve a document-order prefix,
so on a large file the symbol the question was actually about could not be
reached at all — the more code the file held, the smaller the served fraction
and the stronger the top-of-file bias. The budget is unchanged; what fills it
is now scored against the question's content terms.
"""

from __future__ import annotations

from types import SimpleNamespace

from repowise.core.persistence.models import WikiSymbol
from repowise.server.mcp_server.tool_answer.config import _MAX_SYMBOLS_TOP_HIT
from repowise.server.mcp_server.tool_answer.symbols import _hydrate_symbols_for_hits

# Twice the budget, so half the file cannot be served whatever the ordering.
_SYMBOL_COUNT = _MAX_SYMBOLS_TOP_HIT * 2
_BODY_LINES = 4


def _write_module(tmp_path, names: list[str]) -> list[int]:
    """One trivial function per name; returns their 1-indexed def lines."""
    lines: list[str] = []
    starts: list[int] = []
    for name in names:
        starts.append(len(lines) + 1)
        lines.append(f"def {name}(request):")
        lines.extend(f"    step{i} = {i}" for i in range(_BODY_LINES))
        lines.append("    return request")
    (tmp_path / "app.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return starts


async def _hydrate(session, repo_id, tmp_path, names, question):
    starts = _write_module(tmp_path, names)
    for i, name in enumerate(names):
        session.add(
            WikiSymbol(
                id=f"sym-{i}",
                repository_id=repo_id,
                file_path="app.py",
                symbol_id=f"app.py::{name}",
                name=name,
                qualified_name=f"app.{name}",
                kind="function",
                signature=f"def {name}(request)",
                start_line=starts[i],
                end_line=starts[i] + _BODY_LINES + 1,
                docstring="",
                visibility="public",
                is_async=False,
                complexity_estimate=1,
                language="python",
                parent_name=None,
            )
        )
    await session.commit()
    hits = [{"target_path": "app.py", "page_type": "file_page"}]
    await _hydrate_symbols_for_hits(
        session, repo_id, hits, SimpleNamespace(path=tmp_path), question=question
    )
    return hits[0]["symbols"]


_ROUTING_NAMES = [f"helper{i}" for i in range(_SYMBOL_COUNT - 1)] + ["find_route"]
_ROUTING_QUESTION = "How does routing work in this app?"


async def test_late_symbol_named_by_the_question_survives_the_cap(
    session, repo_id, tmp_path
) -> None:
    """The routing code is at the end of the file; the question asks about it."""
    served = await _hydrate(
        session, repo_id, tmp_path, _ROUTING_NAMES, _ROUTING_QUESTION
    )
    names = [s["name"] for s in served]

    assert "find_route" in names, "document order buried the symbol the question named"
    assert len(names) <= _MAX_SYMBOLS_TOP_HIT, "the budget itself must not grow"


async def test_no_content_term_falls_back_to_document_order(
    session, repo_id, tmp_path
) -> None:
    """With nothing to score, the served slice is the old start_line prefix."""
    names = [f"helper{i}" for i in range(_SYMBOL_COUNT)]
    served = await _hydrate(session, repo_id, tmp_path, names, "How does it work?")

    assert [s["name"] for s in served] == names[:_MAX_SYMBOLS_TOP_HIT]


async def test_served_slice_stays_in_reading_order(session, repo_id, tmp_path) -> None:
    """Relevance decides what is kept, never what order consumers read it in."""
    served = await _hydrate(
        session, repo_id, tmp_path, _ROUTING_NAMES, _ROUTING_QUESTION
    )
    names = [s["name"] for s in served]

    assert names == sorted(names, key=_ROUTING_NAMES.index)


async def test_prose_question_still_earns_a_source_body(
    session, repo_id, tmp_path
) -> None:
    """A prose question matches no identifier, so nothing would carry code."""
    served = await _hydrate(
        session, repo_id, tmp_path, _ROUTING_NAMES, _ROUTING_QUESTION
    )
    scored = [s for s in served if s["name"] == "find_route"]

    assert scored and scored[0].get("source_excerpt"), (
        "the symbol the question scored against was served without its body"
    )
    assert not any(s["_matched"] for s in served), "prose names no identifier"
