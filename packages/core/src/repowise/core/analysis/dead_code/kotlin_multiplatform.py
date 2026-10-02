"""Kotlin multiplatform ``expect`` / ``actual`` declarations in dead code.

A multiplatform API is one ``expect`` declaration in a common source set and
one ``actual`` per platform source set, all with the same name in the same
package. The compiler binds every use of the API to the ``expect`` and links
each ``actual`` to it, so the graph sees one name declared in several files of
one package. That is exactly the shape the same-package scan refuses to bind
(an ambiguous name gets no edge), and a call from a platform file may bind to
one ``actual`` and leave the others with no edge. Both read as "no importers".

* An ``actual`` is the platform body of its ``expect``: it is never deleted on
  its own, and whether the API is used is the ``expect``'s question. So an
  ``actual`` declaration is not reported, and neither is a file that declares
  one at top level, since the compiler pulls it in for its ``expect``.
* An ``expect`` stays reported, but its own ``actual`` files always write its
  name, so they say nothing about use. Named in some other file, it falls below
  the review floor with the file that names it; named nowhere else, it stays at
  the review tier with a reason that says so.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from ...ingestion.languages.registry import REGISTRY
from .models import DeadCodeFindingData, DeadCodeKind
from .name_occurrences import IDENTIFIER_RE, occurrence_files, searchable_token
from .risk_factors import RISK_CAP_CONFIDENCE

#: Where an ``expect`` another file names lands: below the review floor, so
#: the default report hides it while ``--min-confidence 0`` still lists it.
EXPECT_NAMED_ELSEWHERE_CONFIDENCE = 0.3

_DECLARATION_KEYWORD = r"(?:fun|val|var|class|object|interface|typealias|constructor)"
_KEYWORD_RE = re.compile(rf"\b{_DECLARATION_KEYWORD}\b")
_PLATFORM_RE = re.compile(r"\b(expect|actual)\b")
# Strings and line comments, so ``@Deprecated("the actual one")`` is no modifier.
_NOISE_RE = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*')
# A declaration header line carrying ``actual`` before its keyword.
_ACTUAL_HEADER_RE = re.compile(
    rf"^[ \t]*(?:@[\w.]+(?:\([^)\n]*\))?[ \t]+)*(?:[a-z]+[ \t]+)*actual[ \t]+"
    rf"(?:[a-z]+[ \t]+)*{_DECLARATION_KEYWORD}\b[^\n]*",
    re.MULTILINE,
)
# Lines read from a symbol's start to find its keyword (annotations come first).
_HEADER_LINES = 8


def _is_kotlin(path: str) -> bool:
    return REGISTRY.from_extension(PurePosixPath(path).suffix) == "kotlin"


def _platform_modifier(blob: bytes, start_line: int) -> str | None:
    """``"expect"`` / ``"actual"`` when the declaration at *start_line* has it."""
    lines = blob.split(b"\n")[start_line - 1 : start_line - 1 + _HEADER_LINES]
    header = _NOISE_RE.sub("", b"\n".join(lines).decode("utf-8", "replace"))
    keyword = _KEYWORD_RE.search(header)
    if keyword is None:
        return None
    modifier = _PLATFORM_RE.search(header, 0, keyword.start())
    return modifier.group(1) if modifier else None


def _actual_files_by_name(source_map: dict[str, bytes], names: set[str]) -> dict[str, set[str]]:
    """For each of *names*, the Kotlin files with an ``actual`` header naming it."""
    found: dict[str, set[str]] = {}
    for path, blob in source_map.items():
        if not _is_kotlin(path) or b"actual" not in blob:
            continue
        text = blob.decode("utf-8", "replace")
        for header in _ACTUAL_HEADER_RE.finditer(text):
            for token in IDENTIFIER_RE.findall(header.group().encode("ascii", "ignore")):
                name = token.decode("ascii")
                if name in names:
                    found.setdefault(name, set()).add(path)
    return found


def _declares_top_level_actual(blob: bytes) -> bool:
    """Whether the file declares an unindented (top-level) ``actual``."""
    text = blob.decode("utf-8", "replace")
    return any(not m.group().startswith((" ", "\t")) for m in _ACTUAL_HEADER_RE.finditer(text))


def settle_platform_declarations(
    findings: list[DeadCodeFindingData], source_map: dict[str, bytes]
) -> list[DeadCodeFindingData]:
    """Drop ``actual`` findings and re-judge ``expect`` ones; see the module doc.

    Mutates the ``expect`` findings in place and returns the kept list. A file
    with no source is left as it was.
    """
    kept: list[DeadCodeFindingData] = []
    expects: list[DeadCodeFindingData] = []
    for finding in findings:
        blob = source_map.get(finding.file_path)
        if blob is None or not _is_kotlin(finding.file_path):
            kept.append(finding)
            continue
        if finding.kind is DeadCodeKind.UNREACHABLE_FILE:
            if not _declares_top_level_actual(blob):
                kept.append(finding)
            continue
        modifier = (
            _platform_modifier(blob, finding.start_line)
            if finding.symbol_name and finding.start_line
            else None
        )
        if modifier == "actual":
            continue
        if modifier == "expect":
            expects.append(finding)
        kept.append(finding)
    if expects:
        _judge_expects(expects, source_map)
    return kept


def _judge_expects(expects: list[DeadCodeFindingData], source_map: dict[str, bytes]) -> None:
    # A name the identifier scan cannot see is left to the shared name search,
    # which says it could not be searched for.
    tokens = {id(f): searchable_token(f.symbol_name or "") for f in expects}
    searchable = [f for f in expects if tokens[id(f)]]
    actual_files = _actual_files_by_name(source_map, {f.symbol_name for f in searchable})
    occurrences = occurrence_files(source_map, {tokens[id(f)] for f in searchable})
    for finding in searchable:
        name = finding.symbol_name
        own = {finding.file_path} | actual_files.get(name, set())
        elsewhere = sorted(occurrences.get(tokens[id(finding)], set()) - own)
        finding.safe_to_delete = False
        if elsewhere:
            finding.confidence = min(finding.confidence, EXPECT_NAMED_ELSEWHERE_CONFIDENCE)
            finding.reason = (
                f"Multiplatform expect '{name}' is not imported, but is named elsewhere in the repo"
            )
            finding.evidence.append(
                f"'{name}' is written at {elsewhere[0]}; the import graph does not bind "
                "uses of a name its platform actuals also declare"
            )
        else:
            finding.confidence = min(finding.confidence, RISK_CAP_CONFIDENCE)
            finding.reason = (
                f"Multiplatform expect '{name}' is named only by its own declaration "
                "and its platform actuals"
            )
