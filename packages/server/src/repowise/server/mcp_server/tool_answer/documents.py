"""Documents a question names: Markdown and other prose no wiki page covers.

Only code files get a page, so page retrieval never reaches a README, a design
note or a synced Confluence page. A question word that documents write and no
other file does, in at most ``_SPECIFIC_TOKEN_MAX_FILES`` of them, names those
documents the way an identifier names its definition. They join the hits below
the top three (``place_named_files``) with the lines holding the word as their
excerpt. In a workspace the other repos are read too; their documents are
served as ``other_repo_lines``, since a hit's path is read against this repo.

The lines come from the live scan ``search_codebase`` runs (``_line_hits``),
so nothing is indexed and no provider is needed. Ceilings: words match as the
question writes them, case and all, and a question whose every word is common
reaches no document; ranking documents by how many question words they hold
is the upgrade path.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import re
from pathlib import Path
from typing import Any, NamedTuple

from repowise.core.code_origin import path_origin
from repowise.core.owned_paths import MANAGED_FILES, OWNED_PATTERNS
from repowise.core.support_paths import DOC_EXTENSIONS, classification_token
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._helpers import _get_exclude_spec, is_excluded, read_repo_file_text
from repowise.server.mcp_server._line_hits import _files_holding, _line_row, scan_literal
from repowise.server.mcp_server._page_paths import PAGELESS_FILE
from repowise.server.mcp_server._query_terms import _WORD_RE, QUESTION_FRAME_VERBS, content_terms
from repowise.server.mcp_server.tool_answer.config import _SPECIFIC_TOKEN_MAX_FILES
from repowise.server.mcp_server.tool_answer.symbols import place_named_files

_log = logging.getLogger("repowise.mcp.answer")

# Question words looked up, longest first: one git grep per word per repo.
_DOC_TERMS_MAX = 4
# Documents joining the hits, and the lines served for each.
_DOC_HITS_MAX = 2
_DOC_LINES_MAX = 5
_DOC_GLOBS = tuple(f"*{ext}" for ext in sorted(DOC_EXTENSIONS))


class Documents(NamedTuple):
    hits: list[dict]
    other_repo_lines: list[dict]


_NONE = Documents([], [])


# An issue key (``PROJ-12``), the shape tracker links use: one word, not
# ``PROJ`` and ``12``.
_ISSUE_KEY_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Z][A-Z0-9_]{1,9}-\d+(?![A-Za-z0-9])")


def _terms(question: str) -> list[str]:
    """Content words of *question* as it writes them, framing verbs left out;
    issue keys first, as the most specific.

    Whole words only: ``line`` split out of ``sum_line_amounts`` is not a word
    the question asks about."""
    keys = list(dict.fromkeys(_ISSUE_KEY_RE.findall(question)))
    rest = _ISSUE_KEY_RE.sub(" ", question)
    written = {w.lower(): w for w in _WORD_RE.findall(rest)}
    terms = [
        written[t]
        for t in content_terms(rest, max_terms=None)
        if t in written and t not in QUESTION_FRAME_VERBS
    ]
    return (keys + terms)[:_DOC_TERMS_MAX]


def _repowise_output(path: str) -> bool:
    """A file repowise writes, whole or as a fenced block: its own agent notes, not the project's."""
    owned = any(path.startswith(p) if p.endswith("/") else path == p for p in OWNED_PATTERNS)
    return owned or path in MANAGED_FILES


def _roots(ctx: Any, root: Path, exclude_spec: Any, scope: str | None) -> list[tuple]:
    """``(alias, root, exclude spec)`` per repo read: this one (alias ``None``),
    then the workspace's others unless a *scope* narrows the question to this one."""
    roots: list[tuple] = [(None, root, exclude_spec)]
    registry = _state._registry
    if registry is None or scope:
        return roots
    for alias in registry.get_all_aliases():
        entry = registry.ws_config.get_repo(alias)
        if alias == getattr(ctx, "alias", None) or entry is None:
            continue
        other = (registry.workspace_root / entry.path).resolve()
        if other != root.resolve() and other.is_dir():
            roots.append((alias, other, _get_exclude_spec(other)))
    return roots


def _is_document(path: str) -> bool:
    return classification_token(path) in DOC_EXTENSIONS


def _servable(alias: str | None, path: str, scope: str | None) -> bool:
    """A document worth serving: production-origin, or under the *scope* the question asks about."""
    if scope:
        return alias is None and f"{path}/".startswith(f"{scope.rstrip('/')}/")
    return path_origin(path) == "production"


def _few(files: set[tuple] | None) -> bool:
    """Some files and no more than a word naming one thing is written in."""
    return bool(files) and len(files) <= _SPECIFIC_TOKEN_MAX_FILES


def _front_matter_end(lines: list[str]) -> int:
    """The last line of a leading ``---`` front-matter block, or 0 without one."""
    if not lines or lines[0].strip() != "---":
        return 0
    return next((n for n, line in enumerate(lines[1:], 2) if line.strip() == "---"), 0)


def _lines(root: Path, path: str, terms: list[str]) -> list[dict]:
    """The lines of *path* holding any of *terms*: the body's in file order,
    then front matter's (``key:``, ``url:``), which says less.

    A ticket the question names by its key (``PROJ-12`` for ``PROJ-12.md``)
    serves its opening body lines instead of the lines holding its key: those
    are its key, link and heading, while the opening says what the ticket is.
    The lines another question word holds come first; the opening fills the rest."""
    text = (read_repo_file_text(root, path) or "").splitlines()
    end = _front_matter_end(text)
    own = set(_ISSUE_KEY_RE.findall(Path(path).stem))
    opening = []
    if own & set(terms):
        body = ((n, line) for n, line in enumerate(text[end:], end + 1) if line.strip())
        opening = list(itertools.islice(body, _DOC_LINES_MAX))
    # A ticket with no body still serves the lines naming its key.
    rows: dict[int, dict] = {}
    for term in (t for t in terms if not (opening and t in own)):
        for row in scan_literal(root, [path], term, True)[0]:
            rows.setdefault(row["line"], row)
    for n, line in opening[: max(_DOC_LINES_MAX - len(rows), 0)]:
        rows.setdefault(n, _line_row(path, n, line, "match"))
    return [rows[n] for n in sorted(rows, key=lambda n: (n <= end, n))][:_DOC_LINES_MAX]


def _hit(path: str, lines: list[dict]) -> dict:
    """A pageless hit for a document, its lines as the excerpt. The first line
    is its summary too, which ``why_relevant`` quotes once a low answer drops
    excerpts."""
    first = f"Line {lines[0]['line']}: {lines[0]['text']}" if lines else ""
    return {
        "page_id": f"{PAGELESS_FILE}:{path}",
        "target_path": path,
        "title": path,
        "summary": first,
        "snippet": "",
        "page_type": PAGELESS_FILE,
        "score": 0.0,
        "excerpt": "\n".join(f"{r['line']}: {r['text']}" for r in lines),
    }


async def find_documents(
    question: str, root: Path | None, ctx: Any, *, scope: str | None, exclude_spec: Any
) -> Documents:
    """The documents :mod:`documents` describes; nothing on any failure."""
    try:
        return await _find(question, root, ctx, scope, exclude_spec)
    except Exception:
        _log.warning("get_answer: document scan failed", exc_info=True)
        return _NONE


async def _held(roots: list[tuple], term: str, globs: tuple[str, ...]) -> set[tuple] | None:
    """``(alias, path)`` of every file in every repo holding *term*; ``None`` if any grep failed."""

    async def one(alias: str | None, at: Path, spec: Any) -> set[tuple] | None:
        files = await asyncio.to_thread(_files_holding, at, term, word=True, globs=globs)
        return None if files is None else {
            (alias, f) for f in files if not is_excluded(f, spec) and not _repowise_output(f)
        }

    found = await asyncio.gather(*(one(a, r, s) for a, r, s in roots))
    return None if any(f is None for f in found) else set().union(*found)


def _ticket(term: str, docs: set[tuple] | None) -> bool:
    """An issue key some document is named after (``PROJ-12.md``). The file name
    is what tells a ticket from a key-shaped word such as ``UTF-8``."""
    return bool(docs) and bool(_ISSUE_KEY_RE.fullmatch(term)) and any(
        term in Path(p).stem for _a, p in docs
    )


async def _named_documents(
    terms: list[str], roots: list[tuple], scope: str | None
) -> dict[tuple, list[str]]:
    """The question words each servable ``(alias, path)`` document is named by.

    An issue key with its own document names every document holding it: code
    citing ``PROJ-12`` in a comment is the code legs' to serve, and does not make
    the ticket less its own.
    """
    # Documents first, a cheap grep on a large code base; the whole tree only
    # for a word few documents hold, to see that no other file holds it.
    in_docs = await asyncio.gather(*(_held(roots, t, _DOC_GLOBS) for t in terms))
    named = {t: docs for t, docs in zip(terms, in_docs, strict=True) if _ticket(t, docs)}
    words = [t for t, docs in zip(terms, in_docs, strict=True) if t not in named and _few(docs)]
    in_all = await asyncio.gather(*(_held(roots, t, ()) for t in words))
    for term, files in zip(words, in_all, strict=True):
        if _few(files) and all(_is_document(p) for _a, p in files):
            named[term] = files
    by_doc: dict[tuple, list[str]] = {}
    for term, files in named.items():
        for doc in (d for d in files if _servable(d[0], d[1], scope)):
            by_doc.setdefault(doc, []).append(term)
    return by_doc


async def _find(
    question: str, root: Path | None, ctx: Any, scope: str | None, exclude_spec: Any
) -> Documents:
    terms = _terms(question)
    if root is None or not terms:
        return _NONE
    roots = _roots(ctx, root, exclude_spec, scope)
    by_doc = await _named_documents(terms, roots, scope)
    # Most question words first, then a file the word names (``PROJ-12.md``),
    # then this repo's documents before the others'.
    ranked = sorted(
        by_doc,
        key=lambda d: (
            -len(by_doc[d]),
            not any(t in Path(d[1]).stem for t in by_doc[d]),
            d[0] is not None,
            d[0] or "",
            d[1],
        ),
    )
    at = {a: r for a, r, _s in roots}
    hits: list[dict] = []
    other: list[dict] = []
    for alias, path in ranked[:_DOC_HITS_MAX]:
        lines = await asyncio.to_thread(_lines, at[alias], path, by_doc[(alias, path)])
        if not lines:
            continue  # git grep and the scan disagree on a word edge: no evidence to serve
        if alias is None:
            hits.append(_hit(path, lines))
        else:
            other += [{"repo": alias, "path": path, "line": r["line"], "text": r["text"]} for r in lines]
    return Documents(hits, other[:_DOC_LINES_MAX])


def place_documents(hits: list[dict], documents: list[dict]) -> list[dict]:
    """*hits* with each document hit just below the top three, as a named file
    joins; a hit already carrying the document gets its lines as the excerpt."""
    if not documents:
        return hits
    found = {h["target_path"]: h for h in documents}
    for h in hits:
        if h.get("target_path") in found:
            h.setdefault("excerpt", found[h["target_path"]]["excerpt"])
    return place_named_files(hits, list(found), found)
