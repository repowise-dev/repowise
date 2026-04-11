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


def context_hint(targets: list[str], compact: bool) -> str | None:
    """Hint for `get_context` callers.

    Conservative: only fires when the call shape suggests the agent could
    have used a cheaper tool, AND the suggestion is unambiguously safe.
    """
    if compact:
        # Already in compact mode — don't push further.
        return None
    if not targets:
        return None
    # Single file target where the agent is likely to follow up with a Read:
    # nudge toward get_symbol so they slice instead of reading the whole file.
    if len(targets) == 1 and "::" not in targets[0] and "/" in targets[0]:
        return (
            "If you only need one function from this file, call "
            "get_symbol(symbol_id='{path}::{name}') to get just that "
            "function body — cheaper than Read.".format(
                path=targets[0], name="<symbol_name>"
            )
        )
    return None


def symbol_hint(symbol_id: str, end_line: int, start_line: int) -> str | None:
    """Hint for `get_symbol` callers.

    Suggests context_lines expansion only for very small symbols where the
    body alone may be insufficient.
    """
    span = max(end_line - start_line, 0)
    if span < 5:
        return (
            "Small symbol — pass context_lines=10 if you need surrounding "
            "context (imports, sibling defs)."
        )
    return None


def callers_hint(caller_count: int, callee_count: int) -> str | None:
    """Hint for `get_callers_callees` callers."""
    if caller_count + callee_count == 0:
        return "No call edges found — this symbol may be unused or only called dynamically."
    if caller_count > 10:
        return "Many callers — use get_risk to assess change impact."
    return "Use get_symbol(symbol_id) to read the source of any caller/callee."


def community_hint(neighbor_count: int) -> str | None:
    """Hint for `get_community` callers."""
    if neighbor_count > 3:
        return "This community has many cross-boundary connections — refactoring may have wide impact."
    return None


def metrics_hint(node_type: str, node_id: str) -> str | None:
    """Hint for `get_graph_metrics` callers."""
    if node_type == "file":
        return f"Use get_community('{node_id}') to see all community members."
    if node_type == "symbol":
        return f"Use get_callers_callees('{node_id}') to explore call relationships."
    return None


def flows_hint() -> str | None:
    """Hint for `get_execution_flows` callers."""
    return "Use get_callers_callees on any trace node for detail."


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
