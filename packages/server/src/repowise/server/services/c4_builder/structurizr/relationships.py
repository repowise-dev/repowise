"""Emit the ``source -> destination`` lines.

The description is the verb the label pass already derived (``imports``,
``calls``, ``co-changes``…), and the coupling bucket rides along as a tag so a
user can style or filter by it — Structurizr has no vocabulary for "how hard
is this edge", but its tags do not care what a tag means.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..models import Relation
from .writer import Writer, quote

#: Coupling bucket → tag. Capitalised to match Structurizr's tag convention
#: and to read as a proper noun in a style rule.
_COUPLING_TAGS: dict[str, str] = {
    "loose": "Loose",
    "moderate": "Moderate",
    "tight": "Tight",
}


def write_relationships(
    writer: Writer,
    relations: Iterable[Relation],
    identifiers: dict[str, str],
) -> int:
    """Write every relation whose endpoints are both in *identifiers*.

    Returns the number written. Relations pointing at something we did not
    emit — a component when components are switched off, say — are skipped
    rather than emitted as a dangling reference the parser would reject.

    Ordering is by the rendered identifiers, so the block diffs cleanly
    regardless of the order relations were aggregated in.
    """
    emitted: list[tuple[str, str, Relation]] = []
    for relation in relations:
        source = identifiers.get(relation.source_id)
        target = identifiers.get(relation.target_id)
        if source is None or target is None:
            continue
        emitted.append((source, target, relation))

    emitted.sort(key=lambda row: (row[0], row[1], row[2].label))
    for source, target, relation in emitted:
        header = f"{source} -> {target} {quote(relation.label or 'depends on')}"
        tag = _COUPLING_TAGS.get(relation.coupling)
        if tag is None:
            writer.line(header)
            continue
        # A block rather than the positional tag slot, which would need an
        # empty technology string in front of it — we have nothing honest to
        # put there for a code dependency.
        with writer.block(header):
            writer.line(f"tags {quote(tag)}")
    return len(emitted)
