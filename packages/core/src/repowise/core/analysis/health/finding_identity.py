"""The public identity of one health finding.

Every finding is republished on each analysis, so a storage row id is a fresh
UUID every time and cannot be quoted back. This kernel names the finding by
what it *is* instead, which makes the id stable across runs, storable as a
column, and safe to hand to an agent.

The kernel holds structural coordinates only: dimension, path, marker, the
symbol the finding sits in and where inside it. Detector ``details`` are
evidence (a CCN, a correlation, a coverage percentage) that moves with every
edit, so only the few keys a marker needs to tell two of its own findings on
one symbol apart are hashed (:data:`IDENTITY_DETAIL_KEYS`). Prose is excluded
for the same reason.

A finding inside a known symbol is located by the symbol's name, its ordinal
among same-named symbols in the file, and its line offset within that symbol
(the engine stamps ``symbol_line`` and, when needed, ``symbol`` and
``symbol_index`` into ``details``), so an edit above the symbol does not move
the id. A finding with no symbol, or one stored before that anchor existed,
keeps absolute lines.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from repowise.core.references import path_identity

from .rows import detail_map, field

FINDING_ID_VERSION = 2
"""Version of the identity kernel below.

It is hashed rather than spelled into the id, because the public reference
vocabulary is ``<kind>_<digest>`` across every tool and a finding has no
cross-version resolution story to tell: bumping this simply changes every
value, and an id minted by an older kernel stops matching.
"""

_PREFIX = "finding"

SYMBOL_LINE_KEY = "symbol_line"
"""Detail key holding the first line of the symbol a finding sits in."""

SYMBOL_KEY = "symbol"
"""Detail key naming the enclosing symbol of a finding the detector left unnamed."""

SYMBOL_INDEX_KEY = "symbol_index"
"""Detail key holding the ordinal among same-named symbols in the file, when not 0."""

_STAMP_KEYS = frozenset({SYMBOL_LINE_KEY, SYMBOL_KEY, SYMBOL_INDEX_KEY})

IDENTITY_DETAIL_KEYS: dict[str, tuple[str, ...]] = {
    # A loop can reach two helpers from one call line; the first hop names each.
    "io_in_loop": ("cross_function", "path"),
    "blocking_io_under_lock": ("cross_function", "path"),
    "lazy_load_in_loop": ("relationship",),
    "error_handling": ("kind",),
    "complex_conditional": ("enclosing_construct",),
    "duplicated_assertion_block": ("assertion_lines",),
    "sql_cartesian_join": ("table",),
    "hidden_coupling": ("partner",),
    "contradictory_decision": ("src_decision_id", "dst_decision_id"),
}
"""Per marker, the detail keys that separate two findings of it on one symbol.

Every other marker emits at most one finding per symbol and line, so it hashes
no details at all. ``path`` is reduced to its first hop and line-valued keys
are made relative to the symbol, so neither moves with an unrelated edit.
"""

_DERIVED_DETAIL_KEYS = frozenset(
    {
        "opportunity_id",
        "reliable_entry_reachability",
        "execution_role",
        "dispatch_share",
        "deprecated",
        "gated_off",
        "loop_line",
        "deepest_block",
    }
)
"""Keys version 1 left out of its hash; only :func:`legacy_finding_public_id` reads it."""


def _digest(kernel: dict[str, Any]) -> str:
    payload = json.dumps(kernel, sort_keys=True, separators=(",", ":"), default=str)
    return f"{_PREFIX}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:20]}"


def _identity_details(kind: str, details: dict[str, Any], anchor: int | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in IDENTITY_DETAIL_KEYS.get(kind, ()):
        value = details.get(key)
        if key == "path" and isinstance(value, list):
            value = value[1] if len(value) > 1 else None
        elif key == "assertion_lines" and anchor is not None and isinstance(value, list):
            value = [line - anchor for line in value if isinstance(line, int)]
        out[key] = value
    return out


def finding_public_id(row: Any) -> str:
    """The stable public id for one finding row."""
    details = detail_map(row)
    kind = field(row, "biomarker_type", "") or ""
    symbol = field(row, "function_name", None) or details.get(SYMBOL_KEY) or ""
    line_start = field(row, "line_start", None)
    anchor = details.get(SYMBOL_LINE_KEY)
    if not (symbol and isinstance(anchor, int) and isinstance(line_start, int)):
        anchor = None
    if anchor is not None:
        location: dict[str, Any] = {
            "symbol_index": details.get(SYMBOL_INDEX_KEY, 0),
            "line_offset": line_start - anchor,
        }
    else:
        location = {"line_start": line_start, "line_end": field(row, "line_end", None)}
    return _digest(
        {
            "kernel_version": FINDING_ID_VERSION,
            "dimension": field(row, "dimension", None) or "defect",
            "path": path_identity(field(row, "file_path", "") or ""),
            "kind": kind,
            "symbol": symbol,
            **location,
            "details": _identity_details(kind, details, anchor),
        }
    )


def legacy_finding_public_id(row: Any) -> str:
    """The id kernel version 1 minted for *row* (absolute lines, all details).

    Only the triage carry-over reads it, to match a row triaged under version 1
    to its re-detection. Removable once stores written before version 2 are gone.
    """
    details = {
        key: value
        for key, value in detail_map(row).items()
        if key not in _DERIVED_DETAIL_KEYS and key not in _STAMP_KEYS
    }
    return _digest(
        {
            "kernel_version": 1,
            "dimension": field(row, "dimension", None) or "defect",
            "path": path_identity(field(row, "file_path", "") or ""),
            "kind": field(row, "biomarker_type", "") or "",
            "symbol": field(row, "function_name", None) or "",
            "line_start": field(row, "line_start", None),
            "line_end": field(row, "line_end", None),
            "details": details,
        }
    )


def is_finding_public_id(value: str) -> bool:
    """Whether a caller-supplied string has the shape this kernel mints."""
    prefix, separator, digest = value.partition("_")
    return bool(separator) and prefix == _PREFIX and len(digest) == 20


__all__ = [
    "FINDING_ID_VERSION",
    "IDENTITY_DETAIL_KEYS",
    "SYMBOL_INDEX_KEY",
    "SYMBOL_KEY",
    "SYMBOL_LINE_KEY",
    "finding_public_id",
    "is_finding_public_id",
    "legacy_finding_public_id",
]
