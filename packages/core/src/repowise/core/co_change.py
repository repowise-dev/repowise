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
    "MIN_CO_CHANGE_WEIGHT",
    "CoChangePartner",
    "canonical_pair",
    "parse_partners",
]


# Tau in ``exp(-age_days / tau)``, which puts the half-life near 125 days.
# Defined here rather than in the indexer because two independent producers
# apply it (per-repo history and the cross-repo session miner), and a drift
# between them would silently change what a weight means.
CO_CHANGE_DECAY_TAU: float = 180.0

# Minimum decayed weight for a pair to be worth recording. Two co-changes in
# the last few weeks clear it; older ones need proportionally more.
MIN_CO_CHANGE_WEIGHT: int = 2


@dataclass(frozen=True)
class CoChangePartner:
    """One file that changed together with the file the record belongs to.

    ``weight`` is the recency-decayed sum the indexer persists, not a number of
    commits. ``record`` is the verbatim source record, for callers that put it
    back on the wire or read a field this module does not model; it is excluded
    from equality so two partners compare on their meaning.
    """

    file_path: str
    weight: float
    last_co_change: str | None = None
    record: dict = field(default_factory=dict, compare=False, repr=False)


def parse_partners(raw: object) -> list[CoChangePartner]:
    """Partners from a ``co_change_partners_json`` cell, strongest first.

    Tolerates an absent cell, malformed JSON, a non-list document, and non-record
    elements within it, yielding fewer partners rather than raising. Also
    accepts an already-decoded list, which the graph builder holds. The ``path``
    and ``count`` aliases are accepted alongside the canonical ``file_path`` and
    ``co_change_count``.
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
        out.append(
            CoChangePartner(
                file_path=str(path),
                weight=weight,
                last_co_change=last if isinstance(last, str) else None,
                record=record,
            )
        )
    out.sort(key=lambda p: -p.weight)
    return out


def canonical_pair(a: str, b: str) -> tuple[str, str]:
    """The two paths in a stable order, so an undirected pair deduplicates."""
    return (a, b) if a < b else (b, a)
