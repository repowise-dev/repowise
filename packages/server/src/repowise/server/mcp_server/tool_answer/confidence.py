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

    Retrieval dominance alone doesn't tell you whether the LLM produced a
    usable answer — the underlying model happily admits insufficiency even
    on a top-scoring hit. Treat an admitted non-answer as low confidence,
    regardless of how dominant retrieval was.

    Typographic apostrophes are normalized to ASCII first: the markers use
    plain "can't" / "i can't", but the LLM routinely emits the curly U+2019,
    which would slip every apostrophe-bearing marker and let a hedged answer
    ride through as high confidence.
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

# Digit-grouping separators: ``100_000`` (source) and ``100,000`` (prose) are
# the same value as ``100000``. Strip them on both sides before comparing, or
# a correct constant like ``MAX = 100_000`` reads as ungrounded against an
# answer that says "100000" — a false downgrade on the exact value-shaped
# constants this gate exists to protect.
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
        # A concept-anchored hit carries a rationale comment mined live from the
        # source. It grounds the question's number (the comment was selected
        # because it contains it) and is surfaced to the agent as code_rationale.
        # Without it the value/frame gates would flag the (correct) number the
        # answer echoes from the question as if synthesis invented it.
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

    The exact failure this guards: synthesis confidently inventing a default
    ("the minimum count is 3") when no retrieved excerpt ever contained a 3.
    Compares the answer's standalone numbers against the numbers present in
    everything the LLM was shown for the hits — titles, summaries, snippets,
    and hydrated symbols (signatures, docstrings, source excerpts).
    """
    asserted = _asserted_numbers(answer_text)
    if not asserted:
        return []

    grounded = _numbers_in(_retrieval_corpus(hits))
    return sorted(asserted - grounded)


# Identifier-shaped tokens an answer uses to NAME a mechanism: CamelCase
# (``PageRank``), snake_case (``apply_pagerank_bias``), dotted paths
# (``Foo.bar``), or anything bearing a digit. Pure-lowercase English
# (``centrality``, ``fallback``, ``cache``) is intentionally excluded —
# only distinctive, code-like terms are strong enough signal that a wrong
# "why" frame imported a foreign name. Mirrors the question-identifier
# shape rule in ``symbols._extract_question_identifiers``.
_FRAME_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")


def _distinctive_terms(text: str) -> set[str]:
    """Identifier-shaped terms in *text*: internal-caps, snake_case, or digit.

    A LEADING capital alone is NOT enough. Sentence-initial words and markdown
    headers (``Because``, ``Determine``, ``Mechanism``, ``Short``, ``Since``,
    ``What``) are prose, not mechanisms — they never appear verbatim in source,
    so an "any uppercase" rule flags them as ungrounded frame terms and the gate
    over-fires on the answer's own formatting. Requiring an *internal* uppercase
    letter keeps real code names (``PageRank``, ``WikiSymbol``, ``AnswerCache``,
    ``API``) while dropping capitalized English. A single leading-cap class name
    (``Repository``) is conservatively skipped too: missing a frame term only
    weakens the gate, whereas over-firing on prose breaks it.
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

    Returns ``(ungrounded, grounded_count)``. A wrong "why" frame betrays
    itself by importing a distinctive code-like term — a class, a function,
    a module — that the cited material never contained, while the surface
    facts (the number, the file) can be right. This surfaces the absent
    terms so the gate can downgrade when they are not outweighed by grounded
    ones. Terms the question itself named are excluded: echoing the user's
    own framing is not a synthesised frame.
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


# Unattributed exclusivity tokens. Words like "entirely" / "the sole" assert
# a global property ("I have seen every relevant site") that get_answer cannot
# observe from a top-k slice. They are only valid when the retrieved material
# itself makes the claim (a type constraint, an assertion, an explicit comment).
# "always" / "never" are intentionally excluded — those are temporal, not
# spatial exhaustiveness claims, and are legitimate when quoting a constraint.
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

    The co-occurrence of (1) an unattributed exclusivity token in the prose
    and (2) truncated: true on any symbol_bodies entry is the structural bug
    from issue #1444: exhaustiveness is asserted from a sample the pipeline
    knows is incomplete.

    Does not fire when no symbol body was truncated — the check gates on the
    structured flag already present in the response, so it is a no-op on the
    common case where all bodies were served whole.
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

    A bare word-boundary match is not usable here. Withheld symbols include
    ``on``, ``line``, ``input``, ``width`` and ``join``, all ordinary English,
    so ``\bon\b`` matches "based on the excerpts" and scores a harmless
    truncation as harmful. Measured across the transcripts on disk, the naive
    matcher put the harm rate 4+ points high and its hits were visibly prose.

    The call shape allows no space before the paren: ``the width (in pixels)``
    is a prose parenthetical, not a call, and admitting the space made it one.
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

    Internal capital, underscore or digit — the same shape rule
    ``_distinctive_terms`` uses. ``_validate`` and ``TodoStore`` qualify;
    ``write``, ``main``, ``line`` and ``on`` do not, and those are exactly the
    names that collapse confidence on an ordinary English question.
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

    * distinctive names (``_validate``, ``TodoStore``) match case-insensitively
      on a word boundary — no English word has that shape;
    * a leading-capital name (``Store``) matches only with its own case, so
      "the store where results land" does not implicate ``Store``, and not at
      the start of a sentence, where the capital is grammar rather than a name
      ("Run the indexer, then what?" must not implicate a symbol ``Run``);
    * an all-lowercase name (``on``, ``line``, ``write``, ``main``) needs an
      actual code context, because a bare word match on those demotes ordinary
      questions like "what is the main entry point?".
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

    Truncation on its own is not a defect: measured on the transcripts on disk,
    31% of responses truncate something and in 22% of those the withheld range
    holds nothing the response relies on. Capping confidence on the bare flag
    would spend `high` on those for no gain.

    What matters is whether a symbol the response DEPENDS ON is in the withheld
    range. Two independent routes, and the split is deliberate:

    * the **question** names it. Always present, so it fires on any answer,
      including one whose prose never mentions the symbol at all.
    * the **answer** names it in a code context. Only when synthesis ran.

    The question route is what stops this becoming the mistake the previous gate
    made: a check conditioned solely on prose stays silent on every answer whose
    prose happens not to name the symbol, which was 3 responses in 4 measured.

    It is NOT what protects the no-LLM modes, and an earlier version of this
    docstring wrongly claimed it was. Both of those paths now serve bodies:
    ``_degraded_payload`` builds them from the question's anchors, and the
    homonym-union early return inlines every definition, and neither calls this
    gate. The union path caps on truncation alone. The degraded path does not gate
    at all, because it has no synthesised claim to demote: its ``confidence`` is
    already "low" for want of prose, and what a cut body costs it is said in the
    payload instead, by the ``continuation`` on the entry and by the next action.

    ``body_continues`` entries sort first: a symbol whose body was cut by the
    truncation boundary is the sharper failure, because the response has already
    shown its signature and may reason about behaviour it never saw.
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

    RRF fusion compresses scores: a page both retrievers rank #1 barely
    outscores one they rank #2, so the numeric dominance ratio calls the *most*
    confident retrieval "non-dominant" and demotes it. This reads the per-source
    ranks instead: when two retrievers put the SAME page at (or within a rank of)
    the top, that consensus is a stronger ground-truth signal than any RRF score
    margin.

    Conservative. Requires the top hit to be found by BOTH retrievers near the
    top of each, to rank no lower than the runner-up in either source, and the
    runner-up to be meaningfully weaker. Otherwise returns False and the caller
    falls back to the pure ratio/gap gate. Agreement can only LIFT — the demotion
    gates still apply.

    ``vector_leg_keyless`` swaps the vector leg for the symbol leg. On an index
    with no semantic vectors the vector leg is skipped outright, so ``_vec_rank``
    is never written for any question and a fixed FTS+vector pair makes this
    signal permanently unreachable — every keyless answer is then graded by the
    pure ratio gate, which is exactly the gate this function exists because it
    mis-reads. The symbol leg runs on every index and records ``_sym_rank``.

    **The caller must pass the retrieval leg's own status, not infer it from
    the hits.** By the time this runs, ``hits`` is capped to the top 5 out of a
    much larger fused pool, so "no hit carries a ``_vec_rank``" is *not*
    evidence the leg was skipped: a keyed index whose vector leg timed out,
    errored, was scope-filtered, or was simply outranked by five FTS-and-symbol
    hits presents identically. Substituting on that inference would fire exactly
    when evidence is weakest and manufacture "high" confidence from it.

    The symbol pair is held to a stricter rank than the vector pair. FTS and the
    symbol leg are not independent — the wiki page FTS indexes contains the
    public symbol table the symbol leg matches on — so their agreeing is closer
    to one lexical match observed twice than to two retrievers concurring, and
    the fusion beside this already prices that leg well below the others.
    Requiring an exact rank-0 tie keeps the weaker signal from carrying the
    stronger claim.

    Note the consequence: at ``top_rank_max = 0`` the runner-up comparison below
    can no longer reject anything, because two hits cannot share rank 0 within
    one leg. The symbol pair therefore reduces exactly to "the top hit is #1 in
    FTS and #1 in the symbol leg", which is the intended rule; the shared gap
    check is retained for the vector pair, where it does constrain.
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
    # Top must be a consensus pick: found by both retrievers, near the top of
    # each. A one-retriever top hit is exactly the ambiguous case we must NOT
    # lift.
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

    The single owner of "did retrieval clearly point at ONE page". Callers that
    only need the verdict take :func:`is_dominant`; the note builder needs the
    reason, because the three tiers measure different things and a note that
    quotes the wrong one refutes itself.

    * ``"ratio"`` — the top hit outscores the runner-up by a clear multiple.
    * ``"gap"`` — where both scores are excellent a close ratio is expected
      (6.0 vs 5.4 is a clear win reading as 1.11x), so dominance is an absolute
      gap and the RATIO IS NOT THE MEASUREMENT. Quoting it as one prints a near
      tie as the reason for confidence.
    * ``"agreement"`` — both retrievers independently rank this page top. RRF
      compresses fused scores, so this fires around 1.02x; the ratio is not the
      measurement here either.
    * ``"sole_hit"`` — one hit, nothing to be ambiguous against.

    Coverage (fraction of query terms in the top hit) biases ranking but is
    deliberately NOT a gate: natural-language questions rarely put every content
    term in one page, so a coverage threshold over-fires.

    No hits at all is not dominance: there is no page for the claim to be about,
    and rating that dominant would grade an empty retrieval as merely
    under-scoring. Unreachable from the synthesised path, which returns early on
    empty hits, but the degraded path rates its retrieval through here too.
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

    Three callers ask it and they must not disagree: the confidence grade uses it
    as both the starting grade and the ceiling, :func:`_retrieval_quality` rates
    the retrieval from it, and the payload folds in the ambiguous-retrieval
    evidence on it. It used to be computed twice — a two-tier version in the
    answer module and a ratio-only re-derivation inside the grade — and the two
    disagree in a real window (a 6.0/5.4 pair is dominant by gap and not by
    ratio), which is how one payload came to assert that the top result "clearly
    dominates" while appending the caveat that retrieval found no dominant page.
    """
    return dominance_reason(hits, agreement_dominant=agreement_dominant) is not None


def _retrieval_quality(hits: list[dict], agreement_dominant: bool) -> str:
    """Rate the retrieval, independently of the text it fed.

    Kept as one function because the degraded path needs the same rating and must
    not invent a second one. That path has no synthesised text to rate (its
    ``confidence`` stays low, correctly), but it ran exactly the same retrieval,
    and "high" has to mean the same thing to a keyless caller as to a keyed one
    or the field is worth less than nothing.

    Reads :func:`is_dominant` rather than re-deriving the ratio, so "weak" means
    exactly "not dominant" — the same fact the confidence ceiling and the
    ambiguity caveat are keyed on. Without that the payload could rate retrieval
    weak and treat it as dominant in the same breath.
    """
    top_score = _confidence_score(hits[0]) if hits else 0.0
    dominant_grade = is_dominant(hits, agreement_dominant=agreement_dominant)
    if dominant_grade and top_score >= _HIGH_CONFIDENCE_SCORE_FLOOR:
        return "high"
    return "partial" if dominant_grade else "weak"


def _is_question_named_body_cut_by_us(entry: dict, question_ids: set[str]) -> bool:
    """Whether this body is the question's own symbol, cut because WE ran out of lines.

    ``truncated`` alone is not trustworthy enough to demote on. The stale-bound
    case this was written for is now fixed at its source — ``check_symbol_bounds``
    clamps every bound to the live file — but the guard still earns its keep on
    the ``source_excerpt`` fallback, where the served bytes come from the index
    rather than from disk and the live length says nothing. Requiring the served
    span to have reached the line cap says the cut was ours, which is the only
    case where something was really withheld.
    """
    if not (entry.get("truncated") and entry.get("continuation")):
        return False
    if entry.get("name") not in question_ids:
        return False
    return entry["lines"][1] - entry["lines"][0] + 1 >= _INLINE_BODY_MAX_LINES


def _is_enclosing_continuation(entry: dict, implicated: set[str]) -> bool:
    """Whether this body simply continues past the cut, rather than losing a symbol.

    A withheld entry carrying the served body's OWN name is the enclosing symbol
    continuing past the cut, not something that never arrived. Calling that "not
    served" is wrong about the payload directly above the note, and sends the
    caller to get_symbol for a body they already hold most of. The accurate
    pointer is the ``continuation`` the entry already carries.
    """
    name = entry.get("name")
    if not (entry.get("continuation") and name in implicated):
        return False
    return any(s.get("name") == name for s in (entry.get("withheld_symbols") or []))


class _Grade(NamedTuple):
    """The confidence verdict, and every finding the notes are written from.

    The gates do not just produce a label: each one that fires records WHAT
    it objected to, and the payload builder turns that into the note and the
    next action. Carrying the findings out beside the verdict is what keeps
    the two in step.
    """

    confidence: str
    hedged: bool
    ratio: float
    top_score: float
    second_score: float
    #: Why the grade is "high", or None when it is not: a
    #: :func:`dominance_reason` tier, or "symbol_body" / "grounding". The note is
    #: written from this rather than from the bare label, because the reasons
    #: license different sentences — only "ratio" may quote the ratio as the
    #: measurement, and only a dominance tier or "symbol_body" may tell the agent
    #: not to re-read. Writing one reason for every high is how a payload came to
    #: quote a 1.00x dominance ratio, a tie, as the reason it was confident.
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

    One starting grade from retrieval dominance, then a run of gates that can
    only demote it. Several are guarded on the answer still being at high, so
    that one response cannot be pushed two levels for one problem. Read them
    as a list of reasons not to trust the prose: the order is the order they
    were added, and each comment says which failure it was built to catch.

    ``dominance`` is :func:`dominance_reason`'s verdict, computed once by the
    caller and used here for the starting grade, the ceiling, and the reason the
    note is written from. A bare bool used to be accepted for the ceiling alone
    while the starting grade re-derived a ratio-only version of the same
    question, so a retrieval the rest of the pipeline treated as dominant could
    start the cascade as if it were not.
    """
    dominant = dominance is not None
    _ratio = _top_two_score_ratio(hits)
    _top_score = _confidence_score(hits[0]) if hits else 0.0
    _second_score = _confidence_score(hits[1]) if len(hits) >= 2 else 0.0

    # A response can EARN "high" on a NON-dominant retrieval, by two routes that
    # are NOT equally good and so are tracked apart. Both need the score floor,
    # and a retrieval that clears the floor and dominates is already high — so
    # earning fires exactly when `retrieval_quality` reads "weak".
    #
    # * `earned_body` — the question named a symbol and its live body is inlined
    #   in this payload. Resolved by exact name, not by ranking, so how ambiguous
    #   the ranking was does not bear on it, and "do not re-read the source" is
    #   literally true: the source is in the response. (An oversized body can
    #   still arrive cut, which is why the note it writes says "the live body"
    #   rather than "the full body" and lets the truncation tail speak.)
    # * `earned_grounding` — every distinctive mechanism term the answer names
    #   appears in the material it was shown, and a cited hit carries real symbol
    #   bodies. That is evidence the model did not FABRICATE a mechanism. It is
    #   not evidence retrieval found the right page, which is the only thing in
    #   doubt on a weak retrieval — an answer can be perfectly consistent with
    #   the wrong file. Off by default; see the env flag's comment.
    #
    # The demotion gates below (hedge, value, claim-support) still pull an earned
    # high back down.
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
        # Dominant but weak — the right file relative to its siblings, but
        # the signal isn't strong enough to trust the synthesised answer
        # without verification. Downgrade so the consumer Reads the source.
        confidence = "medium"
    else:
        confidence = "medium"

    # Second gate: downgrade when the LLM's own answer admits insufficiency.
    # Retrieval dominance only tells us we indexed the right file; it does
    # not mean the synthesized text is usable. Shipping a hedged answer with
    # confidence="high" misleads the consumer AND drags the full retrieval
    # payload through the conversation cache for no benefit.
    hedged = _answer_is_hedged(answer_text)
    if hedged:
        # A hedge means the synthesised PROSE is weak — but when the exact
        # symbol the question named is inlined in symbol_bodies (tier-0 anchor,
        # full live body), the answer's ground truth is already in-hand. Labeling
        # that "low" contradicts the payload and fires the "go Read" hint the
        # body makes unnecessary, so the agent bails to Read when it never needed
        # to. Hold such a response at medium; the note redirects the agent from
        # the hedged prose to the served body.
        confidence = "medium" if served_named_body else "low"

    # Third gate — identifier-citation gate: when the question explicitly
    # names identifiers (classes / methods / snake_case / CamelCase) and
    # NONE of the top retrieval hits contain any of those identifiers as a
    # hydrated symbol, retrieval may be pointing at plausible-but-wrong
    # files (same module family, similar vocabulary). Downgrade high->medium
    # so the consumer Reads the `fallback_targets`. Only applies when the
    # question actually names identifiers — mechanism-descriptive questions
    # (no symbol names) are unaffected.
    if confidence == "high" and question_ids:
        top_n = [h for h in hits[:_ENRICH_TOP_N_HITS] if h.get("symbols")]
        has_match = any(s.get("_matched") for h in top_n for s in (h.get("symbols") or []))
        if not has_match:
            confidence = "medium"

    # Fourth gate — value grounding: on value-shaped questions (default /
    # threshold / limit / how many), every number the answer asserts must
    # appear somewhere in the material retrieval actually contained. A
    # number synthesis produced from thin air is a factual error delivered
    # with authority — the single worst calibration failure, because the
    # consumer was told not to verify. Cap and say why.
    ungrounded_values: list[str] = []
    if not hedged and _is_value_question(question):
        ungrounded_values = _ungrounded_numbers(answer_text, hits)
        if ungrounded_values:
            # A value derived from grounded operands is not a fabrication, so
            # soften one notch instead of capping — but only on a clean high over
            # dominant retrieval (earn_high on weak retrieval stays capped), and
            # only when some OTHER asserted number is grounded. Without that
            # second clause a lone invented value would soften too, which is the
            # case this gate exists to catch. The note and next_action_hint fire
            # on ungrounded_values regardless of tier.
            grounded_sibling = len(_asserted_numbers(answer_text)) > len(ungrounded_values)
            if grounded_sibling and confidence == "high" and dominant:
                confidence = "medium"
            else:
                confidence = "low"

    # Fifth gate — citation-source gate: a high-confidence answer must cite
    # at least one page that contributed actual source material (hydrated
    # symbols with signatures/bodies), not just file summaries. Summary-only
    # grounding is how plausible-but-wrong syntheses get through.
    if confidence == "high":
        cited = set(citations)
        if not any(h.get("symbols") for h in hits if h.get("target_path") in cited):
            confidence = "medium"

    # Sixth gate — claim-support / frame grounding: a high-confidence answer
    # must name its mechanism in terms the cited material actually contains. The
    # dominance gate is generous on repo-internal questions (an anchored symbol +
    # a dominant hit clear it), so a synthesis that conflates two mechanisms —
    # right file, wrong reason/function — rides through at high confidence. The
    # tell is a distinctive code-like term (a class / function / module the
    # answer names AS the mechanism) that appears nowhere in everything retrieval
    # showed. When such terms are not outweighed by grounded ones, downgrade
    # high->medium so the consumer verifies instead of trusting.
    #
    # The original gate fired only on "why" questions, but the same failure
    # occurs on "how" questions that name the mechanism in the ANSWER, not the
    # question. Value questions have their own numeric gate above; naming/lookup
    # questions legitimately just echo the named symbol, so they are excluded.
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

    # Seventh gate — completeness scope over truncated bodies: prose asserts an
    # unqualified exclusivity claim ("entirely", "the sole", "the only") while a
    # cited symbol body arrived truncated, so the answer asserts a global
    # property from a sample it knows is incomplete.
    # Guard: only fires at high (like gates 3 / 5 / 6) so it cannot stack with
    # other downgrades and push a single response two levels for one problem.
    exclusivity_over_truncated = False
    if confidence == "high" and not hedged:
        exclusivity_over_truncated = _has_unqualified_exclusivity_over_truncated(
            answer_text, symbol_bodies
        )
        if exclusivity_over_truncated:
            confidence = "medium"

    # Eighth gate — a WITHHELD symbol the response depends on. The seventh gate
    # needs an exclusivity token in the prose as well as truncation, and across
    # repeated runs of the reference defect the token usually does not appear:
    # the rest are equally incomplete, equally "high", and it stays silent. It is
    # also inert by construction in no-LLM mode, where there is no prose to hold
    # a token.
    #
    # This gate keys on the dependency instead. It fires when a symbol in the
    # withheld range is named by the QUESTION (every mode) or referenced as code
    # by the ANSWER (LLM mode). Truncation alone is deliberately NOT enough: a
    # large minority of truncations withhold nothing the response leans on, and
    # `high` is worth keeping when it is earned.
    withheld_implicated = implicated_withheld_symbols(question, answer_text, symbol_bodies)
    if confidence == "high" and withheld_implicated:
        confidence = "medium"

    # Ninth gate — the LOOKUP half of the eighth, and a routing hole rather
    # than a threshold problem. Gate 8 asks whether the question names a
    # WITHHELD symbol; on a bare-name lookup it provably never can, because the
    # question names the symbol that was SERVED and the withheld names are that
    # symbol's own inner members. The union path caps on truncation alone for
    # exactly this shape, on the grounds that where the caller asked for a symbol
    # the bodies ARE the answer. But a name with a single definition never
    # reaches the union path — `_anchor_symbol_hits` short-circuits at
    # `len(cands) == 1` before `homonyms["union"]` is built — so it lands here
    # with the same shape and, until now, no cap. The dominance ratio is not the
    # cause and is not touched.
    #
    # Deliberately narrow. It needs the question to read as a symbol lookup
    # rather than prose, AND the truncated body to be the very symbol the
    # question named. On a prose question the body is evidence for a claim rather
    # than the answer itself, and truncation alone is a poor signal there, which
    # is why gate 8 keeps the dependency test for that population. Kept as the
    # entry rather than a flag so the note can quote the real served range and
    # continuation instead of describing the cut in the abstract.
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

    # Non-dominant ceiling: ambiguous retrieval is the calibration cost of
    # always synthesizing — the answer may be right, but with no single dominant
    # page it must never read "high" (cite without verifying). Cap at medium even
    # if every gate passed. (A non-dominant retrieval already scores <high via
    # the ratio, so this is usually a no-op; it is explicit so the
    # always-synthesize contract — "answered, but verify" — is self-documenting.)
    # Exception: an answer that EARNED high is not "cite without verifying". By
    # default that means only the served-body route — the source IS in the
    # payload — since grounded PROSE is merely consistent with material this same
    # retrieval says may be the wrong material, and an agent reads "high" as
    # permission to skip the verification that would catch precisely that. The
    # exemption is written against `earn_high` rather than `earned_body` so the
    # opt-in genuinely restores the old behaviour instead of being overruled two
    # lines later.
    if not dominant and not earn_high and confidence == "high":
        confidence = "medium"

    # Name the reason while the evidence for it is still in scope. The dominance
    # tier first, and the specific tier rather than "dominant": the gap and
    # agreement tiers fire at ratios near 1.0, so a note quoting the ratio as the
    # measurement would print a near tie as its own justification — the reported
    # defect, one layer down.
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
