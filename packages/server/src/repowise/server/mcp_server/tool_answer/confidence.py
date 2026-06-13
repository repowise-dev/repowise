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

    corpus_parts: list[str] = []
    for h in hits or []:
        for key in ("title", "summary", "snippet", "excerpt"):
            v = h.get(key)
            if v:
                corpus_parts.append(str(v))
        for s in h.get("symbols") or []:
            for key in ("name", "signature", "docstring", "source_excerpt"):
                v = s.get(key)
                if v:
                    corpus_parts.append(str(v))
    grounded = _numbers_in("\n".join(corpus_parts))
    return sorted(asserted - grounded)
