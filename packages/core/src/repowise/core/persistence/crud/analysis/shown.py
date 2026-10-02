"""The finding registry as SQL: what a serving read may show.

``finding_registry`` decides by finding type (``excluded_types``) and by layer
and language (``LANGUAGE_GATES``). Reads that list or count rows for a surface
filter here, so every surface agrees on what is held back. Reads by id and the
writers do not: a held-back row is still stored and addressable.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import and_, case, func, not_, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from ....analysis.finding_registry import (
    GateLayer,
    excluded_types,
    gated_extensions,
    gated_summary,
    gates_on,
)


def _cells(layer: GateLayer, path_col: Any, kind_col: Any) -> list[tuple[str, Any]]:
    """``(language, row matches that gate)`` for each gate on *layer*."""
    cells = []
    for gate in gates_on(layer):
        match = or_(*(path_col.ilike(f"%{ext}") for ext in gated_extensions(gate.language)))
        if gate.kinds is not None:
            match = and_(match, kind_col.in_(sorted(gate.kinds)))
        cells.append((gate.language, match))
    return cells


def ungated(
    layer: GateLayer, path_col: Any, kind_col: Any = None, *, include_unverified: bool = False
) -> Any:
    """Rows of *layer* no language gate holds back (everything when opted in)."""
    cells = [] if include_unverified else _cells(layer, path_col, kind_col)
    return not_(or_(*(match for _, match in cells))) if cells else true()


def shown(
    layer: GateLayer,
    kind_col: Any,
    path_col: Any,
    *,
    requested: Iterable[str] = (),
    include_unverified: bool = False,
) -> Any:
    """Rows whose type the registry shows and whose language no gate holds back.

    *kind_col* holds a registry type (a biomarker type or a dead-code kind).
    ``include_unverified`` opts into provisional types and gated languages alike.
    """
    return and_(
        kind_col.not_in(
            excluded_types(requested=requested, include_provisional=include_unverified)
        ),
        ungated(layer, path_col, kind_col, include_unverified=include_unverified),
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

    ``{language: {count, precision, reason}}``, from one grouped count, so a
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
