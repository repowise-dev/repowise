"""What repowise has written, and what removing it would take.

Split from the command for one reason: the plan has to be computable without
writing anything. ``--dry-run`` is then the plan printed and nothing else, and a
real run is the same plan executed, so the two cannot describe different work.
"""

from __future__ import annotations

from .inventory import Group, Item, Plan, build_plan
from .runner import execute

__all__ = ["Group", "Item", "Plan", "build_plan", "execute"]
