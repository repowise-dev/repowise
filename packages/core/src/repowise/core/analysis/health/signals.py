"""Per-file process / people / topology signals.

Phase 3 of health surfacing: consolidate the per-file signals we already
compute and persist — git history (process + people) and graph topology — into
one captioned contract the file-detail surfaces render. Pure surfacing: no
recompute, no new measurement, no LLM. State-free like :mod:`trends.py` so the
join logic stays unit-testable without a DB — callers pass already-loaded rows
and get a plain dataclass back. The same :func:`file_signals` assembler backs
the dashboard drawer, the file-page Health tab, and the MCP ``get_context``
health block, so the contract is defined once here.

The honesty rule mirrors the per-file trend: a value is ``None`` ("no signal")
only when its *source row* is absent — never imputed. A git-tracked file whose
``prior_defect_count`` is genuinely ``0`` reports ``0`` (a real, reassuring
signal), whereas a file with no git history at all reports ``None`` for the
whole process/people group. Topology degree is ``None`` when the file is not a
graph node.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol


class GitMetaLike(Protocol):
    """The git-metadata fields the signals view reads (duck-typed).

    Matches ``persistence.models.GitMetadata``; declared as a Protocol so the
    assembler stays free of any ORM import and unit tests can pass a stub.
    """

    prior_defect_count: int | None
    change_entropy_pct: float | None
    lines_added_90d: int | None
    lines_deleted_90d: int | None
    commit_count_90d: int | None
    age_days: int | None
    primary_owner_name: str | None
    primary_owner_commit_pct: float | None
    recent_owner_name: str | None
    recent_owner_commit_pct: float | None


@dataclass
class FileSignals:
    """A file's process / people / topology signals — all surfacing-only.

    Every field is ``None`` when its source row is absent so consumers render
    an honest "no signal" rather than a misleading zero. ``change_entropy_pct``
    is normalized to 0-100 to match the hotspot API contract (the column is
    stored on a 0-1 scale).
    """

    # Process — how the file changes over time.
    prior_defect_count: int | None
    change_entropy_pct: float | None
    lines_added_90d: int | None
    lines_deleted_90d: int | None
    commit_count_90d: int | None
    age_days: int | None
    # People — who owns it recently vs over its whole life.
    primary_owner_name: str | None
    primary_owner_commit_pct: float | None
    recent_owner_name: str | None
    recent_owner_commit_pct: float | None
    # Topology — how connected it is in the dependency graph.
    in_degree: int | None
    out_degree: int | None

    @property
    def has_any(self) -> bool:
        """True when at least one signal carries data.

        Drives the "no signal" empty state: a file with neither git history
        nor a graph node has nothing to show, so the panel renders nothing
        rather than a wall of dashes.
        """
        return any(v is not None for v in asdict(self).values())


def _entropy_pct(raw: float | None) -> float | None:
    """Normalize the stored 0-1 change entropy to a 0-100 percentile.

    Mirrors ``routers/git.py::_hotspot_from_row`` so every consumer treats the
    value on the same scale. ``None`` stays ``None`` (no signal).
    """
    if raw is None:
        return None
    return round(raw * 100.0, 1)


def file_signals(
    git_meta: GitMetaLike | None,
    degrees: dict[str, int] | None,
) -> FileSignals:
    """Assemble a file's :class:`FileSignals` from already-loaded rows.

    *git_meta* is the ``GitMetadata`` row (or ``None`` when the file has no git
    history); *degrees* is the ``{"in_degree", "out_degree"}`` map from
    ``get_node_degree_counts`` (or ``None`` when the file is not a graph node).
    No DB access and no recompute — this is the single join the drawer, the
    file page, and MCP all reuse.
    """
    g = git_meta
    return FileSignals(
        prior_defect_count=g.prior_defect_count if g else None,
        change_entropy_pct=_entropy_pct(g.change_entropy_pct) if g else None,
        lines_added_90d=g.lines_added_90d if g else None,
        lines_deleted_90d=g.lines_deleted_90d if g else None,
        commit_count_90d=g.commit_count_90d if g else None,
        age_days=g.age_days if g else None,
        primary_owner_name=g.primary_owner_name if g else None,
        primary_owner_commit_pct=g.primary_owner_commit_pct if g else None,
        recent_owner_name=g.recent_owner_name if g else None,
        recent_owner_commit_pct=g.recent_owner_commit_pct if g else None,
        in_degree=degrees["in_degree"] if degrees else None,
        out_degree=degrees["out_degree"] if degrees else None,
    )
