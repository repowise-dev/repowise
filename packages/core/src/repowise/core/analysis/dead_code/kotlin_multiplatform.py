"""Kotlin multiplatform ``expect`` / ``actual`` declarations in dead code.

A multiplatform API is one ``expect`` declaration in a common source set and
one ``actual`` per platform source set, all with the same name in the same
package. The compiler binds every use of the API to the ``expect`` and links
each ``actual`` to it, so the graph sees one name declared in several files of
one package. That is exactly the shape the same-package scan refuses to bind
(an ambiguous name gets no edge), and a call from a platform file may bind to
one ``actual`` and leave the others with no edge. Both read as "no importers".

A declaration pairs by package and name, read from the source.

* An ``actual`` whose ``expect`` is in the repository is the platform body of
  that ``expect``: it is never deleted on its own, and whether the API is used
  is the ``expect``'s question. So it is not reported, and neither is a file
  whose top-level ``actual`` pairs that way. An ``actual`` with no ``expect`` in
  view is left as it was.
* An ``expect`` stays reported, but is re-judged on a real use: its name written
  in code (comments and strings blanked) in a file that sees its package (the
  same package, or an import of the name or of the package), on a line that is
  not one of the API's own declarations. With such a use it falls below the
  review floor with the line that uses it; without one it stays at the review
  tier with a reason that says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from ...ingestion.languages.registry import REGISTRY
from .models import DeadCodeFindingData, DeadCodeKind
from .name_occurrences import occurrence_files, searchable_token
from .risk_factors import RISK_CAP_CONFIDENCE

#: Where an ``expect`` with a real use lands: below the review floor, so the
#: default report hides it while ``--min-confidence 0`` still lists it.
EXPECT_USED_CONFIDENCE = 0.3

# ``[annotations] [modifiers] expect|actual [modifiers] keyword [<T>] [Receiver.]Name``.
# Constructors are members of an ``actual class`` and never a finding of their own.
_PLATFORM_DECL_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:@[\w.]+(?:\([^)\n]*\))?\s+)*(?:[a-z]+[ \t]+)*?"
    r"(?P<modifier>expect|actual)[ \t]+(?:[a-z]+[ \t]+)*?"
    r"(?:fun|val|var|class|object|interface|typealias)[ \t]+"
    r"(?:<[^>\n]*>[ \t]*)?(?:[\w<>?, *]+\.)?(?P<name>[A-Za-z_]\w*)",
    re.MULTILINE,
)
_PACKAGE_RE = re.compile(r"^[ \t]*package[ \t]+([\w.]+)", re.MULTILINE)
_IMPORT_RE = re.compile(r"^[ \t]*import[ \t]+([\w.]*\w(?:\.\*)?)", re.MULTILINE)
# Comments and string literals, replaced by spaces so line numbers hold.
_NOISE_RE = re.compile(r'/\*.*?\*/|//[^\n]*|"""(?:.|\n)*?"""|"(?:\\.|[^"\\\n])*"', re.DOTALL)


@dataclass(frozen=True)
class _PlatformDecl:
    path: str
    package: str
    line: int
    modifier: str
    top_level: bool


def _is_kotlin(path: str) -> bool:
    return REGISTRY.from_extension(PurePosixPath(path).suffix) == "kotlin"


def _blank_noise(text: str) -> str:
    return _NOISE_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), text)


def _package(text: str) -> str:
    match = _PACKAGE_RE.search(text)
    return match.group(1) if match else ""


def _platform_index(source_map: dict[str, bytes]) -> dict[tuple[str, str], list[_PlatformDecl]]:
    """``(package, name)`` -> its ``expect`` / ``actual`` declarations."""
    index: dict[tuple[str, str], list[_PlatformDecl]] = {}
    for path, blob in source_map.items():
        if not _is_kotlin(path) or (b"expect" not in blob and b"actual" not in blob):
            continue
        text = _blank_noise(blob.decode("utf-8", "replace"))
        package = _package(text)
        for match in _PLATFORM_DECL_RE.finditer(text):
            line = text.count("\n", 0, match.start("modifier")) + 1
            top_level = not match.group("indent")
            decl = _PlatformDecl(path, package, line, match.group("modifier"), top_level)
            index.setdefault((package, match.group("name")), []).append(decl)
    return index


def _pairs_with_expect(decls: list[_PlatformDecl]) -> bool:
    return any(d.modifier == "expect" for d in decls)


def _paired_actual_files(index: dict[tuple[str, str], list[_PlatformDecl]]) -> set[str]:
    """Files holding a top-level ``actual`` whose ``expect`` is in the index."""
    return {
        d.path
        for decls in index.values()
        if _pairs_with_expect(decls)
        for d in decls
        if d.modifier == "actual" and d.top_level
    }


def _role(
    finding: DeadCodeFindingData,
    index: dict[tuple[str, str], list[_PlatformDecl]],
    packages: dict[str, str],
    paired_files: set[str],
) -> str | None:
    """``"actual"`` (paired, not reported), ``"expect"`` (re-judged) or None."""
    if finding.kind is DeadCodeKind.UNREACHABLE_FILE:
        return "actual" if finding.file_path in paired_files else None
    package = packages.get(finding.file_path)
    decls = index.get((package, finding.symbol_name), []) if package is not None else []
    own = next((d for d in decls if d.path == finding.file_path), None)
    if own is None or (own.modifier == "actual" and not _pairs_with_expect(decls)):
        return None
    return own.modifier


def settle_platform_declarations(
    findings: list[DeadCodeFindingData], source_map: dict[str, bytes]
) -> list[DeadCodeFindingData]:
    """Drop paired ``actual`` findings and re-judge ``expect`` ones; see the module doc.

    Mutates the ``expect`` findings in place and returns the kept list. A file
    with no source is left as it was.
    """
    if not any(_is_kotlin(f.file_path) for f in findings):
        return findings
    index = _platform_index(source_map)
    packages = {d.path: d.package for decls in index.values() for d in decls}
    paired_files = _paired_actual_files(index)
    roles = {id(f): _role(f, index, packages, paired_files) for f in findings}
    expects = [f for f in findings if roles[id(f)] == "expect"]
    if expects:
        _judge_expects(expects, source_map, index, packages)
    return [f for f in findings if roles[id(f)] != "actual"]


def _sees(text: str, package: str, name: str) -> bool:
    """Whether a file in its own package, or importing the name or the package, can use it."""
    if _package(text) == package:
        return True
    wanted = {f"{package}.{name}", f"{package}.*"}
    return any(imported in wanted for imported in _IMPORT_RE.findall(text))


def _first_use(
    source_map: dict[str, bytes],
    candidates: set[str],
    key: tuple[str, str],
    declarations: set[tuple[str, int]],
) -> str | None:
    """``path:line`` of the first code use of *key* outside its own declarations."""
    package, name = key
    word = re.compile(rf"\b{re.escape(name)}\b")
    for path in sorted(candidates):
        if not _is_kotlin(path):
            continue
        text = _blank_noise(source_map[path].decode("utf-8", "replace"))
        if not _sees(text, package, name):
            continue
        for lineno, line in enumerate(text.split("\n"), start=1):
            if (path, lineno) not in declarations and word.search(line):
                return f"{path}:{lineno}"
    return None


def _judge_expects(
    expects: list[DeadCodeFindingData],
    source_map: dict[str, bytes],
    index: dict[tuple[str, str], list[_PlatformDecl]],
    packages: dict[str, str],
) -> None:
    # A name the identifier scan cannot see is left to the shared name search,
    # which says it could not be searched for.
    tokens = {id(f): searchable_token(f.symbol_name or "") for f in expects}
    searchable = [f for f in expects if tokens[id(f)]]
    occurrences = occurrence_files(source_map, {tokens[id(f)] for f in searchable})
    for finding in searchable:
        name = finding.symbol_name
        key = (packages[finding.file_path], name)
        decls = index.get(key, [])
        declarations = {(d.path, d.line) for d in decls}
        used_at = _first_use(source_map, occurrences.get(tokens[id(finding)], set()), key, declarations)
        finding.safe_to_delete = False
        if used_at:
            finding.confidence = min(finding.confidence, EXPECT_USED_CONFIDENCE)
            finding.reason = f"Multiplatform expect '{name}' is not imported, but is used in its package"
            finding.evidence.append(
                f"'{name}' is used at {used_at}; the import graph does not bind "
                "uses of a name its platform actuals also declare"
            )
            continue
        finding.confidence = min(finding.confidence, RISK_CAP_CONFIDENCE)
        actuals = sum(1 for d in decls if d.modifier == "actual")
        finding.reason = (
            f"Multiplatform expect '{name}' has no use outside its {actuals} platform actual(s)"
            if actuals
            else f"Multiplatform expect '{name}' has no actual and no use in the repo"
        )
