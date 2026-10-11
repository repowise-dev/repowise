"""Resume phase vocabulary.

These are *coarse* checkpoint boundaries chosen for resume value, distinct
from the fine-grained progress-bar phases the orchestrator emits. Each maps
to one persist step and one rehydration recipe:

- ``INDEX``       parse + graph build (incl. centrality) + git history. The
                  expensive part of a first run (the reporter's log: ~18 min
                  git + ~20 min centrality). Persisted as graph nodes/edges/
                  metrics + symbols + git metadata/commits.
- ``ANALYSIS``    dead code + health + decisions + governance.
- ``GENERATION``  LLM wiki pages + knowledge-graph enrichment.

INDEX is treated as a single unit because its two halves (ingestion and git)
run concurrently and analysis needs both; resuming "half an index" buys
nothing over rebuilding it.
"""

from __future__ import annotations

import enum


class ResumePhase(enum.StrEnum):
    INDEX = "index"
    ANALYSIS = "analysis"
    GENERATION = "generation"


# Persisted order — a phase may only be skipped on resume if it and every
# phase before it completed in a prior run.
RESUME_PHASE_ORDER: tuple[ResumePhase, ...] = (
    ResumePhase.INDEX,
    ResumePhase.ANALYSIS,
    ResumePhase.GENERATION,
)
