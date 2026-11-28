"""Grouped-tree compaction of file listings (find, ls, dir, tree, fd).

A 500-line ``find`` dump becomes one line per directory with a file count and
a few example names — the structure survives, the bulk does not.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import ClassVar

from repowise.core.distill.filters.base import OutputFilter
from repowise.core.distill.registry import filter_registry

_COMMAND_RE = re.compile(r"^(ls\b|dir\b|tree\b|find\b|fd\b|get-childitem\b|gci\b|git ls-files\b)")

# A bare path: no spaces (or escaped ones), with directory separators or an
# extension. The optional drive prefix covers absolute Windows paths
# (``C:\Users\...`` from ``dir /s /b``), which the sniff used to reject
# wholesale because of the colon.
_PATH_LINE_RE = re.compile(r"^(?:[A-Za-z]:[\\/])?[\w.@~-]+(?:[\\/][\w.@\[\]-]+)*[\\/]?$")

# ls -l style: permission bits then columns ending in the name.
_LS_LONG_RE = re.compile(r"^[-dlbcps][-rwxsStT]{9}[\s+@]")

_EXAMPLES_PER_DIR = 4
_MAX_DIRS = 60
_MAX_FLAT_ENTRIES = 50

#: Up to this many entries the compaction is lossless: every name survives and
#: only repeated directory prefixes and ``ls -l`` columns go. The names are
#: what a listing is asked for, and sampling a small one forces a second call
#: that costs more than the few hundred tokens it saved (a 94-entry ``ls`` cut
#: to four names did exactly that). Above it, a flood is sampled as before.
#: A fixed ceiling, not tuned: raise it if agents re-list floods above it.
_LOSSLESS_ENTRIES = 200


@filter_registry.register
class FileListingFilter(OutputFilter):
    name: ClassVar[str] = "file_listing"
    priority: ClassVar[int] = 40
    min_lines: ClassVar[int] = 40

    def matches_command(self, command: str) -> bool:
        return bool(_COMMAND_RE.match(command))

    def matches_content(self, output: str) -> bool:
        lines = [ln for ln in output.splitlines() if ln.strip()]
        if len(lines) < self.min_lines:
            return False
        pathish = sum(1 for ln in lines if _PATH_LINE_RE.match(ln.strip()))
        return pathish / len(lines) > 0.8

    def distill(self, output: str, *, command: str = "", exit_code: int = 0) -> str:
        lines = [ln for ln in output.splitlines() if ln.strip()]
        if sum(1 for ln in lines if _LS_LONG_RE.match(ln)) > len(lines) * 0.5:
            return _distill_ls_long(lines)
        paths = [ln.strip() for ln in lines if _PATH_LINE_RE.match(ln.strip())]
        if len(paths) < len(lines) * 0.8:
            raise ValueError("not a recognizable file listing")
        return _distill_paths(paths)


def _distill_paths(paths: list[str]) -> str:
    groups: OrderedDict[str, list[str]] = OrderedDict()
    for p in paths:
        normalized = p.replace("\\", "/").rstrip("/")
        parent, _, name = normalized.rpartition("/")
        groups.setdefault(parent or ".", []).append(name)

    lossless = len(paths) <= _LOSSLESS_ENTRIES
    examples_per_dir = len(paths) if lossless else _EXAMPLES_PER_DIR
    out = [f"{len(paths)} entries in {len(groups)} directories:"]
    for i, (parent, names) in enumerate(groups.items()):
        if i >= _MAX_DIRS and not lossless:
            remaining = len(groups) - _MAX_DIRS
            files_left = sum(len(v) for v in list(groups.values())[_MAX_DIRS:])
            out.append(f"  ... ({remaining} more directories, {files_left} entries)")
            break
        examples = ", ".join(names[:examples_per_dir])
        more = f", +{len(names) - examples_per_dir} more" if len(names) > examples_per_dir else ""
        out.append(f"  {parent}/  ({len(names)}: {examples}{more})")
    return "\n".join(out)


def _distill_ls_long(lines: list[str]) -> str:
    entries: list[str] = []
    for ln in lines:
        if not _LS_LONG_RE.match(ln):
            continue
        fields = ln.split()
        if len(fields) < 9:
            continue
        name = " ".join(fields[8:])
        suffix = "/" if ln.startswith("d") else ""
        entries.append(f"{name}{suffix}  ({fields[4]})")
    if not entries:
        raise ValueError("not ls -l output")
    keep = len(entries) if len(entries) <= _LOSSLESS_ENTRIES else _MAX_FLAT_ENTRIES
    out = [f"{len(entries)} entries:"]
    out.extend(entries[:keep])
    if len(entries) > keep:
        out.append(f"  ... ({len(entries) - keep} more entries)")
    return "\n".join(out)
