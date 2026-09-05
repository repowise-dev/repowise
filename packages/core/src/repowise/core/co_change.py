"""Single home for reading persisted co-change records.

``GitMetadata.co_change_partners_json`` is written in one place
(:mod:`ingestion.git_indexer.co_change`) and read in seventeen. Every reader
used to re-derive the format for itself, and they disagreed: on which key
aliases to accept, on whether the weight was an int or a float, and on whether
a malformed cell should raise. Two of them read a partner record without first
checking it is a record, which turned one bad row into an ``AttributeError``
out of a REST handler.

This module sits beside :mod:`test_paths` and :mod:`support_paths`, and for the
same reason those do: the column is read during ingestion, during analysis, and
at query time, so the one answer cannot live under ``ingestion/`` without the
server importing across a layer to reach it.

**One reader.** :func:`parse_partners` is it. Callers that must put the record
back on the wire, or that read a field this module does not model, take it from
:attr:`CoChangePartner.record` rather than decoding the cell a second time.

**Weights are floats.** The producer rounds to 2dp because the value is a
recency-decayed sum, not a count of commits. Truncating it to ``int`` loses the
part that decides a severity band.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

__all__ = [
    "CO_CHANGE_DECAY_TAU",
    "MAX_PARTNERS_PER_FILE",
    "MIN_CO_CHANGE_SUPPORT",
    "STRUCTURAL_CORROBORATED",
    "STRUCTURAL_NOT_APPLICABLE",
    "STRUCTURAL_UNEXPLAINED",
    "CoChangePartner",
    "canonical_pair",
    "parse_partners",
]


# Tau in ``exp(-age_days / tau)``, which puts the half-life near 125 days.
# Defined here rather than in the indexer because two independent producers
# apply it (per-repo history and the cross-repo session miner), and a drift
# between them would silently change what a weight means.
CO_CHANGE_DECAY_TAU: float = 180.0

# Minimum number of shared commits for a pair to be worth recording. Counted
# raw, so it keeps its meaning if the weighting changes.
MIN_CO_CHANGE_SUPPORT: int = 2

# Strongest partners kept per file. Bounds the column on a repo where a few
# files co-change with thousands of others.
MAX_PARTNERS_PER_FILE: int = 25

# Whether an import-graph edge explains a pair. ``NOT_APPLICABLE`` is not a
# weaker ``UNEXPLAINED``: no resolver can emit an edge for a manifest, a
# changelog or a file the parser never saw, so there is nothing to
# corroborate. Only ``UNEXPLAINED`` is a finding. ``None`` means an index
# written before the distinction existed.
STRUCTURAL_CORROBORATED = "corroborated"
STRUCTURAL_UNEXPLAINED = "unexplained"
STRUCTURAL_NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class CoChangePartner:
    """One file that changed together with the file the record belongs to.

    ``weight`` is the recency-decayed sum the indexer persists, not a number of
    commits; ``support`` is that plain count. ``self_commits`` and
    ``partner_commits`` are each file's commit total over the same walk, so
    ``support / self_commits`` is an honest directional confidence. They are
    zero on an index written before they were recorded.

    ``dependency_kind`` is the ``edge_type`` behind a ``corroborated``
    verdict, and is ``None`` for every other verdict.

    ``record`` is the verbatim source record, for callers that put it back on
    the wire or read a field this module does not model; it is excluded from
    equality so two partners compare on their meaning.
    """

    file_path: str
    weight: float
    last_co_change: str | None = None
    support: int = 0
    self_commits: int = 0
    partner_commits: int = 0
    structural: str | None = None
    dependency_kind: str | None = None
    record: dict = field(default_factory=dict, compare=False, repr=False)


def _as_count(value: object) -> int:
    """A non-negative integer, or 0 for anything that is not one."""
    try:
        return max(int(value), 0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def parse_partners(raw: object) -> list[CoChangePartner]:
    """Partners from a ``co_change_partners_json`` cell, strongest first.

    Tolerates an absent cell, malformed JSON, a non-list document, and non-record
    elements within it, yielding fewer partners rather than raising. Also
    accepts an already-decoded list, which the graph builder holds. The ``path``
    and ``count`` aliases are accepted alongside the canonical ``file_path`` and
    ``co_change_count``; ``frequency`` is the support count, named to match the
    cross-repo miner's records.
    """
    if not raw:
        return []
    if isinstance(raw, (str, bytes)):
        try:
            parsed: object = json.loads(raw)
        except (TypeError, ValueError):
            return []
    else:
        parsed = raw
    if not isinstance(parsed, list):
        return []

    out: list[CoChangePartner] = []
    for record in parsed:
        if not isinstance(record, dict):
            continue
        path = record.get("file_path") or record.get("path")
        if not path:
            continue
        try:
            weight = float(record.get("co_change_count") or record.get("count") or 0.0)
        except (TypeError, ValueError):
            continue
        last = record.get("last_co_change")
        structural = record.get("structural")
        kind = record.get("dependency_kind")
        out.append(
            CoChangePartner(
                file_path=str(path),
                weight=weight,
                last_co_change=last if isinstance(last, str) else None,
                support=_as_count(record.get("frequency")),
                self_commits=_as_count(record.get("self_commits")),
                partner_commits=_as_count(record.get("partner_commits")),
                structural=structural if isinstance(structural, str) else None,
                dependency_kind=kind if isinstance(kind, str) else None,
                record=record,
            )
        )
    out.sort(key=lambda p: -p.weight)
    return out


def canonical_pair(a: str, b: str) -> tuple[str, str]:
    """The two paths in a stable order, so an undirected pair deduplicates."""
    return (a, b) if a < b else (b, a)
