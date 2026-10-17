"""Small finding and graph-node helpers shared by the dead-code passes."""

from __future__ import annotations

from .file_reachability import CSHARP_SUFFIX
from .models import DeadCodeFindingData, DeadCodeKind


def _is_synthetic_node(node: str) -> bool:
    """True for ``external:`` (third-party) and ``framework:`` (convention-loading) anchors.

    Neither is ever judged dead itself. The zombie-package pass still counts a
    ``framework:`` predecessor as a cross-package importer, never an ``external:`` one.
    """
    return node.startswith("external:") or node.startswith("framework:")


# Evidence line for a finding whose ``lines`` is ``None``: the count is
# omitted rather than estimated.
_LINES_UNKNOWN = "Line count unavailable: source was not read"


def _csharp_exports(findings: list[DeadCodeFindingData]) -> list[DeadCodeFindingData]:
    """The unused-export findings in ``.cs`` files."""
    return [
        f
        for f in findings
        if f.kind is DeadCodeKind.UNUSED_EXPORT and f.file_path.endswith(CSHARP_SUFFIX)
    ]


def _names_by_file(findings: list[DeadCodeFindingData]) -> dict[str, set[str]]:
    """File -> the symbol names *findings* report there."""
    out: dict[str, set[str]] = {}
    for f in findings:
        out.setdefault(f.file_path, set()).add(f.symbol_name or "")
    return out


def _symbol_span(data: dict) -> dict[str, int | None]:
    """``lines``/``start_line``/``end_line`` for a symbol finding.

    Both-or-neither: a half-known span is worse than none. Spans are
    inclusive, so a one-line symbol is 1 line; an unknown span is ``None``.
    """
    start, end = data.get("start_line") or None, data.get("end_line") or None
    if start is None or end is None or end < start:
        return {"lines": None, "start_line": None, "end_line": None}
    return {"lines": end - start + 1, "start_line": start, "end_line": end}
