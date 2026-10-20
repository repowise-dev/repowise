"""Cobertura XML parser (stdlib ``xml.etree`` only).

Cobertura layout (abbreviated):

    <coverage line-rate="0.83" branch-rate="0.5" ...>
      <sources><source>/abs/path</source></sources>
      <packages>
        <package name="pkg" line-rate="..." branch-rate="...">
          <classes>
            <class filename="pkg/file.py" line-rate="..." branch-rate="...">
              <lines>
                <line number="3" hits="2" branch="false"/>
                <line number="5" hits="0" branch="true"
                      condition-coverage="50% (1/2)"/>
              </lines>
            </class>
          </classes>
        </package>
      </packages>
    </coverage>

We aggregate per ``filename`` because some Cobertura producers (notably
``coverage.py``'s XML output) emit multiple ``<class>`` rows for the
same file (one per top-level class).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from .model import CoverageReport, FileCoverage, file_coverage, parse_xml

_CONDITION_RE = re.compile(r"\((\d+)\s*/\s*(\d+)\)")


def parse_cobertura(text: str) -> CoverageReport:
    root = parse_xml(text)
    if root is None:
        return CoverageReport(source_format="cobertura", files=[])

    # Aggregate by filename: several <class> rows can share one file.
    per_file: dict[str, _FileLines] = {}
    for cls in root.iter("class"):
        filename = cls.get("filename") or ""
        if filename:
            bucket = per_file.setdefault(Path(filename).as_posix(), _FileLines())
            for line in cls.iter("line"):
                bucket.add_line(line)

    # Filenames are relative to one of these; the resolver tries each.
    roots = tuple(
        dict.fromkeys(s.text.strip() for s in root.iter("source") if s.text and s.text.strip())
    )
    files = [b.file_coverage(path) for path, b in per_file.items()]
    return CoverageReport(source_format="cobertura", files=files, source_roots=roots)


def _line_hits(line: ET.Element) -> tuple[int, int] | None:
    """``(line number, hits)`` of a ``<line>``, ``None`` when unreadable."""
    try:
        line_no = int(line.get("number", "0"))
        hits = int(line.get("hits", "0"))
    except ValueError:
        return None
    return (line_no, hits) if line_no > 0 else None


def _condition(line: ET.Element) -> tuple[int, int] | None:
    """``(taken, total)`` from ``condition-coverage="50% (1/2)"``, ``None`` without it."""
    m = _CONDITION_RE.search(line.get("condition-coverage", ""))
    return (int(m.group(1)), int(m.group(2))) if m else None


@dataclass
class _FileLines:
    covered: set[int] = field(default_factory=set)
    coverable: set[int] = field(default_factory=set)
    # Branches on lines without ``condition-coverage`` (two assumed per line).
    branches_found: int = 0
    branches_hit: int = 0
    branch_lines: dict[int, tuple[int, int]] = field(default_factory=dict)

    def add_line(self, line: ET.Element) -> None:
        parsed = _line_hits(line)
        if parsed is None:
            return
        line_no, hits = parsed
        self.coverable.add(line_no)
        if hits > 0:
            self.covered.add(line_no)
        if line.get("branch") == "true":
            self._add_branches(line_no, hits, _condition(line))

    def _add_branches(self, line_no: int, hits: int, condition: tuple[int, int] | None) -> None:
        if condition is None:
            # No per-line figure to keep: the file total assumes two branches.
            self.branches_found += 2
            self.branches_hit += 2 if hits > 0 else 0
            return
        taken, total = condition
        if total:
            # A line repeated across <class> rows takes the max, never the sum.
            prev_taken, prev_total = self.branch_lines.get(line_no, (0, 0))
            self.branch_lines[line_no] = (max(prev_taken, taken), max(prev_total, total))

    def file_coverage(self, path: str) -> FileCoverage:
        return file_coverage(
            path,
            self.covered,
            self.coverable,
            branches_found=self.branches_found + sum(t for _, t in self.branch_lines.values()),
            branches_hit=self.branches_hit + sum(h for h, _ in self.branch_lines.values()),
            branch_lines=self.branch_lines,
        )
