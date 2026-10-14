"""Pair base-side and head-side findings, and say what changed between them."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from ..changed_lines import map_old_line
from ..health import HealthFindingData
from ..health.aggregation import finding_raw_deduction
from .identity import finding_key, measured_shape, normalize_path, severity_rank
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
    finding rather than one introduced plus one resolved. Proximity reads a
    base line where the diff moved it (*hunks*, by head path), so lines
    inserted above a symbol do not cross-pair two findings inside it.

    A second pass pairs what is left over across a symbol rename: a head
    finding with no peer takes a resolved base finding of the same measured
    shape in the same file.
    """

    def __init__(
        self,
        rename_map: dict[str, str] | None = None,
        hunks: dict[str, list[tuple[int, int, int, int]]] | None = None,
    ) -> None:
        self.rename_map = rename_map or {}
        self.hunks = hunks or {}

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
        self._pair_renames(result)
        return result

    # -- internals ----------------------------------------------------------

    def _pair_renames(self, result: MatchResult) -> None:
        """Pair introduced head findings with resolved base findings of one shape.

        Only symbol-owned findings whose symbol name differs: the exact pass
        already settled every same-name pair, so what remains with an equal
        shape is the same finding under a new name.
        """
        introduced = [m for m in result.matched if m.base is None and m.head.function_name]
        base = [f for f in result.resolved if f.function_name]
        if not introduced or not base:
            return
        shapes = [measured_shape(f, path=self._base_path(f)) for f in base]
        peers = _closest_first(
            (self._distance(b, m.head), mi, bi)
            for mi, m in enumerate(introduced)
            for bi, b in enumerate(base)
            if shapes[bi] == measured_shape(m.head) and b.function_name != m.head.function_name
        )
        for mi, bi in peers.items():
            m = introduced[mi]
            m.base, m.kind = base[bi], _classify(base[bi], m.head)
        claimed = {id(base[bi]) for bi in peers.values()}
        result.resolved = [f for f in result.resolved if id(f) not in claimed]

    def _base_path(self, finding: HealthFindingData) -> str:
        return normalize_path(finding.file_path, self.rename_map)

    def _distance(self, base: HealthFindingData, head: HealthFindingData) -> int:
        """Line distance after moving *base* to where the diff put its line."""
        a, b = base.line_start, head.line_start
        if a is None or b is None:
            return 0
        hunks = self.hunks.get(self._base_path(base))
        return abs((map_old_line(hunks, a) if hunks else a) - b)

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
        peers = _closest_first(
            (self._distance(b, h), hi, bi) for hi, h in enumerate(head) for bi, b in enumerate(base)
        )
        matched = []
        for ordinal, item in enumerate(head):
            peer = base[peers[ordinal]] if ordinal in peers else None
            matched.append(MatchedFinding(item, peer, key, ordinal, _classify(peer, item)))
        taken = set(peers.values())
        return matched, [b for i, b in enumerate(base) if i not in taken]


def _closest_first(candidates: Iterable[tuple[int, int, int]]) -> dict[int, int]:
    """``{head_index: base_index}`` from ``(distance, head, base)`` candidates.

    Each side is used once, nearest pairs first.
    """
    peers: dict[int, int] = {}
    taken: set[int] = set()
    for _distance, head_index, base_index in sorted(candidates):
        if head_index in peers or base_index in taken:
            continue
        peers[head_index] = base_index
        taken.add(base_index)
    return peers


def _classify(base: HealthFindingData | None, head: HealthFindingData) -> ChangeKind:
    """Introduced when nothing matched; worsened only on a real regression."""
    if base is None:
        return "introduced"
    if severity_rank(head.severity) > severity_rank(base.severity):
        return "worsened"
    if severity_rank(head.severity) < severity_rank(base.severity):
        return "unchanged"
    if finding_raw_deduction(head) - finding_raw_deduction(base) > _IMPACT_EPSILON:
        return "worsened"
    return "unchanged"
