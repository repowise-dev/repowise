"""Semantic identity for findings, stable across line movement and renames."""

from __future__ import annotations

import hashlib
from typing import Any

from ..health import HealthFindingData
from .models import SEVERITY_RANK, FindingKey

_ID_PREFIX = "chf_"

#: Bumped when the identity inputs change, so ids from two versions of this
#: module are never mistaken for one another.
IDENTITY_VERSION = 1


def severity_rank(severity: Any) -> int:
    return SEVERITY_RANK.get(str(severity).lower(), 0)


def finding_key(finding: HealthFindingData, *, path: str | None = None) -> FindingKey:
    """The identity of *finding*, optionally under a rename-normalized *path*.

    Line numbers are deliberately absent: a finding that moves with its symbol
    is the same finding. Where a detector reports no owning symbol the file is
    the anchor, which is the honest granularity for a file-level marker.
    """
    return FindingKey(
        dimension=str(finding.dimension),
        biomarker_type=str(finding.biomarker_type),
        path=path or finding.file_path,
        symbol=finding.function_name or None,
    )


def change_finding_id(key: FindingKey, ordinal: int, *, comparison: str = "") -> str:
    """A deterministic, ephemeral id for one surfaced change finding.

    Deterministic so the same comparison always names a finding the same way
    and an agent can drill straight back into it; ephemeral because the finding
    may exist in no persisted health run at all.

    *comparison* binds the id to the two revisions it came from, so an id
    carried to a different revspec fails to resolve rather than silently
    matching a same-shaped finding from an unrelated diff.
    """
    payload = "\x00".join(
        [
            str(IDENTITY_VERSION),
            comparison,
            *(str(part) for part in key.as_tuple()),
            str(ordinal),
        ]
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{_ID_PREFIX}{digest}"


def normalize_path(path: str, rename_map: dict[str, str]) -> str:
    """Map a base-side path onto its head-side name when the file moved."""
    return rename_map.get(path, path)


def line_distance(left: HealthFindingData, right: HealthFindingData) -> int:
    """Proximity tie-breaker for two findings that share an identity."""
    a, b = left.line_start, right.line_start
    if a is None or b is None:
        return 0
    return abs(a - b)
