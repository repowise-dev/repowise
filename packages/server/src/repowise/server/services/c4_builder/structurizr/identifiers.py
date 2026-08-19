"""Turn our node ids into Structurizr identifiers, stably.

Structurizr identifiers are ``[a-zA-Z_0-9]`` only, while ours are shaped like
``pkg:packages/core`` and ``cmp:packages/core/ingestion``. Sanitising is easy;
staying stable is the hard part, and it is the requirement that matters —
people commit the emitted file and read its diff, so an identifier that moves
between runs makes a correct model look broken.

Two rules give that:

* **Collisions are resolved from the id, never from iteration order.** When
  two different ids sanitise to the same string, *both* get a suffix derived
  from a hash of the full original id. Numbering them "1" and "2" in the order
  they happened to be visited means adding an unrelated file can renumber
  them, which is exactly the churn we are avoiding.
* **The whole scope is mapped at once.** :func:`identifiers_for` takes every
  id in a scope and returns the complete mapping, so whether an id collides
  cannot depend on when it was asked for.

Pure: knows nothing about C4, the database, or Structurizr's grammar beyond
the character set. Kept that way so it is reusable if we ever emit a second
format.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable

_UNSAFE = re.compile(r"[^a-zA-Z0-9_]+")

#: Hex digits of id hash appended to a colliding identifier. Six keeps the
#: identifier readable while making an accidental second collision remote.
_SUFFIX_LEN = 6

#: Prepended when sanitising leaves something that cannot open an identifier.
_LEADING_PAD = "id_"


def sanitize(raw: str) -> str:
    """Reduce *raw* to Structurizr's identifier character set.

    Lossy and deliberately not injective — ``API Gateway``, ``api/gateway``
    and ``API-Gateway`` all land on the same string. :func:`identifiers_for`
    is what makes the result unique; do not use this alone.
    """
    cleaned = _UNSAFE.sub("_", raw).strip("_")
    if not cleaned:
        return _LEADING_PAD.rstrip("_")
    if cleaned[0].isdigit():
        return _LEADING_PAD + cleaned
    return cleaned


def _disambiguator(raw: str) -> str:
    """A short, stable suffix derived from the whole original id."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:_SUFFIX_LEN]


def identifiers_for(raw_ids: Iterable[str]) -> dict[str, str]:
    """Map every id in one scope to a unique Structurizr identifier.

    The result depends only on the *set* of ids, so re-running on an unchanged
    model produces an unchanged file, and adding one element never renames
    another.
    """
    unique = sorted(set(raw_ids))
    by_slug: dict[str, list[str]] = defaultdict(list)
    for raw in unique:
        by_slug[sanitize(raw)].append(raw)

    out: dict[str, str] = {}
    for slug, owners in by_slug.items():
        if len(owners) == 1:
            out[owners[0]] = slug
            continue
        # Every owner is suffixed, not just the ones after the first: which id
        # "wins" the bare slug would otherwise depend on ordering.
        for raw in owners:
            out[raw] = f"{slug}_{_disambiguator(raw)}"
    return out
