"""Repowise normalized-JSON coverage parser.

A small, explicit JSON schema so coverage produced by *any* runner
(``pytest-cov``, ``c8``/``nyc``, ``cargo-llvm-cov``, ``go test
-coverprofile``, JaCoCo, coverlet, or a Codecov/Coveralls scrape) can be
normalized once to a single shape and fed to ``repowise health
--coverage``. Keyed by **repo-relative POSIX path**.

Schema (``format: "repowise-coverage-v1"``)::

    {
      "format": "repowise-coverage-v1",
      "commit_sha": "abc123",            # optional
      "files": {
        "src/foo.py": {
          "line_coverage_pct": 87.5,     # 0..100; derivable from covered/total
          "branch_coverage_pct": 70.0,   # optional, may be null
          "covered_lines": [1, 2, 5],    # optional explicit hit set
          "total_coverable_lines": 40    # optional; derivable from pct+covered
        },
        ...
      }
    }

``files`` may also be a **list** of the same per-file objects, each
carrying its own ``file_path``/``path`` — both shapes parse identically.

Tolerant by design: a file entry needs only enough to pin down a line
percentage. When ``line_coverage_pct`` is absent it is derived from
``covered_lines`` / ``total_coverable_lines``; when ``total_coverable_lines``
is absent it is derived from the percentage and the covered set. An entry
that pins down neither is skipped (absent, *not* zero — see Phase-7 §5).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model import CoverageReport, FileCoverage

_FORMAT_TAG = "repowise-coverage-v1"


def parse_repowise_json(text: str) -> CoverageReport:
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return CoverageReport(source_format="unknown")
    if not isinstance(data, dict):
        return CoverageReport(source_format="unknown")

    raw_files = data.get("files")
    entries: list[tuple[str | None, dict[str, Any]]] = []
    if isinstance(raw_files, dict):
        entries = [(str(k), v) for k, v in raw_files.items() if isinstance(v, dict)]
    elif isinstance(raw_files, list):
        entries = [(None, v) for v in raw_files if isinstance(v, dict)]

    files: list[FileCoverage] = []
    for key, entry in entries:
        fc = _parse_entry(key, entry)
        if fc is not None:
            files.append(fc)

    return CoverageReport(
        source_format="repowise-json",
        files=files,
        commit_sha=data.get("commit_sha") or None,
    )


def _parse_entry(key: str | None, entry: dict[str, Any]) -> FileCoverage | None:
    path = key or entry.get("file_path") or entry.get("path")
    if not path:
        return None
    path = Path(str(path).replace("\\", "/")).as_posix()

    covered = entry.get("covered_lines")
    covered_lines = sorted({int(x) for x in covered}) if isinstance(covered, (list, tuple)) else []

    total = entry.get("total_coverable_lines")
    total = int(total) if isinstance(total, (int, float)) and total >= 0 else None

    pct = entry.get("line_coverage_pct")
    pct = float(pct) if isinstance(pct, (int, float)) else None

    # Reconcile the three (pct, covered, total) so any two pin the file down.
    if pct is None:
        if total and total > 0:
            pct = len(covered_lines) / total * 100.0
        elif covered_lines:
            # Have hits but no total/pct — treat the hit set as the universe
            # (fully covered). Rare; keeps a file that only reports hits visible.
            total = len(covered_lines)
            pct = 100.0
        else:
            return None  # nothing to anchor a percentage → skip (absent != 0)
    if total is None:
        if covered_lines and pct > 0:
            total = round(len(covered_lines) * 100.0 / pct)
        else:
            total = len(covered_lines)

    branch = entry.get("branch_coverage_pct")
    branch_pct = float(branch) if isinstance(branch, (int, float)) else None

    return FileCoverage(
        file_path=path,
        line_coverage_pct=round(max(0.0, min(100.0, pct)), 2),
        branch_coverage_pct=round(branch_pct, 2) if branch_pct is not None else None,
        covered_lines=covered_lines,
        total_coverable_lines=int(total or 0),
    )
