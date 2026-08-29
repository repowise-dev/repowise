"""Pair base-side and head-side findings, and say what changed between them."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..health import HealthFindingData
from .identity import finding_key, line_distance, normalize_path, severity_rank
from .models import ChangeKind, FindingKey

#: Health-impact movement below this is rounding, not a regression.
_IMPACT_EPSILON = 0.01


@dataclass(slots=True)
class MatchedFinding:
    """One head finding and the base finding it corresponds to, if any."""

    head: HealthFindingData
    base: HealthFindingData | None
    key: FindingKey
    ordinal: int
    kind: ChangeKind

    @property
    def severity_before(self) -> str | None:
        return str(self.base.severity) if self.base is not None else None


@dataclass(slots=True)
class MatchResult:
    matched: list[MatchedFinding] = field(default_factory=list)
    resolved: list[HealthFindingData] = field(default_factory=list)

    @property
    def unchanged_total(self) -> int:
        return sum(1 for m in self.matched if m.kind == "unchanged")

    def of_kind(self, *kinds: ChangeKind) -> list[MatchedFinding]:
        return [m for m in self.matched if m.kind in kinds]


class FindingMatcher:
    """Match two finding sets by tolerant identity, then classify each pair.

    Pairing is per identity group: findings sharing a key are zipped by line
    proximity so a marker that fires twice in one symbol keeps a stable
    correspondence, and a same-marker replacement reads as one unchanged
    finding rather than one introduced plus one resolved.
    """

    def __init__(self, rename_map: dict[str, str] | None = None) -> None:
        self.rename_map = rename_map or {}

    def match(self, base: list[HealthFindingData], head: list[HealthFindingData]) -> MatchResult:
        base_groups = self._group(base, normalize=True)
        head_groups = self._group(head, normalize=False)
        result = MatchResult()
        for key, head_items in head_groups.items():
            base_items = base_groups.pop(key, [])
            matched, unmatched = self._pair(key, base_items, head_items)
            result.matched.extend(matched)
            # Base findings this group could not account for are gone from head.
            result.resolved.extend(unmatched)
        for leftovers in base_groups.values():
            result.resolved.extend(leftovers)
        return result

    # -- internals ----------------------------------------------------------

    def _group(
        self, findings: list[HealthFindingData], *, normalize: bool
    ) -> dict[FindingKey, list[HealthFindingData]]:
        groups: dict[FindingKey, list[HealthFindingData]] = {}
        for finding in findings:
            path = (
                normalize_path(finding.file_path, self.rename_map)
                if normalize
                else finding.file_path
            )
            groups.setdefault(finding_key(finding, path=path), []).append(finding)
        for items in groups.values():
            items.sort(key=lambda f: (f.line_start or 0, f.line_end or 0))
        return groups

    def _pair(
        self, key: FindingKey, base: list[HealthFindingData], head: list[HealthFindingData]
    ) -> tuple[list[MatchedFinding], list[HealthFindingData]]:
        """Pair the group's findings closest-first, and report what is left over.

        Closest-first over all candidate pairs, not first-come-first-served:
        letting the earliest head finding claim the only base peer lets an
        unrelated new finding absorb a moved one, which both hides the new
        finding and reports the moved one as introduced.
        """
        candidates = sorted(
            (line_distance(b, h), hi, bi)
            for hi, h in enumerate(head)
            for bi, b in enumerate(base)
        )
        peers: dict[int, HealthFindingData] = {}
        taken: set[int] = set()
        for _distance, head_index, base_index in candidates:
            if head_index in peers or base_index in taken:
                continue
            peers[head_index] = base[base_index]
            taken.add(base_index)
        matched = [
            MatchedFinding(item, peers.get(ordinal), key, ordinal, _classify(peers.get(ordinal), item))
            for ordinal, item in enumerate(head)
        ]
        return matched, [b for i, b in enumerate(base) if i not in taken]


def _classify(base: HealthFindingData | None, head: HealthFindingData) -> ChangeKind:
    """Introduced when nothing matched; worsened only on a real regression."""
    if base is None:
        return "introduced"
    if severity_rank(head.severity) > severity_rank(base.severity):
        return "worsened"
    if severity_rank(head.severity) < severity_rank(base.severity):
        return "unchanged"
    if (head.health_impact or 0.0) - (base.health_impact or 0.0) > _IMPACT_EPSILON:
        return "worsened"
    return "unchanged"
