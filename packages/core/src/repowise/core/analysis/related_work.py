"""What the other lenses say about the same files.

Each lens (findings, Fix first, refactoring, performance, dead code) ranks its
own work; a reader in one drawer has no way to see that the file in front of
them also has a plan, a performance cause or dead code. This folds rows the
caller already read into one compact answer per file, so every surface links
across lenses the same way.

Session-free: rows arrive as ORM rows, SQL rows, dataclasses or wire dicts
(read through :func:`~repowise.core.analysis.health.rows.field`) and nothing is
re-derived. ``code_origin`` and ``deprecated`` are copied only when the row
already carries them.

A lens is one entry in :data:`_LENSES`: how to read a row's file, how to order
rows, and how to project one onto the item shape. Another lens (migration
groups, say) is one more entry and one more keyword argument.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from repowise.core.analysis.health.aggregation import SEVERITY_ORDER
from repowise.core.analysis.health.rows import detail_map, field

#: Items kept per file per lens. The total still counts every row, so a hot
#: file reports "5 of 40" rather than shipping 40.
DEFAULT_PER_LENS_LIMIT = 5

_SEVERITY_RANK = {name: i for i, name in enumerate(SEVERITY_ORDER)}
_LAST = 1 << 30


@dataclass(frozen=True, slots=True)
class RelatedItem:
    lens: str
    id: str
    kind: str | None = None
    title: str | None = None
    symbol: str | None = None
    severity: str | None = None
    tier: str | None = None
    rank: int | None = None
    line: int | None = None
    code_origin: str | None = None
    deprecated: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "lens": self.lens,
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "symbol": self.symbol,
            "severity": self.severity,
            "tier": self.tier,
            "rank": self.rank,
            "line": self.line,
        }
        # Present only when the row carried them, so absent stays absent.
        if self.code_origin is not None:
            out["code_origin"] = self.code_origin
        if self.deprecated is not None:
            out["deprecated"] = self.deprecated
        return out


@dataclass(frozen=True, slots=True)
class RelatedLens:
    items: tuple[RelatedItem, ...]
    total: int

    def as_dict(self) -> dict[str, Any]:
        return {"items": [i.as_dict() for i in self.items], "total": self.total}


@dataclass(frozen=True, slots=True)
class RelatedFile:
    file_path: str
    #: Only lenses with at least one row, in :data:`LENSES` order.
    lenses: dict[str, RelatedLens] = dc_field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "lenses": {name: lens.as_dict() for name, lens in self.lenses.items()},
        }


@dataclass(frozen=True, slots=True)
class RelatedWork:
    files: tuple[RelatedFile, ...]
    per_lens_limit: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "files": [f.as_dict() for f in self.files],
            "per_lens_limit": self.per_lens_limit,
        }


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _or_last(value: Any) -> int:
    """Sort key for an optional position: absent sorts after every real one."""
    n = _int(value)
    return _LAST if n is None else n


def _flags(row: Any, details: dict[str, Any]) -> dict[str, Any]:
    """``code_origin`` / ``deprecated`` from the row or its details, if there."""
    out: dict[str, Any] = {}
    origin = field(row, "code_origin", None) or details.get("code_origin")
    if isinstance(origin, str):
        out["code_origin"] = origin
    deprecated = field(row, "deprecated", None)
    if deprecated is None:
        deprecated = details.get("deprecated")
    if isinstance(deprecated, bool):
        out["deprecated"] = deprecated
    return out


# -- lenses ---------------------------------------------------------------


def _finding_item(row: Any) -> RelatedItem:
    details = detail_map(row)
    return RelatedItem(
        lens="findings",
        id=str(field(row, "id")),
        kind=field(row, "biomarker_type"),
        symbol=field(row, "function_name"),
        severity=field(row, "severity"),
        line=_int(field(row, "line_start")),
        **_flags(row, details),
    )


def _finding_key(row: Any) -> tuple[Any, ...]:
    return (
        _SEVERITY_RANK.get(str(field(row, "severity", "")).lower(), len(SEVERITY_ORDER)),
        _or_last(field(row, "line_start")),
        str(field(row, "id", "")),
    )


def _fix_first_file(item: Any) -> str | None:
    return field(field(item, "target"), "file_path")


def _fix_first_item(item: Any) -> RelatedItem:
    target = field(item, "target")
    return RelatedItem(
        lens="fix_first",
        id=str(field(item, "id")),
        kind=field(item, "kind"),
        title=field(item, "title"),
        symbol=field(target, "symbol"),
        tier=field(item, "tier"),
        rank=_int(field(item, "rank")),
        line=_int(field(target, "line_start")),
    )


def _fix_first_key(item: Any) -> tuple[Any, ...]:
    return (_or_last(field(item, "rank")), str(field(item, "id", "")))


def _refactoring_item(row: Any) -> RelatedItem:
    details = detail_map(row)
    return RelatedItem(
        lens="refactoring",
        id=str(field(row, "opportunity_id")),
        kind=field(row, "lead_refactoring_type"),
        tier=field(row, "effort_bucket"),
        rank=_int(field(row, "rank_position")),
        **_flags(row, details),
    )


def _rank_key(row: Any) -> tuple[Any, ...]:
    return (_or_last(field(row, "rank_position")), str(field(row, "opportunity_id", "")))


def _performance_item(row: Any) -> RelatedItem:
    details = detail_map(row)
    return RelatedItem(
        lens="performance",
        id=str(field(row, "opportunity_id")),
        kind=field(row, "biomarker_type"),
        symbol=field(row, "intervention_symbol"),
        tier=field(row, "actionability_state"),
        rank=_int(field(row, "rank_position")),
        **_flags(row, details),
    )


def _dead_code_item(row: Any) -> RelatedItem:
    return RelatedItem(
        lens="dead_code",
        id=str(field(row, "id")),
        kind=field(row, "kind"),
        symbol=field(row, "symbol_name"),
        tier="safe_to_delete" if field(row, "safe_to_delete", False) else "review",
        line=_int(field(row, "start_line")),
        **_flags(row, {}),
    )


def _dead_code_key(row: Any) -> tuple[Any, ...]:
    return (
        -float(field(row, "confidence", 0.0) or 0.0),
        _or_last(field(row, "start_line")),
        str(field(row, "id", "")),
    )


def _file_path(row: Any) -> str | None:
    return field(row, "file_path")


@dataclass(frozen=True, slots=True)
class _Lens:
    file_of: Callable[[Any], str | None]
    order: Callable[[Any], tuple[Any, ...]]
    project: Callable[[Any], RelatedItem]


_LENSES: dict[str, _Lens] = {
    "findings": _Lens(_file_path, _finding_key, _finding_item),
    "fix_first": _Lens(_fix_first_file, _fix_first_key, _fix_first_item),
    "refactoring": _Lens(_file_path, _rank_key, _refactoring_item),
    "performance": _Lens(_file_path, _rank_key, _performance_item),
    "dead_code": _Lens(_file_path, _dead_code_key, _dead_code_item),
}

#: Every lens, in the order a surface lists them.
LENSES: tuple[str, ...] = tuple(_LENSES)


def related_work(
    file_paths: Sequence[str],
    *,
    findings: Iterable[Any] = (),
    fix_first: Iterable[Any] = (),
    refactoring: Iterable[Any] = (),
    performance: Iterable[Any] = (),
    dead_code: Iterable[Any] = (),
    per_lens_limit: int = DEFAULT_PER_LENS_LIMIT,
) -> RelatedWork:
    """Group each lens's rows under the requested files, capped per lens.

    Rows for files not in *file_paths* are ignored, so a caller may pass a
    wider read. Files come back in request order, each listing only the lenses
    that have something for it.
    """
    inputs = {
        "findings": findings,
        "fix_first": fix_first,
        "refactoring": refactoring,
        "performance": performance,
        "dead_code": dead_code,
    }
    wanted = list(dict.fromkeys(file_paths))
    grouped: dict[str, dict[str, list[Any]]] = {path: {} for path in wanted}
    for name, rows in inputs.items():
        lens = _LENSES[name]
        for row in rows:
            bucket = grouped.get(lens.file_of(row) or "")
            if bucket is not None:
                bucket.setdefault(name, []).append(row)

    limit = max(per_lens_limit, 0)
    files = []
    for path in wanted:
        lenses = {}
        for name in LENSES:
            rows = grouped[path].get(name)
            if not rows:
                continue
            lens = _LENSES[name]
            kept = sorted(rows, key=lens.order)[:limit]
            lenses[name] = RelatedLens(tuple(lens.project(r) for r in kept), len(rows))
        files.append(RelatedFile(path, lenses))
    return RelatedWork(tuple(files), limit)


__all__ = [
    "DEFAULT_PER_LENS_LIMIT",
    "LENSES",
    "RelatedFile",
    "RelatedItem",
    "RelatedLens",
    "RelatedWork",
    "related_work",
]
