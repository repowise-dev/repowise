"""How much a page actually says, and whether that is enough to index it.

Search fetches a fixed number of rows before it filters anything, so every
indexed page occupies a slot whether or not it can answer a question. A page
that restates its own filename and then says three sections' worth of
nothing still takes one of those slots from a page that could have.

The page itself is kept — it is a valid link target and a reader arriving at
it learns the file exists and has no callers. It is only kept out of the
index, where its cost is paid by other pages.
"""

from __future__ import annotations

import os
import re

__all__ = [
    "DEFAULT_INFORMATION_FLOOR",
    "INFORMATION_FLOOR_ENV",
    "information_floor",
    "meets_information_floor",
    "substantive_text",
]

# Chars of substantive prose a page needs before it is worth a search slot.
#
# Zero: off. Nothing is excluded until a value is set, so installing this
# changes no one's index.
#
# The mechanism ships ahead of the number on purpose. Excluding pages is a
# deletion, and what it costs is a retrieval measurement rather than an
# argument — a thin page can still be the only route to a file, in which case
# holding it out loses the file. Measured against a 3,678-page corpus:
#
#     floor 200 →  146 pages excluded  (4.0%)
#     floor 300 →  516                (14.0%)
#     floor 400 →  914                (24.9%)
#     floor 500 → 1186                (32.2%)
#
# No orientation page is excluded at any of those levels — no layer page, no
# onboarding page, no architecture diagram. What goes is file pages whose
# every section is a placeholder, and symbol spotlights that restate a
# signature. 300 is the level at which what is dropped is plainly not an
# answer to anything, and is the first value worth sweeping.
DEFAULT_INFORMATION_FLOOR = 0

# Tunable without a redeploy, because the right value depends on how verbose a
# repository's generated pages are, and finding it means sweeping it.
INFORMATION_FLOOR_ENV = "REPOWISE_INDEX_INFORMATION_FLOOR"

_FENCE = re.compile(r"^\s*```")
_HEADING = re.compile(r"^\s*#{1,6}\s")
# ``_No internal dependencies resolved._`` and its siblings: a whole line in
# italics, which in these templates is only ever a "there is nothing here".
_PLACEHOLDER = re.compile(r"^\s*_[^_]*_\s*$")
# ``**Kind:** interface | **Defined in:** ...`` — a metadata strip, not prose.
_FIELD_LINE = re.compile(r"^\s*\*\*[^*]+:\*\*")
_TABLE_ROW = re.compile(r"^\s*\|")
_HORIZONTAL_RULE = re.compile(r"^\s*-{3,}\s*$")
# The provenance trailer every generated page ends with, in italics. It is on
# 97% of pages in the measured corpus and says the same thing on all of them.
#
# Three things open with ``*`` and only one of them is this: a bullet written
# ``* item`` (a space follows) and a bold field line ``**Kind:** …`` (another
# star follows). Both are excluded here, and the field line is also classified
# before this runs — matching it as an unterminated italic opened a block that
# never closed and swallowed every page that carried one.
_ITALIC_OPEN = re.compile(r"^\s*\*(?![\s*])")
_ITALIC_CLOSE = re.compile(r"\*\s*$")


def substantive_text(content: str) -> str:
    """The prose of *content*, with what every page carries stripped out.

    Removed: fenced code, headings, placeholder lines, metadata field lines,
    table rows, horizontal rules and the italic provenance trailer.

    Bullet lists are *kept*. They look like structure and are not: on a file
    page the dependency and caller lists are the page's most specific
    content, and on a layer or onboarding page the bullets are the page.
    Dropping them takes the exclusion from 3% of the measured corpus to 36%
    and starts excluding orientation pages, which are the ones a reader most
    needs to find.
    """
    out: list[str] = []
    in_fence = False
    in_italics = False
    for line in content.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence or not line.strip():
            continue
        # The trailer wraps over several lines and only the first carries the
        # opening marker, so it is consumed as a block rather than per line.
        if in_italics:
            if _ITALIC_CLOSE.search(line):
                in_italics = False
            continue
        # Classified before emphasis, so a line that merely starts with a star
        # cannot be mistaken for an italic block that never ends.
        if (
            _HEADING.match(line)
            or _PLACEHOLDER.match(line)
            or _FIELD_LINE.match(line)
            or _TABLE_ROW.match(line)
            or _HORIZONTAL_RULE.match(line)
        ):
            continue
        if _ITALIC_OPEN.match(line):
            if not _ITALIC_CLOSE.search(line.strip()[1:]):
                in_italics = True
            continue
        out.append(line.strip())
    return " ".join(out)


def information_floor() -> int:
    """The active floor, from the environment or the default.

    An unparseable or negative value falls back to the default rather than
    disabling the floor: a typo in a deployment variable should not silently
    change what is in the index.
    """
    raw = os.environ.get(INFORMATION_FLOOR_ENV)
    if raw is None:
        return DEFAULT_INFORMATION_FLOOR
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_INFORMATION_FLOOR
    return value if value >= 0 else DEFAULT_INFORMATION_FLOOR


def meets_information_floor(content: str, floor: int | None = None) -> bool:
    """Whether *content* says enough to be worth a slot in the index.

    A floor of 0 admits everything, which is how the feature is turned off.
    """
    limit = information_floor() if floor is None else floor
    if limit <= 0:
        return True
    return len(substantive_text(content)) >= limit
