"""Resolve a user intent into the set of wiki page ids to (re)generate.

``repowise generate`` lets a user name *which* pages to write with a model:
everything unwritten, everything, a path prefix, explicit ids, or the stale
ones. This module turns that intent into a concrete id set, evaluated against
the pages that already exist in the wiki.

It is deliberately pure: it takes a list of lightweight :class:`PageRecord`
views (built by the CLI/server from the persisted pages) plus a
:class:`PageSelectionIntent`, and returns a ``set[str]`` of page ids. The
scoped generation engine (``generate_all(only_page_ids=...)``) consumes that
set verbatim, and the cascade layer expands it before it reaches the engine.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field

# Page freshness values that count as "needs refresh" for ``--stale``.
_STALE_STATUSES = frozenset({"stale", "expired"})


@dataclass(frozen=True)
class PageRecord:
    """A minimal view of one persisted wiki page.

    Built by the caller from the page store (id, type, target path, provenance
    and freshness). Kept tiny so the resolver never depends on persistence
    models and stays trivially unit-testable.
    """

    page_id: str
    page_type: str
    target_path: str
    is_template: bool
    freshness_status: str = "fresh"

    @property
    def is_stale(self) -> bool:
        return self.freshness_status in _STALE_STATUSES


@dataclass(frozen=True)
class PageSelectionIntent:
    """What the user asked to generate.

    The flags and filters are additive: the resolved set is the union of every
    non-empty selector. An intent with nothing set resolves to the default the
    caller chooses (``repowise generate`` defaults to ``unwritten``); this
    module does not invent a default, it just unions what it is given.
    """

    all_pages: bool = False
    unwritten: bool = False
    stale: bool = False
    path_globs: tuple[str, ...] = ()
    page_ids: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        return not (
            self.all_pages or self.unwritten or self.stale or self.path_globs or self.page_ids
        )


@dataclass
class PageSelectionResult:
    """Resolved id set plus what was asked for but not found.

    ``unknown_page_ids`` lets the caller warn on an explicit ``--page`` id that
    matches no existing page (a typo, or a page type that must be generated
    fresh) rather than silently dropping it.
    """

    page_ids: set[str] = field(default_factory=set)
    unknown_page_ids: tuple[str, ...] = ()

    def __len__(self) -> int:
        return len(self.page_ids)


def _matches_glob(target_path: str, globs: tuple[str, ...]) -> bool:
    """True if *target_path* matches any glob (both slash-normalized).

    A bare prefix like ``packages/cli`` matches everything under it, so callers
    can pass a directory without a trailing ``/**``.
    """
    norm = target_path.replace("\\", "/")
    for raw in globs:
        g = raw.replace("\\", "/").rstrip("/")
        if fnmatch.fnmatch(norm, g) or fnmatch.fnmatch(norm, f"{g}/*") or norm == g:
            return True
    return False


def resolve_page_selection(
    records: list[PageRecord],
    intent: PageSelectionIntent,
) -> PageSelectionResult:
    """Resolve *intent* against the existing *records* into a page-id set.

    The result is the union of every selector the intent sets:

    * ``all_pages`` — every existing page.
    * ``unwritten`` — every template (deterministic) page, i.e. the pages a
      model has not written yet.
    * ``stale`` — every page whose freshness is stale or expired.
    * ``path_globs`` — every page whose ``target_path`` matches a glob/prefix.
    * ``page_ids`` — the named ids, when they exist. Names that match no record
      are returned in ``unknown_page_ids`` instead of being included.
    """
    by_id = {r.page_id: r for r in records}
    selected: set[str] = set()

    if intent.all_pages:
        selected.update(by_id)
    if intent.unwritten:
        selected.update(r.page_id for r in records if r.is_template)
    if intent.stale:
        selected.update(r.page_id for r in records if r.is_stale)
    if intent.path_globs:
        selected.update(
            r.page_id for r in records if _matches_glob(r.target_path, intent.path_globs)
        )

    unknown: list[str] = []
    for pid in intent.page_ids:
        if pid in by_id:
            selected.add(pid)
        else:
            unknown.append(pid)

    return PageSelectionResult(page_ids=selected, unknown_page_ids=tuple(unknown))
