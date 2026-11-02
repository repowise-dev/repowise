"""Agent prompts rendered in core: plain wire dicts in, prompt text out.

No session and no store: a caller loads the item the way it already does and
hands over its ``as_dict()``. The only renderer of these prompts: the web shows
them from the server, and ``tests/fixtures/agent_prompts`` pins the bytes.
"""

from __future__ import annotations

from .action import render_action
from .fix_first import render_fix_item, render_verify_lines
from .preamble import FLAVORS, Flavor
from .refactoring import render_opportunity, render_plan

__all__ = [
    "FLAVORS",
    "Flavor",
    "render_action",
    "render_fix_item",
    "render_opportunity",
    "render_plan",
    "render_verify_lines",
]
