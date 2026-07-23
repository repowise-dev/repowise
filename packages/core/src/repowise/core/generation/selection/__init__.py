"""Page selection — single source of truth for which pages get generated.

Used by both ``page_generator.generate_all()`` (to decide what to emit)
and ``cost_estimator.build_generation_plan()`` (to estimate cost without
running the LLM). Both paths call the same :func:`select_pages` function
so the estimate and the actual run can never drift apart.

The selection is a pure function of (parsed_files, graph metrics,
config). It scores every candidate and returns the allow-set: every
candidate that clears its bucket's floor, with nothing rationed.

Import direction (one-way):
    ingestion.models  ←  generation.models  ←  selection
"""

from .scoring import (
    score_api_contract,
    score_file,
    score_infra,
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
    "ModuleGroup",
    "Selection",
    "SelectionInputs",
    "score_api_contract",
    "score_file",
    "score_infra",
    "score_scc",
    "score_symbol",
    "select_pages",
    "summarize_selection",
]
