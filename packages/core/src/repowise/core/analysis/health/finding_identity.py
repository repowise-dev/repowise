"""The public identity of one health finding.

Every finding is republished on each analysis, so a storage row id is a fresh
UUID every time and cannot be quoted back. This kernel names the finding by
what it *is* instead, which makes the id stable across runs, storable as a
column, and safe to hand to an agent.

The kernel holds structural coordinates and detector evidence. It deliberately
excludes prose and derived values: ``reason`` is generated text that a wording
change would churn, and two ``details`` keys are outputs of later passes rather
than facts about the location, so leaving them in would make the id of a
finding move whenever an unrelated model changed its mind.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from repowise.core.references import path_identity

from .rows import detail_map, field

FINDING_ID_VERSION = 1
"""Version of the identity kernel below.

It is hashed rather than spelled into the id, because the public reference
vocabulary is ``<kind>_<digest>`` across every tool and a finding has no
cross-version resolution story to tell: bumping this simply changes every
value, and an id minted by an older kernel stops matching.
"""

_PREFIX = "finding"

_DERIVED_DETAIL_KEYS = frozenset({"opportunity_id", "reliable_entry_reachability"})
"""Detail keys written by later passes, not by the detector that found the row.

``opportunity_id`` is stamped by causal grouping, so leaving it in would make
every finding id churn whenever the performance model version moved.
``reliable_entry_reachability`` is a repository-wide graph answer that flips
when unrelated code changes.
"""


def finding_public_id(row: Any) -> str:
    """The stable public id for one finding row."""
    details = {
        key: value
        for key, value in detail_map(row).items()
        if key not in _DERIVED_DETAIL_KEYS
    }
    kernel = {
        "kernel_version": FINDING_ID_VERSION,
        "dimension": field(row, "dimension", None) or "defect",
        "path": path_identity(field(row, "file_path", "") or ""),
        "kind": field(row, "biomarker_type", "") or "",
        "symbol": field(row, "function_name", None) or "",
        "line_start": field(row, "line_start", None),
        "line_end": field(row, "line_end", None),
        "details": details,
    }
    payload = json.dumps(kernel, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return f"{_PREFIX}_{digest}"


def is_finding_public_id(value: str) -> bool:
    """Whether a caller-supplied string has the shape this kernel mints."""
    prefix, separator, digest = value.partition("_")
    return bool(separator) and prefix == _PREFIX and len(digest) == 20


__all__ = ["FINDING_ID_VERSION", "finding_public_id", "is_finding_public_id"]
