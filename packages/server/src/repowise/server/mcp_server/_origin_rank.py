"""One rule for ranking test, generated and vendored code below shipped code.

Shared by search_codebase (file rows and symbols) and get_answer (full and
degraded replies). A file's origin is its ``code_origin``: the origin stored at
index time (``health_file_metrics.code_origin``), else :func:`path_origin`.
Test support (mocks, fixtures, ``conftest.py``) has origin ``test``.

An origin is demoted only when the query does not ask for it: a query about
tests, mocks or fixtures ranks test code naturally, one about generated code
ranks generated files naturally, and so on. A file path in the query counts
as asking for that path's origin.

The same module ranks a file in another language than the question's below
the question's own: a ``.proto`` or shell script under a C++ or Go question.
The question's language is read from the index, never guessed from its words:
the stored language (``graph_nodes.language``) of the files defining a symbol
the question names, else the language leading the ranked files.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from typing import Any

from repowise.core.code_origin import path_origin
from repowise.core.ingestion.languages.registry import REGISTRY
from repowise.core.ingestion.models import TS_JS_LANGUAGES
from repowise.server.mcp_server._page_paths import FILE_ROW_TYPES, file_path_of
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


# Languages one question spans as one: a C++ header beside its C source, a
# JavaScript file beside TypeScript, Kotlin beside the Java it calls.
_LANGUAGE_FAMILY: dict[str, str] = {
    "c": "cpp",
    "objectivec": "cpp",
    "kotlin": "java",
    "scala": "java",
} | {tag: "typescript" for tag in TS_JS_LANGUAGES}
_SCHEMA_LANGUAGES = frozenset({"proto", "graphql", "openapi", "json", "sql"})
_CONFIG_LANGUAGES = frozenset({"yaml", "json", "toml"})
_DOC_LANGUAGES = frozenset({"markdown", "asciidoc"})
# File-type words that ask for a language without naming it.
_KIND_WORDS: dict[str, frozenset[str]] = {
    "script": frozenset({"shell"}),
    "scripts": frozenset({"shell"}),
    "bash": frozenset({"shell"}),
    "docker": frozenset({"dockerfile"}),
    "protobuf": frozenset({"proto"}),
    "protobufs": frozenset({"proto"}),
    "schema": _SCHEMA_LANGUAGES,
    "schemas": _SCHEMA_LANGUAGES,
    "config": _CONFIG_LANGUAGES,
    "configuration": _CONFIG_LANGUAGES,
    "manifest": _CONFIG_LANGUAGES,
    "manifests": _CONFIG_LANGUAGES,
    "helm": _CONFIG_LANGUAGES,
    "compose": _CONFIG_LANGUAGES,
    "workflow": _CONFIG_LANGUAGES,
    "workflows": _CONFIG_LANGUAGES,
    "ci": _CONFIG_LANGUAGES,
    "migration": frozenset({"sql"}),
    "migrations": frozenset({"sql"}),
    "doc": _DOC_LANGUAGES,
    "docs": _DOC_LANGUAGES,
    "documentation": _DOC_LANGUAGES,
    "readme": _DOC_LANGUAGES,
}
# How many leading distinct files vote for the question's language.
_VOTE_FILES = 8


def _row_file(item: dict) -> str | None:
    """The file a ranked row stands for: a file, an API contract, a symbol's file."""
    return file_path_of(item.get("page_type"), item.get("target_path"))


def _family(language: str) -> str:
    return _LANGUAGE_FAMILY.get(language, language)


def _is_program(language: str) -> bool:
    """A language that implements behaviour: code, not data, markup or infra."""
    spec = REGISTRY.get(language)
    return bool(spec and spec.is_code and not spec.is_infra)


def _word_language(word: str) -> str | None:
    """The language a query word names: its tag (``proto``, ``go``), or a
    dotted extension (``.tsx``). A bare extension is not enough: ``page`` and
    ``h`` are words first."""
    if word.startswith("."):
        tag = REGISTRY.from_extension(word)
        return tag if tag and tag != "unknown" else None
    return word if REGISTRY.get(word) is not None else None


def asked_languages(query: str) -> frozenset[str]:
    """Language families *query* asks for: a language tag or extension, a
    file of that language, or a file-type word (``script``, ``schema``)."""
    asked: set[str] = set()
    for word in re.findall(r"\.?[a-z0-9+#]+", query.lower()):
        asked.update(_KIND_WORDS.get(word.lstrip("."), ()))
        if (tag := _word_language(word)) is not None:
            asked.add(tag)
    for token in path_tokens(query):
        tag = REGISTRY.from_extension(os.path.splitext(token)[1].lower())
        if tag and tag != "unknown":
            asked.add(tag)
    return frozenset(_family(tag) for tag in asked)


def question_language(hits: list[dict], languages: Mapping[str, str]) -> tuple[str | None, bool]:
    """The language family a question is about, read from stored languages,
    and whether a symbol the question names decided it.

    The files defining a symbol the question names decide first; else the
    first few distinct files of the ranking vote, one vote each. Only
    languages that implement behaviour vote, so a run of ``.proto`` or ``.md``
    rows cannot make the question about them. ``None`` unless one family holds
    a strict majority of the votes: a mixed ranking demotes nothing.
    """
    files = list(dict.fromkeys(p for p in map(_row_file, hits) if p))
    anchored = list(
        dict.fromkeys(p for h in hits if h.get("_symbol_anchored") and (p := _row_file(h)))
    )
    votes: dict[str, int] = {}
    for path in anchored or files[:_VOTE_FILES]:
        lang = languages.get(path, "")
        if _is_program(lang):
            votes[_family(lang)] = votes.get(_family(lang), 0) + 1
    if not votes:
        return None, False
    best = max(votes, key=votes.__getitem__)
    if 2 * votes[best] <= sum(votes.values()):
        return None, False
    return best, bool(anchored)


def is_off_language_row(
    item: dict,
    language: str | None,
    asked: frozenset[str],
    languages: Mapping[str, str],
    *,
    named: bool,
) -> bool:
    """True when a ranked row is backed by a file in another language than
    the question's, and the question does not ask for that language.

    Data, markup and build files (``.proto``, ``.md``, shell) are off-language
    whenever the question's language is known. Code in another language is
    only when a symbol the question names set the language (*named*): a vote
    of the ranking is not evidence enough to sink a repository's other half,
    since a backend question's best file can rank among frontend ones.
    """
    if language is None:
        return False
    lang = languages.get(_row_file(item) or "", "")
    if not lang or REGISTRY.get(lang) is None or lang == "unknown":
        return False
    fam = _family(lang)
    if fam == language or fam in asked:
        return False
    return named or not _is_program(lang)


async def load_row_languages(session: Any, repo_id: str, rows: Iterable[dict]) -> dict[str, str]:
    """Stored language (``graph_nodes.language``) of the files behind *rows*.

    ``{}`` on a lookup error, so ranking falls back to leaving every row where
    it is and a missing language never fails the tool call.
    """
    from sqlalchemy import select

    from repowise.core.persistence.models import GraphNode

    wanted = sorted({p for p in map(_row_file, rows) if p})
    out: dict[str, str] = {}
    try:
        for i in range(0, len(wanted), 400):
            result = await session.execute(
                select(GraphNode.node_id, GraphNode.language).where(
                    GraphNode.repository_id == repo_id,
                    GraphNode.node_type == "file",
                    GraphNode.node_id.in_(wanted[i : i + 400]),
                )
            )
            out.update({row.node_id: row.language for row in result if row.language})
    except Exception:
        return {}
    return out
