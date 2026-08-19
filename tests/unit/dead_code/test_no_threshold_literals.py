"""The dead-code tier boundaries are compared against constants, never literals.

``SAFE_CONFIDENCE_THRESHOLD`` (0.7) and ``RISK_CAP_CONFIDENCE`` (0.4) own the
high/medium boundaries. They were re-typed as bare literals in thirteen places
across three modules that all already imported from ``risk_factors``, or in one
case named the constant in the very string beside the literal.

This *shape* has already shipped a bug, though not on the Python side: the
comment in ``packages/types/src/dead-code.ts`` records three TypeScript surfaces
disagreeing, which put one 0.72 finding in "high" on the summary card and
"Medium" in the breakdown grid at once. That was fixed by giving TypeScript one
owner (#1087). No user-visible bug is known to have come from the Python
copies — they agreed with the constants — which is the argument for removing
them while they still do.

``test_confidence_parity.py`` guards the TypeScript mirror, which cannot import
from Python. This guards the Python side, where importing is available and so a
literal is never the right answer.

``get_dead_code`` brackets its own tiers at 0.8 / 0.5, not at these two, and
that is deliberate — an agent acts on the answer with less review, so the bar
is higher (``docs/layers/DEAD_CODE.md``). Those numbers are a different
decision with a different owner and must not be folded into these constants;
``tool_dead_code.py`` is guarded here only against the 0.7 / 0.4 pair.

Ceiling: matched on source text, over a named file set. It catches the shapes
that recurred — a comparison, a ``min()`` cap, or a ``min_confidence`` default
written against the number instead of the name — and stays blind to a threshold
spelled some other way (``max()``, ``== 0.7``), or appearing in a module not
listed below. It also does not skip docstrings, so a docstring quoting
``>= 0.7`` inside a guarded module would false-positive; that is the safe
direction and no such docstring exists today. Assignments are deliberately not
matched: the git-age evidence ladder in ``_score_unreachable_file`` sets 0.7 and
0.4 as rungs of a scale, not as boundaries, and must not move when a boundary
does.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_PACKAGES = pathlib.Path(__file__).resolve().parents[3] / "packages"

#: Modules that decide, cap, or bucket by the dead-code confidence tiers.
_GUARDED = [
    "core/src/repowise/core/analysis/dead_code/analyzer.py",
    "core/src/repowise/core/analysis/dead_code/name_occurrences.py",
    "core/src/repowise/core/analysis/dead_code/risk_factors.py",
    "core/src/repowise/core/persistence/crud/analysis/dead_code.py",
    "cli/src/repowise/cli/commands/dead_code_cmd.py",
    "server/src/repowise/server/routers/dead_code.py",
    "server/src/repowise/server/mcp_server/tool_dead_code.py",
    "server/src/repowise/server/chat_tools.py",
]

#: ``0.7``, ``0.70``, ``0.4``, ``0.400`` — the same number, spelled loosely.
_N = r"0\.[47]0*"

_BANNED = (
    # ``confidence >= 0.7``, ``0.4 <= f.confidence``, ``confidence < 0.4``
    re.compile(rf"[<>]=?\s*{_N}\b"),
    # ``min(confidence, 0.4)`` / ``max(confidence, 0.7)``
    re.compile(rf"\b(?:min|max)\([^)]*\b{_N}\b"),
    # ``cfg.get("min_confidence", 0.4)``, ``min_confidence: float = 0.4``
    re.compile(rf"min_confidence\b[^\n]*?=\s*{_N}\b|min_confidence\"\s*,\s*{_N}\b"),
    # A JSON-schema default two lines under a ``"min_confidence"`` key, which
    # is how the thirteenth site hid: no ``min_confidence`` token on its own
    # line for the rule above to see.
    re.compile(rf"\"default\"\s*:\s*{_N}\b"),
)


def _offending_lines(text: str) -> list[str]:
    hits = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if any(pattern.search(line) for pattern in _BANNED):
            hits.append(stripped)
    return hits


@pytest.mark.parametrize("relative", _GUARDED)
def test_no_bare_threshold_literal(relative: str) -> None:
    path = _PACKAGES / relative
    assert path.exists(), f"guarded module moved or was renamed: {relative}"

    offenders = _offending_lines(path.read_text(encoding="utf-8"))
    assert not offenders, (
        f"{relative} compares against a dead-code confidence threshold by value. "
        "Import SAFE_CONFIDENCE_THRESHOLD / RISK_CAP_CONFIDENCE from "
        "repowise.core.analysis.dead_code.risk_factors instead:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize(
    "probe",
    [
        "        safe = confidence >= 0.7",
        "        low = sum(1 for f in findings if f.confidence < 0.4)",
        "        medium = sum(1 for f in findings if 0.4 <= f.confidence < 0.7)",
        "            confidence = min(confidence, 0.4)",
        '        min_conf = cfg.get("min_confidence", 0.4)',
        '                    "default": 0.4,',
        # Not removed by this change; the shape a consumer would regress *to*
        # if it stopped importing the constant for its signature default.
        "    min_confidence: float = 0.4,",
        # Loose spellings of the same two numbers.
        "        safe = confidence >= 0.70",
        "            confidence = min(confidence, 0.400)",
    ],
)
def test_guard_catches_the_shapes_that_shipped(probe: str) -> None:
    """The first six are lines this change removed, verbatim; see the comments
    above for the three that are near-misses rather than history."""
    assert _offending_lines(probe) == [probe.strip()]


def test_guard_ignores_the_evidence_ladder() -> None:
    """Assigning 0.7 as a git-age score is not a boundary and stays legal."""
    assert _offending_lines("            confidence = 0.7\n        confidence = 0.4") == []
