"""JaCoCo XML parser (stdlib ``xml.etree``).

JaCoCo layout (abbreviated):

    <!DOCTYPE report PUBLIC "-//JACOCO//DTD Report 1.1//EN" "report.dtd">
    <report name="...">
      <group name="...">                  <!-- aggregate reports only -->
        <package name="com/foo">
          <class name="com/foo/Bar" sourcefilename="Bar.java">...</class>
          <sourcefile name="Bar.java">
            <line nr="3" mi="0" ci="2" mb="1" cb="1"/>
            <counter type="LINE" missed="1" covered="4"/>
          </sourcefile>
        </package>
      </group>
    </report>

Per-line data lives on ``<sourcefile>``; ``mi``/``ci`` are missed/covered
instructions and ``mb``/``cb`` missed/covered branches. A line is coverable
when it has any instruction and covered when any ran. ``<class>``,
``<method>`` and ``<counter>`` summaries are ignored.

Paths are ``package/SourceFile.java`` (package dirs, not repo dirs); suffix
matching maps them to ``src/main/java/...`` later. The DTD is never fetched.
"""

from __future__ import annotations

from .model import CoverageReport, file_coverage, parse_xml


def _int(value: str | None) -> int:
    try:
        return int(value or 0)
    except ValueError:
        return 0


def parse_jacoco(text: str) -> CoverageReport:
    root = parse_xml(text)
    if root is None:
        return CoverageReport(source_format="jacoco", files=[])

    # path -> (covered, coverable, line -> (branches, branches hit)). The same
    # source file can appear under several groups: merged hit-wins, and
    # branches per line take the max so a duplicate never double counts.
    merged: dict[str, tuple[set[int], set[int], dict[int, tuple[int, int]]]] = {}
    for package in root.iter("package"):
        pkg_name = (package.get("name") or "").strip("/")
        for sourcefile in package.findall("sourcefile"):
            name = sourcefile.get("name") or ""
            if not name:
                continue
            path = f"{pkg_name}/{name}" if pkg_name else name
            covered, coverable, branches = merged.setdefault(path, (set(), set(), {}))
            for line in sourcefile.findall("line"):
                nr = _int(line.get("nr"))
                if nr <= 0:
                    continue
                ci = _int(line.get("ci"))
                if _int(line.get("mi")) + ci > 0:
                    coverable.add(nr)
                if ci > 0:
                    covered.add(nr)
                cb = _int(line.get("cb"))
                total = _int(line.get("mb")) + cb
                if total:
                    prev_total, prev_hit = branches.get(nr, (0, 0))
                    branches[nr] = (max(prev_total, total), max(prev_hit, cb))

    files = [
        file_coverage(
            path,
            covered,
            coverable,
            branches_found=sum(t for t, _ in branches.values()),
            branches_hit=sum(h for _, h in branches.values()),
        )
        for path, (covered, coverable, branches) in merged.items()
        # No executable lines (e.g. an interface) means unmeasured, not 0%.
        if coverable
    ]
    return CoverageReport(source_format="jacoco", files=files)
