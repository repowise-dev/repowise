"""Question identifier extraction + WikiSymbol hydration for retrieval hits.

The pieces that turn a ranked file into LLM-ready symbol context: pull the
identifiers a question names, read real signatures/source from disk, and
promote question-matched symbols to the top of each hit's symbol list.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, NamedTuple

from sqlalchemy import select

from repowise.core.persistence.models import WikiSymbol
from repowise.server.mcp_server._page_paths import hit_file_path
from repowise.server.mcp_server._query_terms import content_terms, split_humps
from repowise.server.mcp_server._verify import verify_and_heal
from repowise.server.mcp_server.tool_answer.config import (
    _DEFINES_MAX_FILES,
    _DEFINES_PER_CANDIDATE,
    _ENRICH_TOP_N_HITS,
    _HIGH_CONFIDENCE_SCORE_FLOOR,
    _HOMONYM_UNION_BODY_MAX_LINES,
    _HOMONYM_UNION_CHAR_BUDGET,
    _HOMONYM_UNION_PROSE_DEF_CEILING,
    _MATCHED_SYMBOL_SOURCE_LINES,
    _MAX_RICH_SIG_LINES,
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
from repowise.server.mcp_server.tool_search import _prose_dominates

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


def _read_repo_text(repo_root: Path | None, file_path: str) -> str | None:
    """Read a repo file's live text, refusing paths outside the root.

    The single disk read shared by the bounds gate and the signature/body
    slices below, so a hydrated file is read once rather than once per helper.
    """
    if repo_root is None:
        return None
    try:
        abs_path = (repo_root / file_path).resolve()
        abs_path.relative_to(repo_root.resolve())
        return abs_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None


def _read_symbol_source(
    repo_root: Path | None,
    file_path: str,
    start_line: int,
    end_line: int,
    max_lines: int = _MATCHED_SYMBOL_SOURCE_LINES,
    *,
    text: str | None = None,
) -> str | None:
    """Return the literal source body for a symbol, bounded to max_lines.

    The bounded source is the key ingredient for question-matched symbols.
    The LLM was already getting the file-level summary and a truncated
    docstring; what it was missing was the actual code. With 40 lines of
    the method body in front of it, the synthesis step can answer "how
    does X work" without hedging back to "you should inspect the source".

    ``text`` lets a caller that already read the file (the hydrator reads it
    once for the bounds gate) pass the live source in, so a hydrated file is
    read once instead of once per symbol.
    """
    if start_line < 1:
        return None
    if text is None:
        text = _read_repo_text(repo_root, file_path)
    if text is None:
        return None
    lines = text.splitlines()
    if start_line > len(lines):
        return None
    hi = end_line if end_line and end_line >= start_line else start_line + max_lines
    hi = min(hi, start_line + max_lines, len(lines))
    body = "\n".join(lines[start_line - 1 : hi])
    return body


# Where a signature stops when it does not end in a Python colon: after the
# closing paren of the parameter list (with an optional return annotation), at a
# trailing brace, or at a semicolon (an abstract/interface member).
_SIG_TERMINATOR_RE = re.compile(r"\)\s*(?:->[^:{]*)?\s*[:{]|\{\s*$|;\s*$")


def _read_signature_from_source(
    repo_root: Path | None, file_path: str, start_line: int, *, text: str | None = None
) -> str | None:
    """Read the symbol's actual signature line from disk.

    Returns the def/class line (or its multi-line continuation) verbatim from
    the source file. Captures everything WikiSymbol.signature strips:
      * base classes for `class Foo(Bar, Baz):`
      * decorators (one line above the def)
      * full type annotations across line continuations

    ``text`` reuses the caller's already-read source (see _read_symbol_source).
    None on any failure — caller falls back to the stored signature.
    """
    if text is None:
        text = _read_repo_text(repo_root, file_path)
    if text is None:
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
        stripped = line.rstrip()
        # "ends with a colon" alone leaves a one-line body (``def go(self): pass``)
        # and every brace language (``func f() error {``, ``render() {``) with no
        # terminator at all, so the signature absorbs the lines after it.
        if paren_depth <= 0 and (
            stripped.endswith(":") or _SIG_TERMINATOR_RE.search(stripped)
        ):
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


# ---------------------------------------------------------------------------
# What a truncated body withheld
# ---------------------------------------------------------------------------

# Leading keywords that decorate a declaration without being one. Shared by the
# keyword and brace-method shapes below.
_DECL_MODIFIERS = (
    r"pub|public|private|protected|internal|open|final|static|abstract|override|"
    r"virtual|sealed|export|default|async|suspend|inline|readonly|declare|"
    r"unsafe|extern|partial|data|operator|const"
)

# Definition-line shapes, tried in order; each yields ``indent`` / ``kind`` /
# ``name``. A Python-only regex was the first cut and it made this whole feature
# inert on TS/Go/Java — which, at a 120-line body cap, is exactly where
# truncation bites hardest, since a TS class or a React component is the shape
# that overruns the cap in the first place.
_WITHHELD_DEF_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Python: def / async def / class.
    re.compile(
        r"^(?P<indent>[ \t]*)(?:async[ \t]+)?(?P<kind>def|class)[ \t]+"
        r"(?P<name>[A-Za-z_]\w*)"
    ),
    # Go methods, whose name follows the receiver: ``func (s *Store) Write(``.
    re.compile(
        r"^(?P<indent>[ \t]*)(?P<kind>func)[ \t]+\([^)]*\)[ \t]*"
        r"(?P<name>[A-Za-z_]\w*)"
    ),
    # Declaration keyword + name: Go func/type, Rust fn/struct/trait/impl/enum,
    # Java/C#/Kotlin class/interface/record, TS class/interface/type/enum.
    re.compile(
        rf"^(?P<indent>[ \t]*)(?:(?:{_DECL_MODIFIERS})[ \t]+)*"
        r"(?P<kind>func|fn|class|struct|interface|enum|trait|impl|record|type)"
        r"[ \t]+(?P<name>[A-Za-z_$][\w$]*)"
    ),
    # JS/TS function declarations, including ``export default`` and generators.
    # The separator after ``function`` is REQUIRED (or a generator star). With
    # ``[ \t]*`` the keyword matched as a mere prefix of a longer identifier, so
    # ``function_name: Mapped[str] = ...`` reported a symbol called ``_name``:
    # measured at 507 fabricated entries across this repo, the single largest
    # source of ids that resolve to nothing.
    re.compile(
        r"^(?P<indent>[ \t]*)(?:export[ \t]+)?(?:default[ \t]+)?(?:async[ \t]+)?"
        r"(?P<kind>function)(?:[ \t]*\*[ \t]*|[ \t]+)(?P<name>[A-Za-z_$][\w$]*)"
    ),
    # JS/TS arrow bindings: ``export const Panel = (props) => {``. The arrow has
    # to belong to the binding itself, with only an optional return annotation
    # between: allowing slack before it turns every local initialised from a
    # callback-taking call (``const pages = all.filter((p) => ...)``) into a
    # "definition" with a symbol_id that resolves to nothing.
    re.compile(
        r"^(?P<indent>[ \t]*)(?:export[ \t]+)?(?P<kind>const|let|var)[ \t]+"
        r"(?P<name>[A-Za-z_$][\w$]*)[^=]*=[ \t]*(?:async[ \t]+)?"
        r"(?:\([^)]*\)(?:[ \t]*:[^=]*?)?|[A-Za-z_$][\w$]*)[ \t]*=>"
    ),
    # Brace-language members: ``  public void run(String a) {``, ``  render() {``.
    # Between the parameter list and the brace only a return type or a throws
    # clause may appear -- no parens, or an assertion call in a test file
    # (``expect(x).toMatchObject({``) reads as a method declaration. The brace
    # itself is optional so Allman style (``void Beta()`` with ``{`` on the next
    # line, the C# default) is reachable; the caller supplies the next line.
    re.compile(
        rf"^(?P<indent>[ \t]*)(?:(?:{_DECL_MODIFIERS})[ \t]+)*"
        r"(?:[A-Za-z_$][\w$<>,.\[\]]*[ \t]+)?(?P<name>[A-Za-z_$][\w$]*)[ \t]*"
        r"\([^;{]*\)(?P<tail>[^;{()]*)(?P<brace>\{)?[ \t]*$"
    ),
)
_BRACE_MEMBER = len(_WITHHELD_DEF_PATTERNS) - 1

# The brace-member shape above also matches control flow (``if (x) {``), a
# statement whose keyword the optional type group swallows (``raise
# ValueError(f"... {x}")`` reports ``ValueError``), and any call taking a
# callback (``describe("x", () => {``, ``it("x", function () {``). Both the
# matched NAME and the line's own first word are checked, because the type group
# hides the keyword from a name-only guard.
_NOT_A_DEFINITION = frozenset(
    {
        "if", "for", "while", "switch", "catch", "else", "do", "try", "return",
        "with", "using", "lock", "foreach", "case", "synchronized", "await",
        "yield", "new", "typeof", "in", "of", "when", "unless", "match",
        "raise", "throw", "assert", "del", "delete", "print", "elif", "except",
        "finally", "import", "from", "global", "nonlocal", "pass", "break",
        "continue", "go", "defer", "select", "range", "constructor",
    }
)

_FIRST_WORD_RE = re.compile(r"[A-Za-z_$][\w$]*")

# Words no language lets you NAME a definition, so a match producing one is a
# parse accident whatever shape it came from: ``fn is not None`` reads as Rust's
# ``fn <name>`` and reported a symbol called ``is``. Kept strictly to reserved
# words -- ``match``, ``range`` and ``print`` are all real function names.
_RESERVED_NAMES = frozenset(
    {
        "is", "not", "and", "or", "in", "if", "else", "elif", "for", "while",
        "return", "none", "true", "false", "null", "undefined", "class", "def",
        "import", "from", "as", "with", "pass", "lambda", "del", "global",
        "raise", "try", "except", "finally", "yield", "await", "assert",
        "break", "continue", "nonlocal", "var", "let", "const", "function",
    }
)


def _match_definition(raw: str, next_raw: str = "") -> re.Match[str] | None:
    """First definition shape *raw* matches, or None.

    ``next_raw`` is the following source line, consulted only for Allman-style
    braces where the declaration and its ``{`` sit on separate lines.
    """
    for i, pattern in enumerate(_WITHHELD_DEF_PATTERNS):
        m = pattern.match(raw)
        if not m:
            continue
        if m.group("name").lower() in _RESERVED_NAMES:
            continue
        if i == _BRACE_MEMBER:
            head = _FIRST_WORD_RE.match(raw.strip())
            if m.group("name").lower() in _NOT_A_DEFINITION:
                continue
            if head and head.group(0).lower() in _NOT_A_DEFINITION:
                continue
            # An anonymous function passed as an argument, in either syntax.
            if "=>" in raw[: m.end()] or "function" in raw[: m.end()]:
                continue
            # Go's third spelling of the same thing: ``func(req *http.Request)
            # (*http.Response, error) {``. There is no space after ``func``, so
            # the optional return-type group matches empty and the name group
            # takes the keyword itself, yielding an unresolvable ``path::func``.
            # This cannot be handled by either general-purpose set above:
            # ``_RESERVED_NAMES`` is tested for every pattern and ``def func():``
            # is a real Python definition (41 of them in django alone), while
            # ``_NOT_A_DEFINITION`` is also tested against the line's FIRST
            # word, which is ``func`` on every named Go function too.
            #
            # Requiring ``func`` to open the line is what keeps it to the Go
            # literal: ``int func(int a) {`` is a real definition named ``func``
            # in C, C++, Java, C# and Kotlin, and a name-only test suppresses
            # all five.
            if m.group("name") == "func" and head and head.group(0) == "func":
                continue
            # Allman: the brace is on the next line. A declaration never ends in
            # a comma, but an argument on its own line inside a multi-line call
            # does -- and when the following argument is a dict literal, the
            # next line really is ``{`` (``bool(matched_nums),`` then ``{``).
            if not m.group("brace") and (
                next_raw.strip() != "{" or raw.rstrip().endswith(",")
            ):
                continue
        return m
    return None


# Anything that could open a string or a comment. A line with none of these
# cannot change the scanner's state, so it skips the character walk.
_QUOTEISH_RE = re.compile(r"""["'`#]|/[*/]""")


def _skip_quoted(raw: str, i: int) -> int:
    """Index just past the single- or double-quoted run starting at ``i``."""
    quote, i = raw[i], i + 1
    while i < len(raw):
        if raw[i] == "\\":
            i += 2
            continue
        if raw[i] == quote:
            return i + 1
        i += 1
    return i


# Extensions where a backtick actually opens a string: Go raw strings and the
# JS/TS template-literal family. Everywhere else a backtick is punctuation --
# Rust doc comments, Ruby heredocs and Python docstrings all carry markdown
# fences and shell quotes -- so masking on it can only ever be a misfire.
#
# Not a style choice, a measured one. Before this gate, a markdown fence inside
# an ordinary Rust string opened a phantom frame that a stray backtick 260 lines
# later re-closed, and `goose/crates/goose-cli/src/session/export.rs` lost FIVE
# real `pub fn` definitions from `withheld_symbols` with the containment below
# provably inert. Three more files in the corpus did the same (two Ruby
# heredocs, one Rust raw string).
_BACKTICK_STRING_SUFFIXES = frozenset(
    {".go", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}
)

# Characters after which a ``/`` starts a REGEX rather than a division. Notably
# excludes ``)``, ``]`` and anything alphanumeric, which are the positions where
# a division's left operand ends -- so Go's and Python's ``a / b`` is never
# mistaken for a regex.
_REGEX_CAN_START_AFTER = frozenset("(,=:[!&|?{};+-*%~^<>\n")

# ...and the keywords a regex can follow, which end in an alphanumeric and so
# would otherwise read as a division's left operand. `return /[`]/.test(x)` is
# the one that matters here: it opens a phantom frame exactly like the
# character-class case `_skip_regex` exists for.
_REGEX_CAN_START_AFTER_WORD = frozenset(
    {
        "return", "case", "typeof", "yield", "await", "throw", "in", "of",
        "new", "delete", "instanceof", "do", "else", "void",
    }
)


def _regex_position(raw: str, i: int, prev: str) -> bool:
    """Whether the ``/`` at ``i`` starts a regex rather than a division."""
    if prev in _REGEX_CAN_START_AFTER:
        return True
    if not (prev.isalnum() or prev == "_"):
        return False
    word = ""
    j = i - 1
    while j >= 0 and raw[j].isspace():
        j -= 1
    while j >= 0 and (raw[j].isalnum() or raw[j] == "_"):
        word = raw[j] + word
        j -= 1
    return word in _REGEX_CAN_START_AFTER_WORD


def _skip_regex(raw: str, i: int) -> int:
    """Index past a regex literal starting at ``i``, or ``i`` if it is not one.

    Only exists because a backtick inside a regex otherwise opens a phantom
    template literal. When a later backtick closes that phantom again the walk
    ends with an EMPTY stack, so the containment in ``_string_masked_lines``
    never fires and the mask silently eats everything between.

    Isolated on one real file: ``mui/packages/markdown/parseMarkdown.js:203``,
    ``return matches[1].replace(/`/g, '');``. Without this branch that file
    masks 61 lines instead of 52, stays balanced, and hides the real
    ``function getDescription`` at line 206.

    Regex literals cannot span lines, so a run with no closing ``/`` on the same
    line is not one and is left alone.
    """
    j = i + 1
    in_class = False
    while j < len(raw):
        c = raw[j]
        if c == "\\":
            j += 2
            continue
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        elif c == "/" and not in_class:
            return j + 1
        j += 1
    return i


def _walk_string_state(
    lines: tuple[str, ...], *, backticks: bool
) -> tuple[set[int], set[int], bool]:
    """(lines starting inside a string, inside a block comment, literal left open).

    The two sets are kept apart because callers need to tell them apart and this
    is a hot path: a string body proves the enclosing expression is still open,
    a comment between two declarations proves nothing.

    ``stack`` models template-literal nesting: a ``None`` frame is the string
    part of a backtick literal, an ``int`` frame is the unclosed-brace depth
    inside a ``${...}`` interpolation. An interpolation holds CODE, so its lines
    are not masked and a backtick inside one opens its own nested literal rather
    than closing the outer one.

    That nesting is not optional sophistication, and the reason is not the one
    it looks like. Nesting alone does NOT unbalance a flat open/close counter --
    four synthetic nested fixtures all re-balance. What breaks is the
    combination: a flat counter reads the nested literal's OPENING backtick as
    closing the outer one, which puts the walk at code level inside what is
    really string content, and a code-level rule then eats the rest of the line
    along with the real closing backtick. On mui's
    ``DisabledDefaultClasses.tsx`` that rule is ``#`` firing on the CSS colour
    ``#fff``, and the outer literal then never closes.

    The same shape has two other triggers, and those two are worse because they
    leave the stack BALANCED, which the containment in ``_string_masked_lines``
    cannot see: an escaped backtick (handled here) and a backtick inside a regex
    (handled by ``_skip_regex``). The nesting case usually ends UNBALANCED and
    so is caught by the fallback anyway -- interpolation tracking is here for
    precision, measured as 1,926 fabrications against 60 on the 16 corpus files
    where a flat walk and this one disagree.
    """
    strings: set[int] = set()
    comments: set[int] = set()
    delim: str | None = None
    in_block = False
    # Line the currently-open ``delim`` run or ``/* */`` block started on. The
    # two are mutually exclusive (a frame is only ever opened at code level), so
    # one variable serves both.
    open_line = 0
    stack: list[int | None] = []
    for n, raw in enumerate(lines, 1):
        # The three states are mutually exclusive: a frame is only ever pushed
        # at code level, so an if/elif chain is faithful to the walk below.
        if delim is not None or (stack and stack[-1] is None):
            strings.add(n)
        elif in_block:
            comments.add(n)
        elif not stack and not _QUOTEISH_RE.search(raw):
            # Nothing on this line can open a string or comment, so the
            # character walk below cannot change state. Most lines are this
            # line, and skipping them is what keeps a whole-file scan cheap.
            continue
        i = 0
        # Last non-space character seen at CODE level, for the regex test below.
        prev = "\n"
        while i < len(raw):
            if stack:
                if stack[-1] is None:
                    if raw[i] == "\\":
                        # An escaped backtick does not close the literal. Without
                        # this, `` `x\`y` `` closes early and re-opens on the real
                        # terminator, inverting the parity for the rest of the
                        # file with a balanced stack the containment cannot see.
                        i += 2
                    elif raw.startswith("${", i):
                        stack.append(1)
                        i += 2
                    elif raw[i] == "`":
                        stack.pop()
                        i += 1
                    else:
                        i += 1
                    continue
                # Inside ${...}, so the ordinary code tokens apply again --
                # including the regex probe, without which a backtick in a
                # character class here (``${s.replace(/[`]/g, "")}``) opens the
                # same phantom frame _skip_regex exists to prevent.
                if raw[i] == "{":
                    stack[-1] += 1
                elif raw[i] == "}":
                    stack[-1] -= 1
                    if stack[-1] <= 0:
                        stack.pop()
                elif raw[i] == "`":
                    stack.append(None)
                elif raw.startswith("//", i):
                    break
                elif raw[i] == "/" and _regex_position(raw, i, prev):
                    j = _skip_regex(raw, i)
                    if j > i:
                        i, prev = j, "/"
                        continue
                elif raw[i] in ('"', "'"):
                    i = _skip_quoted(raw, i)
                    prev = '"'
                    continue
                if not raw[i].isspace():
                    prev = raw[i]
                i += 1
                continue
            if delim is not None:
                if raw.startswith(delim, i):
                    delim, open_line, i = None, 0, i + len(delim)
                else:
                    i += 1
                continue
            if in_block:
                if raw.startswith("*/", i):
                    in_block, open_line, i = False, 0, i + 2
                else:
                    i += 1
                continue
            if raw.startswith('"""', i) or raw.startswith("'''", i):
                delim, open_line, i = raw[i : i + 3], n, i + 3
                continue
            if backticks and raw[i] == "`":
                stack.append(None)
                i += 1
                continue
            if raw.startswith("/*", i):
                in_block, open_line, i = True, n, i + 2
                continue
            if raw[i] == "#" or raw.startswith("//", i):
                break
            if raw[i] == "/" and _regex_position(raw, i, prev):
                j = _skip_regex(raw, i)
                if j > i:
                    i, prev = j, "/"
                    continue
            if raw[i] in ('"', "'"):
                i = _skip_quoted(raw, i)
                prev = '"'
                continue
            if not raw[i].isspace():
                prev = raw[i]
            i += 1
    # A run still open at EOF is a walk that lost track, not a file with an
    # unterminated construct, and masking to EOF hides every definition below
    # it. The template-literal stack has had this containment since the backtick
    # work; ``delim`` and ``/* */`` never did, and both fire on the same shape --
    # a delimiter belonging to another language, sitting inside a string this
    # walk cannot see. Measured on Rust: ``${0%/*}`` in a raw string masked 103
    # lines and cost 6 real ``fn``; ``description = """#`` masked 3,002 and cost
    # 10. Discarding the trailing run under-masks instead, which costs a
    # spurious name in a list rather than an absent real one.
    if delim is not None:
        strings = {n for n in strings if n < open_line}
    elif in_block:
        comments = {n for n in comments if n < open_line}
    return strings, comments, bool(stack)


def _has_backtick_strings(file_path: str) -> bool:
    """Whether a backtick opens a string in this file's language."""
    dot = file_path.rfind(".")
    return dot != -1 and file_path[dot:].lower() in _BACKTICK_STRING_SUFFIXES


class _Masked(NamedTuple):
    """1-based line numbers, split by what is hiding them.

    ``all`` is precomputed rather than unioned per call: every caller wants it,
    and this is a cached whole-file walk.
    """

    strings: frozenset[int]
    comments: frozenset[int]
    all: frozenset[int]


@lru_cache(maxsize=8)
def _string_masked_lines(lines: tuple[str, ...], backticks: bool = True) -> _Masked:
    """1-based line numbers that START inside a multi-line string or comment.

    Repowise's own docstrings are full of indented ``def``/``class`` examples,
    and without this every one of them becomes a ``symbol_id`` the note tells the
    agent to fetch and that resolves to nothing.

    Deliberately a lexer-lite: it tracks Python triple quotes, backtick template
    literals / Go raw strings, and C-style ``/* */`` blocks, and stops at ``#`` /
    ``//``. Known ceilings, all measured rather than assumed:

    * C# verbatim strings (``@"..."``) and Rust raw strings (``r#"..."#``) are
      not tracked. C# because the exposure is 27 multi-line literals in 4,284
      files; Rust because its 35,484 interior lines yielded 0 fabrications --
      Go raw strings hold GraphQL, which matches the definition patterns, while
      Rust raw strings hold TOML, which does not.
    * Inside a ``${...}`` interpolation, ``/* */`` and ``#`` are not handled, so
      a ``}`` inside a block comment there can close the frame early. Every
      constructed case ended unbalanced and was caught by the fallback below.
    * Markdown fenced blocks and inline code spans are now masked too, which is
      wanted (a ``def`` inside a ```` ``` ```` fence is not a definition) but is
      a behaviour change worth knowing about.

    Cached on the line tuple: this is a per-character Python loop over the whole
    file (68 ms on a 424 KB one), and the homonym-union path calls it once per
    truncated body, re-masking the same file each time. The fallback below can
    double that on a file whose literals do not balance, because the fast-path
    line skip is disabled while a frame is open -- measured at 8x on a
    synthetic 1.2 MB file with one stray backtick on line 1. Bounded at two
    walks, and the cache means it is paid once per file.
    """
    strings, comments, template_left_open = _walk_string_state(
        lines, backticks=backticks
    )
    if template_left_open:
        # The lexer-lite lost track: a template literal opened and never closed,
        # so every line below it is masked to EOF and every definition there is
        # silently suppressed. That failure is invisible -- no error, just
        # missing symbols -- so it must not be the one we ship. Fall back to the
        # pre-backtick walk for this file, which under-masks instead: the cost is
        # a spurious name in a list, not an absent real one.
        strings, comments, _ = _walk_string_state(lines, backticks=False)
    return _Masked(
        frozenset(strings), frozenset(comments), frozenset(strings | comments)
    )


def _indent_width(raw: str) -> int:
    return len(raw) - len(raw.lstrip())


# Stand-in indent for "the cut is inside something still open", used when every
# withheld line is a string body or a bracket tail and none carries a real one.
_UNBOUNDED_INDENT = 1 << 30


# Cap on how many withheld definitions are surfaced. This block exists to let
# the agent CONTINUE inside the tool rather than fall back to Read, so it has to
# stay small enough that it never competes with the answer for the window: names
# and signatures only, never bodies.
_WITHHELD_MAX_SYMBOLS: int = 8


def withheld_definitions(
    repo_root: Path | None, continuation: str | None
) -> list[dict]:
    """Definitions that live in the range a truncated body did NOT serve.

    ``continuation`` is the ``path:first-last`` pointer already attached to a
    truncated ``symbol_bodies`` entry, so this reads exactly the lines the
    payload admits it withheld.

    Why this exists rather than just flagging the truncation: measured on the
    transcripts on disk, when a body is truncated the withheld range contains a
    symbol the answer goes on to talk about **78% of the time**, and the
    responses are at ``confidence: high`` in most of those. A flag alone does
    not help the consumer, and the ``get_symbol`` pointer the payload already
    carries was followed ZERO times across the runs measured. Names and
    signatures are cheap and keep the agent inside the tool.

    Returns ``[{name, kind, line, symbol_id, signature}]``, the boundary-cut
    symbol first, empty on any failure (a probe that cannot read must not
    manufacture doubt).
    """
    if not continuation:
        return []
    path, _, span = continuation.rpartition(":")
    first, _, last = span.partition("-")
    if not path or not first.isdigit() or not last.isdigit():
        return []
    lo, hi = int(first), int(last)
    text = _read_repo_text(repo_root, path)
    if text is None:
        return []
    lines = text.splitlines()
    if lo < 1 or lo > len(lines):
        return []
    mask = _string_masked_lines(tuple(lines), _has_backtick_strings(path))
    masked = mask.all

    def _entry(line_no: int, m: re.Match[str], *, cut: bool = False) -> dict:
        name = m.group("name")
        # The brace-member shape has no keyword to report, so it is named for
        # what it is rather than mislabelled as a Python `def`.
        kind = m.groupdict().get("kind") or "member"
        sig = _read_signature_from_source(repo_root, path, line_no, text=text)
        e = {
            "name": name,
            "kind": kind,
            "line": line_no,
            "symbol_id": f"{path}::{name}",
            "signature": (sig or f"{kind} {name}").strip(),
        }
        if cut:
            e["body_continues"] = True
        return e

    out: list[dict] = []

    # The symbol whose body is CUT BY the boundary, which is the case that
    # motivated this whole helper and the one a naive implementation misses.
    # In the reference defect the served range ended at 166 and `_validate`
    # starts at 164: its `def` line was served, so it does not appear anywhere
    # in the withheld range, while the line that actually causes the bug (176)
    # sits inside it. Reporting only defs that START after the cut would say
    # nothing about the symbol the answer is about.
    #
    # Taking the nearest preceding definition unconditionally is wrong, though:
    # a symbol that ENDED before the cut was served whole, and reporting it as
    # continuing puts a fully-served name at the head of the note and into the
    # get_symbol pointer. A definition at indent I reaches line ``lo`` only if
    # every non-blank line from it up to the first non-blank withheld line is
    # indented deeper than I, so walking backwards while tracking the running
    # minimum indent decides it exactly, in one pass and with no re-scan.
    #
    # The anchor obeys the same two exclusions as the walk. Taking the first
    # non-blank withheld line flatly is what put the walk one line short of
    # reality: when the cut lands ON a multi-line signature's own ``) -> dict:``
    # (or on a flush-left docstring line), the anchor reads as column 0, the
    # walk dies at once, and the payload ships truncated with NO withheld
    # symbols -- gate 8 inert on exactly the long entry points that truncate.
    _end = min(hi, len(lines))
    _usable = [
        n
        for n in range(lo, _end + 1)
        if lines[n - 1].strip()
        and n not in masked
        and lines[n - 1].strip()[0] not in ")]}{"
    ]
    if lo in mask.strings:
        # The cut is INSIDE a multi-line string, so the expression holding that
        # string -- and everything enclosing it -- is still open at ``lo``.
        # Without this the anchor reads from the first line BELOW the string,
        # usually a top-level declaration at column 0, and the walk dies at once
        # (D9: 8 real definitions lost across cli/cli and mui). A block COMMENT
        # cannot stand in for this: one sitting between two methods would report
        # the preceding method as continuing when it has already ended.
        anchor = _UNBOUNDED_INDENT
    elif _usable:
        anchor = _indent_width(lines[_usable[0] - 1])
    elif any(lines[n - 1].strip() for n in range(lo, _end + 1)):
        # Every withheld line is a string body or a bracket tail, so whatever
        # encloses the cut is certainly still open: let any preceding
        # definition qualify.
        anchor = _UNBOUNDED_INDENT
    else:
        anchor = None
    if anchor is not None:
        min_indent = anchor
        for back in range(lo - 1, 0, -1):
            if min_indent <= 0:
                break  # nothing can be shallower, so nothing can still be open
            raw = lines[back - 1]
            stripped = raw.strip()
            if not stripped:
                continue
            # A line opening with a closing bracket is the tail of a multi-line
            # construct, not a statement at its own indent. Folding it is what
            # made this miss the live reference case: ``get_answer``'s signature
            # spans lines and ends ``) -> dict:`` at column 0, so the running
            # minimum hit zero on the signature's own closing paren and the walk
            # gave up two lines short of the ``async def`` it was looking for.
            # An Allman brace (``{`` alone) is likewise part of the declaration
            # above it, not a statement.
            if stripped[0] in ")]}{":
                continue
            ind = _indent_width(raw)
            nxt = lines[back] if back < len(lines) else ""
            m = None if back in masked else _match_definition(raw, nxt)
            if m is not None and ind < min_indent:
                out.append(_entry(back, m, cut=True))
                break
            min_indent = min(min_indent, ind)

    seen = {d["name"] for d in out}
    for offset, raw in enumerate(lines[lo - 1 : _end]):
        line_no = lo + offset
        if line_no in masked:
            continue
        nxt = lines[line_no] if line_no < len(lines) else ""
        m = _match_definition(raw, nxt)
        if m is None or m.group("name") in seen:
            continue
        seen.add(m.group("name"))
        out.append(_entry(line_no, m))
        if len(out) >= _WITHHELD_MAX_SYMBOLS:
            break
    return out
