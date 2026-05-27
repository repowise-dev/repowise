"""Confidence-signal helpers for get_answer.

The confidence / retrieval_quality gating is sequenced inline by the
orchestrator (it is interleaved with payload construction), but the hedge
detector — the gate that overrides dominance when the LLM admits it can't
answer — is a pure predicate and lives here.
"""

from __future__ import annotations

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
