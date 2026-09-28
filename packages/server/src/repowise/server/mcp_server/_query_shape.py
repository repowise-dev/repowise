"""How ``search_codebase`` reads a query's shape, and its relevance constants.

Stdlib only at import time: no database session, no MCP tool registry, so a
caller can reuse the routing without loading either. The language registry is
imported on the first extension check, not at import.
"""

from __future__ import annotations

import os.path
import re
from functools import cache


@cache
def _code_exts() -> frozenset[str]:
    from repowise.core.ingestion.languages.registry import REGISTRY

    return REGISTRY.all_code_extensions()


# Words that mark a string as a natural-language question rather than a path.
# Keep this small — false positives here send genuine paths to the NL branch,
# which is harmless (path lookup also runs as a fallback) but slower.
_NL_QUESTION_TOKENS = frozenset(
    {
        "why",
        "how",
        "what",
        "when",
        "where",
        "who",
        "which",
        "should",
        "can",
        "does",
        "do",
        "is",
        "are",
        "was",
        "were",
    }
)


def _is_path(query: str) -> bool:
    """Heuristic: does this string look like a file or module path?

    Natural-language questions take precedence over the slash heuristic
    because phrases like "two-phase plan/apply flow" or "client/server
    boundary" contain a slash without being paths. We treat anything with
    a question mark, that starts with a question word, or that has 4+
    whitespace-separated tokens including a question word, as NL.
    """
    stripped = query.strip()
    if not stripped:
        return False

    # Trailing "?" is an unambiguous NL signal.
    if stripped.endswith("?"):
        return False

    tokens = stripped.split()

    # First token is a question word → NL.
    if tokens and tokens[0].lower().rstrip(",.;:") in _NL_QUESTION_TOKENS:
        return False

    # Sentence-shaped input (multiple words including a question word) → NL.
    if len(tokens) >= 4 and any(t.lower().rstrip(",.;:") in _NL_QUESTION_TOKENS for t in tokens):
        return False

    # A path can't contain whitespace.
    if any(ch.isspace() for ch in stripped):
        return False

    if "/" in stripped or "\\" in stripped:
        return True
    _, ext = os.path.splitext(stripped)
    return ext in _code_exts()


def _qual_norm(name: str | None) -> str:
    """Normalize a qualified name's separators (``::``/``/`` -> ``.``), lowered."""
    s = name or ""
    for sep in ("::", "/"):
        s = s.replace(sep, ".")
    return s.lower()


# Pure-identifier pattern: a single bareword that looks like a code symbol
# (no spaces, no punctuation other than _/.). These are almost always
# better handled by Grep than by semantic search — vector embeddings of a
# bare ``getCurrentUser`` quickly drift to thematically-similar but
# textually-distant pages, while Grep would have found the literal usage
# in milliseconds. We hint to Grep but still run the search so callers
# that genuinely want fuzzy symbol search are not blocked.
_IDENT_QUERY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{1,29}$")


def _canonical_symbol_query(query: str) -> tuple[str, str] | None:
    """Return ``(path, symbol)`` for an exact canonical ``path::Symbol`` query."""
    stripped = query.strip().replace("\\", "/")
    if "::" not in stripped:
        return None
    path, symbol = stripped.rsplit("::", 1)
    if not path or not symbol or "/" not in path:
        return None
    return path, symbol


def _looks_like_exact_token(query: str) -> bool:
    """True when the query is a single identifier-shaped token best served by Grep."""
    stripped = query.strip()
    if not stripped or " " in stripped:
        return False
    return bool(_IDENT_QUERY_RE.match(stripped))


# Identifier-shaped tokens inside a longer query: snake_case of any casing
# (≥1 underscore, incl. _UPPER_SNAKE constants) or CamelCase (≥2 humps).
# Plain English words never match.
_IDENT_TOKEN_RE = re.compile(
    r"\b(?:_*[A-Za-z0-9]+_[A-Za-z0-9_]+|[A-Z][A-Za-z0-9_]+)"
    r"(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b"
    r"|\b(?:_*[A-Za-z0-9]+_[A-Za-z0-9_]+|[A-Z][a-z][a-z0-9]*(?:[A-Z][a-z0-9]+)+)\b"
)


def _embedded_identifiers(query: str) -> list[str]:
    """Identifier-shaped tokens carried inside a natural-language query."""
    return _IDENT_TOKEN_RE.findall(query)


def _identifier_candidates(query: str, mode: str) -> list[str]:
    """Identifier tokens the query is asking after, for the exact-match signal.

    A single-token query IS the identifier (symbol mode); a natural-language
    query carrying identifiers (hybrid mode) exposes them the same way
    ``_resolve_mode`` used to route here. Concept/path queries name none.
    """
    if mode == "symbol":
        q = query.strip()
        canonical = _canonical_symbol_query(q)
        return [q, canonical[1]] if canonical else ([q] if q else [])
    if mode == "hybrid":
        return _embedded_identifiers(query)
    return []


def _qualified_name_matches(qn: str, wanted: set[str]) -> bool:
    if not qn:
        return False
    if qn in wanted:
        return True
    return any("." in candidate and qn.endswith(f".{candidate}") for candidate in wanted)


def _symbol_matches_name(item: dict, wanted: set[str]) -> bool:
    symbol_id = (item.get("symbol_id") or "").strip().lower().replace("\\", "/")
    if symbol_id and symbol_id in wanted:
        return True
    name = (item.get("name") or "").strip().lower()
    if name and name in wanted:
        return True
    return _qualified_name_matches(_qual_norm(item.get("qualified_name")), wanted)


def _has_exact_symbol(candidates: list[str], symbols: list[dict]) -> bool:
    """True when some returned symbol's name/qualified-name equals a candidate.

    Reuses the scorer's separator-normalisation so an agent's ``Class.method``
    matches a ``Class::method`` qualified_name in the index. This is the score
    cliff made explicit: an exact hit and a fuzzy neighbour look identical in
    the result list otherwise, and the agent anchors on whatever ranks first.
    """
    if not candidates or not symbols:
        return False
    wanted = {c.strip().lower() for c in candidates if c.strip()}
    wanted |= {_qual_norm(c) for c in candidates if c.strip()}
    return any(_symbol_matches_name(symbol, wanted) for symbol in symbols)


_VALID_MODES = {"auto", "concept", "symbol", "path", "hybrid"}


def _resolve_mode(query: str, mode: str | None) -> str:
    """Resolve ``mode="auto"`` to a concrete branch from the query shape.

    Explicit modes pass through. ``auto`` routes path-shaped queries to path
    search, single identifier-shaped tokens to symbol search, and queries that
    merely *carry* an identifier inside natural language to hybrid; everything
    else stays concept (the original wiki-semantic path). The routing reuses
    the exact heuristics that previously only emitted a grep_hint.
    """
    m = (mode or "auto").lower()
    if m not in _VALID_MODES:
        m = "auto"
    if m != "auto":
        return m
    if _canonical_symbol_query(query):
        return "symbol"
    if _is_path(query):
        return "path"
    if _looks_like_exact_token(query):
        return "symbol"
    if _embedded_identifiers(query):
        return "hybrid"
    return "concept"


# Minimum relevance score below which results are dropped. Prevents
# returning semantically unrelated pages when the corpus has no real match.
_MIN_RELEVANCE_SCORE = 0.03


# Decision records are short, dense title-statements; they win cosine
# similarity against long file-page embeddings on any query containing
# design nouns ("store", "SQLite", "cap", "prune") and crowd file pages
# out of the top ranks entirely. Down-weight them unless the query is
# why-shaped — rationale questions are get_why's territory, but a caller
# who phrases one here clearly wants the decision pages ranked honestly.
_DECISION_DOWNWEIGHT = 0.6


_WHY_SHAPED_RE = re.compile(
    r"^\s*(why|when\s+did|when\s+was|who\s+decided|who\s+chose|what\s+was\s+the\s+(reason|rationale))\b"
    r"|\b(decision|decided|rationale|adr)\b",
    re.IGNORECASE,
)


def _is_why_shaped(query: str) -> bool:
    """True when the query asks for rationale, so decision records should rank naturally."""
    return bool(_WHY_SHAPED_RE.search(query))


def _fetch_limit_for(limit: int, kind: str | None) -> int:
    """Over-fetch headroom for post-filters and decision down-weighting.

    Always over-fetch at least 3x: without headroom the down-weighting can
    only reorder a window that decision records may already fill, so file
    pages never surface. ``kind`` trims hardest (decision/module/overview
    pages all classify as "doc"), so it gets 6x — 3x was measured to leave
    zero implementation pages in the window on decision-heavy queries.
    """
    return limit * (6 if kind else 3)
