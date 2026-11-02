"""Change Records: merged changes reconstructed from git, stored and exported.

:mod:`.reconstruct` reads changes off the main line, :mod:`.source` is the
seam forge metadata comes through (git only by default), :mod:`.record` is the
versioned JSON shape and its in-toto export, and :mod:`.store` keeps the
``changes`` table in step with each index and update. :mod:`.evidence` is the
checklist attached to a record, and :mod:`.collect` gathers and caches it.
:mod:`.outcomes` reads what later history says about a change (reverts, fixes
that name it, its files' later fix rate) through the ``change-outcome`` seam.
"""

from __future__ import annotations

from repowise.core.changes.evidence import (
    EVIDENCE_SCHEMA_VERSION,
    ChangeEvidence,
    EvidenceInputs,
    EvidenceItem,
    build_change_evidence,
)
from repowise.core.changes.reconstruct import ReconstructedChange, reconstruct_changes
from repowise.core.changes.record import (
    PREDICATE_TYPE,
    SCHEMA_VERSION,
    change_record,
    in_toto_statement,
)
from repowise.core.changes.source import (
    ChangeMetadata,
    ChangeMetadataSource,
    change_metadata_sources,
)

__all__ = [
    "EVIDENCE_SCHEMA_VERSION",
    "PREDICATE_TYPE",
    "SCHEMA_VERSION",
    "ChangeEvidence",
    "ChangeMetadata",
    "ChangeMetadataSource",
    "EvidenceInputs",
    "EvidenceItem",
    "ReconstructedChange",
    "build_change_evidence",
    "change_metadata_sources",
    "change_record",
    "in_toto_statement",
    "reconstruct_changes",
]
