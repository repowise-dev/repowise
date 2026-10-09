"""One composition of the change-review primitives, for every surface.

The primitives each answer one question well -- risk scoring, health delta,
contract impact, test impact, independent grouping, branch overlap -- and every
surface has been composing them itself. :class:`ChangeReviewService` composes
them once, over a single change manifest, into a versioned
:class:`ChangeReviewBundle` that carries the full population and says which
lanes were actually consulted.

Semantics live here; caps, ordering for display and rendering live in the
surface. Nothing in this package imports ``repowise.server``, an MCP registry,
a response budget, GitHub, or a database session.
"""

from __future__ import annotations

from .models import (
    CHANGE_REVIEW_CONTRACT_VERSION,
    LANES,
    ChangeManifestEntry,
    ChangeReviewBundle,
    ChangeReviewRequest,
    LaneState,
)
from .service import ChangeReviewEvidence, ChangeReviewService, ContractInputs

__all__ = [
    "CHANGE_REVIEW_CONTRACT_VERSION",
    "LANES",
    "ChangeManifestEntry",
    "ChangeReviewBundle",
    "ChangeReviewEvidence",
    "ChangeReviewRequest",
    "ChangeReviewService",
    "ContractInputs",
    "LaneState",
]
