"""Chat view parts: tables, charts and report sections.

Twin of ``packages/types/src/views.ts``; read that module for what each field
means. View builders produce these from tool results already in a chat turn,
so every data part names its source result, basis, coverage and index commit.

``ViewPart`` is the kind registry: a discriminated union with one model per
kind, from which ``VIEW_MODELS`` and ``VIEW_KINDS`` are derived. Each model
declares ``is_data`` (true for kinds with provenance) and ``references()``
(ids of data parts it names); ``ViewPartList`` only iterates those. A new kind
is one model plus one union line here, and its cases in
``packages/types/fixtures/views.conformance.json``, which both this module's
tests and the TypeScript validator's run.
"""

from __future__ import annotations

import math
from collections import Counter
from itertools import pairwise
from typing import Annotated, ClassVar, Literal, get_args

from pydantic import Field, RootModel, field_validator, model_validator

from repowise.server.schemas.evidence import (
    MAX_WIRE_NUMBER,
    CardBasis,
    CardRef,
    StrictContract,
    WireFloat,
    WireInt,
)

VIEW_SCHEMA_VERSION = 2
#: Versions a reader accepts: 1 is every part stored before treemaps and heatmaps.
VIEW_SCHEMA_VERSIONS: tuple[int, ...] = (1, 2)

ChartType = Literal[
    "bar",
    "horizontal_bar",
    "stacked_bar",
    "line",
    "area",
    "donut",
    "scatter",
    "treemap",
    "heatmap",
]
CHART_TYPES: tuple[str, ...] = get_args(ChartType)

ViewCoverageMetric = Literal["attribution", "test_reach"]
VIEW_COVERAGE_METRICS: tuple[str, ...] = get_args(ViewCoverageMetric)

ChartSortOrder = Literal["asc", "desc"]
CHART_SORT_ORDERS: tuple[str, ...] = get_args(ChartSortOrder)

#: Size caps; a payload over any of them is rejected, not cut.
VIEW_LIMITS: dict[str, int] = {
    "parts": 50,
    "columns": 20,
    "rows": 500,
    "chart_points": 100,
    "chart_series": 8,
    "donut_min_parts": 2,
    "donut_max_parts": 8,
    "treemap_groups": 3,
    "narrative_blocks": 50,
    "section_views": 20,
}

NonEmpty = Annotated[str, Field(min_length=1)]
PartId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")]
ViewCell = str | WireInt | WireFloat | CardRef | None


def _duplicates(items: list[str]) -> list[str]:
    return sorted(item for item, n in Counter(items).items() if n > 1)


class ViewSource(StrictContract):
    artifact_id: NonEmpty
    tool_name: NonEmpty
    args_hash: str = Field(pattern=r"^[0-9a-f]{8,128}$")


class ViewCoverage(StrictContract):
    metric: ViewCoverageMetric
    ratio: float = Field(ge=0, le=1)


class ViewPartBase(StrictContract):
    #: Data parts carry provenance and are what other parts may name.
    is_data: ClassVar[bool] = False

    schema_version: Literal[1, 2]
    id: PartId
    title: NonEmpty
    summary: NonEmpty | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def _not_a_bool(cls, value: object) -> object:
        # Literal[1, 2] alone accepts True, which the TypeScript side rejects.
        if isinstance(value, bool):
            raise ValueError(f"expected one of {VIEW_SCHEMA_VERSIONS}")
        return value

    def references(self) -> list[str]:
        """Ids of data parts this part names; each must be in the same list."""
        return []


class ViewProvenance(StrictContract):
    is_data: ClassVar[bool] = True

    source: ViewSource
    basis: CardBasis
    coverage: ViewCoverage | None = None
    stale: bool
    as_of: Annotated[str, Field(pattern=r"^[0-9a-f]{7,64}$")] | None


class ViewColumn(StrictContract):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    label: NonEmpty
    unit: NonEmpty | None = None
    basis: CardBasis | None = None


class ViewTableData(StrictContract):
    columns: list[ViewColumn] = Field(max_length=VIEW_LIMITS["columns"])
    rows: list[dict[str, ViewCell]] = Field(max_length=VIEW_LIMITS["rows"])
    total_rows: int = Field(ge=0, le=MAX_WIRE_NUMBER)
    truncated: bool

    @model_validator(mode="after")
    def _table_rules(self) -> ViewTableData:
        keys = [c.key for c in self.columns]
        if not keys:
            raise ValueError("a table needs at least one column")
        if dupes := _duplicates(keys):
            raise ValueError(f"duplicate column keys: {dupes}")
        known = set(keys)
        for i, row in enumerate(self.rows):
            if set(row) != known:
                raise ValueError(f"rows[{i}] keys differ from the column keys")
        if self.total_rows < len(self.rows):
            raise ValueError("total_rows is fewer than the rows given")
        if self.truncated != (self.total_rows > len(self.rows)):
            raise ValueError("truncated must be true exactly when total_rows exceeds the rows")
        return self


class TableView(ViewTableData, ViewProvenance, ViewPartBase):
    kind: Literal["table"]


_SERIES_BOUNDS: dict[str, tuple[int, int]] = {
    "bar": (1, VIEW_LIMITS["chart_series"]),
    "horizontal_bar": (1, VIEW_LIMITS["chart_series"]),
    "line": (1, VIEW_LIMITS["chart_series"]),
    "area": (1, VIEW_LIMITS["chart_series"]),
    "stacked_bar": (2, VIEW_LIMITS["chart_series"]),
    "donut": (1, 1),
    "scatter": (1, VIEW_LIMITS["chart_series"]),
    "treemap": (1, 1),
    "heatmap": (1, VIEW_LIMITS["chart_series"]),
}
_PARTS_OF_A_WHOLE = frozenset({"stacked_bar", "donut", "treemap"})


class ChartView(ViewProvenance, ViewPartBase):
    """A chart over its own table; see ``ChartView`` in views.ts for the rules."""

    kind: Literal["chart"]
    chart_type: ChartType
    data: ViewTableData
    x: NonEmpty
    series: list[NonEmpty] = Field(max_length=VIEW_LIMITS["chart_series"])
    sort: ChartSortOrder | None = None
    summary: NonEmpty
    groups: list[NonEmpty] | None = Field(default=None, max_length=VIEW_LIMITS["treemap_groups"])
    color: NonEmpty | None = None

    @model_validator(mode="after")
    def _chart_rules(self) -> ChartView:
        columns = {c.key: c for c in self.data.columns}
        rows = self.data.rows
        kind = self.chart_type
        if self.x not in columns:
            raise ValueError("x is not a column of data")
        self._treemap_rules(columns)
        for s in self.series:
            if s not in columns:
                raise ValueError(f"series {s!r} is not a column of data")
            if s == self.x:
                raise ValueError(f"series {s!r} is the x column")
        if _duplicates(self.series):
            raise ValueError("series repeats a column")
        low, high = _SERIES_BOUNDS[kind]
        if not low <= len(self.series) <= high:
            raise ValueError(f"a {kind} chart plots {low} to {high} series")
        if len(rows) > VIEW_LIMITS["chart_points"]:
            raise ValueError(f"a chart plots at most {VIEW_LIMITS['chart_points']} rows")
        if len({columns[s].unit for s in self.series}) > 1:
            raise ValueError("plotted columns must share one unit")
        total: float = 0
        for i, row in enumerate(rows):
            if row[self.x] is None:
                raise ValueError(f"rows[{i}]: x is null")
            if kind == "scatter" and not _finite_number(row[self.x]):
                raise ValueError(f"rows[{i}]: a scatter x is a number")
            for s in self.series:
                cell = row[s]
                if cell is None:
                    if kind == "donut":
                        raise ValueError(f"rows[{i}].{s}: a donut part is null")
                    if self.sort is not None:
                        raise ValueError(f"rows[{i}].{s}: a sorted chart has no nulls")
                elif not isinstance(cell, (int, float)):
                    raise ValueError(f"rows[{i}].{s}: plotted values must be numbers")
                elif cell < 0 and kind in _PARTS_OF_A_WHOLE:
                    raise ValueError(f"rows[{i}].{s}: a {kind} value cannot be negative")
                else:
                    total += cell
        if kind in _PARTS_OF_A_WHOLE and rows and total <= 0:
            raise ValueError(f"a {kind} needs a positive total")
        if kind == "donut":
            low, high = VIEW_LIMITS["donut_min_parts"], VIEW_LIMITS["donut_max_parts"]
            if not low <= len(rows) <= high:
                raise ValueError(f"a donut has {low} to {high} parts")
            if self.data.truncated:
                raise ValueError("a donut must show the whole")
        if self.sort is not None:
            totals = [_row_total(row, self.series) for row in rows]
            sign = 1 if self.sort == "asc" else -1
            if any(sign * (b - a) < 0 for a, b in pairwise(totals)):
                raise ValueError(f"rows are not in {self.sort} order of their series total")
        return self

    def _treemap_rules(self, columns: dict[str, ViewColumn]) -> None:
        if self.groups is None and self.color is None:
            return
        if self.chart_type != "treemap":
            raise ValueError("groups and color apply to a treemap only")
        groups = self.groups or []
        for g in groups:
            if g not in columns:
                raise ValueError(f"group {g!r} is not a column of data")
            if g == self.x or g in self.series:
                raise ValueError(f"group {g!r} is the x or a series column")
        if _duplicates(groups):
            raise ValueError("groups repeats a column")
        if self.color is not None and (self.color not in columns or self.color == self.x):
            raise ValueError("color is not a column of data other than x")


def _finite_number(cell: object) -> bool:
    """A number as the TypeScript ``isFiniteNumber`` sees one: no bools, no NaN or infinity."""
    return isinstance(cell, (int, float)) and not isinstance(cell, bool) and math.isfinite(cell)


def _row_total(row: dict[str, ViewCell], series: list[str]) -> float:
    """A row's numeric series values summed in series order, as the TypeScript side sums."""
    total: float = 0
    for s in series:
        cell = row[s]
        if isinstance(cell, (int, float)):
            total += cell
    return total


class NarrativeBlock(StrictContract):
    text: NonEmpty
    cites: list[PartId] = Field(max_length=VIEW_LIMITS["parts"])


class SectionView(ViewPartBase):
    kind: Literal["section"]
    narrative: list[NarrativeBlock] = Field(max_length=VIEW_LIMITS["narrative_blocks"])
    view_ids: list[PartId] = Field(max_length=VIEW_LIMITS["section_views"])

    @model_validator(mode="after")
    def _section_rules(self) -> SectionView:
        if not self.narrative and not self.view_ids:
            raise ValueError("a section needs narrative or views")
        if _duplicates(self.view_ids):
            raise ValueError("view_ids repeats a part")
        return self

    def references(self) -> list[str]:
        return [*self.view_ids, *(cite for block in self.narrative for cite in block.cites)]


# The kind registry. A new kind is one model added here.
ViewPart = Annotated[TableView | ChartView | SectionView, Field(discriminator="kind")]

VIEW_MODELS: tuple[type[ViewPartBase], ...] = get_args(get_args(ViewPart)[0])
VIEW_KINDS: tuple[str, ...] = tuple(
    get_args(model.model_fields["kind"].annotation)[0] for model in VIEW_MODELS
)


class ViewPartList(RootModel[list[ViewPart]]):
    """The parts of one turn or report: unique ids, and every id a part names
    is a data part in the same list."""

    root: list[ViewPart] = Field(max_length=VIEW_LIMITS["parts"])

    @model_validator(mode="after")
    def _references_resolve(self) -> ViewPartList:
        if dupes := _duplicates([part.id for part in self.root]):
            raise ValueError(f"duplicate part ids: {dupes}")
        data_ids = {part.id for part in self.root if part.is_data}
        for part in self.root:
            missing = [ref for ref in dict.fromkeys(part.references()) if ref not in data_ids]
            if missing:
                raise ValueError(f"part {part.id!r} names no data part: {missing}")
        return self
