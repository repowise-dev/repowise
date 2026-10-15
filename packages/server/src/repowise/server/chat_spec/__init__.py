"""The chart spec: parse and repair it, fit it to the rows, then turn rows into chart rows.

Pure functions over a list of row dicts. ``parse_spec`` checks a spec's shape
and repairs the obvious slips; ``fit`` checks it against the rows and repairs
what the rows decide; ``transform`` filters, groups, sorts and cuts in one
pass and picks the chart type. Every refusal is a ``SpecError`` whose message
says what to change. Callers: :mod:`repowise.server.chat_chart_part`.
"""

from repowise.server.chat_spec.fit import check_fields, field_types, fit
from repowise.server.chat_spec.kinds import default_title
from repowise.server.chat_spec.parse import (
    AGGREGATES,
    ARROW,
    KINDS,
    OPS,
    OPTIONS,
    OTHER_LABEL,
    SPEC_HELP,
    ChartSpec,
    Filter,
    SpecError,
    label,
    parse_spec,
    read_kind,
)
from repowise.server.chat_spec.transform import ChartRows, transform

__all__ = [
    "AGGREGATES",
    "ARROW",
    "KINDS",
    "OPS",
    "OPTIONS",
    "OTHER_LABEL",
    "SPEC_HELP",
    "ChartRows",
    "ChartSpec",
    "Filter",
    "SpecError",
    "check_fields",
    "default_title",
    "field_types",
    "fit",
    "label",
    "parse_spec",
    "read_kind",
    "transform",
]
