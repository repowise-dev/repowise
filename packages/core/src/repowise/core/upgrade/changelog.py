"""Parse ``CHANGELOG.md`` (Keep-a-Changelog) into structured release entries.

Shared by the CLI ("what's new" panel + ``repowise whats-new``) and the web
``/api/meta/changelog`` endpoint, so the format is parsed once here rather than
in each presenter.

The parser is intentionally forgiving: it recognises ``## [X.Y.Z] - date``
release headers (and ``## [Unreleased]``, as version ``unreleased``) and
``### Section`` subsection headers, and collects the bullet lines under each.
The prose between a release header and its first section is the release
summary. ``### Highlights`` bullets (``- **Title** · blurb. → /route``) come back
as structured highlights, and ``### By the numbers`` is left out of the sections
because its figures ship as data in ``_data/release_stats.json``. Anything it
does not understand (the preamble, HTML comments, horizontal rules) is skipped
rather than treated as an error.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ``## [0.21.0] - 2026-06-19`` or ``## [0.21.0] — Unreleased`` (any dash). The
# version must be a dotted numeric or ``Unreleased``; the trailing label (date /
# "Unreleased") is optional and captured loosely.
# The trailing label group allows an empty label (a stray ``-`` with no date)
# so a malformed header degrades to ``label=None`` instead of dropping the whole
# release. ``.*\S`` keeps the captured label trimmed of trailing whitespace.
_RELEASE_RE = re.compile(
    r"^##\s*\[(?P<version>\d+\.\d+(?:\.\d+)?|[Uu]nreleased)\]\s*(?:[-–—]\s*(?P<label>.*\S)?)?\s*$"
)
_SECTION_RE = re.compile(r"^###\s+(?P<name>.+?)\s*$")
_BULLET_RE = re.compile(r"^[-*]\s+(?P<text>.+?)\s*$")
# ``**Title** · blurb. → /route``. The separator after the title may be ``·``,
# ``:``, ``|`` or a dash, and the trailing route (``→`` or ``->``) is optional,
# so a loosely written highlight still parses.
_HIGHLIGHT_RE = re.compile(
    r"^\*\*(?P<title>.+?)\*\*(?:\s*[·:|]|\s+[-–—](?=\s))?\s*(?P<rest>.*)$"
)
_ROUTE_RE = re.compile(r"\s*(?:→|->)\s*(?P<href>/\S*)\s*$")

#: Version string the parser gives ``## [Unreleased]``.
UNRELEASED = "unreleased"
HIGHLIGHTS_SECTION = "Highlights"
NUMBERS_SECTION = "By the numbers"

# Unreleased sorts above every release, so it is never "already seen".
_UNRELEASED_RANK = 10**9


@dataclass(frozen=True, slots=True)
class ChangelogSection:
    """A ``### Added`` / ``### Fixed`` group within one release."""

    name: str
    items: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ChangelogHighlight:
    """One ``### Highlights`` bullet. ``id`` is the title's slug, so it stays
    stable while the blurb is edited."""

    id: str
    title: str
    blurb: str
    href: str | None = None


@dataclass(frozen=True, slots=True)
class ChangelogEntry:
    """One ``## [X.Y.Z]`` release with its grouped bullet sections."""

    version: str
    label: str | None
    sections: tuple[ChangelogSection, ...] = field(default_factory=tuple)
    #: The prose paragraphs between the release header and its first section.
    summary: str = ""
    highlights: tuple[ChangelogHighlight, ...] = field(default_factory=tuple)

    @property
    def release_tuple(self) -> tuple[int, ...]:
        """Numeric release tuple for ordering/comparison (e.g. (0, 21, 0))."""
        if self.version == UNRELEASED:
            return (_UNRELEASED_RANK,)
        return tuple(int(p) for p in self.version.split("."))

    @property
    def is_empty(self) -> bool:
        return not self.summary and not self.highlights and not any(s.items for s in self.sections)


def parse_highlight(text: str) -> ChangelogHighlight:
    """Parse one highlight bullet. A bullet without a bold title keeps its whole
    text as the title, so a loosely written line is never lost."""
    href = None
    route = _ROUTE_RE.search(text)
    if route:
        href = route.group("href")
        text = text[: route.start()]
    m = _HIGHLIGHT_RE.match(text.strip())
    if m:
        title, blurb = m.group("title").strip(), m.group("rest").strip()
    else:
        title, blurb = text.strip(), ""
    return ChangelogHighlight(id=_slug(title), title=title, blurb=blurb, href=href)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def parse_changelog(text: str) -> list[ChangelogEntry]:
    """Parse Keep-a-Changelog *text* into entries, newest first (as written).

    An ``Unreleased`` block with no content is dropped (the normal state right
    after a release).
    """
    entries = [_parse_release(*block) for block in _release_blocks(text)]
    return [e for e in entries if not (e.version == UNRELEASED and e.is_empty)]


def _release_blocks(text: str):
    """``(version, label, lines)`` per release header; the preamble is skipped."""
    version: str | None = None
    label: str | None = None
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        rel = _RELEASE_RE.match(line)
        if rel:
            if version is not None:
                yield version, label, lines
            found = rel.group("version")
            version = UNRELEASED if found.lower() == UNRELEASED else found
            label, lines = rel.group("label"), []
        elif version is not None:
            lines.append(line)
    if version is not None:
        yield version, label, lines


def _parse_release(version: str, label: str | None, lines: list[str]) -> ChangelogEntry:
    """One release's lines: summary prose up to the first ``###``, then
    sections of bullets. Highlights come out separately and By the numbers
    is dropped."""
    summary: list[str] = []
    groups: list[tuple[str, list[str]]] = []
    for line in lines:
        sec = _SECTION_RE.match(line)
        if sec:
            groups.append((sec.group("name"), []))
        elif not groups:
            summary.append(line)
        elif bullet := _BULLET_RE.match(line):
            groups[-1][1].append(bullet.group("text"))
    highlights = [parse_highlight(i) for name, items in groups if name == HIGHLIGHTS_SECTION for i in items]
    sections = [
        ChangelogSection(name=name, items=tuple(items))
        for name, items in groups
        if name not in (HIGHLIGHTS_SECTION, NUMBERS_SECTION)
    ]
    return ChangelogEntry(
        version=version,
        label=label,
        sections=tuple(sections),
        summary=_join_paragraphs(summary),
        highlights=tuple(highlights),
    )


def _join_paragraphs(lines: list[str]) -> str:
    """Summary lines to paragraphs, dropping rules and HTML comments."""
    paragraphs: list[str] = []
    cur: list[str] = []
    for line in [*lines, ""]:
        text = line.strip()
        if not text or text == "---" or text.startswith("<!--"):
            if cur:
                paragraphs.append(" ".join(cur))
            cur = []
            continue
        cur.append(text)
    return "\n\n".join(paragraphs)


def entries_between(
    entries: list[ChangelogEntry],
    *,
    newer_than: str | None,
    up_to: str | None = None,
) -> list[ChangelogEntry]:
    """Return entries with ``newer_than < version <= up_to``.

    ``newer_than`` is the version the user last saw (exclusive); ``up_to`` is the
    running version (inclusive) when given. ``None`` bounds are open. Entries
    whose version cannot be compared are skipped. Result is newest-first.
    """
    low = _release_tuple(newer_than) if newer_than else None
    high = _release_tuple(up_to) if up_to else None
    out: list[ChangelogEntry] = []
    for e in entries:
        v = e.release_tuple
        if low is not None and not _gt(v, low):
            continue
        if high is not None and _gt(v, high):
            continue
        out.append(e)
    return out


def _release_tuple(version: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(p) for p in version.strip().split("."))
    except (ValueError, AttributeError):
        return None


def _gt(a: tuple[int, ...], b: tuple[int, ...] | None) -> bool:
    if b is None:
        return True
    length = max(len(a), len(b))
    a = a + (0,) * (length - len(a))
    b = b + (0,) * (length - len(b))
    return a > b


__all__ = [
    "HIGHLIGHTS_SECTION",
    "NUMBERS_SECTION",
    "UNRELEASED",
    "ChangelogEntry",
    "ChangelogHighlight",
    "ChangelogSection",
    "entries_between",
    "parse_changelog",
    "parse_highlight",
]
