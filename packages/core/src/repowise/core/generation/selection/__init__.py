"""Page selection — single source of truth for which pages get generated.

Used by both ``page_generator.generate_all()`` (to decide what to emit)
and ``cost_estimator.build_generation_plan()`` (to estimate cost without
running the LLM). Both paths call the same :func:`select_pages` function
so the estimate and the actual run can never drift apart.

The selection is a pure function of (parsed_files, graph metrics,
config). It scores every candidate, allocates a share of the global
budget to each page-type bucket, and returns the allow-set.

Import direction (one-way):
    ingestion.models  ←  generation.models  ←  selection
"""

from .budget import BucketAllocation, allocate_budget
from .scoring import (
    score_api_contract,
    score_file,
    score_infra,
    score_module,
    score_scc,
    score_symbol,
)
from .selector import (
    ModuleGroup,
    Selection,
    SelectionInputs,
    select_pages,
    summarize_selection,
)

__all__ = [
    "BucketAllocation",
    "ModuleGroup",
    "Selection",
    "SelectionInputs",
    "allocate_budget",
    "score_api_contract",
    "score_file",
    "score_infra",
    "score_module",
    "score_scc",
    "score_symbol",
    "select_pages",
    "summarize_selection",
]
