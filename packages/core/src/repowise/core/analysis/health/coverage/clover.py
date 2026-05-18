"""Clover XML parser (stdlib ``xml.etree``).

Clover layout (abbreviated):

    <coverage generated="...">
      <project>
        <package name="...">
          <file path="/abs/or/rel/path/file.ts">
            <metrics statements="10" coveredstatements="7"
                     conditionals="4" coveredconditionals="2" ... />
            <line num="1" count="3" type="stmt"/>
            <line num="2" count="0" type="stmt"/>
            <line num="3" count="2" type="cond" truecount="1" falsecount="1"/>
          </file>
        </package>
      </project>
    </coverage>

We trust the per-line elements over the ``<metrics>`` summary so coverage
percentages match what gets highlighted in the dashboard. Branch coverage
is derived from ``type="cond"`` lines (truecount + falsecount).
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from .model import CoverageReport, FileCoverage


def parse_clover(text: str) -> CoverageReport:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return CoverageReport(source_format="clover", files=[])

    files: list[FileCoverage] = []
    for file_el in root.iter("file"):
        path = file_el.get("path") or file_el.get("name") or ""
        if not path:
            continue
        norm = Path(path).as_posix()

        covered: set[int] = set()
        total: set[int] = set()
        branches_found = 0
        branches_hit = 0
        has_branches = False

        for line in file_el.iter("line"):
            try:
                line_no = int(line.get("num", "0"))
            except ValueError:
                continue
            if line_no <= 0:
                continue
            ltype = line.get("type", "stmt")
            try:
                count = int(line.get("count", "0"))
            except ValueError:
                count = 0
            total.add(line_no)
            if count > 0:
                covered.add(line_no)
            if ltype == "cond":
                has_branches = True
                try:
                    tc = int(line.get("truecount", "0"))
                    fc = int(line.get("falsecount", "0"))
                except ValueError:
                    tc, fc = 0, 0
                branches_found += 2
                branches_hit += (1 if tc > 0 else 0) + (1 if fc > 0 else 0)

        total_n = len(total)
        line_pct = (len(covered) / total_n * 100.0) if total_n else 0.0
        branch_pct: float | None
        if has_branches and branches_found:
            branch_pct = branches_hit / branches_found * 100.0
        else:
            branch_pct = None

        files.append(
            FileCoverage(
                file_path=norm,
                line_coverage_pct=round(line_pct, 2),
                branch_coverage_pct=round(branch_pct, 2) if branch_pct is not None else None,
                covered_lines=sorted(covered),
                total_coverable_lines=total_n,
            )
        )

    return CoverageReport(source_format="clover", files=files)
