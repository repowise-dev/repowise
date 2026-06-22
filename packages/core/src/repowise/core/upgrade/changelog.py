"""Parse ``CHANGELOG.md`` (Keep-a-Changelog) into structured release entries.

Shared by the CLI ("what's new" panel + ``repowise whats-new``) and, in a later
phase, the web ``/api/meta/changelog`` endpoint, so the format is parsed once
here rather than in each presenter.

The parser is intentionally forgiving: it recognises ``## [X.Y.Z] - date``
release headers and ``### Section`` subsection headers, and collects the bullet
lines under each. Anything it does not understand (the preamble, HTML comments,
horizontal rules) is skipped rather than treated as an error.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ``## [0.21.0] - 2026-06-19`` or ``## [0.21.0] — Unreleased`` (any dash). The
# version must be a dotted numeric; the trailing label (date / "Unreleased") is
# optional and captured loosely.
# The trailing label group allows an empty label (a stray ``-`` with no date)
# so a malformed header degrades to ``label=None`` instead of dropping the whole
# release. ``.*\S`` keeps the captured label trimmed of trailing whitespace.
_RELEASE_RE = re.compile(
    r"^##\s*\[(?P<version>\d+\.\d+(?:\.\d+)?)\]\s*(?:[-–—]\s*(?P<label>.*\S)?)?\s*$"  # noqa: RUF001
)
_SECTION_RE = re.compile(r"^###\s+(?P<name>.+?)\s*$")
_BULLET_RE = re.compile(r"^[-*]\s+(?P<text>.+?)\s*$")


@dataclass(frozen=True, slots=True)
class ChangelogSection:
    """A ``### Added`` / ``### Fixed`` group within one release."""

    name: str
    items: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ChangelogEntry:
    """One ``## [X.Y.Z]`` release with its grouped bullet sections."""

    version: str
    label: str | None
    sections: tuple[ChangelogSection, ...] = field(default_factory=tuple)

    @property
    def release_tuple(self) -> tuple[int, ...]:
        """Numeric release tuple for ordering/comparison (e.g. (0, 21, 0))."""
        return tuple(int(p) for p in self.version.split("."))


def parse_changelog(text: str) -> list[ChangelogEntry]:
    """Parse Keep-a-Changelog *text* into entries, newest first (as written)."""
    entries: list[ChangelogEntry] = []
    cur_version: str | None = None
    cur_label: str | None = None
    sections: list[ChangelogSection] = []
    cur_section_name: str | None = None
    cur_items: list[str] = []

    def _flush_section() -> None:
        nonlocal cur_section_name, cur_items
        if cur_section_name is not None:
            sections.append(ChangelogSection(name=cur_section_name, items=tuple(cur_items)))
        cur_section_name = None
        cur_items = []

    def _flush_release() -> None:
        nonlocal cur_version, cur_label, sections
        _flush_section()
        if cur_version is not None:
            entries.append(
                ChangelogEntry(version=cur_version, label=cur_label, sections=tuple(sections))
            )
        cur_version = None
        cur_label = None
        sections = []

    for raw in text.splitlines():
        line = raw.rstrip()
        rel = _RELEASE_RE.match(line)
        if rel:
            _flush_release()
            cur_version = rel.group("version")
            cur_label = rel.group("label")
            continue
        if cur_version is None:
            continue  # still in the preamble
        sec = _SECTION_RE.match(line)
        if sec:
            _flush_section()
            cur_section_name = sec.group("name")
            continue
        bullet = _BULLET_RE.match(line)
        if bullet and cur_section_name is not None:
            cur_items.append(bullet.group("text"))

    _flush_release()
    return entries


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
    "ChangelogEntry",
    "ChangelogSection",
    "entries_between",
    "parse_changelog",
]
