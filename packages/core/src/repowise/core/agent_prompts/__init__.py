"""Agent prompts rendered in core: plain wire dicts in, prompt text out.

No session and no store: a caller loads the item the way it already does and
hands over its ``as_dict()``. The only renderer of these prompts: the web shows
them from the server, and ``tests/fixtures/agent_prompts`` pins the bytes.
"""

from __future__ import annotations

from .action import render_action
from .checks import CHECK_PROMPTS
from .evidence import CONNECT_EVIDENCE, render_connect_evidence
from .first_look import render_first_look_item, render_first_look_report
from .first_prompts import FIRST_PROMPTS, first_prompt, first_prompts, workspace_prompt
from .fix_and_verify import render_fix_and_verify
from .fix_first import render_fix_item, render_verify_lines
from .performance import render_perf_opportunity
from .preamble import FLAVORS, Flavor
from .refactoring import render_opportunity, render_plan
from .setup import FINISH_SETUP, render_finish_setup, render_finish_setup_workspace

__all__ = [
    "CHECK_PROMPTS",
    "CONNECT_EVIDENCE",
    "FINISH_SETUP",
    "FIRST_PROMPTS",
    "FLAVORS",
    "Flavor",
    "first_prompt",
    "first_prompts",
    "render_action",
    "render_connect_evidence",
    "render_finish_setup",
    "render_finish_setup_workspace",
    "render_first_look_item",
    "render_first_look_report",
    "render_fix_and_verify",
    "render_fix_item",
    "render_opportunity",
    "render_perf_opportunity",
    "render_plan",
    "render_verify_lines",
    "workspace_prompt",
]
