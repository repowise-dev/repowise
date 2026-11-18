"""What a git commit id looks like: the one owner of that shape.

A full id is 40 hex digits (SHA-1) or 64 (a SHA-256 repository). An
abbreviation is a prefix of one, 7 digits or more by default. Ids are
lowercase, as git writes them and as every store keeps them, so an id with
uppercase digits does not validate here: a caller reading one that a person
typed or a file carried lowercases it first, then checks.

This module imports nothing from the package, so a parser, a file-format
validator or a forge helper can use it without pulling in git plumbing.
"""

from __future__ import annotations

import re

__all__ = [
    "FULL_SHA_PATTERN",
    "SHA_PREFIX_PATTERN",
    "is_full_sha",
    "is_sha_prefix",
]

#: A full commit id, for callers that scan free text and add their own anchors.
FULL_SHA_PATTERN = r"[0-9a-f]{40}(?:[0-9a-f]{24})?"
#: A full or abbreviated commit id, 7 to 64 digits, for the same callers.
SHA_PREFIX_PATTERN = r"[0-9a-f]{7,64}"

_FULL_SHA = re.compile(FULL_SHA_PATTERN)
_HEX = re.compile(r"[0-9a-f]+")


def is_full_sha(value: object) -> bool:
    """True when *value* is a whole 40- or 64-digit lowercase commit id."""
    return isinstance(value, str) and _FULL_SHA.fullmatch(value) is not None


def is_sha_prefix(value: object, min_len: int = 7) -> bool:
    """True when *value* is *min_len* to 64 lowercase hex digits."""
    return (
        isinstance(value, str)
        and min_len <= len(value) <= 64
        and _HEX.fullmatch(value) is not None
    )
