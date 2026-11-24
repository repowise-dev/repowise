"""DelphiCodeCoverage XML parser (stdlib ``xml.etree``).

``CodeCoverage.exe -xml -xmllines`` writes ``CodeCoverage_Summary.xml``:

    <report>
      <stats><packages value="1"/> ... <totallines value="8"/></stats>
      <data>
        <all name="all classes">
          <package name="uCalc">
            <srcfile name="uCalc.pas">
              <coverage type="line, %" value="50%   (4/8)"/>
              <class name="TCalc">...</class>
            </srcfile>
          </package>
        </all>
        <linehits>
          <file name="uCalc.pas">19=1;20=1;27=0</file>
        </linehits>
      </data>
    </report>

Per-line data is the ``<linehits>`` list of ``line=hits`` pairs: every listed
line is coverable and covered when its hit count is above zero. It is written
only with ``-xmllines``; without it a file falls back to its ``line, %``
counter, which gives a percentage but no line numbers.

File names are bare (``uCalc.pas``), never paths, so they are matched to
repository files by suffix later. Two units of the same name in different
directories cannot be told apart here. The same name listed twice (generic
classes, ``-xmlgenerics`` off) is merged, a line covered when any entry hit it.
"""

from __future__ import annotations

import re
from xml.etree import ElementTree as ET

from .model import CoverageReport, FileCoverage, file_coverage, parse_xml

_RATIO_RE = re.compile(r"\((\d+)/(\d+)\)")


def _line_hits(text: str, covered: set[int], coverable: set[int]) -> None:
    for pair in text.split(";"):
        number, _, hits = pair.partition("=")
        if not number.strip().isdigit():
            continue
        line = int(number)
        coverable.add(line)
        if hits.strip().isdigit() and int(hits) > 0:
            covered.add(line)


def _summary_ratio(srcfile: ET.Element) -> tuple[int, int] | None:
    """``(covered, total)`` of a ``<srcfile>``'s ``line, %`` counter."""
    for counter in srcfile.findall("coverage"):
        if counter.get("type") == "line, %":
            match = _RATIO_RE.search(counter.get("value") or "")
            return (int(match.group(1)), int(match.group(2))) if match else None
    return None


def _merged_line_hits(root: ET.Element) -> dict[str, tuple[set[int], set[int]]]:
    """``name -> (covered, coverable)`` over every ``<linehits>`` file entry, merged by name."""
    lines: dict[str, tuple[set[int], set[int]]] = {}
    for file in root.iter("file"):
        name = file.get("name") or ""
        if name:
            covered, coverable = lines.setdefault(name, (set(), set()))
            _line_hits(file.text or "", covered, coverable)
    return lines


def _summary_only_files(root: ET.Element, listed: set[str]) -> list[FileCoverage]:
    """Files with only a ``line, %`` counter (no ``-xmllines``): totals without line numbers."""
    files: list[FileCoverage] = []
    for srcfile in root.iter("srcfile"):
        name = srcfile.get("name") or ""
        ratio = _summary_ratio(srcfile)
        if name and name not in listed and ratio and ratio[1]:
            files.append(file_coverage(name, [], [], hit=ratio[0], total=ratio[1]))
    return files


def parse_delphi_codecoverage(text: str) -> CoverageReport:
    root = parse_xml(text)
    if root is None:
        return CoverageReport(source_format="delphi-codecoverage", files=[])
    lines = _merged_line_hits(root)
    files = [
        file_coverage(name, covered, coverable)
        for name, (covered, coverable) in lines.items()
        if coverable
    ]
    files.extend(_summary_only_files(root, set(lines)))
    return CoverageReport(source_format="delphi-codecoverage", files=files)
