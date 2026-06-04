"""Output-filter base class and shared line-classification helpers.

Filters are pure: they receive raw command output and return a compact
rendering. They never touch the omission store or markers — the engine in
``repowise.core.distill`` owns persistence, marker rendering, savings
accounting, and the fallback-to-raw guarantee.

The cardinal invariant for every filter: **no error line is ever dropped.**
``is_error_line`` is deliberately greedy — keeping a few extra lines is
cheap, losing the one line that explains a failure is not.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import ClassVar

# Lines that must survive every filter. Greedy on purpose.
ERROR_LINE_RE = re.compile(
    r"(?i)("
    r"\b(error|errors|failed|failure|failures|fatal|panic|panicked|exception|"
    r"traceback|assert|assertion|assertionerror|denied|refused|unreachable)\b"
    r"|error\[E\d+\]"
    r"|--- FAIL"
    r"|\bFAIL\b"
    r"|^\s*[✗✕×✘]"  # jest/vitest failed-test glyphs  # noqa: RUF001
    r")"
)


def is_error_line(line: str) -> bool:
    """True when *line* carries failure information and must never be dropped."""
    return bool(ERROR_LINE_RE.search(line))


def cap_block(lines: list[str], head: int, tail: int) -> list[str]:
    """Cap *lines* to roughly *head* + *tail*, never dropping error lines.

    Middle lines classified as errors are retained between the anchors, and an
    inline ellipsis notes how many lines were elided. Returns the input
    unchanged when it already fits.
    """
    if len(lines) <= head + tail:
        return list(lines)
    middle = lines[head : len(lines) - tail]
    kept_middle = [ln for ln in middle if is_error_line(ln)]
    elided = len(middle) - len(kept_middle)
    capped = lines[:head]
    capped.extend(kept_middle)
    if elided > 0:
        capped.append(f"  ... ({elided} lines elided) ...")
    capped.extend(lines[len(lines) - tail :])
    return capped


class OutputFilter(ABC):
    """One self-contained compaction strategy for a family of command output."""

    #: Registry name; also used as the savings-ledger / store source label.
    name: ClassVar[str]
    #: Lower runs first when several filters match (content sniffing order).
    priority: ClassVar[int] = 100
    #: Outputs shorter than this many lines are never worth distilling.
    min_lines: ClassVar[int] = 8

    def matches_command(self, command: str) -> bool:
        """True when *command* (normalized, lowercase) is in this family."""
        return False

    def matches_content(self, output: str) -> bool:
        """Content sniff for when the command string is absent or ambiguous."""
        return False

    @abstractmethod
    def distill(self, output: str, *, command: str = "", exit_code: int = 0) -> str:
        """Return the compact rendering of *output*.

        May raise on unrecognized input — the engine falls back to raw.
        Must preserve every line for which :func:`is_error_line` is true.
        """
