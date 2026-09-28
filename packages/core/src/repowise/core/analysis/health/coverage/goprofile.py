"""Go ``go test -coverprofile`` parser.

A cover profile is line-oriented:

    mode: set
    example.com/mod/pkg/file.go:10.2,12.16 2 1
    example.com/mod/pkg/file.go:12.16,14.3 1 0

Each block line is ``path:startLine.startCol,endLine.endCol numStmts count``.
Paths are usually module import paths, not repo paths; resolution to repo
keys happens later by suffix matching, so they are kept verbatim.

Concatenated profiles (several test binaries, ``-coverpkg``) repeat the
``mode:`` header and the same block; a block counts as covered when any of
its records has ``count > 0``. Each block expands to the source lines it
spans, and a line is covered when any block covering it was hit.

Percentages are therefore **line-based**, not the statement percentage
``go tool cover -func`` prints. Go profiles carry no branch data, and no
per-line data either: every line a block spans counts as executable, so a
comment inside an unexecuted block reads as an uncovered line.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .model import CoverageReport, file_coverage

# (startLine, startCol, endLine, endCol)
_Block = tuple[int, int, int, int]


def _parse_block_line(line: str) -> tuple[str, _Block, int, int] | None:
    """Parse one block record into ``(path, block, numStmts, count)``, or ``None``."""
    # Split from the right: paths may contain spaces and colons.
    fields = line.rsplit(" ", 2)
    if len(fields) != 3:
        return None
    location, stmts_s, count_s = fields
    path, sep, spec = location.rpartition(":")
    if not sep or not path:
        return None
    try:
        start, end = spec.split(",")
        start_line, start_col = (int(x) for x in start.split("."))
        end_line, end_col = (int(x) for x in end.split("."))
        num_stmts = int(stmts_s)
        count = int(count_s)
    except ValueError:
        return None
    if start_line <= 0 or end_line < start_line:
        return None
    return path, (start_line, start_col, end_line, end_col), num_stmts, count


def _block_lines(block: _Block) -> range:
    """The source lines *block* spans."""
    start_line, _start_col, end_line, end_col = block
    # A block ending at column 1 stops before any code on its last line.
    last = end_line - 1 if end_col <= 1 and end_line > start_line else end_line
    return range(start_line, last + 1)


def _collect_blocks(text: str) -> dict[str, dict[_Block, tuple[int, bool]]]:
    """path -> block -> (numStmts, covered), merging repeated records hit-wins."""
    blocks: dict[str, dict[_Block, tuple[int, bool]]] = defaultdict(dict)
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("mode:"):
            continue
        parsed = _parse_block_line(line)
        if parsed is None:
            continue
        path, key, num_stmts, count = parsed
        per_file = blocks[path]
        prev = per_file.get(key)
        covered = count > 0 or (prev is not None and prev[1])
        per_file[key] = (max(num_stmts, prev[0] if prev else 0), covered)
    return blocks


def _expand_file(per_file: dict[_Block, tuple[int, bool]]) -> tuple[set[int], set[int]]:
    """Expand one file's blocks into ``(hit, coverable)`` line sets."""
    coverable: set[int] = set()
    hit: set[int] = set()
    for block, (num_stmts, covered) in per_file.items():
        if num_stmts <= 0:
            continue
        lines = _block_lines(block)
        coverable.update(lines)
        if covered:
            hit.update(lines)
    return hit, coverable


def parse_go_coverprofile(text: str) -> CoverageReport:
    files = []
    for path, per_file in _collect_blocks(text).items():
        hit, coverable = _expand_file(per_file)
        if not coverable:
            # Only zero-statement blocks: nothing measured, which is not 0%.
            continue
        files.append(file_coverage(Path(path).as_posix(), hit, coverable))
    return CoverageReport(source_format="go-coverprofile", files=files)
