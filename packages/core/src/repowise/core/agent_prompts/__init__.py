"""Agent prompts rendered in core: plain wire dicts in, prompt text out.

No session and no store: a caller loads the item the way it already does and
hands over its ``as_dict()``. The web builders in
``packages/ui/src/health/ai-prompts`` render the same bytes, which the goldens in
``tests/fixtures/agent_prompts`` check from both sides.
"""

from __future__ import annotations

from .action import render_action
from .fix_first import render_fix_item, render_verify_lines
from .preamble import FLAVORS, Flavor

__all__ = ["FLAVORS", "Flavor", "render_action", "render_fix_item", "render_verify_lines"]
