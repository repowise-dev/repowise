"""Grouped-by-file compaction of search results (grep, rg, git grep).

A 400-line match flood becomes one block per file: match count plus the
first and last matching lines as anchors. Files are ordered by match count;
relevance ranking against the index (PageRank) is the *caller's* job — the
augment hook reorders the same groups when a graph is available, this
filter stays pure and index-free.

Deviation from the error-line invariant: search matches are *matches*, not
diagnostics. A grep for "error" would classify every line as an error line
and defeat compaction entirely, so this filter keeps anchors + counts and
relies on the omission marker for full recovery.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import ClassVar

from repowise.core.distill.filters.base import OutputFilter
from repowise.core.distill.registry import filter_registry

_COMMAND_RE = re.compile(
    r"^(rg\b|grep\b|egrep\b|fgrep\b|git(?:\.exe)? grep\b|findstr\b|select-string\b|sls\b)"
)

# path:line:content — tolerates a Windows drive prefix in the path.
_MATCH_LINE_RE = re.compile(r"^(?P<path>(?:[A-Za-z]:)?[^:\n]+?):(?P<line>\d+)[:-](?P<text>.*)$")

# rg "heading" layout: a bare path line followed by `LINE:content` lines.
_HEADED_MATCH_RE = re.compile(r"^(?P<line>\d+)[:-](?P<text>.*)$")
_BARE_PATH_RE = re.compile(r"^(?:[A-Za-z]:)?[\w./\\@~-]+$")

_MAX_FILES = 15
_ANCHORS_PER_FILE = 2
_MAX_TEXT_CHARS = 160


@filter_registry.register
class SearchResultsFilter(OutputFilter):
    name: ClassVar[str] = "search_results"
    priority: ClassVar[int] = 25
    min_lines: ClassVar[int] = 30

    def matches_command(self, command: str) -> bool:
        return bool(_COMMAND_RE.match(command))

    def matches_content(self, output: str) -> bool:
        lines = [ln for ln in output.splitlines() if ln.strip()]
        if len(lines) < self.min_lines:
            return False
        matchish = sum(1 for ln in lines if _MATCH_LINE_RE.match(ln))
        return matchish / len(lines) > 0.7

    def distill(self, output: str, *, command: str = "", exit_code: int = 0) -> str:
        groups = group_search_matches(output)
        if groups is None:
            raise ValueError("not recognizable search output")
        return render_search_digest(groups)


def group_search_matches(output: str) -> OrderedDict[str, list[tuple[int, str]]] | None:
    """Group ``path:line:content`` search output by file, preserving order.

    Handles both the flat ``path:line:content`` layout (rg/grep piped, the
    Claude Code Grep tool) and rg's headed layout (bare path line, then
    ``line:content`` rows). Returns None when fewer than 70% of non-empty
    lines parse as matches — the caller should fall back to raw output.
    """
    groups: OrderedDict[str, list[tuple[int, str]]] = OrderedDict()
    lines = [ln for ln in output.splitlines() if ln.strip()]
    if not lines:
        return None

    current_head: str | None = None
    parsed = 0
    for ln in lines:
        flat = _MATCH_LINE_RE.match(ln)
        if flat:
            current_head = None
            groups.setdefault(flat.group("path"), []).append(
                (int(flat.group("line")), flat.group("text"))
            )
            parsed += 1
            continue
        headed = _HEADED_MATCH_RE.match(ln)
        if headed and current_head is not None:
            groups.setdefault(current_head, []).append(
                (int(headed.group("line")), headed.group("text"))
            )
            parsed += 1
            continue
        if _BARE_PATH_RE.match(ln.strip()) and ("/" in ln or "\\" in ln or "." in ln):
            current_head = ln.strip()
            parsed += 1
            continue
        current_head = None

    if not groups or parsed / len(lines) <= 0.7:
        return None
    return groups


def render_search_digest(
    groups: OrderedDict[str, list[tuple[int, str]]],
    *,
    file_order: list[str] | None = None,
    max_files: int = _MAX_FILES,
) -> str:
    """Render grouped matches as a compact per-file digest.

    *file_order* lets an index-aware caller put high-centrality files first;
    default order is by match count (descending), input order on ties.
    """
    total = sum(len(v) for v in groups.values())
    if file_order is None:
        ordered = sorted(groups, key=lambda p: -len(groups[p]))
    else:
        known = [p for p in file_order if p in groups]
        ordered = known + [p for p in groups if p not in set(known)]

    out = [f"{total} matches in {len(groups)} files:"]
    for path in ordered[:max_files]:
        matches = groups[path]
        anchors = matches[:1] + (matches[-1:] if len(matches) > 1 else [])
        out.append(f"  {path}  ({len(matches)} matches)")
        for line_no, text in anchors[:_ANCHORS_PER_FILE]:
            snippet = text.strip()[:_MAX_TEXT_CHARS]
            out.append(f"    L{line_no}: {snippet}")
    if len(groups) > max_files:
        hidden_files = len(groups) - max_files
        hidden_matches = sum(len(groups[p]) for p in ordered[max_files:])
        out.append(f"  ... ({hidden_files} more files, {hidden_matches} matches)")
    return "\n".join(out)
