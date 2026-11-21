"""One rule for ranking test, generated and vendored code below shipped code.

Shared by search_codebase (file rows and symbols) and get_answer (full and
degraded replies). A file's origin is its ``code_origin``: the origin stored at
index time (``health_file_metrics.code_origin``), else :func:`path_origin`.
Test support (mocks, fixtures, ``conftest.py``) has origin ``test``.

An origin is demoted only when the query does not ask for it: a query about
tests, mocks or fixtures ranks test code naturally, one about generated code
ranks generated files naturally, and so on. A file path in the query counts
as asking for that path's origin.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from repowise.core.code_origin import path_origin
from repowise.server.mcp_server._page_paths import FILE_ROW_TYPES
from repowise.server.mcp_server._query_shape import path_tokens

# Plurals count: "where are the shared fixtures" asks for fixtures.
TEST_QUERY_RE = re.compile(
    r"\b(test|tests|testing|tested|unit[\s-]?tests?|integration[\s-]?tests?|pytest"
    r"|fixtures?|mocks?|conftest|specs?)\b",
    re.IGNORECASE,
)
_ORIGIN_QUERY_RE: dict[str, re.Pattern[str]] = {
    "test": TEST_QUERY_RE,
    "generated": re.compile(r"\b(generated|codegen|code[\s-]gen\w*|zz_generated\w*)\b", re.I),
    # Not bare "vendor": a payment vendor is a domain word, not a request.
    "vendored": re.compile(r"\b(vendored|third[\s_-]?party)\b", re.I),
}


def _named_origins(query: str) -> set[str]:
    return {path_origin(t.replace("\\", "/")) for t in path_tokens(query)}


def is_test_query(query: str) -> bool:
    """True when *query* asks about tests: test words, or a test file path in it."""
    return bool(TEST_QUERY_RE.search(query)) or "test" in _named_origins(query)


def demoted_origins(query: str) -> frozenset[str]:
    """The origins that rank below production code for *query*."""
    named = _named_origins(query)
    return frozenset(
        origin
        for origin, asks in _ORIGIN_QUERY_RE.items()
        if origin not in named and not asks.search(query)
    )


def is_demoted_path(
    path: str, demoted: frozenset[str], origins: Mapping[str, str] | None = None
) -> bool:
    """Whether *path*'s origin (stored in *origins*, else its own) is in *demoted*."""
    if not path or not demoted:
        return False
    return ((origins or {}).get(path) or path_origin(path)) in demoted


def is_demoted_row(
    item: dict, demoted: frozenset[str], origins: Mapping[str, str] | None = None
) -> bool:
    """True when a ranked row is a whole file whose origin is in *demoted*."""
    return item.get("page_type") in FILE_ROW_TYPES and is_demoted_path(
        item.get("target_path") or "", demoted, origins
    )


async def file_origins(session: Any, repo_id: str, paths: Iterable[str | None]) -> dict[str, str]:
    """Stored ``code_origin`` of *paths* (``path_origin`` fallback).

    Page targets that are not files (``a.py::Sym`` spotlights, empty) are
    skipped. ``{}`` on a lookup error: ranking then reads the paths alone, and
    a missing origin never fails the tool call.
    """
    from repowise.core.evidence.ingest import load_origins

    wanted = {p for p in paths if p and "::" not in p}
    if not wanted:
        return {}
    try:
        return await load_origins(session, repo_id, wanted)
    except Exception:
        return {}


async def load_row_origins(session: Any, repo_id: str, rows: Iterable[dict]) -> dict[str, str]:
    """:func:`file_origins` of the whole-file rows among *rows*."""
    return await file_origins(
        session,
        repo_id,
        (r.get("target_path") for r in rows if r.get("page_type") in FILE_ROW_TYPES),
    )
