"""Token estimation for distill — the shared 4-chars-per-token heuristic.

Every repowise subsystem reports savings on this one scale, and no subsystem
carries a tiktoken dependency to do it.

The estimator *lives* here, and ``generation.context.token_budget`` re-exports
it, rather than the other way round. Deliberate: distill is a leaf utility
with no package-level graph, while ``generation.context``'s ``__init__`` pulls
the assembler → the ingestion models → networkx (538ms). Importing four lines
of arithmetic must not cost that, on the hook path or anywhere else.
"""

from __future__ import annotations

__all__ = [
    "READ_DEFAULT_LINES",
    "READ_GUTTER_CHARS",
    "estimate_read_tokens",
    "estimate_tokens",
    "rangeless_read",
    "savings_pct",
]

#: Claude Code's Read renders content as ``cat -n`` output: a six-wide line
#: number and a tab before every line, so each line costs seven more chars.
READ_GUTTER_CHARS = 7
#: Lines one Read call returns when it is given no range.
READ_DEFAULT_LINES = 2000


def estimate_tokens(text: str) -> int:
    """Estimate token count using the 4-chars-per-token heuristic."""
    return len(text) // 4


def rangeless_read(text: str) -> str:
    """The part of *text* a Read with no offset or limit returns."""
    lines = text.splitlines(keepends=True)
    return text if len(lines) <= READ_DEFAULT_LINES else "".join(lines[:READ_DEFAULT_LINES])


def estimate_read_tokens(text: str, *, numbered: bool) -> int:
    """Tokens a Read that returns *text* puts in front of the agent.

    *numbered* adds the line-number gutter, for a reading tool that renders one.
    """
    if not numbered:
        return estimate_tokens(text)
    return (len(text) + READ_GUTTER_CHARS * len(text.splitlines())) // 4


def savings_pct(raw_tokens: int, distilled_tokens: int) -> float:
    """Percentage of tokens saved by distillation (0.0 when nothing saved)."""
    if raw_tokens <= 0:
        return 0.0
    return max(0.0, (raw_tokens - distilled_tokens) / raw_tokens * 100.0)
