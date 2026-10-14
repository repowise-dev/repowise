"""Chat view parts: tables, charts and report sections.

Twin of ``packages/types/src/views.ts``; read that module for what each field
means. View builders produce these from tool results already in a chat turn,
so every data part names its source result, basis, coverage and index commit.

``ViewPart`` is the kind registry: a discriminated union with one model per
kind, from which ``VIEW_MODELS`` and ``VIEW_KINDS`` are derived. A new kind is
one model added to that union. ``packages/types/fixtures/views.conformance.json``
is run by both this module's tests and the TypeScript validator's, so the two
sides accept and reject the same payloads.
"""

from __future__ import annotations

from collections import Counter
from itertools import pairwise
from typing import Annotated, Literal, get_args

from pydantic import Field, RootModel, field_validator, model_validator

from repowise.server.schemas.evidence import CardBasis, CardRef, StrictContract

VIEW_SCHEMA_VERSION = 1

ChartType = Literal["bar", "stacked_bar", "line", "donut"]
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
    "donut_max_parts": 5,
    "narrative_blocks": 50,
    "section_views": 20,
}

NonEmpty = Annotated[str, Field(min_length=1)]
PartId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")]
ViewCell = str | int | float | CardRef | None


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
    schema_version: Literal[1]
    id: PartId
    title: NonEmpty
    summary: NonEmpty | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def _not_a_bool(cls, value: object) -> object:
        # Literal[1] alone accepts True, which the TypeScript side rejects.
        if isinstance(value, bool):
            raise ValueError(f"expected {VIEW_SCHEMA_VERSION}")
        return value


class ViewProvenance(StrictContract):
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
    total_rows: int = Field(ge=0)
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
    "line": (1, VIEW_LIMITS["chart_series"]),
    "stacked_bar": (2, VIEW_LIMITS["chart_series"]),
    "donut": (1, 1),
}
_PARTS_OF_A_WHOLE = frozenset({"stacked_bar", "donut"})


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class ChartView(ViewProvenance, ViewPartBase):
    """A chart over its own table; see ``ChartView`` in views.ts for the rules."""

    kind: Literal["chart"]
    chart_type: ChartType
    data: ViewTableData
    x: NonEmpty
    series: list[NonEmpty] = Field(max_length=VIEW_LIMITS["chart_series"])
    sort: ChartSortOrder | None = None
    summary: NonEmpty

    @model_validator(mode="after")
    def _chart_rules(self) -> ChartView:
        columns = {c.key: c for c in self.data.columns}
        rows = self.data.rows
        if self.x not in columns:
            raise ValueError("x is not a column of data")
        for s in self.series:
            if s not in columns:
                raise ValueError(f"series {s!r} is not a column of data")
            if s == self.x:
                raise ValueError(f"series {s!r} is the x column")
        if _duplicates(self.series):
            raise ValueError("series repeats a column")
        low, high = _SERIES_BOUNDS[self.chart_type]
        if not low <= len(self.series) <= high:
            raise ValueError(f"a {self.chart_type} chart plots {low} to {high} series")
        if len(rows) > VIEW_LIMITS["chart_points"]:
            raise ValueError(f"a chart plots at most {VIEW_LIMITS['chart_points']} rows")
        if len({columns[s].unit for s in self.series}) > 1:
            raise ValueError("plotted columns must share one unit")
        for i, row in enumerate(rows):
            if row[self.x] is None:
                raise ValueError(f"rows[{i}]: x is null")
            for s in self.series:
                cell = row[s]
                if cell is None:
                    if self.chart_type == "donut":
                        raise ValueError(f"rows[{i}].{s}: a donut part is null")
                elif not _is_number(cell):
                    raise ValueError(f"rows[{i}].{s}: plotted values must be numbers")
                elif cell < 0 and self.chart_type in _PARTS_OF_A_WHOLE:  # type: ignore[operator]
                    raise ValueError(f"rows[{i}].{s}: a {self.chart_type} value cannot be negative")
        if self.chart_type == "donut":
            low, high = VIEW_LIMITS["donut_min_parts"], VIEW_LIMITS["donut_max_parts"]
            if not low <= len(rows) <= high:
                raise ValueError(f"a donut has {low} to {high} parts")
            if self.data.truncated:
                raise ValueError("a donut must show the whole")
        if self.sort is not None:
            totals = [
                sum((row[s] if _is_number(row[s]) else 0) for s in self.series)  # type: ignore[misc]
                for row in rows
            ]
            sign = 1 if self.sort == "asc" else -1
            if any(sign * (b - a) < 0 for a, b in pairwise(totals)):
                raise ValueError(f"rows are not in {self.sort} order of their series total")
        return self


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
        """Every part id this section names, in order."""
        return [*self.view_ids, *(cite for block in self.narrative for cite in block.cites)]


# The kind registry. A new kind is one model added here.
ViewPart = Annotated[TableView | ChartView | SectionView, Field(discriminator="kind")]

VIEW_MODELS: tuple[type[ViewPartBase], ...] = get_args(get_args(ViewPart)[0])
VIEW_KINDS: tuple[str, ...] = tuple(
    get_args(model.model_fields["kind"].annotation)[0] for model in VIEW_MODELS
)


class ViewPartList(RootModel[list[ViewPart]]):
    """The parts of one turn or report: unique ids, and every id a section
    names is a table or chart in the same list."""

    root: list[ViewPart] = Field(max_length=VIEW_LIMITS["parts"])

    @model_validator(mode="after")
    def _references_resolve(self) -> ViewPartList:
        ids = [part.id for part in self.root]
        if dupes := _duplicates(ids):
            raise ValueError(f"duplicate part ids: {dupes}")
        data_ids = {part.id for part in self.root if not isinstance(part, SectionView)}
        for part in self.root:
            if isinstance(part, SectionView):
                missing = [ref for ref in dict.fromkeys(part.references()) if ref not in data_ids]
                if missing:
                    raise ValueError(f"section {part.id!r} names no table or chart: {missing}")
        return self
