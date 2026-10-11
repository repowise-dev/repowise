"""Generic log compaction via template collapse.

Lines are normalized (timestamps, numbers, hex ids -> placeholders) into
templates; repeats keep their first occurrence annotated with a count.
Errors are sacred: every ERROR/FATAL/CRITICAL line survives verbatim.
Deliberately the lowest-priority filter — it only sniffs content that
really looks like logs, so it never shadows the structured filters.
"""

from __future__ import annotations

import re
from typing import ClassVar

from repowise.core.distill.filters.base import OutputFilter
from repowise.core.distill.registry import filter_registry

_COMMAND_RE = re.compile(
    r"^(tail\b|journalctl\b|docker(?:\.exe)? logs\b|kubectl(?:\.exe)? logs\b|"
    r"(?:cat|type|gc|get-content)\b.*\.log\b)"
)

_LEVEL_RE = re.compile(r"\b(CRITICAL|FATAL|ERROR|ERR|WARNING|WARN|INFO|DEBUG|TRACE)\b")
_SEVERE_RE = re.compile(r"\b(CRITICAL|FATAL|ERROR|ERR)\b")
_TS_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
    r"|\d{2}:\d{2}:\d{2}(?:[.,]\d+)?"
    r"|\[\d{4}-\d{2}-\d{2}[^\]]*\]"
)
_HEX_RE = re.compile(r"\b[0-9a-fA-F]{8,}\b")
_NUM_RE = re.compile(r"\b\d+(?:\.\d+)?\b")

_MAX_LINES = 100


@filter_registry.register
class LogsFilter(OutputFilter):
    name: ClassVar[str] = "logs"
    priority: ClassVar[int] = 90
    min_lines: ClassVar[int] = 40

    def matches_command(self, command: str) -> bool:
        return bool(_COMMAND_RE.match(command))

    def matches_content(self, output: str) -> bool:
        lines = [ln for ln in output.splitlines() if ln.strip()]
        if len(lines) < self.min_lines:
            return False
        loggy = sum(1 for ln in lines if _LEVEL_RE.search(ln) or _TS_RE.search(ln))
        return loggy / len(lines) > 0.6

    def distill(self, output: str, *, command: str = "", exit_code: int = 0) -> str:
        lines = output.splitlines()
        kept: list[tuple[int, str]] = []  # (original index, line)
        counts: dict[str, int] = {}
        first_idx: dict[str, int] = {}  # template -> position in kept
        severe = 0

        for i, line in enumerate(lines):
            if not line.strip():
                continue
            if _SEVERE_RE.search(line):
                severe += 1
                kept.append((i, line))
                continue
            template = _template(line)
            if template in counts:
                counts[template] += 1
            else:
                counts[template] = 1
                first_idx[template] = len(kept)
                kept.append((i, line))

        if severe == 0 and len(counts) == len([ln for ln in lines if ln.strip()]):
            # Nothing collapsed and nothing severe: not actually repetitive logs.
            raise ValueError("log output with no repetition to collapse")

        rendered: list[str] = []
        for pos, (_, line) in enumerate(kept):
            template_hits = [t for t, p in first_idx.items() if p == pos]
            if template_hits and counts[template_hits[0]] > 1:
                rendered.append(f"{line}  (x{counts[template_hits[0]]} similar)")
            else:
                rendered.append(line)

        if len(rendered) > _MAX_LINES:
            # Keep head/tail plus every severe line in between.
            head, tail = 30, 40
            middle = rendered[head:-tail]
            severe_middle = [ln for ln in middle if _SEVERE_RE.search(ln)]
            elided = len(middle) - len(severe_middle)
            rendered = (
                rendered[:head]
                + severe_middle
                + [f"  ... ({elided} distinct log lines elided) ..."]
                + rendered[-tail:]
            )
        return "\n".join(rendered)


_KV_RE = re.compile(r"(?<==)[^\s,;\]]+")
_QUOTED_RE = re.compile(r'"[^"]*"|\'[^\']*\'')


def _template(line: str) -> str:
    """Normalize volatile fields so repeated events share one template.

    Timestamps, numbers, hex ids, quoted strings, and ``key=value`` values all
    become placeholders — "parsed file path=a.py took_ms=3" and
    "parsed file path=b.py took_ms=9" collapse into one event template.
    """
    t = _TS_RE.sub("<ts>", line)
    t = _QUOTED_RE.sub("<q>", t)
    t = _KV_RE.sub("<v>", t)
    t = _HEX_RE.sub("<hex>", t)
    t = _NUM_RE.sub("<n>", t)
    return t
