"""Lifts for concept questions whose answer the index already names.

The page legs rank prose. A page that talks about a concept a lot (a chart of
bus factor, a doc about entry points) outranks the file that defines it, whose
page may never use the words. Two cheap priors read what the index already
knows and lift those files the way a hit both page legs ranked is weighted:

* A question that asks where or how something is done asks for a definition.
  A file defining a symbol whose name is spelled entirely by the question's
  words (``_bus_factor``, ``computeBusFactor`` for "where is bus factor
  computed") is lifted. Whole words only: ``BusFactorPanel`` carries a word
  the question never said, so it is a use of the concept, not its definition.
* A question about the entry point lifts the repository's curated entry
  points, the list ``get_overview`` serves, and adds the ones no leg reached.
  Words beyond "entry point" narrow them by path ("entry point of the auth
  service" keeps the entry points whose path says both ``auth`` and
  ``service``).

Both read names and paths only, so they hold for any language.
"""

from __future__ import annotations

import logging
from typing import Any

from repowise.core.code_origin import path_origin
from repowise.core.persistence.database import get_session
from repowise.server.mcp_server import tool_search as _pkg
from repowise.server.mcp_server._answer_pipeline import _RRF_K, _RRF_SCORE_SCALE
from repowise.server.mcp_server._helpers import _get_repo
from repowise.server.mcp_server._prose_symbols import _file_pages, _fold, _pageless_row
from repowise.server.mcp_server._query_terms import QUESTION_FRAME_VERBS, STOPWORDS
from repowise.server.mcp_server._retrieval_rank import _PATH_NAME_LIFT_CAP
from repowise.server.mcp_server.tool_overview.pages import _resolve_entry_point_ids
from repowise.server.mcp_server.tool_search_symbols import _tokens

_log = logging.getLogger("repowise.mcp.search")

# Words that make a question ask where or how something is done.
_LOCATE_WORDS = frozenset({"where", "how", "which"})

# Fewer words than this and a name is too generic to call a definition
# (``main``, ``config``, ``run``).
_DEFINITION_MIN_WORDS = 2

# Share of the question's own words a name must spell, so ``git_history``
# is not the definition "where are decisions extracted from git history" asks for.
_CONCEPT_MIN_SHARE = 2 / 3

# Files lifted per question. Past a few, a name that common is not one place.
_MAX_LIFTED = 3

# Each lifted file counts as two page-leg hits at its rank among the lifted.
_LIFT_LEGS = 2

_ENTRY_WORDS = frozenset({"entry", "point", "entrypoint"})
# Said of the entry point itself; they do not narrow which one.
_ENTRY_QUALIFIERS = _ENTRY_WORDS | {"main", "program", "app", "application", "start", "run"}


def _words(text: str) -> set[str]:
    """Whole words of *text* or of an identifier, folded (``computed`` meets ``compute``)."""
    return {_fold(t) for t in _tokens(text) if t not in STOPWORDS}


# Words that frame a question rather than name what it asks about.
_FRAME_WORDS = _words(" ".join(_LOCATE_WORDS | QUESTION_FRAME_VERBS))


def _lift(rank: int) -> float:
    return _LIFT_LEGS * _RRF_SCORE_SCALE / (rank + _RRF_K)


def _defines(name: str, asked: set[str], concept: set[str]) -> bool:
    """Whether *name* is built only of the question's words and spells most of what it asks.

    Every word counts, a leading verb included: ``get_dead_code`` is the tool
    that serves dead code, not where the analysis is done.
    """
    words = _words(name)
    return (
        len(words) >= _DEFINITION_MIN_WORDS
        and words <= asked
        and len(words & concept) >= _CONCEPT_MIN_SHARE * len(concept)
    )


def definition_lifts(query: str, symbol_hits: list[Any]) -> dict[str, float]:
    """Page id -> lift for symbol-leg files defining a name the question spells.

    *symbol_hits* is the symbol leg's results in rank order, each carrying the
    ``symbol_names`` that earned it its place. Shipped code only: a test that
    defines the name is not where the thing is done.
    """
    raw = {t.lower() for t in _tokens(query)}
    if not raw & _LOCATE_WORDS:
        return {}
    asked = _words(query)
    concept = asked - _FRAME_WORDS
    lifts: dict[str, float] = {}
    for hit in symbol_hits:
        path = hit.page_id.partition(":")[2]
        if path_origin(path) != "production":
            continue
        if any(_defines(name, asked, concept) for name in getattr(hit, "symbol_names", ()) or ()):
            lifts[hit.page_id] = _lift(len(lifts))
            if len(lifts) == _MAX_LIFTED:
                break
    return lifts


def _entry_question(query: str) -> set[str] | None:
    """The words narrowing an entry-point question, or None for another question."""
    asked = _words(query)
    if not ({"entry", "point"} <= asked or "entrypoint" in asked):
        return None
    return asked - {_fold(w) for w in _ENTRY_QUALIFIERS}


async def entry_point_rows(ctx: Any, query: str) -> list[dict]:
    """The curated entry points an entry-point question asks for, best first.

    Each row is a file page row (or a pageless file row) with a ``lift``.
    Empty for any other question, and on any failure: the legs still answer.
    """
    narrowing = _entry_question(query)
    if narrowing is None:
        return []
    try:
        async with get_session(ctx.session_factory) as session:
            repository = await _get_repo(session)
            ids = await _resolve_entry_point_ids(
                session, repository, _pkg._get_exclude_spec(ctx.path)
            )
            if narrowing:
                ids = [p for p in ids if narrowing <= _words(p)]
            ids = ids[:_MAX_LIFTED]
            pages = await _file_pages(session, ids) if ids else {}
    except Exception:
        _log.debug("search_codebase: entry point lookup failed", exc_info=True)
        return []
    rows = [pages.get(p) or _pageless_row(p, "") for p in ids]
    for rank, row in enumerate(rows):
        row["lift"] = _lift(rank)
    return rows


def lift_named_rows(rows: list[dict], *, score_key: str) -> None:
    """Weigh each lifted row as a path naming the question's words is weighed.

    Runs after :func:`boost_named_paths`: a page whose path spells the concept
    (``bus-factor-panel.tsx``) gets that lift, and the file the question names
    by its definition or as the entry point gets the same. In place, unsorted.
    """
    for row in rows:
        if row.pop("_named", False) and row.get(score_key):
            row[score_key] = round(row[score_key] * (1.0 + _PATH_NAME_LIFT_CAP), 4)
