"""Files defining a symbol the question names in plain words.

"How is bus factor calculated" names ``_bus_factor`` without writing it. The
identifier routes need the name typed, and the word-level symbol leg scores
``bus`` and ``factor`` apart, so a UI panel carrying both words outranks the
function that computes the value. This leg joins each run of two or three
adjacent question words into the spellings code gives a compound name
(``bus_factor``, ``_bus_factor``, ``BUS_FACTOR``, ``busFactor``, ``BusFactor``)
and asks the symbol index for exact matches. One indexed query, no scan.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select

from repowise.core.code_origin import prefer_production
from repowise.core.persistence.models import GraphNode, WikiSymbol
from repowise.server.mcp_server._helpers import is_excluded
from repowise.server.mcp_server._query_terms import STOPWORDS
from repowise.server.mcp_server.tool_answer.config import _ANCHOR_KINDS

# Phrase lengths tried, longest first: a three-word name is the more specific.
_RUN_LENGTHS = (3, 2)
# A phrase defined in more production files than this is a generic name
# (``get_name``, ``to_dict``): it names none of them.
_MAX_DEFINING_FILES = 6
# Files the leg adds, at most. They join below the top hits, never above them.
_MAX_FILES = 3
_CALLABLE_KINDS = frozenset({"function", "method"})
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")
# Punctuation ends a phrase: "the cache, key order" joins neither pair across it.
_CLAUSE_BREAK_RE = re.compile(r"[,;:.!?()\[\]{}\"'`]")


def _phrases(question: str) -> list[tuple[str, ...]]:
    """Runs of adjacent plain words, longest first. A stopword or an
    identifier-shaped word (the question typed code) breaks the run."""
    out: list[tuple[str, ...]] = []
    for clause in _CLAUSE_BREAK_RE.split(question):
        runs: list[list[str]] = [[]]
        for word in clause.split():
            plain = _WORD_RE.fullmatch(word)
            if not plain or word.lower() in STOPWORDS or _typed_as_code(word):
                runs.append([])
            else:
                runs[-1].append(word.lower())
        for run in runs:
            for n in _RUN_LENGTHS:
                out.extend(tuple(run[i : i + n]) for i in range(len(run) - n + 1))
    out.sort(key=len, reverse=True)
    return out


def _typed_as_code(word: str) -> bool:
    return any(ch.isupper() for ch in word[1:]) or any(ch.isdigit() for ch in word)


def name_forms(words: tuple[str, ...]) -> set[str]:
    """The spellings code gives the compound name *words* make."""
    snake = "_".join(words)
    camel = words[0] + "".join(w.capitalize() for w in words[1:])
    return {snake, f"_{snake}", f"__{snake}", snake.upper(), camel, camel[0].upper() + camel[1:]}


async def concept_defining_files(
    session, repo_id: str, question: str, exclude_spec: Any = None
) -> list[str]:
    """Files defining a symbol a phrase of *question* spells, best first.

    Production definitions only when there are any (``prefer_production``); a
    function or method before a class or interface, since "how is X computed"
    asks for the code that computes it; then by file PageRank, so the
    definition the rest of the code reaches leads. Empty when no phrase
    resolves.
    """
    phrases = _phrases(question)
    if not phrases:
        return []
    forms = {form: words for words in phrases for form in name_forms(words)}
    res = await session.execute(
        select(WikiSymbol.name, WikiSymbol.kind, WikiSymbol.file_path).where(
            WikiSymbol.repository_id == repo_id,
            WikiSymbol.name.in_(sorted(forms)),
            WikiSymbol.kind.in_(_ANCHOR_KINDS),
        )
    )
    by_phrase: dict[tuple[str, ...], list[tuple[str, str]]] = {}
    for name, kind, path in res.all():
        if path and not is_excluded(path, exclude_spec):
            by_phrase.setdefault(forms[name], []).append((kind, path))
    if not by_phrase:
        return []
    paths = sorted({p for rows in by_phrase.values() for _, p in rows})
    ranks = await _file_pageranks(session, repo_id, paths)
    out: list[str] = []
    for words in phrases:
        rows = by_phrase.get(words)
        if not rows:
            continue
        files = prefer_production(sorted({p for _, p in rows}))
        if len(files) > _MAX_DEFINING_FILES:
            continue
        callable_in = {p for kind, p in rows if kind in _CALLABLE_KINDS}
        files.sort(key=lambda p: (p not in callable_in, -ranks.get(p, 0.0), p))
        out.extend(p for p in files if p not in out)
    return out[:_MAX_FILES]


async def _file_pageranks(session, repo_id: str, paths: list[str]) -> dict[str, float]:
    res = await session.execute(
        select(GraphNode.node_id, GraphNode.pagerank).where(
            GraphNode.repository_id == repo_id,
            GraphNode.node_type == "file",
            GraphNode.node_id.in_(paths),
        )
    )
    return {node_id: pagerank or 0.0 for node_id, pagerank in res.all()}
