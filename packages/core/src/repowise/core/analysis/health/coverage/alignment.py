"""Does a report's line numbering still match the source on disk?

A report written before the source changed names lines that have since moved:
its executable lines land on blank lines, comments or past the end of the
file, and every coverage figure read from it describes code that is no longer
there. :func:`drop_misaligned` finds those files at ingest and keeps them out,
so they read as files without coverage.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .model import FileCoverage

#: MISALIGNED_MAX_SHARE: a file whose coverable lines land on a blank line, a
#: comment or past EOF more often than this, and on at least
#: MISALIGNED_MIN_LINES of them, is dropped. A report taken from the current
#: source puts none there, so both only absorb edge cases in small files.
MISALIGNED_MAX_SHARE = 0.05
MISALIGNED_MIN_LINES = 3

# Comment prefixes for languages whose coverage tools report statement lines.
# Ceiling: Go coverprofiles and V8-based JS/TS reports mark every line of a
# block, blank and comment lines included, and Kotlin inline functions map to
# lines past EOF, so those files are not checked. Add a language here once its
# tools are known to report statement lines only. A bare ``*`` (block comment
# body) and PHP ``#`` are left out: code lines can start with them.
_COMMENT_PREFIXES: dict[str, tuple[str, ...]] = {
    ".py": ("#",),
    ".rb": ("#",),
    ".java": ("//", "/*"),
    ".cs": ("//", "/*"),
    ".php": ("//", "/*"),
}


@dataclass
class Alignment:
    """Files dropped for misalignment, why, and the repo-wide line counts."""

    reasons: dict[str, str] = field(default_factory=dict)
    misaligned_lines: int = 0
    checked_lines: int = 0

    @property
    def share(self) -> float:
        return self.misaligned_lines / self.checked_lines if self.checked_lines else 0.0

    def warning(self) -> str | None:
        """One message naming the dropped files, ``None`` when none was dropped."""
        if not self.reasons:
            return None
        path, reason = next(iter(self.reasons.items()))
        cause = (
            "The report is likely older than the source: re-run the tests with "
            "coverage and add the new report."
        )
        if self.share > MISALIGNED_MAX_SHARE:
            cause = (
                f"{self.misaligned_lines} of {self.checked_lines} coverable lines "
                f"({self.share:.0%}) are off. {cause}"
            )
        return (
            f"Coverage for {len(self.reasons)} file(s) was not kept because the "
            f"report's lines no longer match the source (e.g. {path}: {reason}). {cause}"
        )


def _misaligned(lines: Iterable[int], source: list[str], prefixes: tuple[str, ...]) -> int:
    count = 0
    for n in lines:
        text = source[n - 1].strip() if 0 < n <= len(source) else ""
        count += not text or text.startswith(prefixes)
    return count


def _check(repo_root: Path, fc: FileCoverage) -> tuple[int, int] | None:
    """``(misaligned, coverable)`` line counts for *fc*, ``None`` when not checked."""
    prefixes = _COMMENT_PREFIXES.get(Path(fc.file_path).suffix.lower())
    lines = fc.coverable_lines or fc.covered_lines
    if not prefixes or not lines:
        return None
    try:
        source = (repo_root / fc.file_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # Coverage tools number lines by newline only; ``splitlines`` also breaks
    # on form feeds and other separators a source file may contain.
    return _misaligned(lines, source.split("\n"), prefixes), len(lines)


def drop_misaligned(
    repo_root: Path, files: list[FileCoverage]
) -> tuple[list[FileCoverage], Alignment]:
    """*files* without the ones whose line numbers no longer match the source."""
    kept: list[FileCoverage] = []
    result = Alignment()
    for fc in files:
        counts = _check(repo_root, fc)
        if counts is None:
            kept.append(fc)
            continue
        bad, total = counts
        result.misaligned_lines += bad
        result.checked_lines += total
        if bad >= MISALIGNED_MIN_LINES and bad / total > MISALIGNED_MAX_SHARE:
            result.reasons[fc.file_path] = (
                f"{bad} of {total} coverable lines land on blank or comment "
                "lines or past the end of the file"
            )
        else:
            kept.append(fc)
    return kept, result
