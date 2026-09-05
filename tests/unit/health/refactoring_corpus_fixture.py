"""Loader for the checked-in Extract Method soundness corpus.

The corpus lives in ``tests/fixtures/refactoring_corpus``: archetype sources
plus one golden of the spans the slicer offers for each. A characterization run
needs no repository download, no index, and no network.

Each archetype marks its regions with a ``unsound:`` / ``sound:`` comment on
the line above the region's first statement, so the contract is readable in the
source rather than restated as line numbers in a test.

Regenerate the golden with ``REPOWISE_REWRITE_REFACTORING_GOLDEN=1``; never
hand-edit it.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

CORPUS_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "refactoring_corpus"
REWRITE_ENV = "REPOWISE_REWRITE_REFACTORING_GOLDEN"

# One source file per grammar. A language whose dataflow dialect is missing on
# the running interpreter is skipped by the test, not counted as clean.
CASE_FILES: tuple[tuple[str, str], ...] = (("python", "cases.py"), ("typescript", "cases.ts"))

_MARKER = re.compile(r"^\s*(?://|#)\s*(sound|unsound):")


def rewrite_requested() -> bool:
    return os.environ.get(REWRITE_ENV, "") not in {"", "0", "false", "False"}


def source_for(filename: str) -> bytes:
    return (CORPUS_DIR / filename).read_bytes()


def marked_lines(filename: str, kind: str) -> list[int]:
    """1-indexed lines of the statements a ``kind:`` comment marks."""
    text = (CORPUS_DIR / filename).read_text(encoding="utf-8").splitlines()
    return [
        index + 2
        for index, line in enumerate(text)
        if (match := _MARKER.match(line)) and match.group(1) == kind
    ]


def extractions_for(language: str, filename: str) -> dict[str, list[dict[str, Any]]]:
    """Every span the slicer offers, per function, for one corpus file.

    Keyed by function name so a golden diff names the archetype that moved.
    """
    from repowise.core.analysis.health.complexity.languages import get_language_map
    from repowise.core.analysis.health.dataflow.analyze import analyze_file
    from repowise.core.analysis.health.dataflow.slice import find_extractions

    lmap = get_language_map(language)
    result = analyze_file(filename, language, source_for(filename), flagged_only=False)
    out: dict[str, list[dict[str, Any]]] = {}
    for analysis in result.functions:
        out[analysis.name] = [
            {
                "start_line": item.start_line,
                "end_line": item.end_line,
                "params": list(item.params),
                "returns": list(item.returns),
                "slice_nloc": item.slice_nloc,
                "ccn_removed": item.ccn_removed,
            }
            for item in find_extractions(analysis, lmap)
        ]
    return out


def offered_start_lines(language: str, filename: str) -> set[int]:
    spans = extractions_for(language, filename)
    return {item["start_line"] for rows in spans.values() for item in rows}


def load_golden(name: str) -> Any:
    return json.loads((CORPUS_DIR / name).read_text(encoding="utf-8"))


def write_golden(name: str, payload: Any) -> None:
    with (CORPUS_DIR / name).open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
