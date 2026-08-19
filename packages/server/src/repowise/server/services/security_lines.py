"""Serve-time verification that a security finding's line still points at it.

``SecurityFinding.line_number`` is written at scan time and the file may have
changed since. A wrong line on a security finding is worse than no line: it
sends the reader to innocent code and looks authoritative doing it. So the
line is checked against the live file before it is served, the same way
``mcp_server/_verify.py`` gates symbol bounds.

The contract, mirroring that module:

* ``verified: True`` — the snippet was found on the served line (either the
  stored line still holds, or the snippet moved and the line was corrected).
* ``verified: False`` with a line — the snippet is ambiguous in the file, so
  the line is a guess and the surface must mark it as such.
* ``line_number: None`` — the snippet is gone from the file entirely. The
  finding is stale; a line here would point at unrelated code.

For the pattern scan the snippet is ``line.strip()[:120]`` (``security_scan.py``),
so it is always a substring of the line it came from and containment is a sound
gate. The symbol-name scan is the exception: its snippet is a bare identifier,
which recurs all over a file, so those kinds are checked in place and never
relocated or withdrawn — see ``SYMBOL_NAME_KINDS``.
"""

from __future__ import annotations

from dataclasses import dataclass

from repowise.core.analysis.security_scan import SYMBOL_NAME_KINDS


@dataclass(frozen=True)
class LineCheck:
    """Outcome of checking one finding's line against live source."""

    line_number: int | None
    verified: bool


def check_finding_line(
    lines: list[str] | None,
    line_number: int | None,
    snippet: str | None,
    kind: str | None = None,
) -> LineCheck:
    """Verify (and if needed correct) *line_number* against *lines*.

    ``lines`` is the live file split into lines, or None when the file could
    not be read. Without a file or a snippet there is nothing to check
    against, so the stored line is passed through unverified rather than
    being claimed as correct.
    """
    if lines is None or not snippet:
        return LineCheck(line_number=line_number, verified=False)

    if (
        line_number is not None
        and 1 <= line_number <= len(lines)
        and snippet in lines[line_number - 1]
    ):
        return LineCheck(line_number=line_number, verified=True)

    if kind in SYMBOL_NAME_KINDS:
        # A bare identifier is not discriminating enough to relocate on, and
        # its absence from the stored line does not mean the symbol is gone.
        return LineCheck(line_number=line_number, verified=False)

    matches = [i + 1 for i, text in enumerate(lines) if snippet in text]
    if len(matches) == 1:
        return LineCheck(line_number=matches[0], verified=True)
    if matches:
        # Ambiguous: the snippet recurs and the stored line is not one of the
        # hits. Serve the first as a pointer, but never as verified.
        return LineCheck(line_number=matches[0], verified=False)

    # Gone from the file: the finding outlived the code it describes.
    return LineCheck(line_number=None, verified=False)
