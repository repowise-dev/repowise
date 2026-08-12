"""Marker-delimited managed blocks inside a user-owned markdown file.

The one write shape shared by every instructions file: repowise owns the text
between two markers and nothing else in the file. Install replaces the block in
place, uninstall removes it, and content outside the markers round-trips
byte-for-byte. That last property is the whole point — these files are things
users write in, and a managed section that eats a paragraph once will not be
trusted again.

Line endings are forced to ``\\n`` on every write. These files are repo-shared
and frequently committed, so letting them take the platform translation would
mean the same command produces a different file on Windows than on macOS and
the diff churns on every cross-platform edit.

Also reports the two malformed states worth naming rather than silently
repairing, because both mean a user edited inside the block and one of them
loses data if you guess: an orphan marker (a start with no end, or the reverse)
and a duplicated block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .json_merge import atomic_write_text


class BlockState(StrEnum):
    """What a file currently holds for one marker pair."""

    #: File does not exist.
    ABSENT_FILE = "absent-file"
    #: File exists, no marker of ours in it.
    ABSENT = "absent"
    #: Exactly one well-formed block.
    PRESENT = "present"
    #: More than one complete block — an install ran against a hand-edited file.
    DUPLICATED = "duplicated"
    #: A start without its end, or an end without its start.
    ORPHANED = "orphaned"


@dataclass(frozen=True)
class BlockInspection:
    state: BlockState
    #: Body between the markers for the first block found, markers excluded.
    body: str | None = None

    @property
    def is_healthy(self) -> bool:
        return self.state in (BlockState.PRESENT, BlockState.ABSENT, BlockState.ABSENT_FILE)


def inspect(path: Path, start: str, end: str) -> BlockInspection:
    """Report what *path* holds for the ``start``/``end`` marker pair."""
    if not path.exists():
        return BlockInspection(BlockState.ABSENT_FILE)
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return BlockInspection(BlockState.ABSENT_FILE)

    starts = content.count(start)
    ends = content.count(end)
    if starts == 0 and ends == 0:
        return BlockInspection(BlockState.ABSENT)
    if starts != ends:
        return BlockInspection(BlockState.ORPHANED)

    match = re.search(re.escape(start) + r"(.*?)" + re.escape(end), content, flags=re.DOTALL)
    if match is None:
        # Both markers present but the end precedes the start.
        return BlockInspection(BlockState.ORPHANED)
    state = BlockState.DUPLICATED if starts > 1 else BlockState.PRESENT
    return BlockInspection(state, body=match.group(1))


def upsert(
    path: Path,
    body: str,
    start: str,
    end: str,
    *,
    new_file_prefix: str = "",
) -> bool:
    """Ensure *path* carries exactly ``start + body + end``.

    Replaces an existing block in place, or appends one after existing content.
    Returns True when the file changed, so a caller can report ``unchanged``
    for a re-run rather than claiming an update it did not make.

    *new_file_prefix* is written above the block when the file did not exist —
    a header telling the reader this file is theirs and only the marked section
    is managed. Pair it with ``remove(delete_if_only=...)`` so install and
    uninstall round-trip to "no file".

    The replacement goes through a function rather than a string because
    ``re.sub`` interprets backslashes and ``\\g<name>`` group references in a
    replacement *string*. Instruction bodies are prose that can legitimately
    contain either, and the failure mode is a corrupted managed block or a
    ``bad escape`` crash at install time.

    **Known gap, carried over deliberately.** Against a malformed pair this does
    the wrong thing quietly: an orphaned start marker matches ``start in
    existing`` but not the regex, so nothing is written and it reports
    ``False`` — "unchanged" while the block is in fact absent — and a duplicated
    block is rewritten in both places rather than collapsed. :func:`inspect`
    exists to name both states and this function does not yet consult it. The
    hand-rolled writer this replaced had the identical blind spot, so preserving
    it keeps the rewrite behaviour-neutral; it should be closed before Phase 5
    adds targets that inherit this helper.
    """
    wrapped = f"{start}{body}{end}"
    if not path.exists():
        atomic_write_text(path, f"{new_file_prefix}\n{wrapped}\n" if new_file_prefix else wrapped + "\n", newline="\n")
        return True

    existing = path.read_text(encoding="utf-8")
    if start in existing:
        pattern = re.escape(start) + r".*?" + re.escape(end)
        content = re.sub(pattern, lambda _m: wrapped, existing, flags=re.DOTALL)
    else:
        content = existing.rstrip() + "\n\n" + wrapped + "\n"

    # Compare *bytes*, not the decoded text. ``read_text`` collapses CRLF to LF
    # in memory, so a CRLF file whose block is already current compares equal to
    # the LF content we are about to write — and short-circuiting there would
    # leave the file CRLF, where an unconditional write normalised it. That is a
    # whole-file diff on any Windows checkout with ``core.autocrlf=true``.
    if content.encode("utf-8") == path.read_bytes():
        return False
    atomic_write_text(path, content, newline="\n")
    return True


def remove(path: Path, start: str, end: str, *, delete_if_only: str | None = None) -> bool:
    """Strip the managed block, leaving user content untouched.

    *delete_if_only* is the placeholder an install writes into a file it
    created. When removal leaves nothing but that placeholder, the file is
    deleted, so install then uninstall round-trips back to "no file" rather
    than leaving a stub nobody asked for.

    Returns True when something was removed.
    """
    if not path.exists():
        return False
    try:
        existing = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if start not in existing:
        return False

    pattern = r"\n*" + re.escape(start) + r".*?" + re.escape(end) + r"\n?"
    remaining = re.sub(pattern, "", existing, flags=re.DOTALL)
    if remaining and not remaining.endswith("\n"):
        # Upsert rstrips before appending; restore the newline that consumed so
        # removal is a true inverse.
        remaining += "\n"

    try:
        leftovers = {""} | ({delete_if_only.strip()} if delete_if_only else set())
        if remaining.strip() in leftovers:
            path.unlink()
        else:
            atomic_write_text(path, remaining, newline="\n")
    except OSError:
        return False
    return True
