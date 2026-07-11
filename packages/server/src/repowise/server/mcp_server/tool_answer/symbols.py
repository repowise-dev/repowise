"""Question identifier extraction + WikiSymbol hydration for retrieval hits.

The pieces that turn a ranked file into LLM-ready symbol context: pull the
identifiers a question names, read real signatures/source from disk, and
promote question-matched symbols to the top of each hit's symbol list.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.models import WikiSymbol
from repowise.server.mcp_server.tool_answer.config import (
    _ENRICH_TOP_N_HITS,
    _HIGH_CONFIDENCE_SCORE_FLOOR,
    _HOMONYM_UNION_BODY_MAX_LINES,
    _HOMONYM_UNION_CHAR_BUDGET,
    _MATCHED_SYMBOL_SOURCE_LINES,
    _MAX_RICH_SIG_LINES,
    _MAX_SYMBOLS_PER_HIT,
    _MAX_SYMBOLS_TOP_HIT,
    _STOPWORDS,
)


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


def _read_symbol_source(
    repo_root: Path | None,
    file_path: str,
    start_line: int,
    end_line: int,
    max_lines: int = _MATCHED_SYMBOL_SOURCE_LINES,
) -> str | None:
    """Return the literal source body for a symbol, bounded to max_lines.

    The bounded source is the key ingredient for question-matched symbols.
    The LLM was already getting the file-level summary and a truncated
    docstring; what it was missing was the actual code. With 40 lines of
    the method body in front of it, the synthesis step can answer "how
    does X work" without hedging back to "you should inspect the source".
    """
    if repo_root is None or start_line < 1:
        return None
    try:
        abs_path = (repo_root / file_path).resolve()
        try:
            abs_path.relative_to(repo_root.resolve())
        except ValueError:
            return None
        text = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = text.splitlines()
    if start_line > len(lines):
        return None
    hi = end_line if end_line and end_line >= start_line else start_line + max_lines
    hi = min(hi, start_line + max_lines, len(lines))
    body = "\n".join(lines[start_line - 1 : hi])
    return body


def _read_signature_from_source(
    repo_root: Path | None, file_path: str, start_line: int
) -> str | None:
    """Read the symbol's actual signature line from disk.

    Returns the def/class line (or its multi-line continuation) verbatim from
    the source file. Captures everything WikiSymbol.signature strips:
      * base classes for `class Foo(Bar, Baz):`
      * decorators (one line above the def)
      * full type annotations across line continuations

    None on any failure — caller falls back to the stored signature.
    """
    if repo_root is None:
        return None
    try:
        abs_path = (repo_root / file_path).resolve()
        # Defense in depth: never read outside the repo root.
        try:
            abs_path.relative_to(repo_root.resolve())
        except ValueError:
            return None
        text = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = text.splitlines()
    if not lines or start_line < 1 or start_line > len(lines):
        return None
    # Walk forward up to _MAX_RICH_SIG_LINES until we close the parenthesis
    # group (Python signatures often span multiple lines for type hints).
    sig_lines: list[str] = []
    paren_depth = 0
    for i in range(start_line - 1, min(start_line - 1 + _MAX_RICH_SIG_LINES, len(lines))):
        line = lines[i]
        sig_lines.append(line.strip())
        paren_depth += line.count("(") - line.count(")")
        if line.rstrip().endswith(":") and paren_depth <= 0:
            break
    if not sig_lines:
        return None
    return " ".join(sig_lines)


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
        by_name.setdefault(row.name, []).append(row)

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
            homonyms["union"][name] = [_symbol_def_dict(c) for c in narrowed]
        elif targeted:
            # Qualifier present but matched nothing: do not guess.
            homonyms["qualified_miss"].append(name)
        else:
            # Bare homonym, no qualifier: union of every def.
            homonyms["union"][name] = [_symbol_def_dict(c) for c in cands]

    if not chosen:
        return hits, homonyms

    by_path = {h.get("target_path"): h for h in hits}
    top_score = max((h.get("score", 0.0) for h in hits), default=0.0)
    # Above the current top so an exact symbol match dominates the dominance
    # gate (an exact name+parent hit is stronger evidence than a prose match).
    anchor_score = max(top_score + 2.0, _HIGH_CONFIDENCE_SCORE_FLOOR + 1.0)
    for sym in chosen:
        fp = sym.file_path
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
        # parent class name floods every sibling's qualified-name match.
        target.setdefault("_anchor_symbols", []).append(
            {
                "name": sym.name,
                "kind": sym.kind,
                "start_line": sym.start_line,
                "end_line": sym.end_line,
            }
        )
    hits.sort(key=lambda h: h.get("score", 0.0), reverse=True)
    return hits, homonyms


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
      must still answer), matching the CodeGraph "first match always renders"
      contract.
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
        body = _read_symbol_source(
            repo_root, path, start, end, max_lines=_HOMONYM_UNION_BODY_MAX_LINES
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
            if end and end > end_served:
                entry["truncated"] = True
                entry["continuation"] = f"{path}:{end_served + 1}-{end}"
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


async def _hydrate_symbols_for_hits(
    session,
    repo_id: str,
    hits: list[dict],
    ctx: Any = None,
    question_ids: set[str] | None = None,
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
    """
    question_ids = question_ids or set()
    # Case-folded copy for matching.
    qids_lower = {q.lower() for q in question_ids}

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
    for row in res.scalars().all():
        # Constants/variables: the stored signature IS the verbatim assignment
        # line. The disk re-read below walks forward looking for a ":"-closed
        # def line and would join unrelated following lines for assignments.
        if row.kind in ("constant", "variable"):
            rich_sig = None
        else:
            rich_sig = _read_signature_from_source(repo_root, row.file_path, row.start_line)
        # Does the symbol name match any identifier from the question?
        name_lower = (row.name or "").lower()
        qname_lower = (row.qualified_name or "").lower()
        matched = bool(
            qids_lower
            and (
                name_lower in qids_lower
                or qname_lower in qids_lower
                or any(
                    q in name_lower or q in qname_lower
                    for q in qids_lower
                    if len(q) >= 5  # avoid spurious substring matches on short tokens
                )
            )
        )
        entry: dict[str, Any] = {
            "name": row.name,
            "kind": row.kind,
            "signature": rich_sig or row.signature,
            "docstring": row.docstring or "",
            "start_line": row.start_line,
            "end_line": row.end_line,
            "_matched": matched,
        }
        if matched:
            src = _read_symbol_source(repo_root, row.file_path, row.start_line, row.end_line)
            if src:
                entry["source_excerpt"] = src
        by_file.setdefault(row.file_path, []).append(entry)

    # Sort: matched symbols first (document order within the match group),
    # then unmatched in start_line order. Cap per file — top hit gets more
    # slots than secondary hits.
    for i, h in enumerate(hits):
        path = h.get("target_path")
        if path not in by_file:
            continue
        syms = by_file[path]
        syms.sort(key=lambda s: (not s["_matched"], s["start_line"]))
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
        # Sort final slice by start_line for natural reading order.
        kept.sort(key=lambda s: s["start_line"])
        h["symbols"] = kept
