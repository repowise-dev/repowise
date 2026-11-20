"""The first-look report as the web page reads it.

The report itself stays the canonical JSON core writes
(``repowise.core.report.first_look.FirstLookReport.to_json``); its shape is
mirrored by hand in ``packages/types/src/first-look.ts`` and pinned by
``test_wire_vocabulary_parity.py``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from repowise.core.agent_prompts import Flavor


class FirstLookResponse(BaseModel):
    report: dict[str, Any]
    #: The report against the index as it is now (fixed, changed, new ids).
    staleness: dict[str, Any]
    #: "Work through this report", worded for ``flavor``.
    report_prompt: str
    flavor: Flavor
    #: A provider is configured and the writer can run.
    can_rewrite: bool
    #: What one rewrite costs, when it can be priced.
    rewrite_cost_usd: float | None = None

