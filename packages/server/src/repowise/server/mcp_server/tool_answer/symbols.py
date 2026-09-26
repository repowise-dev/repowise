"""Question identifier extraction + symbol anchoring for retrieval hits.

Pull the identifiers a question names, anchor the files that define them, and
inline the union of a homonym's bodies. Hydration, live source reads, masking
and the withheld-definition scan live in sibling modules and are re-exported
here, since callers and tests import them from this module.
"""

from __future__ import annotations

import re
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

    Targets: snake_case (``_local_reachability_density``), CamelCase
    (``NearestCentroid``), dotted paths (``BaseLabelPropagation.fit``).
    Filtered to ≥3 chars, non-stopwords, non-pure-lowercase-English (unless
    they contain an underscore or a digit — otherwise every common word
    matches). The result drives question-aware symbol promotion in
    ``_hydrate_symbols_for_hits``.
    """
    import re

    ids: set[str] = set()
    # Match bare identifiers and dotted paths: first char letter/underscore,
    # rest alnum/underscore, optionally with dotted continuations.
    for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", question):
        # Split dotted paths into both the full thing and the leaf.
        parts = tok.split(".")
        candidates = [tok, *parts]
        for c in candidates:
            if len(c) < 3:
                continue
            if c.lower() in _STOPWORDS:
                continue
            # Heuristic: keep if it contains an uppercase letter anywhere
            # (covers CamelCase and sentence-initial capitalised nouns like
            # ``Version`` that are typically class names in Python), a
            # digit, or an underscore. Pure-lowercase English words like
            # ``method`` / ``class`` / ``dtype`` are dropped — they are
            # poor promotion signals and match too broadly.
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

    Answer-by-union is the right reply for a small set of genuine parallel
    implementations the question is actually about (``_severity_for`` has 4
    across the biomarkers). It is the WRONG reply when a prose question merely
    *mentions* a generic method that happens to have many definitions: measured,
    "how does a wiki page get its provider_name during indexing?" dumped 12
    unrelated provider stubs as a confidence=high answer, and a ``to_dict``
    mention dumped 28. Two signals must both hold before deferring, so the
    narrowest population is affected:

    * ``_prose_dominates`` — the query reads as prose, not a bare symbol lookup.
      A bare ``provider_name`` (prose does not dominate) still unions: that
      caller explicitly asked for every definition.
    * the def count exceeds ``_HOMONYM_UNION_PROSE_DEF_CEILING`` — past a
      handful, the name is a generic method, not a small parallel-impl set.

    Small genuine unions and explicit lookups are untouched; only a prose
    question naming a many-def generic method falls through to synthesis (which
    grounds in the file the question is really about).
    """
    if not union_groups:
        return False
    total_defs = sum(len(defs) for defs in union_groups.values())
    if total_defs <= _HOMONYM_UNION_PROSE_DEF_CEILING:
        return False
    return _prose_dominates(question, list(question_ids))


def is_symbol_lookup_question(question: str, question_ids: set[str]) -> bool:
    """True when the question IS the symbol names, not prose that mentions them.

    ``ModelAdmin`` is a lookup; "how does ModelAdmin dispatch a request" is
    prose that merely names one. The distinction matters wherever the question
    is whether a served BODY is the answer: for a lookup it is, so truncating
    it is a loss on its own; for prose the body is evidence for a claim, and
    truncation alone says little (22% of truncations withhold nothing the
    response leans on).

    **Stricter than ``_prose_dominates``, deliberately.** That predicate counts
    ``[A-Za-z0-9_]+`` tokens, so a question written in Cyrillic, Japanese or
    Chinese tokenises to nothing but its identifiers and reads as a bare
    lookup — and repowise ships an output-language feature, so those callers
    exist. It also misreads dense English ("Why does ModelAdmin call
    get_queryset, get_form and save_model?" is 4 identifiers in 7 tokens).
    Removing the identifiers and asking whether any word character survives is
    script-independent and says what "bare lookup" actually means.
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

    When a question names an identifier and the hydrator matched a
    constant/variable symbol in the top hits, the symbol's signature IS the
    answer — the verbatim assignment line read from live source. No LLM
    call, nothing to hedge, nothing to invent. Exact name matches win over
    substring matches.
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
            # Multi-line values (dicts/arrays): the hydrator attached the
            # live body — include it so the agent never needs a follow-up.
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

    BM25 / vector retrieval misses deep-path files even when the named symbol
    is indexed — "explain DecisionExtractor.extract_all" ranks the pipeline
    orchestrators above ``analysis/decisions/extractor.py`` and never surfaces
    the definition, so synthesis hedges and ``symbol_bodies`` can't fire. When
    a question identifier resolves to a single indexed function / method /
    class, prepend (or boost) its defining file as the dominant hit so the
    answer grounds in the actual definition.

    Homonyms (N>=2 defs of one name) split three ways:

    * The question names the parent / qualifies the name so exactly one def
      survives → anchor that def (as before).
    * The question does NOT qualify the name → the whole def set is returned in
      ``homonyms["union"]`` so the caller can inline the UNION of bodies instead
      of bailing to a best_guesses pointer list (the pointer list is exactly
      what triggers the agent's get_symbol/get_context drill). This is the fix
      for the retrieval-MISS class (``_severity_for`` x 4) - the defs are never
      in the fuzzy candidate set, so an exact-name index scan is the only thing
      that surfaces them.
    * The question qualifies the name (``Parent.leaf``) but NO def matches that
      qualifier → recorded in ``homonyms["qualified_miss"]`` so the caller can
      return not-found instead of synthesizing from a same-named symbol
      elsewhere (a precise query must never degrade to a confident wrong answer).

    Returns ``(hits, homonyms)``; ``hits`` is re-sorted by score (mutated in
    place). ``homonyms = {"union": {name: [def_dict, ...]}, "qualified_miss":
    [name, ...]}``.
    """
    homonyms: dict[str, Any] = {"union": {}, "qualified_miss": []}
    if not question_ids:
        return hits, homonyms
    qids_lower = {q.lower() for q in question_ids}
    # Qualifiers the question used (dotted forms like ``decisionextractor.extract_all``).
    qualifiers = {q for q in qids_lower if "." in q}
    res = await session.execute(
        select(WikiSymbol).where(
            WikiSymbol.repository_id == repo_id,
            WikiSymbol.name.in_(list(question_ids)),
            WikiSymbol.kind.in_(("function", "method", "class", "interface")),
        )
    )
    by_name: dict[str, list] = {}
    for row in res.scalars().all():
        # A pathless row names nothing the reply can point at. Dropping it here
        # keeps it out of every route below: the anchor, and both union branches
        # that would otherwise read the live file at an empty path.
        if not row.file_path:
            continue
        by_name.setdefault(row.name, []).append(row)

    # Verify bounds against the live file before any body is sliced from a
    # stored range. Both the answer-by-union bodies (grounding=exact_symbol,
    # confidence=high) and the anchored tier-0 symbol_bodies serve live source at
    # these bounds, so a drifted row would otherwise ground the strongest-trust
    # answer in the wrong lines. Cheap gate first (string check); a re-parse fires
    # only on a genuine miss and heals the row. One live read per file, cached.
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

    chosen: list = []
    for name, cands in by_name.items():
        if len(cands) == 1:
            chosen.append(cands[0])
            continue
        # Disambiguate a homonym when the question names its parent or the
        # parent appears in the qualified name.
        narrowed = [
            c
            for c in cands
            if (c.parent_name or "").lower() in qids_lower
            or any(
                q in (c.qualified_name or "").lower()
                for q in qids_lower
                if len(q) >= 4 and q != (c.name or "").lower()
            )
        ]
        if len(narrowed) == 1:
            chosen.append(narrowed[0])
            continue
        # Can't narrow to exactly one. Decide union vs qualified-miss.
        leaf = (name or "").lower()
        targeted = any(q.rsplit(".", 1)[-1] == leaf and q != leaf for q in qualifiers)
        if narrowed:
            # Qualifier matched >1 def: union of the narrowed set (still all
            # genuine candidates for the qualified name).
            homonyms["union"][name] = [await _verified_dict(c) for c in narrowed]
        elif targeted:
            # Qualifier present but matched nothing: do not guess.
            homonyms["qualified_miss"].append(name)
        else:
            # Bare homonym, no qualifier: union of every def.
            homonyms["union"][name] = [await _verified_dict(c) for c in cands]

    if not chosen:
        return hits, homonyms

    by_path = {h.get("target_path"): h for h in hits}
    top_score = max((h.get("score", 0.0) for h in hits), default=0.0)
    # Above the current top so an exact symbol match dominates the dominance
    # gate (an exact name+parent hit is stronger evidence than a prose match).
    anchor_score = max(top_score + 2.0, _HIGH_CONFIDENCE_SCORE_FLOOR + 1.0)
    for sym in chosen:
        fp = sym.file_path
        if not fp:
            # The same guard the concept-anchoring twin applies to its winner.
            # An anchor scores above every real hit by construction, so a
            # pathless one takes rank 1 and serves a row carrying nothing but a
            # score — no path, title, summary or excerpt — while displacing a
            # real hit from the synthesis window.
            continue
        target = by_path.get(fp)
        if target is None:
            target = {
                "page_id": f"file_page:{fp}",
                "target_path": fp,
                "title": fp,
                "summary": "",
                "snippet": "",
                "page_type": "file_page",
                "score": anchor_score,
                "_symbol_anchored": True,
            }
            hits.insert(0, target)
            by_path[fp] = target
        else:
            target["score"] = max(target.get("score", 0.0), anchor_score)
            target["_symbol_anchored"] = True
        # Stash the exact symbol the question named so symbol_bodies serves it
        # directly — the fuzzy hydration cap drops a far-down method when the
        # parent class name floods every sibling's qualified-name match. Serve
        # verified bounds only: an unrelocatable (approximate) symbol still
        # boosts its file's rank, but is not stashed for a live-body slice.
        vd = await _verified_dict(sym)
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
    hits.sort(key=lambda h: h.get("score", 0.0), reverse=True)
    return hits, homonyms


def attach_truncation_contract(
    entry: dict, *, indexed_end: int, end_served: int, repo_root: Path | None
) -> None:
    """Mark an inlined body that was cut, and name what the cut withheld.

    Every place that inlines a symbol body owes the consumer the same three
    keys when the indexed body outruns what was served: ``truncated``, a
    ``continuation`` naming the exact range holding the remainder, and the
    ``withheld_symbols`` that range covers.

    Say WHAT was withheld, not just that something was. A bare truncated flag
    plus a get_symbol pointer was followed zero times across the agent runs
    measured, so the consumer needs the names in hand to decide whether it is
    missing anything it cares about, and to continue inside this tool rather
    than falling back to Read.

    Both callers need it for the same reason and one of them needs it more: the
    homonym union payload returns BEFORE synthesis, so it is served in no-LLM
    mode and never reaches any of the confidence gates. Held in one function
    because two copies of this contract drifting apart is a live risk: the
    truncation keys are read by the confidence cascade, and the two sites have
    co-changed ten times.

    ``indexed_end`` is the end line the index recorded, ``end_served`` the last
    line actually inlined. A falsy ``indexed_end`` means the index recorded no
    end at all, which is never a cut: it is tested explicitly rather than left
    to ``indexed_end > end_served``, which would only agree with it while
    ``end_served`` stays non-negative.

    ``indexed_end`` is trusted to lie within the live file, which is
    ``check_symbol_bounds``'s job rather than this one's: it now clamps to
    ``len(lines)`` on every return, so a stored end that overshoots cannot reach
    here and flag a body served WHOLE as truncated (D8). Clamping again here
    would be a second owner and a second disk read.
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
        # Bounds that failed live verification (symbol moved and could not be
        # re-located): don't inline a slice at unreliable lines under a
        # confidence=high envelope. Hand the agent a get_symbol pointer, which
        # verifies on its own path.
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

    The symbol anchor above rescues questions that NAME an indexed symbol. This
    rescues the other retrieval-miss class: a why/value question that pins a
    literal number to a *described behaviour* (a cap / limit / batch size) but
    names no symbol. Fuzzy retrieval lands on a same-vocabulary file and never
    surfaces the one whose comment justifies the number, so it never enters the
    candidate set and the agent re-reads. We grep tracked source for comment
    lines carrying the number + a content noun, score the candidates with the
    existing rationale miner, and inject the winner so retrieval includes it and
    its comment reaches ``code_rationale``.

    Fires only when the question pins a literal number (the high-precision case;
    the prototype showed naive number-free grep is too noisy) and the winning
    file is not already the top retrieval hit (i.e. retrieval genuinely missed
    it). When the winner is already top, the existing confidence machinery decides
    the label - we deliberately do NOT force it past the dominance gate, which
    generalized only to the questions it was tuned on. The mined rationale + its
    line are stashed on the hit so the downstream ``code_rationale`` surfacing
    serves the exact comment without a second grep.

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
    # Precision gate: only number-bearing questions. A bare "why is X limited"
    # would grep the whole cap-family vocabulary and over-fire.
    if not _salient_numbers(question):
        return hits

    # The grep spawns a subprocess and mine reads files off disk - both blocking.
    # Run them in a worker thread so they never stall the server's event loop
    # (this can run inside a stdio MCP server driving the JSON-RPC transport).
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
    # Retrieval-miss gate: only anchor when retrieval did NOT already lead with
    # the winner. If it is already the top hit, leave the confidence label to the
    # existing dominance/confidence machinery - forcing it past the gate only ever
    # helped the questions it was tuned against. The mined comment still reaches
    # the agent via the gated path's code_rationale.
    if hits and hits[0].get("target_path") == winner_path:
        return hits

    near_line = (winner.get("lines") or [0])[0]
    by_path = {h.get("target_path"): h for h in hits}
    top_score = max((h.get("score", 0.0) for h in hits), default=0.0)
    # Above the current top so the comment-justified file dominates the
    # dominance gate and synthesis runs instead of gating low.
    anchor_score = max(top_score + 1.5, _HIGH_CONFIDENCE_SCORE_FLOOR + 0.5)
    target = by_path.get(winner_path)
    if target is None:
        target = {
            "page_id": f"file_page:{winner_path}",
            "target_path": winner_path,
            "title": winner_path,
            "summary": "",
            "snippet": "",
            "page_type": "file_page",
            "score": anchor_score,
        }
        hits.insert(0, target)
        by_path[winner_path] = target
    else:
        target["score"] = max(target.get("score", 0.0), anchor_score)
    target["_concept_anchored"] = True
    target["_concept_near_line"] = near_line
    # Stash the mined comment so the code_rationale surfacing can serve it
    # verbatim on any exit path - including the high path, where the comment IS
    # the answer the agent would otherwise re-read for.
    target["_concept_rationale"] = winner
    hits.sort(key=lambda h: h.get("score", 0.0), reverse=True)
    return hits
