"""Noise demotion: decision records and test file pages sink below real hits."""

from __future__ import annotations

import re

from repowise.core.test_paths import is_test_path
from repowise.server.mcp_server._page_paths import FILE_ROW_TYPES

# Same test-path rules as search_codebase's demotion, on the answer hit shape.
_TEST_QUERY_RE = re.compile(
    r"\b(test|tests|testing|tested|unit[\s-]?test|integration[\s-]?test|pytest|fixture|mock|spec)\b",
    re.IGNORECASE,
)


def demote_noise_hits(hits: list[dict], question: str, *, is_why: bool) -> list[dict]:
    """Stable-partition noise below real hits before the top-5 cap; nothing is dropped.

    Decision records are noise unless the question is why-shaped, test files
    unless it is about tests. Test support such as ``conftest.py`` is not noise.
    """
    if not hits:
        return hits
    test_focused = bool(_TEST_QUERY_RE.search(question))

    def _is_noise(h: dict) -> bool:
        pt = h.get("page_type")
        return (pt == "decision_record" and not is_why) or (
            pt in FILE_ROW_TYPES and not test_focused and is_test_path(h.get("target_path") or "")
        )

    real = [h for h in hits if not _is_noise(h)]
    noise = [h for h in hits if _is_noise(h)]
    return real + noise
