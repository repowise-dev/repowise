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

from .model import CoverageReport, FileCoverage, file_coverage, parse_xml


def parse_clover(text: str) -> CoverageReport:
    root = parse_xml(text)
    if root is None:
        return CoverageReport(source_format="clover", files=[])

    files: list[FileCoverage] = []
    for file_el in root.iter("file"):
        path = file_el.get("path") or file_el.get("name") or ""
        if not path:
            continue

        covered: set[int] = set()
        coverable: set[int] = set()
        branches_found = 0
        branches_hit = 0

        for line in file_el.iter("line"):
            try:
                line_no = int(line.get("num", "0"))
            except ValueError:
                continue
            if line_no <= 0:
                continue
            try:
                count = int(line.get("count", "0"))
            except ValueError:
                count = 0
            coverable.add(line_no)
            if count > 0:
                covered.add(line_no)
            if line.get("type", "stmt") == "cond":
                try:
                    tc = int(line.get("truecount", "0"))
                    fc = int(line.get("falsecount", "0"))
                except ValueError:
                    tc, fc = 0, 0
                branches_found += 2
                branches_hit += (1 if tc > 0 else 0) + (1 if fc > 0 else 0)

        files.append(
            file_coverage(
                Path(path).as_posix(),
                covered,
                coverable,
                branches_found=branches_found,
                branches_hit=branches_hit,
            )
        )

    return CoverageReport(source_format="clover", files=files)
