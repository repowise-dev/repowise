"""LCOV ``.info`` parser.

LCOV is a flat, line-oriented format:

    TN:<test name>
    SF:<source file>
    DA:<line>,<hits>[,<checksum>]
    BRDA:<line>,<block>,<branch>,<taken>
    LF:<lines found>
    LH:<lines hit>
    BRF:<branches found>
    BRH:<branches hit>
    end_of_record

We do not depend on LF/LH/BRF/BRH being present — they are derived from
DA/BRDA when missing so partial reports still parse cleanly.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from .model import CoverageReport, FileCoverage


def parse_lcov(text: str) -> CoverageReport:
    files: list[FileCoverage] = []

    current_path: str | None = None
    covered_lines: set[int] = set()
    total_lines: set[int] = set()
    branches_found = 0
    branches_hit = 0
    has_branches = False
    explicit_lf: int | None = None
    explicit_lh: int | None = None

    def flush() -> None:
        nonlocal current_path, covered_lines, total_lines
        nonlocal branches_found, branches_hit, has_branches, explicit_lf, explicit_lh
        if current_path is None:
            return
        total = explicit_lf if explicit_lf is not None else len(total_lines)
        hit = explicit_lh if explicit_lh is not None else len(covered_lines)
        line_pct = (hit / total * 100.0) if total else 0.0
        branch_pct: float | None
        if has_branches and branches_found:
            branch_pct = branches_hit / branches_found * 100.0
        else:
            branch_pct = None
        files.append(
            FileCoverage(
                file_path=current_path,
                line_coverage_pct=round(line_pct, 2),
                branch_coverage_pct=round(branch_pct, 2) if branch_pct is not None else None,
                covered_lines=sorted(covered_lines),
                total_coverable_lines=total,
            )
        )
        current_path = None
        covered_lines = set()
        total_lines = set()
        branches_found = 0
        branches_hit = 0
        has_branches = False
        explicit_lf = None
        explicit_lh = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line == "end_of_record":
            flush()
            continue
        if ":" not in line:
            continue
        tag, _, rest = line.partition(":")
        if tag == "SF":
            current_path = _normalize_path(rest)
        elif tag == "DA":
            parts = rest.split(",")
            if len(parts) >= 2:
                try:
                    line_no = int(parts[0])
                    hits = int(parts[1])
                except ValueError:
                    continue
                total_lines.add(line_no)
                if hits > 0:
                    covered_lines.add(line_no)
        elif tag == "BRDA":
            parts = rest.split(",")
            if len(parts) == 4:
                has_branches = True
                branches_found += 1
                if parts[3] not in ("-", "0"):
                    branches_hit += 1
        elif tag == "LF":
            with contextlib.suppress(ValueError):
                explicit_lf = int(rest)
        elif tag == "LH":
            with contextlib.suppress(ValueError):
                explicit_lh = int(rest)
        elif tag == "BRF":
            try:
                branches_found = max(branches_found, int(rest))
                has_branches = True
            except ValueError:
                pass
        elif tag == "BRH":
            try:
                branches_hit = max(branches_hit, int(rest))
                has_branches = True
            except ValueError:
                pass

    # Some reports omit the final end_of_record.
    flush()
    return CoverageReport(source_format="lcov", files=files)


def _normalize_path(path: str) -> str:
    return Path(path.strip()).as_posix()
