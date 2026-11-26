"""A change's category, read from the project's own labels before its title.

Many projects say what a change is in a form they maintain: a towncrier news
fragment the change adds (``CHANGES/13743.bugfix.rst``, ``changelog/123.feature.md``)
or a label on the pull request (``bug``, ``type: enhancement``). Those beat any
reading of the title, so they decide first:

1. **Changelog fragment.** A file the change adds under a news-fragment
   directory whose name is ``<id>.<type>[.<n>][.<ext>]``. The type is what the
   project's changelog prints the change under.
2. **Forge label.** A label the metadata source read from the forge. The
   default ``git`` source reads none, so this applies only when a source fills
   ``labels``.
3. **Title.** :func:`classify_commit_category`: the keyword fix rule, then a
   conventional-commit type, then keywords.

A fragment type towncrier projects use for a kind we have no category for
(``misc``, ``breaking``, ``trivial``) reads ``other``: the project filed the change
outside its bug fixes and features, so a "Fix ..." title does not make it one. A
fragment type or label not listed here (``needs-review``, ``security``) says
nothing and falls through to the next signal; nothing is mapped by guess. Changesets (``.changeset/*.md``) declare a semver bump, not a
kind of change, so they are not read. The chosen signal is returned with the
category so a record can say where it came from.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from repowise.core.ingestion.git_indexer._constants import (
    EVOLUTION_CATEGORIES,
    classify_commit_category,
)

SIGNAL_FRAGMENT = "changelog_fragment"
SIGNAL_LABEL = "forge_label"
SIGNAL_TITLE = "title"

#: Directory names towncrier-style tools keep unreleased fragments in. Matched
#: on the fragment's parent directory, case-insensitively, at any depth
#: (``src/twisted/newsfragments``, ``docs/changelog.d``).
_FRAGMENT_DIRS = frozenset(
    {
        "changes",
        "changes.d",
        "changelog",
        "changelog.d",
        "changelog_fragments",
        "newsfragments",
        "news",
        "fragments",
    }
)
_FRAGMENT_NAME = re.compile(
    r"^(?P<id>\+[^.\s]*|[^.\s]*\d[^.\s]*)\.(?P<type>[a-z_-]+)(?:\.\d+)?(?:\.(?:rst|md|markdown|txt))?$",
    re.IGNORECASE,
)
#: The id is an issue number, a hash or a ``+`` orphan: it holds a digit or
#: starts with ``+``, so ``news/story.doc`` or ``changes/login.feature`` is not one.
#: Fragment type -> category. Common towncrier types with no category of ours
#: read ``other``; a type not listed is not read as a fragment at all.
_FRAGMENT_TYPES: dict[str, str] = {
    "bugfix": "fix",
    "bugfixes": "fix",
    "bug": "fix",
    "bugs": "fix",
    "fix": "fix",
    "fixes": "fix",
    "fixed": "fix",
    "feature": "feature",
    "features": "feature",
    "feat": "feature",
    "added": "feature",
    "doc": "docs",
    "docs": "docs",
    "documentation": "docs",
    **dict.fromkeys(
        (
            "breaking",
            "deprecation",
            "removal",
            "misc",
            "contrib",
            "packaging",
            "improvement",
            "vendor",
            "trivial",
            "process",
        ),
        "other",
    ),
}
#: A fix ranks first, as :func:`classify_commit_category` checks the fix rule
#: first; the rest keep their timeline order.
_RANK = ("fix", *(c for c in EVOLUTION_CATEGORIES if c != "fix"))
#: Forge label (lowercased, last ``:``/``/`` segment, punctuation trimmed) ->
#: category: ``bug``, ``type: bug``, ``kind/bug`` and ``🐛 bug`` all read ``bug``;
#: other namespaces (``area/docs``) are not read.
_LABELS: dict[str, str] = {
    "bug": "fix",
    "bugfix": "fix",
    "bug fix": "fix",
    "fix": "fix",
    "regression": "fix",
    "feature": "feature",
    "enhancement": "feature",
    "new feature": "feature",
    "documentation": "docs",
    "docs": "docs",
    "dependencies": "deps",
    "deps": "deps",
    "refactor": "refactor",
    "refactoring": "refactor",
    "test": "test",
    "tests": "test",
}
_KIND_NAMESPACES = frozenset({"type", "kind", "category", "changelog"})
_LABEL_TRIM = re.compile(r"^[^a-z0-9]+|[^a-z0-9]+$")


@dataclass(frozen=True)
class CategoryBasis:
    """A change's category and the signal that decided it."""

    category: str
    signal: str
    #: The fragment path or label that decided; ``None`` for the title.
    value: str | None = None


def fragment_category(path: str) -> str | None:
    """The category a news-fragment *path* declares, or ``None`` if it is not one."""
    p = PurePosixPath(path)
    if len(p.parts) < 2 or p.parent.name.lower() not in _FRAGMENT_DIRS:
        return None
    match = _FRAGMENT_NAME.match(p.name)
    if match is None:
        return None
    return _FRAGMENT_TYPES.get(match.group("type").lower())


def label_category(label: str) -> str | None:
    """The category a forge *label* declares, or ``None``.

    A namespaced label counts only under a namespace that names a kind
    (``type: bug``, ``kind/bug``); ``area/docs`` names a place, not a kind.
    """
    parts = re.split(r"[:/]", (label or "").lower(), maxsplit=1)
    if len(parts) == 2 and _LABEL_TRIM.sub("", parts[0]) not in _KIND_NAMESPACES:
        return None
    return _LABELS.get(_LABEL_TRIM.sub("", parts[-1]))


def _first(found: list[tuple[str, str]]) -> tuple[str, str] | None:
    """The hit whose category ranks first: a fix, then the timeline order."""
    if not found:
        return None
    return min(found, key=lambda hit: (_RANK.index(hit[0]), hit[1]))


def classify_change(
    title: str,
    files: Iterable[tuple[str, int, int]] = (),
    labels: Sequence[str] | None = None,
) -> CategoryBasis:
    """The category of a change with *title*, *files* ``(path, added, deleted)`` and *labels*.

    A fragment counts when the change writes it; a change that only deletes
    fragments is a release collecting them, not a change of that kind. When a
    change carries several kinds, a fix wins, as it does for the title, then
    the order of :data:`EVOLUTION_CATEGORIES`.
    """
    fragments = [
        (category, path)
        for path, added, deleted in files
        if not (added == 0 and deleted > 0) and (category := fragment_category(path))
    ]
    if hit := _first(fragments):
        return CategoryBasis(hit[0], SIGNAL_FRAGMENT, hit[1])
    tagged = [(category, label) for label in labels or () if (category := label_category(label))]
    if hit := _first(tagged):
        return CategoryBasis(hit[0], SIGNAL_LABEL, hit[1])
    return CategoryBasis(classify_commit_category(title), SIGNAL_TITLE)


__all__ = [
    "SIGNAL_FRAGMENT",
    "SIGNAL_LABEL",
    "SIGNAL_TITLE",
    "CategoryBasis",
    "classify_change",
    "fragment_category",
    "label_category",
]
