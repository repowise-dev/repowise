"""A Pascal symbol its own unit or a unit that ``uses`` it names is used.

``uses`` brings a unit's whole interface into scope, so the import edge says
nothing about which symbols an importer needs, and a type named only in a
declaration (``FParser: TANSIParser``, ``TFoo(Obj)``, ``TFoo.Create``) carries
no symbol edge. For a Pascal finding, the name written in its own unit or in a
unit importing it is a use, compared case-insensitively since Pascal is.

What does not count: comments, string literals, and a declaration of the name
anywhere in those files: the routine name after ``procedure``/``function``/
``constructor``/``destructor``/``property`` (an ``interface`` signature, an
``implementation`` header, the class qualifier of an out-of-line method
``procedure TFoo.Bar``), and ``Name =`` opening a line (a type or constant
declaration). The symbol's own span is read like any other code: an
error-recovered parse can stretch a span over the routines after it, and their
calls must still count. A recursive call or a local spelled like the symbol
counts too: that is the textual ceiling, and it only ever costs a true finding.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .models import DeadCodeFindingData, drop_used

_PASCAL_EXTENSIONS = (".pas", ".pp", ".dpr", ".dpk", ".lpr")
_COMMENT_OR_STRING = re.compile(r"\{[^}]*\}|\(\*.*?\*\)|//[^\n]*|'(?:[^'\n]|'')*'", re.DOTALL)
_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")
_DECLARING_KEYWORD = re.compile(
    r"(?:\b(?:procedure|function|constructor|destructor|property)\s+(?:[A-Za-z_]\w*\s*\.\s*)*)$",
    re.IGNORECASE,
)
_DECLARED_BY_EQUALS = re.compile(r"\s*(?:<[^>]*>\s*)?=(?!=)")


def _blank(match: re.Match[str]) -> str:
    return "".join("\n" if char == "\n" else " " for char in match.group())


class _PascalUses:
    def __init__(self, source_map: Mapping[str, bytes], graph: Any) -> None:
        self._source_map = source_map
        self._graph = graph
        self._code: dict[str, list[str]] = {}

    def code(self, path: str) -> list[str]:
        lines = self._code.get(path)
        if lines is None:
            raw = self._source_map.get(path, b"").decode("utf-8", errors="replace")
            lines = _COMMENT_OR_STRING.sub(_blank, raw).split("\n")
            self._code[path] = lines
        return lines

    def importers(self, path: str) -> list[str]:
        if path not in self._graph:
            return []
        return sorted(
            pred
            for pred in self._graph.predecessors(path)
            if self._graph[pred][path].get("edge_type") == "imports"
        )

    def is_used(self, finding: DeadCodeFindingData) -> bool:
        name = (finding.symbol_name or "").lower()
        paths = [finding.file_path, *self.importers(finding.file_path)]
        return any(self._names(path, name) for path in paths)

    def _names(self, path: str, name: str) -> bool:
        for line in self.code(path):
            if name not in line.lower():
                continue
            for match in _IDENTIFIER.finditer(line):
                if match.group().lower() != name:
                    continue
                if _DECLARING_KEYWORD.search(line[: match.start()]):
                    continue
                if not line[: match.start()].strip() and _DECLARED_BY_EQUALS.match(
                    line, match.end()
                ):
                    continue
                return True
        return False


def _is_candidate(finding: DeadCodeFindingData) -> bool:
    return (
        finding.is_spanned_symbol
        and finding.file_path.lower().endswith(_PASCAL_EXTENSIONS)
        and (finding.symbol_name or "").isidentifier()
    )


def drop_pascal_name_uses(
    findings: list[DeadCodeFindingData], source_map: Mapping[str, bytes], graph: Any
) -> list[DeadCodeFindingData]:
    """Drop Pascal symbol findings their unit or an importing unit names. Returns a new list."""
    candidates = [f for f in findings if _is_candidate(f)]
    if not candidates or not source_map:
        return findings
    return drop_used(findings, candidates, _PascalUses(source_map, graph).is_used)
