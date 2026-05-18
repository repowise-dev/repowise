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
from pathlib import Path
from xml.etree import ElementTree as ET

from .model import CoverageReport, FileCoverage

_CONDITION_RE = re.compile(r"\((\d+)\s*/\s*(\d+)\)")


def parse_cobertura(text: str) -> CoverageReport:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return CoverageReport(source_format="cobertura", files=[])

    # Aggregate by filename.
    per_file: dict[str, dict[str, object]] = {}

    for cls in root.iter("class"):
        filename = cls.get("filename") or ""
        if not filename:
            continue
        norm = Path(filename).as_posix()
        bucket = per_file.setdefault(
            norm,
            {
                "covered_lines": set(),
                "total_lines": set(),
                "branches_found": 0,
                "branches_hit": 0,
                "has_branches": False,
            },
        )
        for line in cls.iter("line"):
            try:
                line_no = int(line.get("number", "0"))
                hits = int(line.get("hits", "0"))
            except ValueError:
                continue
            if line_no <= 0:
                continue
            assert isinstance(bucket["total_lines"], set)
            assert isinstance(bucket["covered_lines"], set)
            bucket["total_lines"].add(line_no)
            if hits > 0:
                bucket["covered_lines"].add(line_no)

            if line.get("branch") == "true":
                bucket["has_branches"] = True
                cc = line.get("condition-coverage", "")
                m = _CONDITION_RE.search(cc)
                if m:
                    hit, total = int(m.group(1)), int(m.group(2))
                    bucket["branches_found"] = int(bucket["branches_found"]) + total
                    bucket["branches_hit"] = int(bucket["branches_hit"]) + hit
                else:
                    bucket["branches_found"] = int(bucket["branches_found"]) + 2
                    bucket["branches_hit"] = int(bucket["branches_hit"]) + (
                        2 if hits > 0 else 0
                    )

    files: list[FileCoverage] = []
    for path, bucket in per_file.items():
        covered = bucket["covered_lines"]
        total_set = bucket["total_lines"]
        assert isinstance(covered, set)
        assert isinstance(total_set, set)
        total = len(total_set)
        hit = len(covered)
        line_pct = (hit / total * 100.0) if total else 0.0
        branch_pct: float | None
        bf = int(bucket["branches_found"])
        bh = int(bucket["branches_hit"])
        branch_pct = bh / bf * 100.0 if bucket["has_branches"] and bf else None
        files.append(
            FileCoverage(
                file_path=path,
                line_coverage_pct=round(line_pct, 2),
                branch_coverage_pct=round(branch_pct, 2) if branch_pct is not None else None,
                covered_lines=sorted(covered),
                total_coverable_lines=total,
            )
        )

    return CoverageReport(source_format="cobertura", files=files)
