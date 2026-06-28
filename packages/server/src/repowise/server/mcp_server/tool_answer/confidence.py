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
    """
    low = (answer_text or "").lower()
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
