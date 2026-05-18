"""Budget allocation across page-type buckets.

Pure functions — no I/O, no dependency on ParsedFile or graph types.
Given ``coverage_pct`` and per-bucket shares, compute how many pages of
each type to emit.

Design choices
--------------
- **No absolute cap.** Budget = ``int(N_files * coverage_pct)``. The user
  chose the coverage percentage; we honor it even on huge repos.
- **Per-bucket floors** ensure tiny repos still get at least one
  ``repo_overview`` / ``architecture_diagram`` / onboarding slot.
- **Unused share spills into ``file_page``.** When a bucket can't fill
  its share (e.g. only 2 SCCs exist but share gives 5), the difference
  is reallocated to the file_page bucket — file pages are the most
  flexible bucket.
"""

from __future__ import annotations

from dataclasses import dataclass


# Page-type identifiers handled by the budget. Onboarding is curated and
# sized by ``len(specs)``; it is not budgeted here.
BUCKET_TYPES = (
    "file_page",
    "symbol_spotlight",
    "module_page",
    "api_contract",
    "infra_page",
    "scc_page",
)

# Per-bucket floor — minimum pages emitted when the share would round to
# below this and at least one candidate exists. ``api_contract``,
# ``infra_page``, and ``scc_page`` are inherently small, bounded buckets
# (a repo has at most a handful of each); when candidates are present
# we always emit at least one so important infra/API/cycle documentation
# isn't lost to percentage rounding. ``repo_overview`` +
# ``architecture_diagram`` are not budgeted (always 1 each).
_BUCKET_FLOOR: dict[str, int] = {
    "file_page": 0,
    "symbol_spotlight": 0,
    "module_page": 0,
    "api_contract": 1,
    "infra_page": 1,
    "scc_page": 1,
}


@dataclass(frozen=True)
class BucketAllocation:
    """Per-bucket page count target.

    Caller picks the top-K candidates from each bucket where K equals
    the matching field. ``file_page`` absorbs any leftover share that
    other buckets couldn't fill.
    """

    file_page: int
    symbol_spotlight: int
    module_page: int
    api_contract: int
    infra_page: int
    scc_page: int

    @property
    def total(self) -> int:
        return (
            self.file_page
            + self.symbol_spotlight
            + self.module_page
            + self.api_contract
            + self.infra_page
            + self.scc_page
        )


# Repos with fewer files than this are considered "small" — the
# coverage percentage rounds badly, so every available bucket gets at
# least one page instead of being squeezed out by share arithmetic.
SMALL_REPO_THRESHOLD = 20


def compute_budget(n_files: int, coverage_pct: float) -> int:
    """Return the global page budget for a repo of ``n_files``.

    Pure percentage; no absolute cap. For repos at or below
    :data:`SMALL_REPO_THRESHOLD` files, the budget floors at ``n_files``
    so percentage rounding never zeros out tiny repos.
    """
    if n_files <= 0:
        return 0
    pct = max(0.0, min(coverage_pct, 1.0))
    raw = int(n_files * pct)
    if n_files <= SMALL_REPO_THRESHOLD:
        return max(raw, n_files)
    return raw


def allocate_budget(
    *,
    budget: int,
    candidates_per_bucket: dict[str, int],
    shares: dict[str, float],
    n_files: int = 0,
) -> BucketAllocation:
    """Split *budget* into per-bucket targets.

    Parameters
    ----------
    budget:
        Total page budget (see :func:`compute_budget`).
    candidates_per_bucket:
        Number of *available* candidates per bucket. A bucket's target
        is capped at its candidate count — no point allocating 20 infra
        pages when only 7 infra files exist.
    shares:
        Fractional share of the budget per bucket. Keys must include
        every entry in :data:`BUCKET_TYPES`. Shares need not sum to 1;
        any leftover budget after capping spills into ``file_page``.
    """
    # For small repos, every bucket with at least one candidate gets
    # at least one page — this avoids percentage rounding zeroing out
    # buckets when the budget is tiny.
    small_repo_floor = 1 if 0 < n_files <= SMALL_REPO_THRESHOLD else 0

    raw: dict[str, int] = {}
    for bucket in BUCKET_TYPES:
        share = max(0.0, shares.get(bucket, 0.0))
        target = int(round(budget * share))
        target = max(target, _BUCKET_FLOOR[bucket])
        available = candidates_per_bucket.get(bucket, 0)
        if available > 0:
            target = max(target, small_repo_floor)
        target = min(target, available)
        raw[bucket] = target

    # Spill unused share into file_page (the most flexible bucket).
    spent = sum(raw.values())
    spill = max(0, budget - spent)
    if spill > 0:
        max_file = candidates_per_bucket.get("file_page", 0)
        raw["file_page"] = min(max_file, raw["file_page"] + spill)

    return BucketAllocation(
        file_page=raw["file_page"],
        symbol_spotlight=raw["symbol_spotlight"],
        module_page=raw["module_page"],
        api_contract=raw["api_contract"],
        infra_page=raw["infra_page"],
        scc_page=raw["scc_page"],
    )
