"""``repowise health`` command package.

Split out of a single ~1160-line module into cohesive parts (mirroring
``init_cmd``/``update_cmd``):

* :mod:`.command` — the Click command + single-repo ingest/analyze/render
* :mod:`.summary` — top-of-report console lines (performance, distribution,
  badge, defect-accuracy)
* :mod:`.refactoring_targets` — impact/effort ranking + the per-type plan
  renderers (Extract Class/Helper/Method, Move Method, Break Cycle, Split File)
* :mod:`.codegen` — opt-in LLM code generation for one suggestion
* :mod:`.trends` — health-snapshot history rendering
* :mod:`.persist` — write health + coverage to the repo's wiki.db

``_render_refactoring_targets`` is re-exported alongside the public command so
``repowise.cli.commands.health_cmd`` stays a stable import surface for the
test-suite.
"""

from __future__ import annotations

from .command import health_command
from .refactoring_targets import _render_refactoring_targets

__all__ = ["_render_refactoring_targets", "health_command"]
