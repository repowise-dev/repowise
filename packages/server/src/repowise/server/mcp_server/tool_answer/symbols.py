"""Question identifier extraction + symbol anchoring for retrieval hits.

Pull the identifiers a question names, anchor the files that define them, and
inline the union of a homonym's bodies. Hydration, live source reads, masking
and the withheld-definition scan live in sibling modules and are re-exported
here, since callers and tests import them from this module.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.models import WikiSymbol
from repowise.server.mcp_server._verify import verify_and_heal
from repowise.server.mcp_server.tool_answer.config import (
    _ENRICH_TOP_N_HITS,
    _HIGH_CONFIDENCE_SCORE_FLOOR,
    _HOMONYM_UNION_BODY_MAX_LINES,
    _HOMONYM_UNION_CHAR_BUDGET,
    _HOMONYM_UNION_PROSE_DEF_CEILING,
    _STOPWORDS,
)
from repowise.server.mcp_server.tool_answer.hydration import (  # noqa: F401  re-exported
    _DEFINE_KIND_RANK,
    _STEM_SUFFIXES,
    _hydrate_candidate_defines,
    _hydrate_symbols_for_hits,
    _question_names_symbol,
    _stem,
    _stem_hit,
    _symbol_relevance,
    _text_stems,
)
from repowise.server.mcp_server.tool_answer.source import (  # noqa: F401  re-exported
    _SIG_TERMINATOR_RE,
    _read_repo_text,
    _read_signature_from_source,
    _read_symbol_source,
)
from repowise.server.mcp_server.tool_answer.string_mask import (  # noqa: F401  re-exported
    _BACKTICK_STRING_SUFFIXES,
    _QUOTEISH_RE,
    _REGEX_CAN_START_AFTER,
    _REGEX_CAN_START_AFTER_WORD,
    _has_backtick_strings,
    _Masked,
    _regex_position,
    _skip_quoted,
    _skip_regex,
    _string_masked_lines,
    _walk_string_state,
)
from repowise.server.mcp_server.tool_answer.withheld import (  # noqa: F401  re-exported
    _BRACE_MEMBER,
    _DECL_MODIFIERS,
    _FIRST_WORD_RE,
    _NOT_A_DEFINITION,
    _RESERVED_NAMES,
    _UNBOUNDED_INDENT,
    _WITHHELD_DEF_PATTERNS,
    _WITHHELD_MAX_SYMBOLS,
    _indent_width,
    _match_definition,
    withheld_definitions,
)
from repowise.server.mcp_server.tool_search import _prose_dominates


def _extract_question_identifiers(question: str) -> set[str]:
    """Pull out Python-looking identifiers the question names explicitly.

    snake_case, CamelCase and dotted paths, at least 3 chars and not
    stopwords. Drives question-aware promotion in ``_hydrate_symbols_for_hits``.
    """
    import re

    ids: set[str] = set()
    for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", question):
        # A dotted path yields both the full thing and each part.
        parts = tok.split(".")
        candidates = [tok, *parts]
        for c in candidates:
            if len(c) < 3:
                continue
            if c.lower() in _STOPWORDS:
                continue
            # Pure-lowercase words (``method``, ``class``) match too broadly.
            has_upper = any(ch.isupper() for ch in c)
            has_under = "_" in c
            has_digit = any(ch.isdigit() for ch in c)
            if has_upper or has_under or has_digit:
                ids.add(c)
    return ids


def union_defers_to_synthesis(
    question: str, question_ids: set[str], union_groups: dict
) -> bool:
    """True when an answer-by-union should fall through to synthesis.

    A union answers a small set of parallel implementations, not a prose
    question that merely mentions a generic many-def method. Both must hold:

    * ``_prose_dominates``: a bare symbol lookup still unions.
    * The def count exceeds ``_HOMONYM_UNION_PROSE_DEF_CEILING``.
    """
    if not union_groups:
        return False
    total_defs = sum(len(defs) for defs in union_groups.values())
    if total_defs <= _HOMONYM_UNION_PROSE_DEF_CEILING:
        return False
    return _prose_dominates(question, list(question_ids))


def is_symbol_lookup_question(question: str, question_ids: set[str]) -> bool:
    """True when the question IS the symbol names, not prose that mentions them.

    For a lookup the served body is the answer, so truncating it is a loss on
    its own; for prose the body is evidence for a claim.

    Stricter than ``_prose_dominates``, which counts ASCII tokens and so reads
    non-Latin-script questions and identifier-dense English as bare lookups.
    Removing the identifiers and checking for any surviving word character is
    script-independent.
    """
    if not question_ids:
        return False
    residual = question
    for ident in sorted(question_ids, key=len, reverse=True):
        residual = residual.replace(ident, " ")
    if re.search(r"\w", residual, re.UNICODE):
        return False
    return not _prose_dominates(question, list(question_ids))


def _extract_value_answer(hits: list[dict], question_ids: set[str]) -> dict | None:
    """Verbatim-assignment answer for value-shaped questions (the C1 fast path).

    A matched constant/variable's signature is the verbatim assignment from
    live source, so it is the answer, with no LLM call. Exact name matches win
    over substring matches.
    """
    qids_lower = {q.lower() for q in question_ids}
    candidates: list[dict] = []
    for h in hits[:_ENRICH_TOP_N_HITS]:
        path = h.get("target_path")
        if not path:
            continue
        for s in h.get("symbols") or []:
            if not s.get("_matched") or s.get("kind") not in ("constant", "variable"):
                continue
            sig = s.get("signature") or ""
            if "=" not in sig:
                continue
            entry = {
                "name": s.get("name"),
                "signature": sig,
                "file": path,
                "line": s.get("start_line"),
                "answer": f"{sig}  ({path}:{s.get('start_line')})",
            }
            # Multi-line values: include the live body the hydrator attached.
            excerpt = s.get("source_excerpt")
            if excerpt and excerpt.strip() != sig.strip():
                entry["value_source"] = excerpt
            if (s.get("name") or "").lower() in qids_lower:
                return entry
            candidates.append(entry)
    return candidates[0] if candidates else None


def _symbol_def_dict(sym) -> dict:
    """Plain-dict view of a WikiSymbol def (decouples answer.py from the ORM)."""
    return {
        "name": sym.name,
        "kind": sym.kind,
        "file_path": sym.file_path,
        "start_line": sym.start_line,
        "end_line": sym.end_line,
        "qualified_name": sym.qualified_name,
        "parent_name": sym.parent_name,
    }


async def _anchor_symbol_hits(
    session,
    repo_id: str,
    question_ids: set[str],
    hits: list[dict],
    repo_root: Path | None = None,
    session_factory: Any = None,
) -> tuple[list[dict], dict[str, Any]]:
    """Inject the defining file of a question-named indexed symbol into hits.

    Fuzzy retrieval can miss a deep-path file even when the named symbol is
    indexed. A question identifier resolving to one indexed def makes its file
    the dominant hit, so the answer grounds in the definition.

    Homonyms (several defs of one name) split three ways:

    * The question narrows it to exactly one def: anchor that def.
    * The question does not qualify the name: the defs go to
      ``homonyms["union"]`` so the caller inlines the union of bodies, which
      fuzzy retrieval would never surface.
    * The question qualifies the name (``Parent.leaf``) but no def matches:
      ``homonyms["qualified_miss"]``, so the caller answers not-found rather
      than from a same-named symbol elsewhere.

    Returns ``(hits, homonyms)``; ``hits`` is re-sorted by score (mutated in
    place). ``homonyms = {"union": {name: [def_dict, ...]}, "qualified_miss":
    [name, ...]}``.
    """
    homonyms: dict[str, Any] = {"union": {}, "qualified_miss": []}
    if not question_ids:
        return hits, homonyms
    qids_lower = {q.lower() for q in question_ids}
    res = await session.execute(
        select(WikiSymbol).where(
            WikiSymbol.repository_id == repo_id,
            WikiSymbol.name.in_(list(question_ids)),
            WikiSymbol.kind.in_(("function", "method", "class", "interface")),
        )
    )
    by_name: dict[str, list] = {}
    for row in res.scalars().all():
        # A pathless row names nothing the reply can point at: drop it from
        # every route below.
        if not row.file_path:
            continue
        by_name.setdefault(row.name, []).append(row)

    # Verify bounds before slicing: union and anchored bodies are served at
    # high trust, so a drifted row would ground them in the wrong lines.
    # One live read per file, cached.
    _text_cache: dict[str, str | None] = {}

    async def _verified_dict(row) -> dict:
        d = _symbol_def_dict(row)
        if row.file_path not in _text_cache:
            _text_cache[row.file_path] = _read_repo_text(repo_root, row.file_path)
        text = _text_cache[row.file_path]
        if text is None:
            d["_approx"] = True
            return d
        check = await verify_and_heal(session_factory, row, text)
        d["start_line"], d["end_line"] = check.start_line, check.end_line
        if not check.verified:
            d["_approx"] = True
        return d

    chosen = await _choose_anchor_symbols(by_name, qids_lower, _verified_dict, homonyms)
    if not chosen:
        return hits, homonyms
    await _anchor_chosen_symbols(hits, chosen, _verified_dict)
    hits.sort(key=lambda h: h.get("score", 0.0), reverse=True)
    return hits, homonyms


def _question_names_parent(cand, qids_lower: set[str]) -> bool:
    """Whether the question names this def's parent, or a qualifier of it."""
    if (cand.parent_name or "").lower() in qids_lower:
        return True
    qualified = (cand.qualified_name or "").lower()
    name = (cand.name or "").lower()
    return any(q in qualified for q in qids_lower if len(q) >= 4 and q != name)


async def _choose_anchor_symbols(
    by_name: dict[str, list],
    qids_lower: set[str],
    verified_dict: Callable[[Any], Awaitable[dict]],
    homonyms: dict[str, Any],
) -> list:
    """The one def per question-named symbol to anchor on.

    Names with several defs that cannot be narrowed to one are recorded in
    *homonyms* instead, as a union or a qualified miss.
    """
    # Qualifiers the question used (dotted forms like ``decisionextractor.extract_all``).
    qualifiers = {q for q in qids_lower if "." in q}
    chosen: list = []
    for name, cands in by_name.items():
        if len(cands) == 1:
            chosen.append(cands[0])
            continue
        # Disambiguate a homonym when the question names its parent or the
        # parent appears in the qualified name.
        narrowed = [c for c in cands if _question_names_parent(c, qids_lower)]
        if len(narrowed) == 1:
            chosen.append(narrowed[0])
            continue
        # Can't narrow to exactly one. Decide union vs qualified-miss.
        leaf = (name or "").lower()
        targeted = any(q.rsplit(".", 1)[-1] == leaf and q != leaf for q in qualifiers)
        if narrowed:
            # Qualifier matched >1 def: union of the narrowed set (still all
            # genuine candidates for the qualified name).
            homonyms["union"][name] = [await verified_dict(c) for c in narrowed]
        elif targeted:
            # Qualifier present but matched nothing: do not guess.
            homonyms["qualified_miss"].append(name)
        else:
            # Bare homonym, no qualifier: union of every def.
            homonyms["union"][name] = [await verified_dict(c) for c in cands]
    return chosen


async def _anchor_chosen_symbols(
    hits: list[dict],
    chosen: list,
    verified_dict: Callable[[Any], Awaitable[dict]],
) -> None:
    """Boost (or insert) each chosen def's file and stash the def on it."""
    by_path = {h.get("target_path"): h for h in hits}
    top_score = max((h.get("score", 0.0) for h in hits), default=0.0)
    # Above the current top: an exact symbol match is stronger than prose.
    anchor_score = max(top_score + 2.0, _HIGH_CONFIDENCE_SCORE_FLOOR + 1.0)
    for sym in chosen:
        fp = sym.file_path
        if not fp:
            # A pathless anchor would take rank 1 as an empty row.
            continue
        target = _boost_or_insert_file_hit(hits, by_path, fp, anchor_score)
        target["_symbol_anchored"] = True
        # Stash the named symbol so symbol_bodies serves it even when the
        # hydration cap would drop it. Verified bounds only; an approximate
        # symbol still boosts its file.
        vd = await verified_dict(sym)
        if vd.get("_approx"):
            continue
        target.setdefault("_anchor_symbols", []).append(
            {
                "name": sym.name,
                "kind": sym.kind,
                "start_line": vd["start_line"],
                "end_line": vd["end_line"],
            }
        )


def _boost_or_insert_file_hit(
    hits: list[dict], by_path: dict, path: str, score: float
) -> dict:
    """The hit for *path* raised to at least *score*, or a new one at the front.

    Shared by the symbol and concept anchors, which differ only in the flags
    they set on the returned hit.
    """
    target = by_path.get(path)
    if target is None:
        target = {
            "page_id": f"file_page:{path}",
            "target_path": path,
            "title": path,
            "summary": "",
            "snippet": "",
            "page_type": "file_page",
            "score": score,
        }
        hits.insert(0, target)
        by_path[path] = target
    else:
        target["score"] = max(target.get("score", 0.0), score)
    return target


def attach_truncation_contract(
    entry: dict, *, indexed_end: int, end_served: int, repo_root: Path | None
) -> None:
    """Mark an inlined body that was cut, and name what the cut withheld.

    Every site that inlines a body owes the same keys when the indexed body
    outruns what was served: ``truncated``, a ``continuation`` range, and the
    ``withheld_symbols`` in it, so the consumer knows what it is missing. One
    function, because the confidence cascade reads these keys and two copies
    would drift.

    A falsy ``indexed_end`` means the index recorded no end, which is never a
    cut. ``indexed_end`` is trusted to lie within the live file:
    ``check_symbol_bounds`` owns that clamp.
    """
    if indexed_end and indexed_end > end_served:
        entry["truncated"] = True
        entry["continuation"] = f"{entry['path']}:{end_served + 1}-{indexed_end}"
        withheld = withheld_definitions(repo_root, entry["continuation"])
        if withheld:
            entry["withheld_symbols"] = withheld


def build_homonym_union_bodies(
    repo_root: Path | None,
    union_groups: dict[str, list[dict]],
    char_budget: int = _HOMONYM_UNION_CHAR_BUDGET,
) -> tuple[list[dict], list[dict]]:
    """Inline the UNION of a homonym's defining bodies, char-budgeted.

    ``union_groups`` maps a symbol name to the list of its indexed defs (from
    ``_anchor_symbol_hits``). Returns ``(symbol_bodies, more_definitions)``:

    * ``symbol_bodies``: Read-parity entries (same shape as get_answer's
      existing ``symbol_bodies``: ``path`` / ``name`` / ``lines`` / ``source``,
      plus ``truncated`` / ``continuation`` when the body was line-capped)
      rendered greedily until ``char_budget`` is exhausted. The first def always
      renders even if it alone exceeds the budget (a homonym with one huge def
      must still answer).
    * ``more_definitions``: the defs that did not fit, each ``{file, name,
      line, symbol_id, hint}`` with a "call get_symbol, do NOT Read" redirect so
      the agent never falls back to Read for the remainder.

    Defs are ordered by (name, file_path) so output is deterministic across runs.
    """
    symbol_bodies: list[dict] = []
    more: list[dict] = []
    spent = 0
    defs: list[dict] = []
    for name in sorted(union_groups):
        for d in sorted(union_groups[name], key=lambda x: (x.get("file_path") or "")):
            defs.append(d)

    for d in defs:
        path = d.get("file_path")
        name = d.get("name")
        start = d.get("start_line") or 0
        end = d.get("end_line") or 0
        symbol_id = f"{path}::{name}"
        # Unverified bounds: a get_symbol pointer instead of a slice at
        # unreliable lines under a high-confidence envelope.
        body = (
            None
            if d.get("_approx")
            else _read_symbol_source(
                repo_root, path, start, end, max_lines=_HOMONYM_UNION_BODY_MAX_LINES
            )
        )
        # Budget: always render the first, then only while under budget.
        if body and (not symbol_bodies or spent + len(body) <= char_budget):
            served = body.count("\n") + 1
            end_served = start + served - 1
            entry: dict = {
                "path": path,
                "name": name,
                "lines": [start, end_served],
                "source": body,
            }
            attach_truncation_contract(
                entry, indexed_end=end, end_served=end_served, repo_root=repo_root
            )
            symbol_bodies.append(entry)
            spent += len(body)
        else:
            more.append(
                {
                    "file": path,
                    "name": name,
                    "line": start,
                    "symbol_id": symbol_id,
                    "hint": f"call get_symbol id='{symbol_id}' for this definition, do NOT Read",
                }
            )
    return symbol_bodies, more


async def _concept_anchor_hits(
    repo_root: Path | None,
    question: str,
    hits: list[dict],
) -> list[dict]:
    """Anchor the file whose rationale COMMENT explains a number-bearing question.

    For a question that pins a literal number to a described behaviour (a cap,
    a limit) but names no symbol. Greps comments carrying the number, scores
    them with the rationale miner, and injects the winning file so its comment
    reaches ``code_rationale``.

    Fires only on number-bearing questions (number-free grep is too noisy) and
    only when retrieval missed the winner; otherwise the confidence machinery
    decides. The mined rationale is stashed on the hit to avoid a second grep.

    Returns ``hits`` re-sorted by score (mutated in place). Best-effort: any
    failure leaves ``hits`` untouched.
    """
    import asyncio

    from repowise.server.mcp_server._code_rationale import (
        _salient_numbers,
        grep_comment_candidates,
        mine_rationale,
    )

    if repo_root is None or not question:
        return hits
    # Precision gate: only number-bearing questions.
    if not _salient_numbers(question):
        return hits

    # Blocking grep and file reads: keep them off the server's event loop.
    def _grep_and_mine() -> dict | None:
        candidates = grep_comment_candidates(repo_root, question)
        if not candidates:
            return None
        mined = mine_rationale(repo_root, candidates, question)
        return mined[0] if mined else None

    winner = await asyncio.to_thread(_grep_and_mine)
    if not winner:
        return hits
    winner_path = winner.get("path")
    if not winner_path:
        return hits
    # Retrieval-miss gate: if the winner already leads, leave it to the
    # confidence machinery.
    if hits and hits[0].get("target_path") == winner_path:
        return hits

    near_line = (winner.get("lines") or [0])[0]
    by_path = {h.get("target_path"): h for h in hits}
    top_score = max((h.get("score", 0.0) for h in hits), default=0.0)
    # Above the current top, so synthesis runs instead of gating low.
    anchor_score = max(top_score + 1.5, _HIGH_CONFIDENCE_SCORE_FLOOR + 0.5)
    target = _boost_or_insert_file_hit(hits, by_path, winner_path, anchor_score)
    target["_concept_anchored"] = True
    target["_concept_near_line"] = near_line
    # Served verbatim as ``code_rationale`` on any exit path.
    target["_concept_rationale"] = winner
    hits.sort(key=lambda h: h.get("score", 0.0), reverse=True)
    return hits
