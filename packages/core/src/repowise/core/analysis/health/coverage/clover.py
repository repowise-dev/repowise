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
is derived from ``type="cond"`` lines, whose two attributes mean one of two
things depending on the writer:

* evaluation counts (the original format): ``truecount`` / ``falsecount``
  are how often the condition was true / false, so a line has two branches
  and each is taken when its count is above zero;
* branch counts: ``truecount`` is branches covered and ``falsecount``
  branches not covered, so the line has ``truecount + falsecount``
  branches. That writer marks its reports with a fixed
  ``<project name="All files">`` and ``clover="3.2.0"`` on the root; both
  must be present.
"""

from __future__ import annotations

from pathlib import Path

from .model import CoverageReport, FileCoverage, file_coverage, parse_xml

#: The markers the branch-count writer always emits (see the module docstring).
_BRANCH_COUNT_PROJECT = "All files"
_BRANCH_COUNT_VERSION = "3.2.0"


def parse_clover(text: str) -> CoverageReport:
    root = parse_xml(text)
    if root is None:
        return CoverageReport(source_format="clover", files=[])

    project = root.find("project")
    branch_counts = (
        project is not None
        and project.get("name") == _BRANCH_COUNT_PROJECT
        and root.get("clover") == _BRANCH_COUNT_VERSION
    )
    files: list[FileCoverage] = []
    for file_el in root.iter("file"):
        path = file_el.get("path") or file_el.get("name") or ""
        if not path:
            continue

        covered: set[int] = set()
        coverable: set[int] = set()
        branches_found = 0
        branches_hit = 0
        branch_lines: dict[int, tuple[int, int]] = {}

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
                taken, total = (tc, tc + fc) if branch_counts else ((tc > 0) + (fc > 0), 2)
                branches_found += total
                branches_hit += taken
                if total:
                    branch_lines[line_no] = (taken, total)

        files.append(
            file_coverage(
                Path(path).as_posix(),
                covered,
                coverable,
                branches_found=branches_found,
                branches_hit=branches_hit,
                branch_lines=branch_lines,
            )
        )

    return CoverageReport(source_format="clover", files=files)
