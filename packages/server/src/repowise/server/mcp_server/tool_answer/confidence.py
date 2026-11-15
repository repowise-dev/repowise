"""Confidence and retrieval_quality grading for get_answer.

``confidence`` is the one trust signal an agent reads: how much to trust the
answer, from how the retrieval went and whether its top hit covers what was
asked. ``retrieval_quality`` rates the retrieval alone; it feeds the degraded
grade and is served only under ``include=["diagnostics"]``, so two ratings of
one answer never disagree in front of the agent.

:func:`_grade_answer` runs the gate cascade — one starting grade from retrieval
dominance, then a run of gates that can only demote it — and the predicates each
gate reads live beside it.
"""

from __future__ import annotations

import functools
import re
from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import NamedTuple

from repowise.server.mcp_server._answer_context import (
    is_mechanism_question as _is_mechanism_question,
)
from repowise.server.mcp_server._answer_context import (
    is_why_question as _is_why_question,
)
from repowise.server.mcp_server._meta import _normalize_target_path
from repowise.server.mcp_server._query_terms import (
    QUESTION_FRAME_VERBS,
    paths_the_question_names,
)
from repowise.server.mcp_server._retrieval_rank import window_term_weight
from repowise.server.mcp_server.tool_answer.config import (
    _AGREEMENT_HIGH_SCORE_FLOOR,
    _AGREEMENT_RANK_GAP,
    _AGREEMENT_TOP_RANK_MAX,
    _ANSWER_COVERAGE_FLOOR,
    _CLAIM_SUPPORT_GATE_ENV,
    _DOMINANCE_ABS_GAP,
    _DOMINANCE_ABS_SCORE_FLOOR,
    _DOMINANCE_RATIO,
    _EARN_HIGH_GROUNDING_ENV,
    _EARN_HIGH_ON_WEAK_RETRIEVAL_ENV,
    _ENRICH_TOP_N_HITS,
    _GAP_MARKERS,
    _HEDGE_MARKERS,
    _HIGH_CONFIDENCE_SCORE_FLOOR,
    _INLINE_BODY_MAX_LINES,
    _LEAD_HYBRID_TOP_K,
    _SYMBOL_AGREEMENT_TOP_RANK_MAX,
    _flag_on,
    _opt_in,
)
from repowise.server.mcp_server.tool_answer.cut_off import answer_cut_off
from repowise.server.mcp_server.tool_answer.hydration import (
    _stem,
    _stem_hit,
    _text_stems,
    _text_words,
)
from repowise.server.mcp_server.tool_answer.symbols import is_symbol_lookup_question


def _answer_is_hedged(answer_text: str, question: str = "") -> bool:
    """True when the answer admits it cannot answer before it states anything.

    An admission (a hedge or gap phrase) that comes before any sentence naming
    code the question did not is a non-answer, low however dominant retrieval
    was. One that follows such a claim is a caveat on a grounded answer:
    :func:`_admitted_gap` holds that at "medium" instead.
    """
    for sentence in _sentences(answer_text):
        if _is_admission(sentence):
            return True
        if _names_code(sentence, question):
            return False
    return False


# Markdown emphasis and code spans: ``does **not include**`` must read as
# "does not include". Underscores only at a word edge, so snake_case stays whole.
_MARKDOWN_MARKUP_RE = re.compile(r"[*`]+|(?<!\w)_+|_+(?!\w)")


def _normalized(answer_text: str) -> str:
    """Lower-cased, without markdown markup, with curly apostrophes folded and
    spaces collapsed, so every phrase marker matches however the prose is styled.
    "The excerpts don't" reads as "the excerpts do not"; a contraction about the
    code ("the loop doesn't include a backoff") is left alone."""
    text = _MARKDOWN_MARKUP_RE.sub("", (answer_text or "").lower())
    text = text.replace("\u2019", "'").replace("\u02bc", "'")
    text = _EXCERPT_CONTRACTION_RE.sub(r"\1 \2 not", text)
    return re.sub(r"\s+", " ", text)


# "The (retrieved) excerpts don't ...": the contraction says what the evidence lacks.
_EXCERPT_CONTRACTION_RE = re.compile(
    r"\b((?:excerpts?|evidence|material|snippets?)(?: (?:shown|provided|retrieved|here|above))?)"
    r" (do|does)n't\b"
)


_SENTENCE_BREAK_RE = re.compile(r"(?<=[.!?])\s+|\n+")

# "X is not included" with nothing after it but the clause end. Followed by a
# preposition it describes the code ("not included in the checksum").
_BARE_GAP_RE = re.compile(
    r"\b(?:is|are)(?:n't| not) (?:included|shown)\b"
    r"(?!\s+(?:in|by|on|from|with|as|for|to|within|under|when|if|unless)\b)"
    # "... is not present." ends the sentence: nothing left for it to describe.
    r"|\b(?:is|are)(?:n't| not) present\s*\.?$"
)


# "truncated in the (retrieved) excerpts", ending the clause or followed by a
# preposition. "truncated in the excerpt builder" describes code instead.
_TRUNCATED_GAP_RE = re.compile(
    r"\btruncated in the (?:(?:retrieved|provided) )?(?:excerpts?|evidence|material|snippets?)"
    r"(?=\s*(?:$|[^\w\s]|(?:of|for|from|in|at|before|after|so|and|but|which|here)\b))"
)


def _sentences(answer_text: str) -> list[str]:
    return [s for s in (p.strip() for p in _SENTENCE_BREAK_RE.split(answer_text or "")) if s]


_LINE_NUMBER_RE = re.compile(
    r"\bline[- ]?numbers?\b|\bline[- ]numbered\b|\bline ranges?\b|\bcite (?:the )?(?:exact|specific) lines?\b"
)
_CLAUSE_BREAK_RE = re.compile(r"([,;:()]|\bso\b)")
# The words a "no line numbers" clause is made of; any other word names something missing.
_LINE_CLAUSE_FILLER = frozenset((
    "a", "an", "the", "it", "its", "their", "these", "those", "this", "them", "they", "i",
    "here", "there", "given", "any", "no", "not", "do", "does", "did", "is", "are", "was",
    "were", "isn't", "aren't", "can't", "cannot", "cite", "exact", "specific", "lines", "line",
    "include", "includes", "included", "show", "shows", "shown", "give", "gives", "carry",
    "carries", "have", "has", "provide", "provides", "provided", "list", "lists", "listed",
    "for", "in", "of", "with", "excerpt", "excerpts", "retrieved", "summary", "wiki",
    "declaration", "declarations", "body", "available", "unknown",
))
# A clause going on with what else is missing (", nor the validator").
_LIST_GOES_ON_RE = re.compile(r"\s*(?:nor|or|and|plus|as well as|along with|let alone)\b")


def _line_number_only(clause: str) -> bool:
    """Does *clause* say nothing but that the excerpts carry no line numbers?"""
    if not _LINE_NUMBER_RE.search(clause):
        return False
    words = re.findall(r"[a-z][a-z']*", _LINE_NUMBER_RE.sub(" ", clause))
    return all(w in _LINE_CLAUSE_FILLER for w in words)


def _is_admission(sentence: str) -> bool:
    """Does *sentence* say something asked is not in the evidence?

    A clause that only says the excerpts carry no line numbers is not one:
    the code is served, only its location is missing.
    """
    # Clauses at even indices, their delimiters kept between them, so every other
    # marker reads the sentence as written.
    parts = _CLAUSE_BREAK_RE.split(_normalized(sentence))
    low = "".join(
        ""
        if i % 2 == 0
        and _line_number_only(p)
        and not (i + 2 < len(parts) and _LIST_GOES_ON_RE.match(parts[i + 2]))
        else p
        for i, p in enumerate(parts)
    )
    return (
        _BARE_GAP_RE.search(low) is not None
        or _TRUNCATED_GAP_RE.search(low) is not None
        or any(marker in low for marker in _HEDGE_MARKERS)
        or any(marker in low for marker in _GAP_MARKERS)
    )


def _names_code(sentence: str, question: str) -> bool:
    """Does *sentence* name a code term (not a path) that *question* did not?"""
    q_lower = (question or "").lower()
    terms = _distinctive_terms(_PATH_TOKEN_RE.sub(" ", sentence))
    return any(t.lower() not in q_lower for t in terms)


def _admitted_gap(answer_text: str) -> str | None:
    """The sentence in which the answer says part of the question is outside its evidence."""
    return next((s for s in _sentences(answer_text) if _is_admission(s)), None)


# Question shapes that ask for a specific value: defaults, thresholds,
# limits, counts. These are the questions where a confidently-asserted
# number that retrieval never contained is a factual error, not a nuance.
_VALUE_QUESTION_RE = re.compile(
    r"\b(default|threshold|constant|limit|cap|max|min|value|timeout|"
    r"how many|how much|how large|how big|how long)\b",
    re.IGNORECASE,
)

# file.py:123 / file.py:123-145 — line refs the LLM adds for citations are
# not value assertions and must not feed the grounding check.
_FILE_LINE_REF_RE = re.compile(r"[\w./-]+:\d+(?:-\d+)?")

# "(line 27)", "`a.go`, lines 26-27", "at line 40": the prompt prints each body's
# line range, so answers cite it in prose. Only a citation context counts, so
# "max lines 500" or "set to line 30" still assert their number.
_PROSE_LINE_REF_RE = re.compile(
    r"\b(?:lines?|L)\s*\d+(?:\s*(?:-|–|to|and|through)\s*L?\d+)?\b", re.IGNORECASE
)
_LINE_REF_LEAD_RE = re.compile(
    r"(?:[(,]|\b(?:at|on|in|see|from|of|source)|\.\w{1,5}`?|^)\s*$", re.IGNORECASE
)


def _strip_line_refs(text: str) -> str:
    """*text* with citation line refs blanked, file:line and prose forms alike."""
    text = _FILE_LINE_REF_RE.sub(" ", text)

    def blank_citation(m: re.Match) -> str:
        lead = text[max(0, m.start() - 40) : m.start()]
        return " " if _LINE_REF_LEAD_RE.search(lead) else m[0]

    return _PROSE_LINE_REF_RE.sub(blank_citation, text)


# Standalone numbers (int or decimal). Lookarounds keep version-ish and
# identifier-embedded digits (v2, utf-8, sha256, 2.5.1) out while still
# matching sentence-final numbers ("the default is 3.").
_NUMBER_RE = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?!\w)(?!\.\d)")

# Digit-grouping separators: ``100_000`` and ``100,000`` equal ``100000``.
# Stripped on both sides, or a correct constant reads as ungrounded.
_THOUSANDS_SEP_RE = re.compile(r"(?<=\d)[,_](?=\d)")


def _numbers_in(text: str) -> set[str]:
    """Standalone numbers in *text*, with digit-grouping separators removed."""
    return set(_NUMBER_RE.findall(_THOUSANDS_SEP_RE.sub("", text or "")))


def _asserted_numbers(answer_text: str) -> set[str]:
    """Standalone numbers the answer asserts, citation line refs removed.

    Shared by :func:`_ungrounded_numbers` and the value-grounding gate so the
    two never disagree about what counts as an asserted value.
    """
    return _numbers_in(_strip_line_refs(answer_text or ""))


def _is_value_question(question: str) -> bool:
    """True when the question asks for a concrete value."""
    return bool(_VALUE_QUESTION_RE.search(question or ""))


def _retrieval_corpus(hits: list[dict], *, include_paths: bool = False) -> str:
    """All text the LLM was shown for *hits*, joined for a grounding check.

    Titles, summaries, snippets, and every hydrated symbol field (name,
    signature, docstring, source body). ``include_paths`` adds the file paths
    and anchored-symbol names — useful when grounding identifier-shaped terms
    (which often live in a path) but deliberately OFF for the number check,
    where a digit inside a path would falsely ground an asserted value.
    """
    parts: list[str] = []
    for h in hits or []:
        keys = ("title", "summary", "snippet", "excerpt")
        if include_paths:
            keys = (*keys, "target_path")
        for key in keys:
            v = h.get(key)
            if v:
                parts.append(str(v))
        for s in h.get("symbols") or []:
            for key in ("name", "signature", "docstring", "source_excerpt"):
                v = s.get(key)
                if v:
                    parts.append(str(v))
        # A concept-anchored hit's rationale comment was selected for containing
        # the question's number; without it the value/frame gates would flag the
        # correct number the answer echoes as invented.
        cr = h.get("_concept_rationale")
        if isinstance(cr, dict) and cr.get("comment"):
            parts.append(str(cr["comment"]))
        if include_paths:
            for a in h.get("_anchor_symbols") or []:
                v = a.get("name")
                if v:
                    parts.append(str(v))
    return "\n".join(parts)


def _ungrounded_numbers(answer_text: str, hits: list[dict]) -> list[str]:
    """Numbers the answer asserts that appear nowhere in the retrieval material.

    Guards against synthesis inventing a default ("the minimum count is 3")
    when no retrieved excerpt contained a 3.
    """
    asserted = _asserted_numbers(answer_text)
    if not asserted:
        return []

    grounded = _numbers_in(_retrieval_corpus(hits))
    return sorted(asserted - grounded)


# Identifier-shaped tokens an answer uses to NAME a mechanism (CamelCase,
# snake_case, dotted, digit-bearing). Plain lowercase English is excluded: only
# code-like terms signal that a wrong frame imported a foreign name.
_FRAME_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")


def _distinctive_terms(text: str) -> set[str]:
    """Identifier-shaped terms in *text*: internal-caps, snake_case, or digit.

    A leading capital alone is not enough: sentence-initial words and headers
    (``Because``, ``What``) would read as ungrounded frame terms. A single
    leading-cap class name is skipped too, since missing a frame term only
    weakens the gate while over-firing on prose breaks it.
    """
    terms: set[str] = set()
    for tok in _FRAME_TOKEN_RE.findall(text or ""):
        for c in (tok, *tok.split(".")):
            if len(c) < 4:
                continue
            has_internal_upper = any(ch.isupper() for ch in c[1:])
            if has_internal_upper or "_" in c or any(ch.isdigit() for ch in c):
                terms.add(c)
    return terms


def _frame_term_grounding(
    answer_text: str, question: str, hits: list[dict]
) -> tuple[list[str], int]:
    """Split the answer's mechanism-naming terms by whether retrieval grounds them.

    Returns ``(ungrounded, grounded_count)``. A wrong "why" frame betrays itself
    by importing a code-like term the cited material never contained, even when
    the surface facts are right. Terms the question named are excluded: echoing
    the user's framing is not a synthesised frame.
    """
    answer_terms = _distinctive_terms(answer_text)
    if not answer_terms:
        return [], 0
    q_lower = (question or "").lower()
    corpus = _retrieval_corpus(hits, include_paths=True).lower()
    ungrounded: list[str] = []
    grounded = 0
    for t in answer_terms:
        tl = t.lower()
        if tl in q_lower:
            continue
        if tl in corpus:
            grounded += 1
        else:
            ungrounded.append(t)
    return sorted(ungrounded), grounded


# Unattributed exclusivity tokens: they assert a global property a top-k slice
# cannot observe. "always" / "never" are excluded: temporal, not spatial,
# and legitimate when quoting a constraint.
_EXCLUSIVITY_TOKENS = (
    "entirely",
    "solely",
    "the only",
    "the sole",
    "only cause",
    "only place",
    "depends only on",
    "only reason",
)


def _has_unqualified_exclusivity_over_truncated(
    answer_text: str,
    symbol_bodies: list[dict],
) -> bool:
    """True when the prose makes an exclusivity claim over a truncated body.

    Exhaustiveness asserted from a sample the pipeline knows is incomplete.
    A no-op when every body was served whole.
    """
    if not any(b.get("truncated") for b in (symbol_bodies or [])):
        return False
    low = _normalized(answer_text)
    return any(tok in low for tok in _EXCLUSIVITY_TOKENS)



# A backticked span ending in a source-file extension is a PATH, so its dotted
# components are not symbol references: ``store.py`` says nothing about a symbol
# named ``py`` (or one named ``store``).
_SOURCE_EXTS = (
    r"(py|pyi|ts|tsx|js|jsx|mjs|cjs|go|java|rb|php|rs|c|h|cc|cpp|hpp|cs|kt|kts|"
    r"swift|scala|sql|sh|md|json|ya?ml|toml|txt|cfg|ini|lock)"
)
_PATHISH_SPAN_RE = re.compile(rf"\.{_SOURCE_EXTS}$", re.IGNORECASE)
# A file path inside prose: naming where to look is not a claim about the code.
_PATH_TOKEN_RE = re.compile(rf"[\w./-]*\.{_SOURCE_EXTS}\b", re.IGNORECASE)


def _code_reference(text: str, name: str) -> bool:
    """Does *text* refer to *name* as CODE rather than as an English word?

    A bare word match is unusable: withheld names like ``on`` or ``width`` are
    ordinary English. The call shape allows no space before the paren, since
    ``the width (in pixels)`` is a prose parenthetical.
    """
    n = re.escape(name)
    patterns = [
        rf"\b{n}\(",         # called
        rf"\b{n}\s*=(?!=)",  # assigned (never a comparison)
    ]
    # Attribute access, unless the name IS a file extension: ``store.py`` is a
    # path, and reading it as an attribute access implicates a symbol ``py``.
    if not _PATHISH_SPAN_RE.search(f".{name}"):
        patterns.append(rf"\.{n}\b")
    if any(re.search(p, text) for p in patterns):
        return True
    for span in re.findall(r"`([^`]+)`", text):
        head = span.strip().split("(")[0].strip()
        if head == name:
            return True
        if _PATHISH_SPAN_RE.search(head):
            continue
        if name in head.split("."):  # `Store._validate`
            return True
    return False


def _is_distinctive_name(name: str) -> bool:
    """Is *name* code-shaped enough that a bare word match cannot be prose?

    Internal capital, underscore or digit, the shape rule ``_distinctive_terms``
    uses. ``_validate`` qualifies; ``main`` and ``on`` do not.
    """
    return (
        any(ch.isupper() for ch in name[1:])
        or "_" in name
        or any(ch.isdigit() for ch in name)
    )


# Nothing but whitespace since the start of the string or the last sentence
# terminator: a capital here may be grammar rather than a symbol.
_STARTS_SENTENCE_RE = re.compile(r"(?:^|[.!?])\s*$")

# ...but only when a determiner follows, which is what makes it read as an
# imperative verb taking an object ("Run the indexer", "Update the schema").
# "Store keeps what?" is a sentence-initial capital too, and there it IS the
# symbol, so the exclusion must not swallow it.
_IMPERATIVE_OBJECT_RE = re.compile(
    r"^\s+(the|a|an|all|any|my|our|its|this|that|these|those|every|each)\b",
    re.IGNORECASE,
)


def _question_names(question: str, name: str) -> bool:
    """Does *question* name the symbol *name*, as code rather than as English?

    Three tiers, loosest first:

    * distinctive names match case-insensitively on a word boundary;
    * a leading-capital name (``Store``) matches only with its own case, and
      not as a sentence-initial imperative ("Run the indexer" is not ``Run``);
    * an all-lowercase name (``main``) needs a code context, or "what is the
      main entry point?" would be demoted.
    """
    if not question:
        return False
    if _is_distinctive_name(name):
        return re.search(rf"\b{re.escape(name)}\b", question, re.IGNORECASE) is not None
    if any(ch.isupper() for ch in name):
        return any(
            not (
                _STARTS_SENTENCE_RE.search(question[: m.start()])
                and _IMPERATIVE_OBJECT_RE.match(question[m.end() :])
            )
            for m in re.finditer(rf"\b{re.escape(name)}\b", question)
        )
    return _code_reference(question, name)


def implicated_withheld_symbols(
    question: str,
    answer_text: str,
    symbol_bodies: list[dict],
) -> list[str]:
    """Withheld symbols the response actually leans on, worst case first.

    Truncation alone is not a defect: often the withheld range holds nothing
    the response relies on. What matters is whether a symbol the response
    DEPENDS ON was withheld, found by two independent routes:

    * the **question** names it, which fires even when the prose never
      mentions the symbol;
    * the **answer** names it in a code context (only when synthesis ran).

    Neither no-LLM path calls this: the union path caps on truncation alone,
    and the degraded path has no synthesised claim to demote.

    ``body_continues`` entries sort first: the response has shown that
    symbol's signature and may reason about behaviour it never saw.
    """
    hits: list[tuple[int, str]] = []
    for body in symbol_bodies or []:
        if not isinstance(body, dict) or not body.get("truncated"):
            continue
        for sym in body.get("withheld_symbols") or []:
            name = sym.get("name")
            if not name:
                continue
            in_question = _question_names(question, name)
            in_answer = bool(answer_text) and _code_reference(answer_text, name)
            if in_question or in_answer:
                hits.append((0 if sym.get("body_continues") else 1, name))
    seen: set[str] = set()
    out: list[str] = []
    for _rank, name in sorted(hits, key=lambda t: t[0]):
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _confidence_score(hit: dict) -> float:
    """Return a score on get_answer's absolute coverage confidence scale."""
    score = hit.get("score", 0.0)
    factor = hit.get("_confidence_score_factor", 1.0)
    if isinstance(factor, (int, float)) and factor >= 0:
        return score * factor
    return score


def _top_two_score_ratio(hits: list[dict]) -> float:
    """The top hit's retrieval score over the runner-up's.

    Reporting only. :func:`dominance_reason` owns the dominance decision and does
    not call this; the grade carries the ratio out so a note can quote it, and
    a note may quote it only when ``"ratio"`` is the tier that actually fired.
    A lone hit has nothing to be ambiguous against, so it is infinitely dominant;
    no hits at all is zero.
    """
    if len(hits) >= 2:
        return _confidence_score(hits[0]) / (_confidence_score(hits[1]) or 1e-9)
    return float("inf") if hits else 0.0


def _agreement_dominant(hits: list[dict], *, vector_leg_keyless: bool = False) -> bool:
    """True when the top hit is the confident pick by retriever AGREEMENT.

    RRF compresses scores, so the ratio calls the most confident retrieval
    (both retrievers rank the same page top) "non-dominant". This reads the
    per-source ranks instead. Conservative: the top hit must be found by BOTH
    retrievers near the top, rank no lower than the runner-up in either, and
    the runner-up must be weaker. Agreement only lifts; demotion gates apply.

    ``vector_leg_keyless`` swaps the vector leg for the symbol leg, since a
    keyless index never writes ``_vec_rank``. **The caller must pass the leg's
    own status, not infer it from the hits:** ``hits`` is capped to the top 5,
    so a missing ``_vec_rank`` is not evidence the leg was skipped (it may have
    timed out or been outranked), and substituting then would manufacture
    "high" exactly when evidence is weakest.

    The symbol pair needs an exact rank-0 tie because FTS and the symbol leg
    read overlapping text. At rank 0 the runner-up check cannot reject anything
    (two hits cannot share rank 0 in one leg), so the rule reduces to "#1 in
    both"; the gap check constrains the vector pair only.
    """
    if len(hits) < 2:
        return False
    if vector_leg_keyless:
        second_field = "_sym_rank"
        top_rank_max = _SYMBOL_AGREEMENT_TOP_RANK_MAX
    else:
        second_field = "_vec_rank"
        top_rank_max = _AGREEMENT_TOP_RANK_MAX
    top = hits[0]
    top_a = top.get("_fts_rank")
    top_b = top.get(second_field)
    # A one-retriever top hit is exactly the ambiguous case not to lift.
    if top_a is None or top_b is None:
        return False
    if top_a > top_rank_max or top_b > top_rank_max:
        return False
    second = hits[1]
    sec_a = second.get("_fts_rank")
    sec_b = second.get(second_field)
    # Runner-up found by only one retriever -> the consensus top clearly wins.
    if sec_a is None or sec_b is None:
        return True
    # Runner-up found by both: the top must rank at least as high in BOTH
    # sources (no source disagrees) and strictly ahead in at least one.
    if top_a <= sec_a and top_b <= sec_b:
        return (sec_a - top_a) >= _AGREEMENT_RANK_GAP or (
            sec_b - top_b
        ) >= _AGREEMENT_RANK_GAP
    return False


def dominance_reason(hits: list[dict], *, agreement_dominant: bool = False) -> str | None:
    """WHICH test found the top hit dominant, or None if none did.

    The single owner of "did retrieval clearly point at ONE page". The note
    builder needs the reason, not just the verdict, because a note quoting the
    wrong tier refutes itself.

    * ``"ratio"``: the top hit outscores the runner-up by a clear multiple.
    * ``"gap"``: both scores are excellent, so dominance is an absolute gap
      and the ratio is NOT the measurement (6.0 vs 5.4 reads as 1.11x).
    * ``"agreement"``: both retrievers rank this page top; fires near 1.02x,
      so the ratio is not the measurement here either.
    * ``"sole_hit"``: one hit, nothing to be ambiguous against.

    Coverage is deliberately not a gate: natural-language questions rarely put
    every content term in one page. No hits at all is not dominance.
    """
    if not hits:
        return None
    if len(hits) < 2:
        return "sole_hit"
    top_score = _confidence_score(hits[0])
    second_score = _confidence_score(hits[1]) or 1e-9
    if top_score >= _DOMINANCE_ABS_SCORE_FLOOR:
        if (top_score - second_score) >= _DOMINANCE_ABS_GAP:
            return "gap"
    elif (top_score / second_score) >= _DOMINANCE_RATIO:
        return "ratio"
    return "agreement" if agreement_dominant else None


def is_dominant(hits: list[dict], *, agreement_dominant: bool = False) -> bool:
    """Did retrieval clearly point at ONE page? See :func:`dominance_reason`.

    The confidence grade, :func:`_retrieval_quality` and the payload's
    ambiguity caveat all read this one test, so they cannot disagree about the
    same retrieval.
    """
    return dominance_reason(hits, agreement_dominant=agreement_dominant) is not None


# Kept apart from `_query_terms.STOPWORDS` on purpose: these are dropped for
# coverage only, retrieval still searches them. The frame verbs plus words a
# coverage check should not demand of a hit ("code", "logic").
_QUESTION_FRAME_STEMS = frozenset(
    _stem(w)
    for w in (
        *QUESTION_FRAME_VERBS, "implements",
        "use", "used", "uses", "using", "way", "done", "code", "logic",
    )
)


def uncovered_terms(question: str, hits: list[dict]) -> list[str]:
    """The question's words the top hit leaves out, when they outweigh the rest.

    Dominance compares hits with each other, so a page about something else
    still dominates a worse page about something else. This asks whether the
    hit mentions what was asked, in the text the answer was built from. Each
    word weighs by how few retrieved hits carry it: one every hit shares (the
    project's own name) counts little, the one naming the topic counts most.
    Returns ``[]`` when the hit covers at least :data:`_ANSWER_COVERAGE_FLOOR`
    of that weight.
    """
    if not hits:
        return []
    served = hit_stems(hits)
    return _left_out(_question_words(question), served[0], served)


def hit_stems(hits: list[dict]) -> list[set[str]]:
    """Each hit's served text as stems, the population coverage weighs words over."""
    return [_text_stems(_retrieval_corpus([h], include_paths=True)) for h in hits]


def body_leaves_out(question: str, body: dict, served: list[set[str]]) -> list[str]:
    """The question's words a body served for the name it carries leaves out.

    A question can name a function as the place it asks about ("the FTS
    timeout in get_answer"): the body is the named code, not the answer. The
    words besides the body's own name and owner that some retrieved hit
    carries (*served*, from :func:`hit_stems`) must be in its source, weighed
    as in :func:`uncovered_terms`. A word no hit carries ("definitions",
    "parameters") is framing, not a fact found elsewhere, so it never counts.
    ``[]`` when nothing else is asked.
    """
    named = _text_stems(f"{body.get('name') or ''} {body.get('parent_name') or ''}")
    words = {
        t: w
        for t, w in _question_words(question).items()
        if t not in named and any(_stem_hit(t, s) for s in served)
    }
    return _left_out(words, _text_stems(body.get("source") or ""), served)


def _unserved_words(question: str, hits: list[dict]) -> bool:
    """Does the question ask a word no retrieved hit carries?"""
    served = hit_stems(hits)
    return any(not any(_stem_hit(t, s) for s in served) for t in _question_words(question))


def _question_words(question: str) -> dict[str, str]:
    return {t: w for t, w in _text_words(question).items() if t not in _QUESTION_FRAME_STEMS}


def _left_out(words: dict[str, str], covering: set[str], served: list[set[str]]) -> list[str]:
    """*words* missing from *covering*, when their rarity weight across *served*
    exceeds what :data:`_ANSWER_COVERAGE_FLOOR` lets go."""
    if not words:
        return []
    weight = {
        t: window_term_weight(sum(_stem_hit(t, s) for s in served), len(served)) for t in words
    }
    missing = [t for t in words if not _stem_hit(t, covering)]
    left_out = sum(weight[t] for t in missing)
    if left_out <= (1.0 - _ANSWER_COVERAGE_FLOOR) * sum(weight.values()):
        return []
    return [words[t] for t in missing]


def _retrieval_quality(
    hits: list[dict],
    agreement_dominant: bool,
    question: str = "",
    uncovered: list[str] | None = None,
) -> str:
    """Rate the retrieval, independently of the text it fed.

    Shared with the degraded path so "high" means the same to keyless and keyed
    callers, and :func:`_degraded_confidence` grades from it. "weak" means not
    dominant per :func:`is_dominant`, or a top hit that leaves out most of what
    *question* asks (:func:`uncovered_terms`; pass *uncovered* when already computed).
    """
    top_score = _confidence_score(hits[0]) if hits else 0.0
    dominant_grade = is_dominant(hits, agreement_dominant=agreement_dominant)
    if uncovered is None:
        uncovered = uncovered_terms(question, hits) if dominant_grade else []
    if dominant_grade and uncovered:
        return "weak"
    if dominant_grade and top_score >= _HIGH_CONFIDENCE_SCORE_FLOOR:
        return "high"
    return "partial" if dominant_grade else "weak"


def file_hybrid_rank(hits: list[dict], path: str | None) -> int | None:
    """The best hybrid rank any hit of *path*'s file carries, or None if none does.

    Hits added after stamping (graph expansion, anchoring) carry no rank and can
    precede the ranked hit of the same file, so the first match is not enough.
    """
    target = _normalize_target_path(path or "")
    ranks = [
        h["_hybrid_rank"]
        for h in hits
        if "_hybrid_rank" in h and _normalize_target_path(h.get("target_path") or "") == target
    ]
    return min(ranks, default=None)


def lead_leaves_retrieval(hits: list[dict], lead: str | None) -> bool:
    """True when the file a keyless answer names first is not where retrieval points.

    A lead outside the top :data:`_LEAD_HYBRID_TOP_K` files of the unreranked
    hybrid order, or never in it, was lifted by a rerank or an injected hit that
    hybrid retrieval does not back.
    """
    if not hits or not lead:
        return False
    # Unstamped hits carry no hybrid order to disagree with.
    if not any("_hybrid_rank" in h for h in hits):
        return False
    rank = file_hybrid_rank(hits, lead)
    return rank is None or rank >= _LEAD_HYBRID_TOP_K


def _degraded_confidence(
    reason: str,
    retrieval_quality: str,
    *,
    lead_outside_top: bool = False,
    graph_answered: bool = False,
) -> str:
    """Grade a synthesis-less payload on what a caller can act on, not on prose.

    The field tells an agent whether it still has work to do, so it is graded
    from the retrieval served rather than pinned "low" for want of prose.
    Two ceilings, both load bearing:

    "high" is unreachable: ``answer`` here is assembled boilerplate, and a
    high-confidence answer is licensed to be cited directly.

    ``synthesis-failed`` stays "low": a provider is configured, so a retry can
    still produce a real answer. "no-llm-provider" is the end of the line, so
    there the evidence is all there is to grade.

    A strong retrieval still grades "low" when the answer leads with a file it
    does not back (:func:`lead_leaves_retrieval`): "medium" tells the caller to
    start from that lead.

    ``graph_answered`` (a caller question the call graph answered) grades
    "medium" on its own: the answer then leads with graph edges, not retrieval.
    """
    if reason == "no-llm-provider" and graph_answered:
        return "medium"
    if reason != "no-llm-provider" or lead_outside_top:
        return "low"
    return "low" if retrieval_quality == "weak" else "medium"


def _is_question_named_body_cut_by_us(entry: dict, question_ids: set[str]) -> bool:
    """Whether this body is the question's own symbol, cut because WE ran out of lines.

    ``truncated`` alone is not enough to demote on: on the ``source_excerpt``
    fallback the bytes come from the index, not disk. A served span at the line
    cap says the cut was ours, the only case where something was withheld.
    """
    if not (entry.get("truncated") and entry.get("continuation")):
        return False
    if entry.get("name") not in question_ids:
        return False
    return entry["lines"][1] - entry["lines"][0] + 1 >= _INLINE_BODY_MAX_LINES


def _is_enclosing_continuation(entry: dict, implicated: set[str]) -> bool:
    """Whether this body simply continues past the cut, rather than losing a symbol.

    A withheld entry carrying the served body's OWN name is the enclosing symbol
    continuing past the cut; the accurate pointer is its ``continuation``, not
    a get_symbol call for a body the caller mostly holds.
    """
    name = entry.get("name")
    if not (entry.get("continuation") and name in implicated):
        return False
    return any(s.get("name") == name for s in (entry.get("withheld_symbols") or []))


class _Grade(NamedTuple):
    """The confidence verdict, and every finding the notes are written from.

    Each gate that fires records WHAT it objected to, and the payload builder
    writes the note and next action from that, keeping the two in step.
    """

    confidence: str
    hedged: bool
    ratio: float
    top_score: float
    second_score: float
    #: Why the grade is "high", or None: a :func:`dominance_reason` tier, or
    #: "symbol_body" / "grounding". The reasons license different sentences:
    #: only "ratio" may quote the ratio, and only a dominance tier or
    #: "symbol_body" may tell the agent not to re-read.
    high_reason: str | None
    ungrounded_values: list[str]
    #: Question words the top hit leaves out, when they outweigh what it covers.
    uncovered: list[str]
    frame_unsupported: list[str]
    exclusivity_over_truncated: bool
    withheld_implicated: list[str]
    lookup_body_truncated: bool
    named_body_cut: dict | None
    #: The file the answer leads with when it cites files but not the top hit.
    lead_off_top: str | None = None
    #: The answer's own sentence saying part of the question is not in the evidence.
    admitted_gap: str | None = None
    #: Code names the question writes that nothing in the repository defines or writes.
    absent_names: tuple[str, ...] = ()
    #: Question names defined in several files the answer does not tell apart, with those files.
    split_names: dict[str, list[str]] | None = None
    #: Later clauses of a compound question whose retrieved file the answer does not cite.
    clauses_unanswered: tuple[str, ...] = ()
    #: Retriever agreement was the only dominance, under the score agreement needs.
    weak_agreement: bool = False
    #: Indexed names the question writes that no top hit hydrates.
    unhydrated_names: tuple[str, ...] = ()
    #: Dominant, but the top score is under the floor high needs.
    under_floor: bool = False
    #: The answer text stops mid-sentence (see :func:`answer_cut_off`).
    cut_off: bool = False


@dataclass
class _GradeState:
    """One :func:`_grade_answer` pass: its inputs, running grade and findings."""

    question: str
    question_ids: set[str]
    answer_text: str
    hits: list[dict]
    citations: list[str]
    symbol_bodies: list[dict]
    served_named_body: bool
    dominant: bool
    earn_high: bool
    uncovered: list[str] | None
    confidence: str
    hedged: bool = False
    ungrounded_values: list[str] = field(default_factory=list)
    frame_unsupported: list[str] = field(default_factory=list)
    exclusivity_over_truncated: bool = False
    withheld_implicated: list[str] = field(default_factory=list)
    lookup_body_truncated: bool = False
    named_body_cut: dict | None = None
    gap: list[str] = field(default_factory=list)
    lead_off_top: str | None = None
    admitted_gap: str | None = None
    unindexed_names: set[str] = field(default_factory=set)
    absent_names: tuple[str, ...] = ()
    split_names: dict[str, list[str]] = field(default_factory=dict)
    clauses_unanswered: list[str] = field(default_factory=list)
    agreement_only: bool = False
    top_score: float = 0.0
    weak_agreement: bool = False
    #: The file an earned high rests on, when the route names one.
    earned_path: str | None = None
    unhydrated_names: tuple[str, ...] = ()
    stop_reason: str | None = None
    cut_off: bool = False

    @functools.cached_property
    def value_line(self) -> dict | None:
        """The verified value line the answer states for the constant the question names."""
        return stated_value_line(self.question, self.question_ids, self.answer_text, self.hits)


def _earned_high(
    question: str,
    question_ids: set[str],
    answer_text: str,
    hits: list[dict],
    citations: list[str],
    served_named_body: bool,
    top_score: float,
) -> tuple[str, str | None] | None:
    """Which route earned "high" on a weak retrieval, and the file it rests on, or None.

    * ``"symbol_body"``: the question-named symbol's live body is inlined,
      resolved by exact name rather than ranking.
    * ``"value_line"``: the answer states the value a verified definition line
      assigns, for a constant the question names (:func:`stated_value_line`).
      That line is the answer, so no retrieval score floor applies.
    * ``"grounding"``: every mechanism term the answer names was in the
      material shown; that shows no fabrication, not that retrieval found the
      right page, so it is opt-in.

    Demotion gates still apply to an earned high.
    """
    if not _flag_on(_EARN_HIGH_GROUNDING_ENV):
        return None
    strong = top_score >= _HIGH_CONFIDENCE_SCORE_FLOOR
    if strong and served_named_body:
        return "symbol_body", None
    value = stated_value_line(question, question_ids, answer_text, hits)
    if value is not None:
        return "value_line", value["path"]
    if strong and _opt_in(_EARN_HIGH_ON_WEAK_RETRIEVAL_ENV):
        cited = set(citations)
        cited_has_body = any(h.get("symbols") for h in hits if h.get("target_path") in cited)
        unsupported, grounded = _frame_term_grounding(answer_text, question, hits)
        if cited_has_body and grounded >= 1 and not unsupported:
            return "grounding", None
    return None


# Words that ask for a value rather than name the constant holding it.
_VALUE_ASK_STEMS = frozenset(_stem(w) for w in ("default", "value", "constant", "set", "defined"))

# An assignment's "=", never a comparison, a walrus or an arrow.
_ASSIGN_RE = re.compile(r"(?<![=!<>:])=(?![=>])")
# A negation before a literal value: "DEBUG is not False".
_NEGATED_RE = re.compile(r"\b(?:not|never|no longer)\b|n't\b", re.IGNORECASE)
# A trailing comment after the assigned value.
_TRAILING_COMMENT_RE = re.compile(r"\s+(?://|#).*$")


def stated_value_line(
    question: str, question_ids: set[str], answer_text: str, hits: list[dict]
) -> dict | None:
    """The verified constant the question names and whose value the answer states.

    A constant's definition IS its assignment line, so a value read live at
    verified bounds grounds the answer as a served body does. The question must
    name the constant exactly, or by every word of its name with the rest of
    the question carried by the constant's own page; a name defined twice in
    the hits names neither.
    """
    found, seen = _verified_constants(hits)
    for hit, sym in found:
        name = sym.get("name") or ""
        if seen[name] > 1:
            continue
        stated = _states_assigned_value(name, _definition_line(sym), answer_text)
        if stated and _question_names_constant(question, question_ids, sym, hit):
            return {"path": hit.get("target_path"), "name": name, "line": sym.get("start_line")}
    return None


def _verified_constants(hits: list[dict]) -> tuple[list[tuple[dict, dict]], Counter]:
    """The verified constants of the top hits, or of any hit when the question
    names the constant exactly, and how often each constant name occurs in all hits.

    An exactly named constant is placed by its name, not its rank.
    """
    constants = [
        (rank, h, sym)
        for rank, h in enumerate(hits)
        for sym in h.get("symbols") or []
        if sym.get("kind") in ("constant", "variable")
    ]
    seen = Counter(sym.get("name") for _, _, sym in constants)
    found = [
        (h, sym)
        for rank, h, sym in constants
        if sym.get("_verified") and (rank < _ENRICH_TOP_N_HITS or sym.get("_matched"))
    ]
    return found, seen


def _definition_line(sym: dict) -> str:
    """The constant's assignment: its signature, or the first line of its live
    excerpt when the signature is the bare name (a Go ``const ( ... )`` block)."""
    signature = sym.get("signature") or ""
    if _ASSIGN_RE.search(signature):
        return signature
    excerpt = (sym.get("source_excerpt") or "").lstrip("\n").split("\n", 1)[0]
    m = _ASSIGN_RE.search(excerpt)
    # Only a line that assigns this name: not a comment or another name's line.
    commented = excerpt.lstrip().startswith(("#", "//", "/*"))
    if m and not commented and _word_re(sym.get("name") or "").search(excerpt[: m.start()]):
        return excerpt
    return signature


def _states_assigned_value(name: str, signature: str, answer_text: str) -> bool:
    """Does a sentence naming *name* state the value *signature* assigns?

    Every sentence naming it with numbers must assert exactly the assigned ones,
    so "RETRIES is 5, not 3" never reads as stating 3.
    """
    value = _assigned_value(signature)
    if value is None:
        return False
    numbers = _numbers_in(value)
    literal = _word_re(value.strip("'\"`"))
    stated = False
    for sentence in _sentences(answer_text):
        if not _word_re(name).search(sentence):
            continue
        asserted = _asserted_numbers(sentence)
        if numbers and asserted and asserted != numbers:
            return False
        stated = stated or (asserted == numbers if numbers else _states_literal(literal, sentence))
    return stated


def _states_literal(literal: re.Pattern, sentence: str) -> bool:
    """Does *sentence* state the literal value, with no negation before it?"""
    m = literal.search(sentence)
    return m is not None and not _NEGATED_RE.search(sentence[: m.start()])


def _assigned_value(signature: str) -> str | None:
    """The value an assignment-shaped signature gives, or None when it gives none."""
    m = _ASSIGN_RE.search(signature)
    if m is None:
        return None
    value = _TRAILING_COMMENT_RE.sub("", signature[m.end() :]).strip().rstrip(";,").strip()
    # One character ("0", "[") or no letter or digit says too little to match on.
    if len(value.strip("'\"`")) < 2 and not value.isdigit():
        return None
    return value if any(ch.isalnum() for ch in value) else None


def _word_re(text: str) -> re.Pattern:
    return re.compile(rf"(?<![\w$]){re.escape(text)}(?![\w$])", re.IGNORECASE)


def _question_names_constant(question: str, question_ids: set[str], sym: dict, hit: dict) -> bool:
    name = sym.get("name") or ""
    name_words = _text_stems(name)
    if name not in question_ids:
        asked = _text_stems(question)
        if len(name_words) < 2 or not all(_stem_hit(w, asked) for w in name_words):
            return False
    # The constant's own lines and its page's heading, not every symbol on the page.
    own = " ".join(
        str(v or "")
        for v in (hit.get("target_path"), hit.get("title"), hit.get("summary"),
                  sym.get("signature"), sym.get("docstring"))
    )
    page = _text_stems(own) | name_words
    return all(
        _stem_hit(t, page) for t in _question_words(question) if t not in _VALUE_ASK_STEMS
    )


def _hedge_gate(s: _GradeState) -> None:
    """Dominance says the right file was indexed, not that the prose is usable.

    With the question-named body served the ground truth is in hand, so the
    demotion stops at "medium" rather than sending the agent to Read. A caveat
    that is not a hedge falls to :func:`_admitted_gap_gate`.
    """
    # A later admission is a caveat only when retrieval carries every word asked.
    s.hedged = _answer_is_hedged(s.answer_text, s.question) or (
        _admitted_gap(s.answer_text) is not None and _unserved_words(s.question, s.hits)
    )
    if s.hedged:
        s.confidence = "medium" if s.served_named_body else "low"


def _cut_off_gate(s: _GradeState) -> None:
    """The answer text was cut off before it finished.

    Whatever the missing part said is not in the answer, so it is not one to
    cite unread.
    """
    s.cut_off = s.confidence == "high" and answer_cut_off(s.answer_text, s.stop_reason)
    if s.cut_off:
        s.confidence = "medium"


def _admitted_gap_gate(s: _GradeState) -> None:
    """The answer says part of what was asked is not in its evidence.

    Dominance measured the retrieval, not whether it covered the question;
    the answer's own admission is the better witness.
    """
    if s.confidence != "high" or s.hedged:
        return
    s.admitted_gap = _admitted_gap(s.answer_text)
    # The answer states the verified value of the constant the question names:
    # what was asked is in hand, so the admission is about something else
    # (where it is read, its line number).
    if s.admitted_gap and s.value_line:
        s.admitted_gap = None
    if s.admitted_gap:
        s.confidence = "medium"


def _absent_name_gate(s: _GradeState) -> None:
    """The question names code that does not exist: no symbol, no production line,
    and nothing retrieved mentions it.

    Whatever the answer says, it describes something else, so it is not one to
    act on.
    """
    if not s.absent_names:
        return
    bodies = [
        f"{b.get('name')} {b.get('source')} {[w.get('name') for w in b.get('withheld_symbols') or []]}"
        for b in s.symbol_bodies
    ]
    served = "\n".join([_retrieval_corpus(s.hits, include_paths=True), *bodies]).lower()
    s.absent_names = tuple(n for n in s.absent_names if n.lower() not in served)
    if s.absent_names:
        s.confidence = "low"


def _identifier_gate(s: _GradeState) -> None:
    """The question names symbols but no top hit hydrates any of them.

    A name no symbol carries (``HTTP``, ``TypeScript``) cannot be hydrated, so
    only indexed names count. The file an earned high rests on counts as a top
    hit: its line is the evidence, wherever it ranked. A top hit whose value
    line the answer states hydrates what was asked: that line is the answer,
    and its page carries every other word of the question.
    """
    names = s.question_ids - s.unindexed_names
    if s.confidence != "high" or not names:
        return
    top_paths = {h.get("target_path") for h in s.hits[:_ENRICH_TOP_N_HITS]}
    if s.value_line and s.value_line["path"] in top_paths:
        return
    top_n = [
        h
        for rank, h in enumerate(s.hits)
        if h.get("symbols")
        and (rank < _ENRICH_TOP_N_HITS or (s.earned_path and h.get("target_path") == s.earned_path))
    ]
    if not any(sym.get("_matched") for h in top_n for sym in (h.get("symbols") or [])):
        s.unhydrated_names = tuple(sorted(names))
        s.confidence = "medium"


def _split_name_gate(s: _GradeState) -> None:
    """A question-named name defined in several files, answered as if it were one.

    Settled when the answer names every defining file in a sentence that names
    the symbol, or when a question word is a path word of exactly one of them
    and the answer names that one so. A file the answer mentions for another
    reason (an importer) does not count.
    """
    if s.confidence != "high":
        s.split_names = {}
        return
    s.split_names = {
        name: paths
        for name, paths in s.split_names.items()
        if not _split_settled(name, paths, s.question, s.answer_text)
    }
    if s.split_names:
        s.confidence = "medium"


def _split_settled(name: str, paths: list[str], question: str, answer_text: str) -> bool:
    basenames = Counter(p.rsplit("/", 1)[-1] for p in paths)
    name_re = _word_re(name)
    about = [x for x in _sentences(answer_text) if name_re.search(x)]

    def named(path: str) -> bool:
        base = path.rsplit("/", 1)[-1]
        return any(path in x or (basenames[base] == 1 and base in x) for x in about)

    if all(named(p) for p in paths):
        return True
    picked = paths_the_question_names(name, paths, question)
    return len(picked) == 1 and named(picked[0])


def _clause_gate(s: _GradeState) -> None:
    """A compound question whose later clause the answer leaves out.

    Each later clause put its best file among the hits (``_clause``); an answer
    citing none of that clause's file used none of its evidence, so the whole
    question is not answered.
    """
    if s.confidence != "high":
        return
    cited = {c.split("::", 1)[0] for c in s.citations}
    s.clauses_unanswered = [
        h["_clause"]
        for h in s.hits
        if h.get("_clause") and (h.get("target_path") or "").split("::", 1)[0] not in cited
    ]
    if s.clauses_unanswered:
        s.confidence = "medium"


def _value_gate(s: _GradeState) -> None:
    """On a value question every asserted number must appear in the retrieved material.

    A value derived from grounded operands softens one notch, but only on a
    dominant high with another asserted number grounded; a lone invented value
    goes "low".
    """
    if s.hedged or not _is_value_question(s.question):
        return
    s.ungrounded_values = _ungrounded_numbers(s.answer_text, s.hits)
    if not s.ungrounded_values:
        return
    grounded_sibling = len(_asserted_numbers(s.answer_text)) > len(s.ungrounded_values)
    if grounded_sibling and s.confidence == "high" and s.dominant:
        s.confidence = "medium"
    else:
        s.confidence = "low"


def _citation_gate(s: _GradeState) -> None:
    """A high answer must cite a page that contributed hydrated symbols."""
    if s.confidence != "high":
        return
    cited = set(s.citations)
    if not any(h.get("symbols") for h in s.hits if h.get("target_path") in cited):
        s.confidence = "medium"


def _named_file_gate(s: _GradeState) -> None:
    """The answer cites files but not the top hit dominance was measured on.
    Kept only when it demotes, so the note never blames it for another gate's grade."""
    if s.confidence != "high" or s.lead_off_top == s.earned_path:
        # Leading with the file an earned high rests on is resting on that high.
        s.lead_off_top = None
    elif s.lead_off_top:
        s.confidence = "medium"


def _claim_gate(s: _GradeState) -> None:
    """A why/how answer naming a code-like mechanism term retrieval never showed.

    That is the tell of a conflated mechanism (right file, wrong function).
    Lookups are out of scope: they legitimately echo the named symbol.
    """
    in_scope = _is_why_question(s.question) or (
        _flag_on(_CLAIM_SUPPORT_GATE_ENV) and _is_mechanism_question(s.question)
    )
    if s.confidence != "high" or s.hedged or not in_scope:
        return
    unsupported, grounded = _frame_term_grounding(s.answer_text, s.question, s.hits)
    if unsupported and len(unsupported) >= grounded:
        s.frame_unsupported = unsupported
        s.confidence = "medium"


def _exclusivity_gate(s: _GradeState) -> None:
    """"The only" / "entirely" asserted over a truncated body."""
    if s.confidence != "high" or s.hedged:
        return
    s.exclusivity_over_truncated = _has_unqualified_exclusivity_over_truncated(
        s.answer_text, s.symbol_bodies
    )
    if s.exclusivity_over_truncated:
        s.confidence = "medium"


def _withheld_gate(s: _GradeState) -> None:
    """A withheld symbol the question names or the answer references as code."""
    s.withheld_implicated = implicated_withheld_symbols(
        s.question, s.answer_text, s.symbol_bodies
    )
    if s.confidence == "high" and s.withheld_implicated:
        s.confidence = "medium"


def _lookup_gate(s: _GradeState) -> None:
    """A bare-name lookup whose own single-definition body we cut.

    The question names the served symbol, so the withheld gate cannot fire.
    The entry is kept so the note can quote the real range.
    """
    s.named_body_cut = next(
        (b for b in s.symbol_bodies if _is_question_named_body_cut_by_us(b, s.question_ids)),
        None,
    )
    s.lookup_body_truncated = (
        s.confidence == "high"
        and s.named_body_cut is not None
        and is_symbol_lookup_question(s.question, s.question_ids)
    )
    if s.lookup_body_truncated:
        s.confidence = "medium"


def _coverage_gate(s: _GradeState) -> None:
    """The top hit leaves out question words that outweigh what it covers.

    Dominance says the top hit beat the others, not that it is about the
    question; a served body does not exempt it. A value line does: it already
    had to carry every question word on its own page (:func:`stated_value_line`).
    """
    if s.confidence != "high" or s.earned_path:
        return
    s.gap = uncovered_terms(s.question, s.hits) if s.uncovered is None else s.uncovered
    if s.gap:
        s.confidence = "medium"


def _agreement_floor_gate(s: _GradeState) -> None:
    """Retriever agreement alone vouches for high only on a strong top score.

    Agreement fires near a 1.0 ratio, so a modest top page can win it while its
    excerpt stops short of what the answer describes. Runs after the specific
    gates so their notes win.
    """
    s.weak_agreement = (
        s.confidence == "high"
        and s.agreement_only
        and s.top_score < _AGREEMENT_HIGH_SCORE_FLOOR
    )
    if s.weak_agreement:
        s.confidence = "medium"


def _non_dominant_ceiling(s: _GradeState) -> None:
    """With no dominant page the answer stays below "high" unless it earned high."""
    if not s.dominant and not s.earn_high and s.confidence == "high":
        s.confidence = "medium"


#: The demotion gates, in order. Several fire only while the answer is still
#: high, so one problem never pushes a response down two levels. Coverage runs
#: late so a more specific gate writes the note.
_GRADE_GATES = (
    _hedge_gate,
    _absent_name_gate,
    _cut_off_gate,
    _admitted_gap_gate,
    _identifier_gate,
    _split_name_gate,
    _clause_gate,
    _value_gate,
    _citation_gate,
    _named_file_gate,
    _claim_gate,
    _exclusivity_gate,
    _withheld_gate,
    _lookup_gate,
    _coverage_gate,
    _agreement_floor_gate,
    _non_dominant_ceiling,
)


def _high_routes(
    dominance: str | None, top_score: float, earned: tuple[str, str | None] | None
) -> tuple[str | None, str | None, str | None]:
    """``(dominance tier that vouches, earned route, file the earned high rests on)``.

    Dominant but under the score floor does not vouch: the right file relative
    to its siblings, not strong enough to trust unverified. An earned high
    carries its own floor (see :func:`_earned_high`). The file is given only
    when the earned route, not dominance, vouches: it exempts the gates that
    measure the top hits.
    """
    vouched = dominance if top_score >= _HIGH_CONFIDENCE_SCORE_FLOOR else None
    route, path = earned or (None, None)
    return vouched, route, None if vouched else path


def _grade_answer(
    *,
    question: str,
    question_ids: set[str],
    answer_text: str,
    hits: list[dict],
    citations: list[str],
    symbol_bodies: list[dict],
    served_named_body: bool,
    dominance: str | None,
    uncovered: list[str] | None = None,
    lead_off_top: str | None = None,
    unindexed_names: Collection[str] = (),
    absent_names: Collection[str] = (),
    split_names: dict[str, list[str]] | None = None,
    stop_reason: str | None = None,
) -> _Grade:
    """Grade the synthesised answer through the gate cascade, in order.

    One starting grade from retrieval dominance, then gates that can only
    demote it. Several fire only while the answer is still high, so one
    response is not pushed two levels for one problem.

    ``dominance`` is :func:`dominance_reason`'s verdict, computed once by the
    caller and used for the starting grade, the ceiling, and the note's reason.
    ``uncovered`` is :func:`uncovered_terms`, when the caller already has it.
    ``lead_off_top`` is ``AnswerFiles.drops_top_hit``. ``unindexed_names`` are
    question identifiers no symbol carries; ``absent_names`` the code-shaped
    ones no production line writes either (``unindexed_question_names``).
    ``split_names`` maps a question name to the files that define it, for names
    defined in more than one. ``stop_reason`` is the synthesis stop reason, None
    when the provider does not report one.
    """
    dominant = dominance is not None
    top_score = _confidence_score(hits[0]) if hits else 0.0
    vouched, earned, earned_path = _high_routes(
        dominance,
        top_score,
        _earned_high(
            question, question_ids, answer_text, hits, citations, served_named_body, top_score
        ),
    )
    earn_high = earned is not None
    start = "high" if vouched or earn_high else "medium"
    s = _GradeState(
        earned_path=earned_path,
        unindexed_names=set(unindexed_names),
        absent_names=tuple(absent_names),
        split_names=dict(split_names or {}),
        question=question,
        question_ids=question_ids,
        answer_text=answer_text,
        hits=hits,
        citations=citations,
        symbol_bodies=symbol_bodies,
        served_named_body=served_named_body,
        dominant=dominant,
        earn_high=earn_high,
        uncovered=uncovered,
        confidence=start,
        lead_off_top=lead_off_top,
        agreement_only=dominance == "agreement" and not earn_high,
        top_score=top_score,
        stop_reason=stop_reason,
    )
    for gate in _GRADE_GATES:
        gate(s)
    # The specific dominance tier, not "dominant": gap and agreement fire near a
    # 1.0 ratio, so quoting the ratio would print a near tie as justification.
    # Under the floor the dominance tier did not vouch: the earned route did.
    high_reason = (vouched or earned) if s.confidence == "high" else None
    return _Grade(
        absent_names=s.absent_names,
        split_names=s.split_names,
        clauses_unanswered=tuple(s.clauses_unanswered),
        weak_agreement=s.weak_agreement,
        unhydrated_names=s.unhydrated_names,
        under_floor=dominant and not (vouched or earn_high),
        cut_off=s.cut_off,
        confidence=s.confidence,
        high_reason=high_reason,
        second_score=_confidence_score(hits[1]) if len(hits) >= 2 else 0.0,
        hedged=s.hedged,
        ratio=_top_two_score_ratio(hits),
        top_score=top_score,
        ungrounded_values=s.ungrounded_values,
        uncovered=s.gap,
        frame_unsupported=s.frame_unsupported,
        exclusivity_over_truncated=s.exclusivity_over_truncated,
        withheld_implicated=s.withheld_implicated,
        lookup_body_truncated=s.lookup_body_truncated,
        named_body_cut=s.named_body_cut,
        lead_off_top=s.lead_off_top,
        admitted_gap=s.admitted_gap,
    )
