"""The public identity of one health finding.

Every finding is republished on each analysis, so a storage row id is a fresh
UUID every time and cannot be quoted back. This kernel names the finding by
what it *is* instead, which makes the id stable across runs, storable as a
column, and safe to hand to an agent.

The kernel holds structural coordinates and detector evidence. It deliberately
excludes prose and derived values: ``reason`` is generated text that a wording
change would churn, and a few ``details`` keys are outputs of later passes or
annotations of the enclosing function, not evidence for the finding, so
leaving them in would make the id of a finding move whenever an unrelated
model changed its mind.

A finding inside a known symbol is located by the symbol's name and its line
offset within that symbol (``details["symbol_line"]`` holds the symbol's first
line), so an edit above the symbol does not move the id. A finding with no
symbol, or one stored before that anchor existed, keeps absolute lines.
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

_DERIVED_DETAIL_KEYS = frozenset(
    {"opportunity_id", "reliable_entry_reachability", "dispatch_share", "deprecated"}
)
"""Detail keys written by later passes, not by the detector that found the row.

``opportunity_id`` is stamped by causal grouping, so leaving it in would make
every finding id churn whenever the performance model version moved.
``reliable_entry_reachability`` is a repository-wide graph answer that flips
when unrelated code changes. ``dispatch_share`` and ``deprecated`` describe the
function a finding sits in, not the finding, and joined after ids were already
stored: counting them would have renamed every complexity finding at once.
"""


SYMBOL_LINE_KEY = "symbol_line"
"""Detail key holding the first line of the symbol a finding sits in.

It is the anchor of the offset, not evidence, so it stays out of the hashed
details: it moves whenever the symbol does.
"""


def _digest(kernel: dict[str, Any]) -> str:
    payload = json.dumps(kernel, sort_keys=True, separators=(",", ":"), default=str)
    return f"{_PREFIX}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:20]}"


def _kernel_details(row: Any) -> dict[str, Any]:
    return {
        key: value
        for key, value in detail_map(row).items()
        if key not in _DERIVED_DETAIL_KEYS and key != SYMBOL_LINE_KEY
    }


def finding_public_id(row: Any) -> str:
    """The stable public id for one finding row."""
    details = detail_map(row)
    symbol = field(row, "function_name", None) or ""
    line_start = field(row, "line_start", None)
    anchor = details.get(SYMBOL_LINE_KEY)
    if symbol and isinstance(anchor, int) and isinstance(line_start, int):
        location: dict[str, Any] = {"line_offset": line_start - anchor}
    else:
        location = {"line_start": line_start, "line_end": field(row, "line_end", None)}
    return _digest(
        {
            "kernel_version": FINDING_ID_VERSION,
            "dimension": field(row, "dimension", None) or "defect",
            "path": path_identity(field(row, "file_path", "") or ""),
            "kind": field(row, "biomarker_type", "") or "",
            "symbol": symbol,
            **location,
            "details": _kernel_details(row),
        }
    )


def legacy_finding_public_id(row: Any) -> str:
    """The id kernel version 1 minted for *row* (absolute lines).

    Only the triage carry-over reads it, to match a row triaged under version 1
    to its re-detection. Removable once stores written before version 2 are gone.
    """
    return _digest(
        {
            "kernel_version": 1,
            "dimension": field(row, "dimension", None) or "defect",
            "path": path_identity(field(row, "file_path", "") or ""),
            "kind": field(row, "biomarker_type", "") or "",
            "symbol": field(row, "function_name", None) or "",
            "line_start": field(row, "line_start", None),
            "line_end": field(row, "line_end", None),
            "details": _kernel_details(row),
        }
    )


def is_finding_public_id(value: str) -> bool:
    """Whether a caller-supplied string has the shape this kernel mints."""
    prefix, separator, digest = value.partition("_")
    return bool(separator) and prefix == _PREFIX and len(digest) == 20


__all__ = [
    "FINDING_ID_VERSION",
    "SYMBOL_LINE_KEY",
    "finding_public_id",
    "is_finding_public_id",
    "legacy_finding_public_id",
]
