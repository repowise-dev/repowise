"""Every inline marker in a file reaches the model.

``markers[:5]`` truncated the per-file list instead of batching it. The caller
runs once per file and the raw-marker fallback only fires on an exception, so
markers past the fifth were never structured, never fell back, and left no log
line — they simply did not exist.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

from repowise.core.analysis.decisions.extractor import DecisionExtractor

_MARKER_LINE_RE = re.compile(r"--- Marker \(\w+\) at line (\d+) ---")

_MARKER_COUNT = 12


def _source() -> str:
    lines = []
    for i in range(_MARKER_COUNT):
        lines.append(f"def fn{i}():")
        lines.append(f"    # DECISION: choose strategy {i} because it is faster")
        lines.append(f"    return {i}")
    return "\n".join(lines) + "\n"


class _RecordingProvider:
    """Answers each call with a decision per marker line in that prompt."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate(self, system: str, prompt: str, **kwargs: object) -> SimpleNamespace:
        self.prompts.append(prompt)
        # Matched on the marker header specifically. Splitting on "at line "
        # also hits the ±20 lines of source context each marker carries, and
        # the resulting ValueError would be swallowed by the raw-marker
        # fallback — the test would stay green while measuring nothing.
        lines = [int(n) for n in _MARKER_LINE_RE.findall(prompt)]
        return SimpleNamespace(
            content=json.dumps(
                [
                    {
                        "marker_line": line,
                        "title": f"Strategy at {line}",
                        "decision": f"choose strategy at {line}",
                    }
                    for line in lines
                ]
            )
        )


async def test_all_markers_are_structured_not_just_the_first_five(tmp_path):
    (tmp_path / "app.py").write_text(_source(), encoding="utf-8")
    provider = _RecordingProvider()

    decisions = await DecisionExtractor(
        repo_path=tmp_path, provider=provider
    ).scan_inline_markers()

    assert len(decisions) == _MARKER_COUNT
    # 12 markers at 5 per call.
    assert len(provider.prompts) == 3
    # Every marker line is represented exactly once.
    lines = sorted(d.evidence_line for d in decisions)
    assert lines == sorted(2 + 3 * i for i in range(_MARKER_COUNT))
    # These came from the structuring path, not the raw-marker fallback. Count
    # and line numbers alone cannot tell the two apart: on any exception the
    # fallback emits one record per marker with these same lines, so without
    # this the test passes with LLM structuring entirely broken.
    assert all(d.confidence == 0.95 for d in decisions)
    assert all(d.title.startswith("Strategy at ") for d in decisions)
