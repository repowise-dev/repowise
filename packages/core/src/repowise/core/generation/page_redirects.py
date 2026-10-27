"""Where a retired wiki page id sends the reader.

Wiki pages are public and linkable, so a page that stops being generated
cannot simply vanish — every id that was ever served has to keep resolving to
something.  This module owns that mapping and nothing else: it turns a retired
page id into the id of the page that took over its material.

Page ids are ``{page_type}:{target_path}`` (see ``models.compute_page_id``).
Retirements come in four shapes and the tables mirror that.  Two axes: whether
the rule is keyed by page *type* or by exact *id*, and whether the successor
can be named up front or has to be resolved against the store.

* **A whole page type folds into another, keeping its path.**  Its target path
  is usually the repository name, which differs per repo, so the rule cannot be
  written as a literal id.  :data:`SUPERSEDED_TYPES` rewrites the type and
  carries the path across unchanged.
* **One page with a fixed target path retires into another named page.**  The
  onboarding slots are ``onboarding/{slot}`` for every repo, so these can be
  written as exact ids in :data:`SUPERSEDED_IDS`.
* **A page type folds into the repository's single page of some other type.**
  Here the retired id carries nothing that identifies the successor — a layer
  page is keyed by its layer slug, and the overview by the repository name — so
  the successor cannot be named without reading the store.
  :data:`SUPERSEDED_TO_REPO_WIDE` names the successor *type* and the serving
  layer resolves it.
* **One page with a fixed target path folds into a repo-wide page.**  Both of
  the above at once, and the shape the retired onboarding slots need: the
  retired id is exact (``onboarding:onboarding/codebase_map`` in every repo)
  but the successor is the overview, whose id carries the repository name.
  Keying these by type is not available — the surviving onboarding pages share
  the type, so a type rule would redirect the whole collection — and
  :data:`SUPERSEDED_IDS` cannot name a per-repo successor.
  :data:`SUPERSEDED_IDS_TO_REPO_WIDE` closes that gap.

Retirements chain: a page folded into a second page that later folds into a
third must land on the third, not the second, or the redirect leads somewhere
that is itself gone.  :func:`resolve_superseded` follows the chain to its end.

Everything here fails loudly.  A redirect table is exactly the kind of thing
that rots silently — a cycle would hang, a typo'd successor would strand every
inbound link — and either would read as "no redirect needed" rather than as an
error.  So a cycle raises, a malformed id raises, and the shipped tables are
walked by a test that resolves every entry in them.
"""

from __future__ import annotations

# Retired page type → the page type that took over its material.  The target
# path is carried across unchanged.

SUPERSEDED_TYPES: dict[str, str] = {
    # The architecture diagram described the same repository at the same
    # altitude as the overview, in the same words.  Its diagram moved to the
    # overview and the page retired.
    "architecture_diagram": "repo_overview",
}
# Retired page id → successor page id, in full.  For pages whose target path is
# the same in every repository.
SUPERSEDED_IDS: dict[str, str] = {}

# Retired page type → the page type of the *one* repo-wide page that took over.
#
# The other two tables can name their successor from the retired id alone.  This
# one cannot: a layer page is keyed by its layer slug (``layer_page:layer:core``)
# and the overview is keyed by the repository name, which the id does not carry.
# Rewriting the type would produce ``repo_overview:layer:core`` — an id no page
# has ever had.
#
# So these resolve at serving time instead, against the store, which is the only
# place that knows which repository is being read.  :func:`repo_wide_successor_type`
# reports the type to look up; the caller finds that repository's page of it.
SUPERSEDED_TO_REPO_WIDE: dict[str, str] = {
    # Layers stopped being pages and became grouping rows in the docs tree,
    # built from provenance stamped on their members.  A row is not a page, so
    # an inbound link to a retired layer page lands on the overview.
    "layer_page": "repo_overview",
}

# Retired page id → the page type of the *one* repo-wide page that took over.
#
# The id-keyed twin of the table above, for a page whose id is fixed across
# repositories but whose successor is not.  ``repo_wide_successor_type`` checks
# this before the type table, so a single retired id can fold into the overview
# while every other page of its type keeps being served.
SUPERSEDED_IDS_TO_REPO_WIDE: dict[str, str] = {
    # Three orientation pages retired together.  The Guided Tour's *data* did
    # not go anywhere — the ordered stops are still computed and still served
    # by ``get_overview`` and Present mode from the overview's own metadata —
    # so the page's material is genuinely on the overview.  The Codebase Map
    # described the repository at the overview's altitude in the overview's
    # words.  The Development Guide reported filename shapes as if they were
    # procedures, and had no successor worth naming.
    "onboarding:onboarding/guided_tour": "repo_overview",
    "onboarding:onboarding/codebase_map": "repo_overview",
    "onboarding:onboarding/development_guide": "repo_overview",
}

# Every retired id, whichever table names it.  The sweep and the config-key
# validator both need "was this an onboarding slot once?" and neither should
# keep its own list: a slot that is redirected but not swept is the exact state
# ``sweep_retired_pages`` exists to clear.
RETIRED_IDS: frozenset[str] = frozenset(SUPERSEDED_IDS) | frozenset(SUPERSEDED_IDS_TO_REPO_WIDE)

# A chain longer than this is a table authoring mistake rather than a real
# history — the orientation set has never held more than a handful of pages.
# Bounded so a table that somehow evades cycle detection cannot spin forever.
_MAX_CHAIN = 16


class SupersededError(Exception):
    """Base for redirect-table failures."""


class SupersededCycleError(SupersededError):
    """A retirement chain loops, so it has no destination."""


class SupersededTargetError(SupersededError):
    """A page id is not ``{page_type}:{target_path}``."""


def _split(page_id: str) -> tuple[str, str]:
    """Split an id into (page_type, target_path) on the first colon.

    Target paths may themselves contain colons, so only the first separator is
    significant.
    """
    page_type, sep, target_path = page_id.partition(":")
    if not sep or not page_type:
        raise SupersededTargetError(f"Page id {page_id!r} is not '{{page_type}}:{{target_path}}'.")
    return page_type, target_path


def _successor(
    page_id: str,
    superseded_types: dict[str, str],
    superseded_ids: dict[str, str],
) -> str | None:
    """One hop, or ``None`` when this id is still live.

    An exact-id rule is more specific than a type-level one and wins.
    """
    exact = superseded_ids.get(page_id)
    if exact is not None:
        _split(exact)  # a successor that is not a page id strands the link
        return exact

    page_type, target_path = _split(page_id)
    successor_type = superseded_types.get(page_type)
    if successor_type is None:
        return None
    return f"{successor_type}:{target_path}"


def resolve_superseded(
    page_id: str,
    *,
    superseded_types: dict[str, str] | None = None,
    superseded_ids: dict[str, str] | None = None,
) -> str | None:
    """The live page id a retired one now points at, or ``None``.

    Follows retirement chains to their end, so the returned id is the page that
    is actually generated today rather than an intermediate that has itself
    been retired.

    Args:
        page_id: The id to resolve, ``{page_type}:{target_path}``.
        superseded_types: Overrides the shipped type table.  For tests.
        superseded_ids: Overrides the shipped exact-id table.  For tests.

    Returns:
        The successor id, or ``None`` when ``page_id`` is not retired.

    Raises:
        SupersededTargetError: ``page_id`` or a successor is malformed.
        SupersededCycleError: the chain loops and has no destination.
    """
    types = SUPERSEDED_TYPES if superseded_types is None else superseded_types
    ids = SUPERSEDED_IDS if superseded_ids is None else superseded_ids

    _split(page_id)  # reject a malformed id even when no rule matches

    seen = [page_id]
    current = page_id
    for _ in range(_MAX_CHAIN):
        nxt = _successor(current, types, ids)
        if nxt is None:
            # ``current`` is live.  It is a redirect only if we moved at all.
            return current if current != page_id else None
        if nxt in seen:
            raise SupersededCycleError(
                "Retirement chain loops and has no destination: " + " -> ".join([*seen, nxt])
            )
        seen.append(nxt)
        current = nxt

    raise SupersededCycleError(
        f"Retirement chain from {page_id!r} exceeded {_MAX_CHAIN} hops: " + " -> ".join(seen)
    )


def repo_wide_successor_type(page_id: str) -> str | None:
    """The page type a retired repo-wide page hands off to, if any.

    Returns the *type* rather than an id because the successor is keyed by the
    repository, which ``page_id`` does not carry.  The caller resolves it
    against the store it is already reading.

    An exact-id rule is more specific than a type-level one and wins, matching
    :func:`_successor`.  That ordering is what lets three onboarding slots
    retire into the overview while every other page of type ``onboarding``
    keeps being served as itself.

    Raises:
        SupersededTargetError: ``page_id`` is not ``{page_type}:{target_path}``.
    """
    exact = SUPERSEDED_IDS_TO_REPO_WIDE.get(page_id)
    if exact is not None:
        return exact

    page_type, _ = _split(page_id)
    return SUPERSEDED_TO_REPO_WIDE.get(page_type)
