"""WikiSymbol hydration for retrieval hits.

Attach to each ranked file the symbols it defines: a cheap name list for the
candidate pool, and for the top hits the question-ranked symbols with live
signatures and source excerpts.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.models import WikiSymbol
from repowise.server.mcp_server._page_paths import hit_file_path
from repowise.server.mcp_server._query_terms import content_terms, split_humps
from repowise.server.mcp_server._verify import verify_and_heal
from repowise.server.mcp_server.tool_answer.config import (
    _DEFINES_MAX_FILES,
    _DEFINES_PER_CANDIDATE,
    _ENRICH_TOP_N_HITS,
    _MAX_SYMBOLS_PER_HIT,
    _MAX_SYMBOLS_TOP_HIT,
    _RELEVANCE_DOC_CHARS,
    _RELEVANCE_DOC_WEIGHT,
    _RELEVANCE_NAME_WEIGHT,
    _RELEVANCE_SIG_WEIGHT,
    _RELEVANT_EXCERPT_MAX_SYMBOLS,
    _STOPWORDS,
    _SYNTH_FULL_BODY_MAX_SYMBOLS,
    _SYNTH_FULL_SOURCE_LINES,
)
from repowise.server.mcp_server.tool_answer.source import (
    _read_repo_text,
    _read_signature_from_source,
    _read_symbol_source,
)

# Suffixes stripped so a question's word reaches the identifier that answers it
# ("routing" -> the `route` symbol). Longest first; never stems below 4 chars.
_STEM_SUFFIXES = ("tion", "ing", "ion", "es", "ed", "er", "s")


def _stem(token: str) -> str:
    for suffix in _STEM_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)]
    return token


def _text_stems(text: str) -> set[str]:
    """Stemmed content tokens of *text*, hump- and separator-split."""
    return {
        _stem(tok.lower())
        for tok in re.split(r"[^A-Za-z0-9]+", split_humps(text))
        if len(tok) >= 3 and tok.lower() not in _STOPWORDS
    }


def _stem_hit(term: str, tokens: set[str]) -> bool:
    """Whether *term* names one of *tokens*, allowing a shared 4-char root."""
    if term in tokens:
        return True
    return any(
        min(len(term), len(tok)) >= 4 and (term.startswith(tok) or tok.startswith(term))
        for tok in tokens
    )


def _question_names_symbol(row, qids_lower: set[str]) -> bool:
    """Whether an identifier from the question names this symbol.

    Matches the symbol's own name, its full qualified name, or its parent (so a
    class reaches its methods), never a substring of the qualified name, which
    would match every symbol in a package sharing a word with the question.
    """
    if not qids_lower:
        return False
    name_lower = (row.name or "").lower()
    parent_lower = (row.parent_name or "").lower()
    return (
        name_lower in qids_lower
        or (row.qualified_name or "").lower() in qids_lower
        or (bool(parent_lower) and parent_lower in qids_lower)
        or any(
            q in name_lower
            for q in qids_lower
            if len(q) >= 5  # avoid spurious substring matches on short tokens
        )
    )


def _symbol_relevance(entry: dict, terms: set[str]) -> int:
    """How strongly a symbol's own text answers the question's content terms.

    Reads only what hydration already loaded, so it adds no I/O to the call.
    """
    if not terms:
        return 0
    name_tokens = _text_stems(entry.get("name") or "")
    sig_tokens = _text_stems(entry.get("signature") or "")
    # Docstrings are the bulk of the text to tokenize and the weakest signal, so
    # they are only read once a term has missed the name and the signature.
    doc_tokens: set[str] | None = None
    score = 0
    for term in terms:
        if _stem_hit(term, name_tokens):
            score += _RELEVANCE_NAME_WEIGHT
        elif _stem_hit(term, sig_tokens):
            score += _RELEVANCE_SIG_WEIGHT
        else:
            if doc_tokens is None:
                doc_tokens = _text_stems(
                    (entry.get("docstring") or "")[:_RELEVANCE_DOC_CHARS]
                )
            if _stem_hit(term, doc_tokens):
                score += _RELEVANCE_DOC_WEIGHT
    return score


# Definition kinds worth naming in `defines`, best first. Absent kinds (imports,
# re-exports) are not emitted: they would fill the budget with names that
# answer nothing.
_DEFINE_KIND_RANK = {
    "class": 0,
    "interface": 1,
    "struct": 1,
    "enum": 1,
    "type": 2,
    "function": 3,
    "method": 4,
    "constant": 5,
}


async def _hydrate_candidate_defines(
    session,
    repo_id: str,
    hits: list[dict],
    question_ids: set[str] | None = None,
) -> None:
    """Mutate *hits* in place: attach ``_defines`` to the candidate-pool files.

    Naming a candidate file's definitions turns "search this file" into "read
    this line", and often answers a where-is-it question outright.

    Deliberately cheap and shallow: one batched, index-covered query over at
    most ``_DEFINES_MAX_FILES`` paths; names and start lines only (bodies live
    elsewhere); line numbers are index-recorded, not verified, a navigation hint.

    Order within a file: question-named symbols, then declaration kind, then
    position. Private names are dropped unless the question named them.
    """
    qids = {q.lower() for q in (question_ids or set())}

    paths = _candidate_paths(hits)
    if not paths:
        return

    res = await session.execute(
        select(
            WikiSymbol.file_path,
            WikiSymbol.name,
            WikiSymbol.kind,
            WikiSymbol.start_line,
        ).where(
            WikiSymbol.repository_id == repo_id,
            WikiSymbol.file_path.in_(paths),
        )
    )

    by_file: dict[str, list[tuple[int, int, str, int]]] = {}
    for file_path, name, kind, start_line in res.all():
        rank = _DEFINE_KIND_RANK.get((kind or "").lower())
        if rank is None or not name:
            continue
        matched = name.lower() in qids
        if not matched and name.startswith("_"):
            continue
        by_file.setdefault(file_path, []).append(
            (0 if matched else 1, rank, name, start_line or 0)
        )
    defines = {path: _pick_defines(rows) for path, rows in by_file.items()}

    for h in hits:
        p = hit_file_path(h)
        if p and defines.get(p):
            h["_defines"] = defines[p]


def _candidate_paths(hits: list[dict]) -> list[str]:
    """Distinct hit file paths in rank order, capped at ``_DEFINES_MAX_FILES``."""
    paths: list[str] = []
    seen: set[str] = set()
    for h in hits:
        p = hit_file_path(h)
        if not p or p in seen:
            continue
        seen.add(p)
        paths.append(p)
        if len(paths) >= _DEFINES_MAX_FILES:
            break
    return paths


def _pick_defines(rows: list[tuple[int, int, str, int]]) -> list[tuple[str, int]]:
    """The first ``_DEFINES_PER_CANDIDATE`` distinct names, best-ranked first."""
    rows.sort(key=lambda r: (r[0], r[1], r[3]))
    picked: list[tuple[str, int]] = []
    taken: set[str] = set()
    for _m, _r, name, start in rows:
        if name in taken:
            continue
        taken.add(name)
        picked.append((name, start))
        if len(picked) >= _DEFINES_PER_CANDIDATE:
            break
    return picked


async def _hydrate_symbols_for_hits(
    session,
    repo_id: str,
    hits: list[dict],
    ctx: Any = None,
    question_ids: set[str] | None = None,
    question: str = "",
) -> None:
    """Mutate `hits` in place: attach `symbols` list to top-N file_page hits.

    Symbols the question names by identifier move to the top of their file's
    list and get a ``source_excerpt``, so synthesis sees the body it is asked
    about rather than only the enclosing class.

    The top hit gets ``_MAX_SYMBOLS_TOP_HIT`` slots, the rest
    ``_MAX_SYMBOLS_PER_HIT``. ``question`` ranks which symbols fill them and
    earns the leading few a body, since a prose question names no identifier.
    """
    question_ids = question_ids or set()
    qids_lower = {q.lower() for q in question_ids}
    # Once per call: the question's terms, stemmed to match identifier roots.
    term_stems = {_stem(t) for t in content_terms(question)}

    enrich_paths = _enrich_paths(hits)
    if not enrich_paths:
        return

    res = await session.execute(
        select(WikiSymbol)
        .where(
            WikiSymbol.repository_id == repo_id,
            WikiSymbol.file_path.in_(enrich_paths),
        )
        .order_by(WikiSymbol.file_path, WikiSymbol.start_line)
    )
    by_file: dict[str, list[dict]] = {}
    repo_root = Path(str(ctx.path)) if ctx and ctx.path else None
    session_factory = getattr(ctx, "session_factory", None)
    # One live read per hydrated file, shared by the bounds gate and the
    # signature/body slices. None when unreadable (missing/outside root).
    text_cache: dict[str, str | None] = {}
    for row in res.scalars().all():
        if row.file_path not in text_cache:
            text_cache[row.file_path] = _read_repo_text(repo_root, row.file_path)
        entry = await _symbol_entry(
            row,
            text_cache[row.file_path],
            repo_root,
            session_factory,
            qids_lower,
            term_stems,
        )
        by_file.setdefault(row.file_path, []).append(entry)

    for i, h in enumerate(hits):
        path = h.get("target_path")
        if path not in by_file:
            continue
        cap = _MAX_SYMBOLS_TOP_HIT if i == 0 else _MAX_SYMBOLS_PER_HIT
        kept = _keep_symbols(by_file[path], h, cap)
        text = text_cache.get(path)
        _attach_relevant_excerpts(kept, repo_root, path, text)
        _deepen_matched_excerpts(kept, repo_root, path, text)
        # Sort final slice by start_line for natural reading order.
        kept.sort(key=lambda s: s["start_line"])
        h["symbols"] = kept


def _enrich_paths(hits: list[dict]) -> list[str]:
    """The top ``_ENRICH_TOP_N_HITS`` file_page paths, in retrieval-rank order."""
    # `hits` is already sorted by descending score upstream.
    enrich_paths: list[str] = []
    for h in hits:
        if len(enrich_paths) >= _ENRICH_TOP_N_HITS:
            break
        if h.get("target_path") and h.get("page_type") == "file_page":
            enrich_paths.append(h["target_path"])
    return enrich_paths


async def _symbol_entry(
    row,
    text: str | None,
    repo_root: Path | None,
    session_factory: Any,
    qids_lower: set[str],
    term_stems: set[str],
) -> dict[str, Any]:
    """One hydrated symbol: verified bounds, live signature, match and relevance."""
    # Trust contract (shared with get_symbol): verify stored bounds before
    # slicing live text. Unverified, fall back to the stored signature and no
    # body: consistent beats a live slice at the wrong lines.
    if text is not None:
        check = await verify_and_heal(session_factory, row, text)
        start_line, end_line, verified = check.start_line, check.end_line, check.verified
    else:
        start_line, end_line, verified = row.start_line, row.end_line, False
    # For constants/variables the stored signature is the verbatim assignment;
    # the def-line reader would join unrelated following lines.
    if row.kind in ("constant", "variable") or not verified:
        rich_sig = None
    else:
        rich_sig = _read_signature_from_source(
            repo_root, row.file_path, start_line, text=text
        )
    matched = _question_names_symbol(row, qids_lower)
    entry: dict[str, Any] = {
        "name": row.name,
        "kind": row.kind,
        "signature": rich_sig or row.signature,
        "docstring": row.docstring or "",
        "start_line": start_line,
        "end_line": end_line,
        "_matched": matched,
        "_verified": verified,
    }
    # Scored once here, not per sort comparison.
    entry["_relevance"] = _symbol_relevance(entry, term_stems)
    if matched and verified:
        src = _read_symbol_source(
            repo_root, row.file_path, start_line, end_line, text=text
        )
        if src:
            entry["source_excerpt"] = src
    return entry


def _keep_symbols(syms: list[dict], hit: dict, cap: int) -> list[dict]:
    """The ``cap`` symbols of one file worth the synthesis context, in priority order."""
    # Decides which symbols are kept; the caller restores reading order.
    syms.sort(key=lambda s: (not s["_matched"], -s["_relevance"], s["start_line"]))
    # Anchored symbols first, so siblings matching through their parent's name
    # cannot evict the one the question asked about.
    anchor_names = {a.get("name") for a in (hit.get("_anchor_symbols") or [])}
    kept: list[dict] = [s for s in syms if s["name"] in anchor_names][:cap]
    # Then the rest of the matched symbols, then unmatched, up to the cap.
    kept.extend(s for s in syms if s["_matched"] and s not in kept)
    kept = kept[:cap]
    for s in syms:
        if s in kept:
            continue
        if len(kept) >= cap:
            break
        kept.append(s)
    return kept


def _attach_relevant_excerpts(
    kept: list[dict], repo_root: Path | None, path: str, text: str | None
) -> None:
    """Give the leading question-relevant symbols without an excerpt a body."""
    # A prose question matches no identifier, so relevance earns the body.
    # `kept` is still in priority order here.
    bodied = 0
    for s in kept:
        if bodied >= _RELEVANT_EXCERPT_MAX_SYMBOLS:
            break
        if s.get("source_excerpt") or not s["_relevance"] or not s["_verified"]:
            continue
        src = _read_symbol_source(
            repo_root,
            path,
            s["start_line"],
            s.get("end_line") or 0,
            text=text,
        )
        if src:
            s["source_excerpt"] = src
            bodied += 1


def _deepen_matched_excerpts(
    kept: list[dict], repo_root: Path | None, path: str, text: str | None
) -> None:
    """Re-read the leading matched excerpts at the inline-body depth."""
    # Runs while `kept` is in priority order. Reading the leading few at the
    # depth ``symbol_bodies`` serves keeps synthesis consistent with it; the
    # short excerpt can stop before the logic. Bounded to protect the prompt.
    upgraded = 0
    for s in kept:
        if upgraded >= _SYNTH_FULL_BODY_MAX_SYMBOLS:
            break
        if not s.get("_matched") or not s.get("source_excerpt"):
            continue
        fuller = _read_symbol_source(
            repo_root,
            path,
            s["start_line"],
            s.get("end_line") or 0,
            max_lines=_SYNTH_FULL_SOURCE_LINES,
            text=text,
        )
        if fuller:
            s["source_excerpt"] = fuller
        upgraded += 1
