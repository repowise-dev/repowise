"""Shared `_meta` envelope helpers for MCP tool responses.

Every tool can attach a small `_meta` dict to its response with timing and
optional hint text. The hint is the killer feature: a short, conservative
nudge toward the cheaper next-tool when one obviously applies. Hints are
intentionally narrow — pushing every agent toward `get_symbol` regardless of
question shape would replicate the over-trust failure mode that drove
jcodemunch's accuracy regression on alive-with-dead-exports tasks.

Rules of thumb baked into the hint generators:
  * NEVER suggest a more compact tool when the original question contains
    explanation words ("explain", "why", "how does", "what is the relationship",
    "describe").
  * Only suggest get_symbol when the agent has already pinpointed a single
    symbol or single file — never as a starting move.
  * Hints are advisory; the harness/agent is free to ignore them.
"""

from __future__ import annotations

from typing import Any

# Question patterns where narrative wiki context wins over symbol-body slicing.
# Used to suppress "use get_symbol" hints — those questions need surrounding prose.
_EXPLAIN_TOKENS = (
    "explain",
    "why ",
    "why is",
    "why does",
    "why was",
    "how does",
    "how do",
    "how is",
    "how are",
    "what is the relationship",
    "describe",
    "walk me through",
    "tell me about",
    "purpose of",
)


def is_explanation_question(question: str | None) -> bool:
    """True if the question reads like 'explain X', not 'find X'.

    Used as a guard before any hint that would push the agent toward
    symbol-level (narrower) retrieval. Conservative by design: any explanation
    cue suppresses the hint.
    """
    if not question:
        return False
    q = question.strip().lower()
    return any(tok in q for tok in _EXPLAIN_TOKENS)


def build_meta(
    *,
    timing_ms: float | None = None,
    hint: str | None = None,
    cached: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct a `_meta` envelope. All fields optional, omitted if falsy.

    Stable shape:
      {
        "timing_ms": float,   # tool wall-time (omitted if None)
        "hint":      str,     # short follow-up suggestion (omitted if None)
        "cached":    bool,    # only included when True
        ...extras
      }
    """
    out: dict[str, Any] = {}
    if timing_ms is not None:
        out["timing_ms"] = round(float(timing_ms), 2)
    if hint:
        out["hint"] = hint
    if cached:
        out["cached"] = True
    if extra:
        out.update(extra)
    return out


def context_hint(targets: list[str], compact: bool, include: set[str] | None = None) -> str | None:
    """Hint for `get_context` callers.

    Conservative: only fires when the call shape suggests the agent could
    have used a cheaper tool, AND the suggestion is unambiguously safe.
    """
    if not targets:
        return None
    # If caller requested source and got a large symbol, nudge toward Read with offset
    if include and "source" in include and len(targets) == 1:
        return None  # source mode provides its own truncation info
    return None


def symbol_hint(symbol_id: str, end_line: int, start_line: int) -> str | None:
    """Hint for source retrieval (kept for backward compat with tool_symbol.py)."""
    return None


def answer_hint(confidence: str, retrieval_count: int) -> str | None:
    """Hint for `get_answer` callers.

    Encourages verification when confidence is low; never tells the agent to
    "trust the answer" — that's the over-trust failure mode.
    """
    if confidence == "low":
        return (
            "Low confidence — Read the listed fallback_targets to verify "
            "before answering."
        )
    if retrieval_count == 0:
        return "No wiki hits — fall back to search_codebase or Grep."
    return None
