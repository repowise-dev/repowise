"""Renamed biomarker ids: old ids still work as input, and public ids stay stable.

Public ids hash a renamed marker's first id, so finding and plan ids, and the
triage recorded against them, hold across a rename.
"""

from __future__ import annotations

from collections.abc import Iterable

LEGACY_BIOMARKER_NAMES: dict[str, str] = {
    "bumpy_road": "repeated_nesting",
}  # old id -> current id; append only

_FIRST_NAMES: dict[str, str] = {new: old for old, new in LEGACY_BIOMARKER_NAMES.items()}


def canonical_biomarker(name: str) -> str:
    """The current id for *name*; ids that were never renamed pass through."""
    return LEGACY_BIOMARKER_NAMES.get(name, name)


def canonical_biomarkers(names: Iterable[str]) -> list[str]:
    """:func:`canonical_biomarker` over *names*, order kept."""
    return [canonical_biomarker(n) for n in names]


def identity_biomarker(name: str) -> str:
    """The id a public-id hash reads for *name*: the marker's first id."""
    current = canonical_biomarker(name)
    return _FIRST_NAMES.get(current, current)
