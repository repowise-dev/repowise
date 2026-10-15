"""The finding registry as SQL: what a serving read may show.

``finding_registry`` decides by finding type (``excluded_types``), by layer
and language (``LANGUAGE_GATES``), and for health findings by the file's stored
origin and test flag (``FILE_HOLDS``), which also hold refactoring plans
back on test files. Reads that list or count rows for a surface
filter here, so every surface agrees on what is held back. Reads by id and the
writers do not: a held-back row is still stored and addressable.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import and_, case, func, not_, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from ....analysis.finding_registry import (
    HELD_ORIGINS,
    GateLayer,
    excluded_types,
    gated_extensions,
    gated_summary,
    gates_on,
    history_kinds,
)
from ...models import (
    HealthFileMetric,
    HealthFinding,
    RefactoringOpportunity,
    RefactoringSuggestion,
)

# The row types each layer's file holds read, and only these: a hold is a join
# on the row's repository and path.
_HELD_MODELS: dict[str, tuple[Any, ...]] = {
    "health": (HealthFinding,),
    "refactoring": (RefactoringSuggestion, RefactoringOpportunity),
}


def _file_holds(layer: GateLayer, path_col: Any, kind_col: Any) -> list[tuple[str, Any]]:
    """``(FILE_HOLDS key, row matches it)`` for health findings and plans, read
    from the file's stored metric row; ahead of the language cells, as
    ``file_hold`` is."""
    model = getattr(path_col, "class_", None)
    if model not in _HELD_MODELS.get(layer, ()):
        return []

    def on_file(*where: Any) -> Any:
        return (
            select(HealthFileMetric.id)
            .where(
                HealthFileMetric.repository_id == model.repository_id,
                HealthFileMetric.file_path == path_col,
                *where,
            )
            .exists()
        )

    if layer == "refactoring":
        return [("test", on_file(HealthFileMetric.is_test.is_(True)))]
    cells = [(origin, on_file(HealthFileMetric.code_origin == origin)) for origin in HELD_ORIGINS]
    if kind_col is not None:
        cells.append(
            (
                "test_history",
                and_(
                    kind_col.in_(sorted(history_kinds())),
                    on_file(HealthFileMetric.is_test.is_(True)),
                ),
            )
        )
    return cells


def _cells(
    layer: GateLayer, path_col: Any, kind_col: Any, *, file_holds: bool = True
) -> list[tuple[str, Any]]:
    """``(key, row matches it)`` for each file hold, then each gate on *layer*."""
    cells = _file_holds(layer, path_col, kind_col) if file_holds else []
    for gate in gates_on(layer):
        match = or_(*(path_col.ilike(f"%{ext}") for ext in gated_extensions(gate.language)))
        if gate.kinds is not None:
            match = and_(match, kind_col.in_(sorted(gate.kinds)))
        cells.append((gate.language, match))
    return cells


def ungated(
    layer: GateLayer,
    path_col: Any,
    kind_col: Any = None,
    *,
    include_unverified: bool = False,
    file_holds: bool = True,
) -> Any:
    """Rows of *layer* no language gate or file hold keeps back (everything when
    opted in). ``file_holds=False`` applies the language gates alone."""
    cells = (
        [] if include_unverified else _cells(layer, path_col, kind_col, file_holds=file_holds)
    )
    return not_(or_(*(match for _, match in cells))) if cells else true()


def shown(
    layer: GateLayer,
    kind_col: Any,
    path_col: Any,
    *,
    requested: Iterable[str] = (),
    include_unverified: bool = False,
    file_holds: bool = True,
) -> Any:
    """Rows whose type the registry shows and whose language no gate holds back.

    *kind_col* holds a registry type (a biomarker type or a dead-code kind).
    ``include_unverified`` opts into provisional types and gated languages alike.
    ``file_holds=False`` keeps the file-held rows: a score or KPI counts them.
    """
    return and_(
        kind_col.not_in(
            excluded_types(requested=requested, include_provisional=include_unverified)
        ),
        ungated(
            layer,
            path_col,
            kind_col,
            include_unverified=include_unverified,
            file_holds=file_holds,
        ),
    )


async def gated_counts(
    session: AsyncSession,
    layer: GateLayer,
    model: Any,
    *where: Any,
    kind_col: Any = None,
    include_unverified: bool = False,
) -> dict[str, dict]:
    """What the gates on *layer* hold back among *model* rows matching *where*.

    ``{language or file hold: {count, reason}}``, from one grouped count, so a
    surface can say how many rows it left out and why instead of reading empty.
    Empty when ``include_unverified``: nothing was held back.
    """
    cells = [] if include_unverified else _cells(layer, model.file_path, kind_col)
    if not cells:
        return {}
    language = case(*((match, lang) for lang, match in cells))
    rows = await session.execute(
        select(language, func.count())
        .select_from(model)
        .where(*where, or_(*(match for _, match in cells)))
        .group_by(language)
    )
    return gated_summary(layer, {lang: int(n) for lang, n in rows.all()})


__all__ = ["gated_counts", "shown", "ungated"]
