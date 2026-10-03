"""Which finding types a surface may show, and on what evidence.

One switch per finding type — a dead-code ``kind`` or a health
``biomarker_type``; the two families share no names — so a type that has not
earned its place can be taken off every surface at once, without deleting the
analyzer that produces it. Visibility only: analyzers still run, rows are still
persisted and scored, and the raw CLI analysis still reports them.

- ``validated``: shown everywhere. A type absent from :data:`REGISTRY` is
  validated — it shipped before measurement existed and stays on until a
  measurement says otherwise.
- ``provisional``: shown only when a caller asks for it by name (or opts into
  unverified types), and then labelled :data:`UNVERIFIED_LABEL`.
- ``hidden``: never shown on a default surface (overview, priorities, MCP,
  REST lists, wiki prompts). Surfaces may report how many were held back.

A type moves back to ``validated`` only when its measured precision clears the
family floor; the measurement fields record the evidence either way.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

FindingStatus = Literal["validated", "provisional", "hidden"]

#: The label a provisional finding carries wherever it is shown.
UNVERIFIED_LABEL = "unverified"


@dataclass(frozen=True)
class FindingTypeStatus:
    status: FindingStatus
    #: Measured precision and its Wilson 95% lower bound, or ``None`` when the
    #: type has not been measured.
    precision: float | None = None
    ci_low: float | None = None
    #: What the measurement ran on, in words (no repository names).
    corpus: str | None = None
    #: ISO date of the measurement.
    measured_on: str | None = None
    #: One line saying why the type is not ``validated``.
    reason: str | None = None


_VALIDATED = FindingTypeStatus("validated")

_LABELLED = "hand-labelled findings from one TS/Python monorepo"

REGISTRY: dict[str, FindingTypeStatus] = {
    "unused_internal": FindingTypeStatus(
        "hidden",
        precision=0.005,
        corpus=f"{_LABELLED}, 1,511 in all",
        measured_on="2026-09-29",
        reason=(
            "Private-symbol findings were almost all false: TS/JS/Python emit no "
            "'reads' edges, so same-file uses are invisible to the graph."
        ),
    ),
    "duplicated_assertion_block": FindingTypeStatus(
        "hidden",
        corpus=f"{_LABELLED}, 1,168 in all",
        measured_on="2026-09-29",
        reason="Clones are matched on token shape only, so unrelated assertions pair up.",
    ),
    # The failing subset is clones over import blocks and data literals. A
    # ClonePair carries only line spans, not the token kinds that would tell
    # that subset apart, so the whole type is hidden until clone detection
    # drops those windows itself (upgrade path: filter in the tokenizer, then
    # re-measure and flip this back).
    "dry_violation": FindingTypeStatus(
        "hidden",
        corpus=_LABELLED,
        measured_on="2026-09-29",
        reason="Flags duplicated import blocks and data literals as design duplication.",
    ),
}


def status_of(finding_type: str) -> FindingTypeStatus:
    return REGISTRY.get(finding_type, _VALIDATED)


def excluded_types(
    *, requested: Iterable[str] = (), include_provisional: bool = False
) -> frozenset[str]:
    """Types a surface must leave out: every hidden type, plus each provisional
    type the caller neither named in *requested* nor opted into."""
    named = set(requested)
    return frozenset(
        name
        for name, entry in REGISTRY.items()
        if entry.status == "hidden"
        or (entry.status == "provisional" and not include_provisional and name not in named)
    )


def is_shown(
    finding_type: str, *, requested: Iterable[str] = (), include_provisional: bool = False
) -> bool:
    return finding_type not in excluded_types(
        requested=requested, include_provisional=include_provisional
    )


def verification_label(finding_type: str) -> str | None:
    """:data:`UNVERIFIED_LABEL` for a provisional type, else ``None``."""
    return UNVERIFIED_LABEL if status_of(finding_type).status == "provisional" else None


def withheld_summary(counts: Mapping[str, int], excluded: Iterable[str]) -> dict[str, dict]:
    """``{type: {count, status, reason}}`` for each excluded type that has rows,
    so a surface can say what it held back instead of silently shrinking."""
    out: dict[str, dict] = {}
    for name in sorted(set(excluded)):
        count = counts.get(name, 0)
        if count:
            entry = status_of(name)
            out[name] = {"count": count, "status": entry.status, "reason": entry.reason}
    return out


__all__ = [
    "REGISTRY",
    "UNVERIFIED_LABEL",
    "FindingStatus",
    "FindingTypeStatus",
    "excluded_types",
    "is_shown",
    "status_of",
    "verification_label",
    "withheld_summary",
]
