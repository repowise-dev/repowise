"""Confidence and retrieval_quality grading for get_answer.

Two ratings that answer different questions. ``confidence`` says how much to
trust the synthesised text; ``retrieval_quality`` says how good the retrieval
that fed it was. The agent reads the first to decide whether to re-read the
source, the second to decide whether to search again.

:func:`_grade_answer` runs the gate cascade — one starting grade from retrieval
dominance, then a run of gates that can only demote it — and the predicates each
gate reads live beside it.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from repowise.server.mcp_server._answer_context import (
    is_mechanism_question as _is_mechanism_question,
)
from repowise.server.mcp_server._answer_context import (
    is_why_question as _is_why_question,
)
from repowise.server.mcp_server.tool_answer.config import (
    _AGREEMENT_RANK_GAP,
    _AGREEMENT_TOP_RANK_MAX,
    _CLAIM_SUPPORT_GATE_ENV,
    _DOMINANCE_ABS_GAP,
    _DOMINANCE_ABS_SCORE_FLOOR,
    _DOMINANCE_RATIO,
    _EARN_HIGH_GROUNDING_ENV,
    _EARN_HIGH_ON_WEAK_RETRIEVAL_ENV,
    _ENRICH_TOP_N_HITS,
    _HEDGE_MARKERS,
    _HIGH_CONFIDENCE_SCORE_FLOOR,
    _INLINE_BODY_MAX_LINES,
    _SYMBOL_AGREEMENT_TOP_RANK_MAX,
    _flag_on,
    _opt_in,
)
from repowise.server.mcp_server.tool_answer.symbols import is_symbol_lookup_question


def _answer_is_hedged(answer_text: str) -> bool:
    """True when the synthesized answer confesses it can't answer.

    The model admits insufficiency even on a top-scoring hit, so an admitted
    non-answer is low confidence however dominant retrieval was. Curly
    apostrophes are normalized first, or they would slip every "can't" marker.
    """
    low = (answer_text or "").lower().replace("\u2019", "'").replace("\u02bc", "'")
    return any(marker in low for marker in _HEDGE_MARKERS)


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
    return _numbers_in(_FILE_LINE_REF_RE.sub(" ", answer_text or ""))


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
    low = (answer_text or "").lower()
    low = low.replace("\u2019", "'").replace("\u02bc", "'")
    return any(tok in low for tok in _EXCLUSIVITY_TOKENS)



# A backticked span ending in a source-file extension is a PATH, so its dotted
# components are not symbol references: ``store.py`` says nothing about a symbol
# named ``py`` (or one named ``store``).
_PATHISH_SPAN_RE = re.compile(
    r"\.(py|pyi|ts|tsx|js|jsx|mjs|cjs|go|java|rb|php|rs|c|h|cc|cpp|hpp|cs|kt|kts|"
    r"swift|scala|sql|sh|md|json|ya?ml|toml|txt|cfg|ini|lock)$",
    re.IGNORECASE,
)


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


def _retrieval_quality(hits: list[dict], agreement_dominant: bool) -> str:
    """Rate the retrieval, independently of the text it fed.

    Shared with the degraded path so "high" means the same to keyless and keyed
    callers, and :func:`_degraded_confidence` grades from it. "weak" means
    exactly "not dominant" per :func:`is_dominant`.
    """
    top_score = _confidence_score(hits[0]) if hits else 0.0
    dominant_grade = is_dominant(hits, agreement_dominant=agreement_dominant)
    if dominant_grade and top_score >= _HIGH_CONFIDENCE_SCORE_FLOOR:
        return "high"
    return "partial" if dominant_grade else "weak"


def _degraded_confidence(reason: str, retrieval_quality: str) -> str:
    """Grade a synthesis-less payload on what a caller can act on, not on prose.

    The field tells an agent whether it still has work to do, so it is graded
    from the retrieval served rather than pinned "low" for want of prose.
    Two ceilings, both load bearing:

    "high" is unreachable: ``answer`` here is assembled boilerplate, and a
    high-confidence answer is licensed to be cited directly.

    ``synthesis-failed`` stays "low": a provider is configured, so a retry can
    still produce a real answer. "no-llm-provider" is the end of the line, so
    there the evidence is all there is to grade.
    """
    if reason != "no-llm-provider":
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
    frame_unsupported: list[str]
    exclusivity_over_truncated: bool
    withheld_implicated: list[str]
    lookup_body_truncated: bool
    named_body_cut: dict | None


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
) -> _Grade:
    """Grade the synthesised answer through the gate cascade, in order.

    One starting grade from retrieval dominance, then gates that can only
    demote it. Several fire only while the answer is still high, so one
    response is not pushed two levels for one problem.

    ``dominance`` is :func:`dominance_reason`'s verdict, computed once by the
    caller and used for the starting grade, the ceiling, and the note's reason.
    """
    dominant = dominance is not None
    _ratio = _top_two_score_ratio(hits)
    _top_score = _confidence_score(hits[0]) if hits else 0.0
    _second_score = _confidence_score(hits[1]) if len(hits) >= 2 else 0.0

    # Two routes EARN "high" on a non-dominant (weak) retrieval, tracked apart:
    # * `earned_body`: the question-named symbol's live body is inlined, resolved
    #   by exact name rather than ranking, so the source is in the response.
    # * `earned_grounding`: every mechanism term the answer names was in the
    #   material shown. Shows no fabrication, not that retrieval found the right
    #   page, so it is off by default (see the env flag's comment).
    # The demotion gates below still pull an earned high back down.
    earned_body = False
    earned_grounding = False
    if _flag_on(_EARN_HIGH_GROUNDING_ENV) and _top_score >= _HIGH_CONFIDENCE_SCORE_FLOOR:
        earned_body = served_named_body
        if _opt_in(_EARN_HIGH_ON_WEAK_RETRIEVAL_ENV):
            _cited = set(citations)
            _cited_has_body = any(h.get("symbols") for h in hits if h.get("target_path") in _cited)
            _fu, _fg = _frame_term_grounding(answer_text, question, hits)
            earned_grounding = _cited_has_body and _fg >= 1 and not _fu
    earn_high = earned_body or earned_grounding

    if (dominant or earn_high) and _top_score >= _HIGH_CONFIDENCE_SCORE_FLOOR:
        confidence = "high"
    elif dominant:
        # Dominant but weak: right file relative to its siblings, but not
        # strong enough to trust the synthesis without verification.
        confidence = "medium"
    else:
        confidence = "medium"

    # Hedge gate: dominance says we indexed the right file, not that the
    # synthesised text is usable.
    hedged = _answer_is_hedged(answer_text)
    if hedged:
        # When the question-named symbol's body is served, the ground truth is
        # in hand; "low" would send the agent to Read for nothing.
        confidence = "medium" if served_named_body else "low"

    # Identifier-citation gate: the question names identifiers but no top hit
    # hydrates any of them, so retrieval may be on plausible-but-wrong files.
    if confidence == "high" and question_ids:
        top_n = [h for h in hits[:_ENRICH_TOP_N_HITS] if h.get("symbols")]
        has_match = any(s.get("_matched") for h in top_n for s in (h.get("symbols") or []))
        if not has_match:
            confidence = "medium"

    # Value gate: on value-shaped questions every asserted number must appear in
    # the retrieved material. An invented number delivered as "high" is the
    # worst calibration failure, because the consumer was told not to verify.
    ungrounded_values: list[str] = []
    if not hedged and _is_value_question(question):
        ungrounded_values = _ungrounded_numbers(answer_text, hits)
        if ungrounded_values:
            # A value derived from grounded operands softens one notch, but only
            # on a dominant high and only when another asserted number is
            # grounded; a lone invented value is what this gate exists to catch.
            grounded_sibling = len(_asserted_numbers(answer_text)) > len(ungrounded_values)
            if grounded_sibling and confidence == "high" and dominant:
                confidence = "medium"
            else:
                confidence = "low"

    # Citation-source gate: a high answer must cite a page that contributed
    # hydrated symbols; summary-only grounding lets plausible-wrong prose through.
    if confidence == "high":
        cited = set(citations)
        if not any(h.get("symbols") for h in hits if h.get("target_path") in cited):
            confidence = "medium"

    # Claim-support gate: a synthesis can conflate mechanisms (right file, wrong
    # function) and still clear dominance. The tell is a code-like term the
    # answer names as the mechanism that retrieval never showed. Covers why and
    # how questions; lookups legitimately echo the named symbol.
    frame_unsupported: list[str] = []
    _claim_scope = _is_why_question(question) or (
        _flag_on(_CLAIM_SUPPORT_GATE_ENV) and _is_mechanism_question(question)
    )
    if confidence == "high" and not hedged and _claim_scope:
        frame_unsupported, _grounded_terms = _frame_term_grounding(answer_text, question, hits)
        if frame_unsupported and len(frame_unsupported) >= _grounded_terms:
            confidence = "medium"
        else:
            frame_unsupported = []

    # Exclusivity gate: "the only" / "entirely" asserted over a truncated body.
    exclusivity_over_truncated = False
    if confidence == "high" and not hedged:
        exclusivity_over_truncated = _has_unqualified_exclusivity_over_truncated(
            answer_text, symbol_bodies
        )
        if exclusivity_over_truncated:
            confidence = "medium"

    # Withheld-dependency gate: a withheld symbol is named by the question or
    # referenced as code by the answer. Unlike the exclusivity gate it needs no
    # prose token; truncation alone is deliberately not enough.
    withheld_implicated = implicated_withheld_symbols(question, answer_text, symbol_bodies)
    if confidence == "high" and withheld_implicated:
        confidence = "medium"

    # Lookup gate: on a bare-name lookup the question names the SERVED symbol,
    # so the withheld-dependency gate can never fire. A multi-def name caps on
    # truncation in the union path; a single-def name never reaches it, so it
    # is capped here. Narrow on purpose: a symbol-lookup question whose own
    # symbol was cut. Kept as the entry so the note can quote the real range.
    named_body_cut = next(
        (b for b in symbol_bodies if _is_question_named_body_cut_by_us(b, question_ids)),
        None,
    )
    lookup_body_truncated = (
        confidence == "high"
        and named_body_cut is not None
        and is_symbol_lookup_question(question, question_ids)
    )
    if lookup_body_truncated:
        confidence = "medium"

    # Non-dominant ceiling: with no single dominant page the answer must not
    # read "high" (cite without verifying) unless it EARNED high. Keyed on
    # `earn_high`, not `earned_body`, so the grounding opt-in really restores
    # the old behaviour. Usually a no-op; explicit so the contract is visible.
    if not dominant and not earn_high and confidence == "high":
        confidence = "medium"

    # The specific dominance tier, not "dominant": gap and agreement fire near a
    # 1.0 ratio, so quoting the ratio would print a near tie as justification.
    high_reason = None
    if confidence == "high":
        high_reason = dominance or ("symbol_body" if earned_body else "grounding")
    return _Grade(
        confidence=confidence,
        high_reason=high_reason,
        second_score=_second_score,
        hedged=hedged,
        ratio=_ratio,
        top_score=_top_score,
        ungrounded_values=ungrounded_values,
        frame_unsupported=frame_unsupported,
        exclusivity_over_truncated=exclusivity_over_truncated,
        withheld_implicated=withheld_implicated,
        lookup_body_truncated=lookup_body_truncated,
        named_body_cut=named_body_cut,
    )
