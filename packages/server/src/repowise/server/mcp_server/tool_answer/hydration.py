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

    Substring-matching the whole qualified name marked every symbol in a package
    whose path shares a word with the question, which flattened the promotion to
    a no-op. Matching is against the symbol's own name, its full qualified name,
    or its parent: asking about a class should still reach its methods.
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


# Definition kinds worth naming in `defines`, best first. A file is
# characterised by what it declares, so a class outranks a bare function and
# both outrank a variable. Kinds absent from this map are not emitted at all:
# imports and re-exports would fill the budget with names that answer nothing.
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

    ``candidates`` names the files retrieval ranked and, before this, said
    nothing about any of them. An agent handed ``django/shortcuts.py`` and
    nothing else has exactly one move available, which is to go and Grep it; the
    Layer B taxonomy judged 89% of post-answer searches to be that move. Naming
    the definitions a file contains turns "search this file" into "read this
    line", and often answers a where-is-it question outright.

    Deliberately cheap and deliberately shallow:

    * **One batched query**, on ``(repository_id, file_path)`` which the
      ``uq_wiki_symbol`` index already covers, over at most
      ``_DEFINES_MAX_FILES`` paths. No live file reads, no bounds verification.
    * **Names and start lines only.** No signature, no docstring, no body. Those
      already have homes (``retrieval[].key_symbols``, ``symbol_bodies``) and
      this block must not compete with them for the payload's byte budget.
    * **Line numbers are index-recorded, not verified.** Unlike ``get_symbol``,
      nothing here checks the stored bounds against the live file. They are a
      navigation hint; the serializer's field documentation says so.

    Ordering within a file: question-named symbols first (they are what the
    agent came for), then by declaration kind, then by position. Dunders and
    private names are dropped unless the question named them.
    """
    qids = {q.lower() for q in (question_ids or set())}

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

    for path, rows in by_file.items():
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
        by_file[path] = picked  # type: ignore[assignment]

    for h in hits:
        p = hit_file_path(h)
        if p and by_file.get(p):
            h["_defines"] = by_file[p]


async def _hydrate_symbols_for_hits(
    session,
    repo_id: str,
    hits: list[dict],
    ctx: Any = None,
    question_ids: set[str] | None = None,
    question: str = "",
) -> None:
    """Mutate `hits` in place: attach `symbols` list to top-N file_page hits.

    Question-aware promotion: if ``question_ids`` contains identifiers that
    match symbols in the retrieved files, those symbols move to the top of
    their file's symbol list, carry a longer docstring, and get a source
    excerpt (``source_excerpt``). This is the difference between the LLM
    seeing ``class LocalOutlierFactor`` at the file top (and hedging on a
    question about ``_local_reachability_density``) vs. seeing the actual
    method body and answering it.

    Top hit gets ``_MAX_SYMBOLS_TOP_HIT`` slots; secondaries get the smaller
    ``_MAX_SYMBOLS_PER_HIT``. Symbols not matching a question id carry the
    short 120-char docstring; matched symbols carry 400 chars + source body.

    ``question`` decides which symbols fill those slots when the file holds more
    than fit, and earns the leading few a source body: a question phrased in
    prose names no identifier, so nothing matches and nothing would carry code.
    """
    question_ids = question_ids or set()
    # Case-folded copy for matching.
    qids_lower = {q.lower() for q in question_ids}
    # Once per call: the question's terms, stemmed to match identifier roots.
    term_stems = {_stem(t) for t in content_terms(question)}

    # Identify the top file_page hits in retrieval-rank order. `hits` is
    # already sorted by descending score upstream.
    enrich_paths: list[str] = []
    for h in hits:
        if (
            h.get("target_path")
            and h.get("page_type") == "file_page"
            and len(enrich_paths) < _ENRICH_TOP_N_HITS
        ):
            enrich_paths.append(h["target_path"])
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
        text = text_cache[row.file_path]
        # Trust contract (shared with get_symbol): verify the stored bounds
        # against the live file before slicing a signature or body out of it.
        # Drift (an edit above the def, or an update lag) otherwise turns into a
        # garbled signature / body served as if fresh. On a re-parse correction
        # the row is healed; when the symbol can't be re-located we fall back to
        # the stored signature and skip the live body — a stored-but-consistent
        # signature beats a live slice at the wrong lines.
        if text is not None:
            check = await verify_and_heal(session_factory, row, text)
            start_line, end_line, verified = check.start_line, check.end_line, check.verified
        else:
            start_line, end_line, verified = row.start_line, row.end_line, False
        # Constants/variables: the stored signature IS the verbatim assignment
        # line. The disk re-read below walks forward looking for a ":"-closed
        # def line and would join unrelated following lines for assignments.
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
        # Scored once here, not in the sort key, so a dense file pays for it per
        # symbol rather than per comparison.
        entry["_relevance"] = _symbol_relevance(entry, term_stems)
        if matched and verified:
            src = _read_symbol_source(
                repo_root, row.file_path, start_line, end_line, text=text
            )
            if src:
                entry["source_excerpt"] = src
        by_file.setdefault(row.file_path, []).append(entry)

    # Sort: matched symbols first, then by relevance to the question, then in
    # start_line order. Cap per file — top hit gets more slots than secondary
    # hits. This decides WHICH symbols are kept; the kept slice is put back into
    # reading order below, so consumers still see document order.
    for i, h in enumerate(hits):
        path = h.get("target_path")
        if path not in by_file:
            continue
        syms = by_file[path]
        syms.sort(key=lambda s: (not s["_matched"], -s["_relevance"], s["start_line"]))
        cap = _MAX_SYMBOLS_TOP_HIT if i == 0 else _MAX_SYMBOLS_PER_HIT
        # Force-include the exact symbol the question named (via anchoring) so a
        # class-name flood — where every sibling method "matches" through the
        # parent's qualified name — can't evict the method the user asked about
        # from the synthesis context. Without this the LLM never sees the body
        # and hedges, which is exactly the failure anchoring exists to prevent.
        anchor_names = {a.get("name") for a in (h.get("_anchor_symbols") or [])}
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
        # A prose question names no identifier, so nothing is `_matched` and the
        # slate would carry signatures only. Give the leading few symbols the
        # question scored against a body, so the excerpts hold the code the
        # question is about. `kept` is still in priority order here.
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
                text=text_cache.get(path),
            )
            if src:
                s["source_excerpt"] = src
                bodied += 1
        # Upgrade the top question-relevant symbols to the inline-body depth
        # BEFORE the reading-order sort, while `kept` is still in priority order
        # (anchors, then matched, then unmatched). The default 40-line excerpt
        # truncates a docstring-heavy definition before its answer-bearing logic,
        # so synthesis hedges on the exact symbol whose full 120-line body the
        # response inlines in symbol_bodies. Reading the leading few at the same
        # depth keeps the LLM's view and the served body consistent. Bounded so a
        # class-name flood can't balloon the prompt; the rest keep the excerpt.
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
                text=text_cache.get(path),
            )
            if fuller:
                s["source_excerpt"] = fuller
            upgraded += 1
        # Sort final slice by start_line for natural reading order.
        kept.sort(key=lambda s: s["start_line"])
        h["symbols"] = kept
