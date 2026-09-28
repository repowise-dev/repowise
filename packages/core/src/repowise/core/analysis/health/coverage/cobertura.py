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

from .model import CoverageReport, file_coverage, parse_xml

_CONDITION_RE = re.compile(r"\((\d+)\s*/\s*(\d+)\)")


def parse_cobertura(text: str) -> CoverageReport:
    root = parse_xml(text)
    if root is None:
        return CoverageReport(source_format="cobertura", files=[])

    # Aggregate by filename: several <class> rows can share one file.
    per_file: dict[str, _FileLines] = {}

    for cls in root.iter("class"):
        filename = cls.get("filename") or ""
        if not filename:
            continue
        bucket = per_file.setdefault(Path(filename).as_posix(), _FileLines())
        for line in cls.iter("line"):
            try:
                line_no = int(line.get("number", "0"))
                hits = int(line.get("hits", "0"))
            except ValueError:
                continue
            if line_no <= 0:
                continue
            bucket.coverable.add(line_no)
            if hits > 0:
                bucket.covered.add(line_no)

            if line.get("branch") == "true":
                m = _CONDITION_RE.search(line.get("condition-coverage", ""))
                if m:
                    bucket.branches_hit += int(m.group(1))
                    bucket.branches_found += int(m.group(2))
                else:
                    bucket.branches_found += 2
                    bucket.branches_hit += 2 if hits > 0 else 0

    files = [
        file_coverage(
            path,
            b.covered,
            b.coverable,
            branches_found=b.branches_found,
            branches_hit=b.branches_hit,
        )
        for path, b in per_file.items()
    ]
    return CoverageReport(source_format="cobertura", files=files)


@dataclass
class _FileLines:
    covered: set[int] = field(default_factory=set)
    coverable: set[int] = field(default_factory=set)
    branches_found: int = 0
    branches_hit: int = 0
