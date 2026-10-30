"""Compound questions: one retrieval per clause, so a later clause gets its own file.

"How does update rebuild the graph, and does it re-resolve imports and calls?"
asks two things. One retrieval over the whole question ranks files for the
dominant clause, so the top five can hold nothing for the second and the answer
covers the first clause only. Each later clause is retrieved on its own, and
its best file joins the served hits below the first clause's top three.

A single-clause question is untouched: no extra retrieval, no extra hit.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from typing import Any

from repowise.server.mcp_server._answer_pipeline import hybrid_retrieve, hydrate_hits
from repowise.server.mcp_server._helpers import filter_dicts_by_key
from repowise.server.mcp_server._page_paths import hit_file_path
from repowise.server.mcp_server._query_terms import content_terms
from repowise.server.mcp_server.tool_answer.retrieval import _rerank_by_coverage
from repowise.server.mcp_server.tool_answer.symbols import place_named_files

_QUESTION_WORDS = r"(?:how|what|where|why|which|when|who|does|do|did|is|are|can|will|should)\b"
# A clause starts at a question word after "?", ";", ", and", " and" or ",".
_CLAUSE_BREAK = re.compile(
    rf"\?\s+|;\s+|,?\s+(?:and|but)\s+(?={_QUESTION_WORDS})|,\s+(?={_QUESTION_WORDS})",
    re.IGNORECASE,
)
_MAX_CLAUSES = 3
# A clause needs this many content words to be retrieved on its own.
_MIN_CLAUSE_TERMS = 2
# Hits synthesis reads: a clause file below them reached no answer.
_SERVED = 5


def split_clauses(question: str) -> list[str]:
    """The question's clauses when it asks more than one thing, else ``[]``."""
    parts = [p.strip(" ?,;") for p in _CLAUSE_BREAK.split(question or "")]
    parts = [p for p in parts if len(content_terms(p)) >= _MIN_CLAUSE_TERMS]
    return parts[:_MAX_CLAUSES] if len(parts) > 1 else []


async def _clause_top(clause: str, ctx: Any, scope: str | None, exclude_spec: Any) -> dict | None:
    """The best file hit for one clause. Runs as its own task, so its retrieval
    legs never overwrite the record the whole question's legs wrote."""
    hits = await hydrate_hits(await hybrid_retrieve(clause, ctx), ctx, scope=scope)
    hits = _rerank_by_coverage(filter_dicts_by_key(hits, "target_path", exclude_spec), clause)
    return next((h for h in hits if hit_file_path(h) == h.get("target_path")), None)


async def cover_clauses(
    question: str, hits: list[dict], ctx: Any, *, scope: str | None, exclude_spec: Any
) -> list[dict]:
    """*hits* with each later clause's best file placed just below the top three."""
    clauses = split_clauses(question)
    if len(clauses) < 2 or not hits:
        return hits
    found: dict[str, dict] = {}
    clause_of: dict[str, str] = {}
    for clause in clauses[1:]:
        with contextlib.suppress(Exception):
            top = await asyncio.create_task(_clause_top(clause, ctx, scope, exclude_spec))
            if top is not None:
                found.setdefault(top["target_path"], top)
                clause_of.setdefault(top["target_path"], clause)
    out = place_named_files(hits, list(found), found)
    # Marked after placing: the hit a clause names may be one already served.
    for h in out[:_SERVED]:
        if (clause := clause_of.get(h.get("target_path"))) is not None:
            h["_clause"] = clause
    return out
