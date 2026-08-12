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

Both malformed states are named rather than glossed over, because they mean a
user edited across one of our markers and the two need opposite answers. A
duplicated block is collapsed to the first copy: every copy is ours, so there is
nothing of theirs to lose. An orphan marker (a start with no end, or the
reverse) is refused outright, because every repair for it can eat the text that
follows. See :func:`upsert`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ..types import FileAction
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
) -> FileAction:
    """Ensure *path* carries exactly one well-formed ``start + body + end``.

    Replaces an existing block in place, appends one after existing content, or
    collapses a duplicated pair down to the first. Returns the action it took,
    so a caller can report ``unchanged`` for a re-run rather than claiming an
    update it did not make.

    *new_file_prefix* is written above the block when the file did not exist —
    a header telling the reader this file is theirs and only the marked section
    is managed. Pair it with ``remove(delete_if_only=...)`` so install and
    uninstall round-trip to "no file".

    The replacement goes through a function rather than a string because
    ``re.sub`` interprets backslashes and ``\\g<name>`` group references in a
    replacement *string*. Instruction bodies are prose that can legitimately
    contain either, and the failure mode is a corrupted managed block or a
    ``bad escape`` crash at install time.

    **An orphaned marker returns** :attr:`~..types.FileAction.KEPT` **and writes
    nothing.** A start with no end means a user deleted or edited across one of
    our markers, and every available repair loses something: appending a fresh
    block leaves the stray start above it, so the next run's non-greedy
    ``start.*?end`` spans from the orphan to *our* end marker and swallows
    whatever the user wrote in between. Refusing is the only option that cannot
    eat a paragraph, and the caller surfaces it as something to fix by hand.
    (Before this, an orphan silently reported "unchanged" while the block was in
    fact absent — the writer this helper replaced had the same blind spot.)
    """
    wrapped = f"{start}{body}{end}"
    inspection = inspect(path, start, end)

    if inspection.state is BlockState.ORPHANED:
        return FileAction.KEPT

    if inspection.state is BlockState.ABSENT_FILE:
        opening = f"{new_file_prefix}\n{wrapped}\n" if new_file_prefix else f"{wrapped}\n"
        atomic_write_text(path, opening, newline="\n")
        return FileAction.CREATED

    existing = path.read_text(encoding="utf-8")
    if inspection.state is BlockState.ABSENT:
        content = existing.rstrip() + "\n\n" + wrapped + "\n"
    else:
        pattern = re.escape(start) + r".*?" + re.escape(end)
        content = re.sub(pattern, lambda _m: wrapped, existing, count=1, flags=re.DOTALL)
        if inspection.state is BlockState.DUPLICATED:
            # Drop every later copy, taking the blank lines that separated it
            # with it so collapsing does not leave a run of empty lines behind.
            # Split on the block just written so the pass cannot touch it.
            head, marker, tail = content.partition(wrapped)
            content = head + marker + re.sub(r"\n*" + pattern + r"\n?", "", tail, flags=re.DOTALL)

    # Compare *bytes*, not the decoded text. ``read_text`` collapses CRLF to LF
    # in memory, so a CRLF file whose block is already current compares equal to
    # the LF content we are about to write — and short-circuiting there would
    # leave the file CRLF, where an unconditional write normalised it. That is a
    # whole-file diff on any Windows checkout with ``core.autocrlf=true``.
    if content.encode("utf-8") == path.read_bytes():
        return FileAction.UNCHANGED
    atomic_write_text(path, content, newline="\n")
    return FileAction.UPDATED


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
