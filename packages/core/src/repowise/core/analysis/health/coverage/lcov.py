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

from .model import CoverageReport, FileCoverage, file_coverage


def parse_lcov(text: str) -> CoverageReport:
    files: list[FileCoverage] = []

    current_path: str | None = None
    covered_lines: set[int] = set()
    total_lines: set[int] = set()
    branches_found = 0
    branches_hit = 0
    explicit_lf: int | None = None
    explicit_lh: int | None = None
    # line -> (taken, total) from BRDA records; ``-`` (never evaluated) is not taken.
    branch_lines: dict[int, tuple[int, int]] = {}

    def flush() -> None:
        nonlocal current_path, covered_lines, total_lines
        nonlocal branches_found, branches_hit, explicit_lf, explicit_lh, branch_lines
        if current_path is None:
            return
        files.append(
            file_coverage(
                current_path,
                covered_lines,
                total_lines,
                branches_found=branches_found,
                branches_hit=branches_hit,
                total=explicit_lf,
                hit=explicit_lh,
                branch_lines=branch_lines,
            )
        )
        current_path = None
        covered_lines = set()
        total_lines = set()
        branches_found = 0
        branches_hit = 0
        explicit_lf = None
        explicit_lh = None
        branch_lines = {}

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
                taken = parts[3] not in ("-", "0")
                branches_found += 1
                branches_hit += taken
                with contextlib.suppress(ValueError):
                    line_no = int(parts[0])
                    prev_taken, prev_total = branch_lines.get(line_no, (0, 0))
                    branch_lines[line_no] = (prev_taken + taken, prev_total + 1)
        elif tag == "LF":
            with contextlib.suppress(ValueError):
                explicit_lf = int(rest)
        elif tag == "LH":
            with contextlib.suppress(ValueError):
                explicit_lh = int(rest)
        elif tag == "BRF":
            with contextlib.suppress(ValueError):
                branches_found = max(branches_found, int(rest))
        elif tag == "BRH":
            with contextlib.suppress(ValueError):
                branches_hit = max(branches_hit, int(rest))

    # Some reports omit the final end_of_record.
    flush()
    return CoverageReport(source_format="lcov", files=files)


def _normalize_path(path: str) -> str:
    return Path(path.strip()).as_posix()
