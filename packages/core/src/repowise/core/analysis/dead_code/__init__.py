"""Dead code detection for repowise.

Pure graph traversal + git metadata. No LLM calls. Must complete in
< 10 seconds on real repos.

Public API (preserved for backwards compatibility — all callers import
from ``repowise.core.analysis.dead_code``):

- ``DeadCodeAnalyzer`` — the orchestrator class.
- ``DeadCodeKind`` — enum of finding kinds.
- ``DeadCodeFindingData`` — per-finding payload.
- ``DeadCodeReport`` — aggregate report returned by ``analyze()``.

Internal layout (Phase 1 refactor):

- ``analyzer.py``         — ``DeadCodeAnalyzer`` class + four detection methods.
- ``models.py``           — public dataclasses + ``DeadCodeKind`` enum.
- ``constants.py``        — never-flag globs, framework decorators, fixture
                            path segments, non-code languages.
- ``dynamic_markers.py``  — ``_DYNAMIC_IMPORT_MARKERS`` source-text dict
                            + ``find_dynamic_import_files()`` scanner.
                            Phase 2 (A1/A2) work lands here.
"""

from __future__ import annotations

from .analyzer import DeadCodeAnalyzer
from .models import DeadCodeFindingData, DeadCodeKind, DeadCodeReport

__all__ = [
    "DeadCodeAnalyzer",
    "DeadCodeFindingData",
    "DeadCodeKind",
    "DeadCodeReport",
]
