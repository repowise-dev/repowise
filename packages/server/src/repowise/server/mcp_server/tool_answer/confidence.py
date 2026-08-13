"""Confidence-signal helpers for get_answer.

The confidence / retrieval_quality gating is sequenced inline by the
orchestrator (it is interleaved with payload construction), but the hedge
detector — the gate that overrides dominance when the LLM admits it can't
answer — is a pure predicate and lives here.
"""

from __future__ import annotations

import re

from repowise.server.mcp_server.tool_answer.config import _HEDGE_MARKERS


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
    text = _FILE_LINE_REF_RE.sub(" ", answer_text or "")
    asserted = _numbers_in(text)
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
