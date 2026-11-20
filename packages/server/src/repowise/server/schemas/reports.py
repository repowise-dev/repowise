"""The repo report as the web page reads it, and a mark written from it.

The report itself stays the JSON core writes
(``repowise.core.report.first_look.living.LivingReport.to_json``); its shape is
mirrored by hand in ``packages/types/src/first-look.ts`` and pinned by
``test_wire_vocabulary_parity.py``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from repowise.core.agent_prompts import Flavor


class FirstLookResponse(BaseModel):
    #: The living report: the report now, each item's status, and the baseline.
    report: dict[str, Any]
    #: "Work through this report", over the open items, worded for ``flavor``.
    report_prompt: str
    flavor: Flavor
    #: A provider is configured, so a model can write the report.
    can_rewrite: bool
    #: What one rewrite costs, when it can be priced.
    rewrite_cost_usd: float | None = None


class FirstLookMarkRequest(BaseModel):
    status: Literal["done", "ack", "false_positive", "snooze"]
    #: Required for ``ack`` and ``false_positive``.
    reason: str | None = Field(default=None, max_length=500)
    #: How long a snooze lasts; core's default when omitted.
    days: int | None = Field(default=None, ge=1, le=365)


class FirstLookMarkResponse(BaseModel):
    id: str
    #: What the item now reads as (a false positive on an action reads as acknowledged).
    status: str
    #: The store the answer went to.
    store: str
    until: str | None = None
