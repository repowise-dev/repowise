"""The chat-only ``chart`` tool: one call from a question to a drawn chart.

``show_view`` needs a tool result first, a list found inside it and an
artifact id; a light model trips on each step. ``chart`` names a dataset
(:mod:`repowise.server.chat_datasets`) and the chart spec instead, and the
server reads every row, filters, aggregates and draws through the same spec,
guards and part validation ``show_view``'s ``chart`` view uses. The part goes
out as the turn's ``view`` event and is stored with the reply, like any view.

Its receipt carries the totals, shares and overall averages the transform
computed, and every repair or kind change it made, so the reply quotes
numbers instead of working them out. Like ``find_suspects`` it reads the
index in process, so it is offered on the local backend only.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from repowise.server import chat_spec
from repowise.server.chat_chart_part import ChartSource, chart_part
from repowise.server.chat_datasets import catalog, dataset_names, get_dataset, load_rows
from repowise.server.chat_references import CHART_TOOL
from repowise.server.chat_views import (
    TurnViews,
    ViewError,
    args_hash,
    finish_part,
    part_receipt,
)

logger = logging.getLogger(__name__)

_NAME_OR_LIST = {"anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]}


def chart_schema() -> dict[str, Any]:
    """OpenAI-format definition of the ``chart`` tool, listing each dataset's fields."""
    names = dataset_names()
    lines = [f"{name}: {', '.join(get_dataset(name).fields)}" for name in names]
    kinds = [k for k in chat_spec.KINDS if k != "auto"]
    return {
        "type": "function",
        "function": {
            "name": CHART_TOOL,
            "description": (
                "Draw a chart or table of repository data in one call. The server reads "
                "every row of the dataset, filters, groups and draws it, and returns the "
                "exact totals, shares and averages to quote. Datasets and fields:\n"
                + "\n".join(lines)
                + "\nCall with only `dataset` for what each field means and an example. "
                + chat_spec.SPEC_HELP
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "enum": names},
                    "x": _NAME_OR_LIST,
                    "y": _NAME_OR_LIST,
                    "aggregate": {"type": "string", "enum": list(chat_spec.AGGREGATES)},
                    "series": {"type": "string"},
                    "filter": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "field": {"type": "string"},
                                "op": {"type": "string", "enum": list(chat_spec.OPS)},
                                "value": {},
                            },
                            "required": ["field", "value"],
                        },
                    },
                    "sort": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string"},
                            "order": {"type": "string", "enum": ["asc", "desc"]},
                        },
                    },
                    "top": {"type": "integer", "minimum": 1},
                    "other": {
                        "type": "boolean",
                        "description": "Add up the rows past top as Other.",
                    },
                    "kind": {"type": "string", "enum": kinds},
                    "color": {"type": "string"},
                    "weight": {
                        "type": "string",
                        "description": "A field an avg is weighted by, or none for a plain mean.",
                    },
                    "title": {"type": "string"},
                },
                "required": ["dataset"],
            },
        },
    }


def _discovery(name: str | None) -> dict[str, Any]:
    entries = catalog()
    if name is not None:
        entries = [e for e in entries if e["dataset"] == name]
    return {"datasets": entries}


async def run_chart(
    arguments: Mapping[str, Any], repo: str | None, views: TurnViews
) -> dict[str, Any]:
    """Run ``chart``: a receipt on success, an error payload (with a fix) otherwise."""
    name = arguments.get("dataset")
    dataset = get_dataset(name) if isinstance(name, str) else None
    if dataset is None:
        return {
            "error": f"no dataset {name!r}; datasets: {dataset_names()}",
            "error_code": "unknown_dataset",
        }
    options = {k: v for k, v in arguments.items() if k != "dataset"}
    if options.get("x") is None:
        # Discovery: the fields and an example, nothing drawn.
        return _discovery(name)
    try:
        loaded = await load_rows(name, repo)
    except Exception as exc:  # a read failure is the model's to report, not the turn's end
        return {"error": f"{type(exc).__name__}: {exc}", "error_code": "tool_failed"}
    source = ChartSource(
        rows=loaded.rows,
        what=dataset.noun,
        basis={"kind": dataset.basis[0], "source": dataset.basis[1]},
        meta=dataset.fields,
        hierarchy=dataset.hierarchy,
        example=dataset.example,
    )
    try:
        part = finish_part(
            chart_part(source, options),
            part_id=views.next_id(),
            source={
                "artifact_id": uuid4().hex,
                "tool_name": CHART_TOOL,
                "args_hash": args_hash(arguments),
            },
            stale=loaded.stale,
            as_of=loaded.as_of,
        )
        extra = part.pop("receipt", None) or {}
        views.keep(part)
    except ViewError as exc:
        payload = exc.payload()
        if isinstance(payload.get("suggested_spec"), Mapping):
            payload["suggested_spec"] = {"dataset": name, **payload["suggested_spec"]}
        return payload
    except Exception:
        logger.exception("chat_chart_failed", extra={"dataset": name})
        return ViewError("the chart builder failed", code="builder_failed").payload()
    return {**part_receipt(part), **extra}


__all__ = ["CHART_TOOL", "chart_schema", "run_chart"]
