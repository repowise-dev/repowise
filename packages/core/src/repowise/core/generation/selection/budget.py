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

FAQ-weighted tilt (:func:`allocate_module_file_pages`) is a second,
optional layer: it keeps the *total* file_page count the bucket
allocation produced but redistributes those slots across modules by how
often agents ask about each, so hot modules get documented deeper and
cold ones lean out. With no demand data it is an exact passthrough.
"""

from __future__ import annotations

from collections import Counter, OrderedDict
from collections.abc import Callable
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
    # Shortcut: when the budget is large enough to cover every available
    # candidate, allocate them all. This is the obviously-correct
    # behavior for ``coverage_pct >= 1.0`` and for repos where the total
    # supply is below the configured budget — there's no signal to be
    # gained by squeezing shares when nothing competes for the slots.
    total_available = sum(candidates_per_bucket.values())
    if total_available > 0 and budget >= total_available:
        return BucketAllocation(
            file_page=candidates_per_bucket.get("file_page", 0),
            symbol_spotlight=candidates_per_bucket.get("symbol_spotlight", 0),
            module_page=candidates_per_bucket.get("module_page", 0),
            api_contract=candidates_per_bucket.get("api_contract", 0),
            infra_page=candidates_per_bucket.get("infra_page", 0),
            scc_page=candidates_per_bucket.get("scc_page", 0),
        )

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


# ---------------------------------------------------------------------------
# FAQ-weighted per-module reallocation of the file_page budget.
# ---------------------------------------------------------------------------

#: A module keeps at least this many file pages when it had any in the
#: demand-free baseline: the "lean baseline" floor of requirement (b). A
#: well-covered but never-asked-about module leans down toward this floor to
#: fund high-demand modules; it can never be zeroed out below it.
LEAN_MODULE_FLOOR = 1


@dataclass(frozen=True)
class ModuleDemandRow:
    """One module's line in the reallocation audit table."""

    module: str
    demand: int
    baseline_pages: int
    allocated_pages: int
    candidates: int

    @property
    def delta(self) -> int:
        return self.allocated_pages - self.baseline_pages


def allocate_module_file_pages(
    ranked_files: list[str],
    total: int,
    demand_by_file: dict[str, int] | None,
    module_of: Callable[[str], str],
) -> tuple[list[str], list[ModuleDemandRow]]:
    """Pick *total* file pages from *ranked_files*, tilted by module demand.

    ``ranked_files`` is the score-descending file-page candidate list.
    ``module_of`` maps a path to its module key (the generation pipeline's
    wiki-page granularity). Returns ``(selected_paths, audit_rows)``.

    Guarantees:

    - **Conservation.** ``len(selected) == min(total, len(ranked_files))``,
      exactly what the demand-free top-*total* would emit. This reallocates
      slots across modules; it never inflates the total.
    - **Floor.** Every module with a baseline page keeps at least
      :data:`LEAN_MODULE_FLOOR` (never dropped below today's lean baseline).
    - **Determinism.** All ordering ties break on the module key, then the
      original score rank, so identical inputs give identical output.
    - **Zero-data passthrough.** ``demand_by_file`` empty (or resolving to no
      in-repo demand) returns ``ranked_files[:total]`` unchanged, so a repo
      with no session history is byte-identical to today.

    ``audit_rows`` carries per-module demand, baseline vs. allocated page
    counts, and available candidates for the inspectable dry-run.
    """
    if total <= 0:
        return [], []
    capped_total = min(total, len(ranked_files))
    baseline = ranked_files[:capped_total]
    if not demand_by_file:
        return baseline, []

    # Candidates grouped by module, preserving global score order.
    mod_files: OrderedDict[str, list[str]] = OrderedDict()
    for path in ranked_files:
        mod_files.setdefault(module_of(path), []).append(path)

    module_demand: dict[str, int] = {
        module: sum(demand_by_file.get(p, 0) for p in paths) for module, paths in mod_files.items()
    }
    if not any(module_demand.values()):
        return baseline, []  # demand exists but none of it lands on candidates

    base_count = Counter(module_of(p) for p in baseline)
    cap = {module: len(paths) for module, paths in mod_files.items()}

    # Floor: modules with a baseline page keep LEAN_MODULE_FLOOR (bounded by
    # their candidate supply and, defensively, by the total budget).
    alloc: dict[str, int] = {module: 0 for module in mod_files}
    reserved = 0
    for module in mod_files:
        if base_count.get(module, 0) >= 1:
            floor = min(LEAN_MODULE_FLOOR, cap[module], capped_total - reserved)
            alloc[module] = floor
            reserved += floor

    # Distribute the remaining slots by demand (largest-remainder, capped at
    # each module's spare candidate supply). Ties break on the module key.
    remaining = capped_total - reserved
    _distribute_by_demand(alloc, cap, module_demand, remaining)

    # Assemble the selection: each module's top-``alloc`` files, then spill any
    # still-unfilled slots back down the demand-free ranking so the total is
    # always met exactly (hot modules can saturate their candidate supply).
    selected: list[str] = []
    picked: set[str] = set()
    for module, paths in mod_files.items():
        for path in paths[: alloc[module]]:
            selected.append(path)
            picked.add(path)
    if len(selected) < capped_total:
        for path in ranked_files:
            if path not in picked:
                selected.append(path)
                picked.add(path)
                if len(selected) >= capped_total:
                    break

    # Re-order to the global score ranking so downstream (landmarks, level
    # ordering) sees the same shape it always has.
    rank = {path: i for i, path in enumerate(ranked_files)}
    selected.sort(key=lambda p: rank[p])

    final_count = Counter(module_of(p) for p in selected)
    audit = [
        ModuleDemandRow(
            module=module,
            demand=module_demand.get(module, 0),
            baseline_pages=base_count.get(module, 0),
            allocated_pages=final_count.get(module, 0),
            candidates=cap[module],
        )
        for module in mod_files
        if module_demand.get(module, 0) or base_count.get(module, 0) != final_count.get(module, 0)
    ]
    audit.sort(key=lambda r: (-r.demand, r.module))
    return selected, audit


def _distribute_by_demand(
    alloc: dict[str, int],
    cap: dict[str, int],
    module_demand: dict[str, int],
    remaining: int,
) -> None:
    """Add *remaining* slots to *alloc* in proportion to demand, capped.

    Largest-remainder apportionment over demand-carrying modules, then any
    leftover (from flooring or saturated caps) goes one at a time to the
    highest-demand module with spare capacity. Mutates *alloc* in place.
    """
    if remaining <= 0:
        return
    total_demand = sum(module_demand.values())
    if total_demand <= 0:
        return

    # Proportional floor share, remembering the fractional remainder.
    order = sorted(module_demand, key=lambda m: (-module_demand[m], m))
    remainders: list[tuple[float, str]] = []
    handed_out = 0
    for module in order:
        spare = cap[module] - alloc[module]
        if spare <= 0 or module_demand[module] <= 0:
            continue
        exact = remaining * module_demand[module] / total_demand
        give = min(int(exact), spare)
        alloc[module] += give
        handed_out += give
        if give < spare:
            remainders.append((exact - int(exact), module))

    # Hand out the rounding leftover by largest fractional remainder, then by
    # demand order for anything still unplaced.
    leftover = remaining - handed_out
    remainders.sort(key=lambda t: (-t[0], t[1]))
    for _, module in remainders:
        if leftover <= 0:
            break
        if cap[module] - alloc[module] > 0:
            alloc[module] += 1
            leftover -= 1
    while leftover > 0:
        progressed = False
        for module in order:
            if leftover <= 0:
                break
            if cap[module] - alloc[module] > 0:
                alloc[module] += 1
                leftover -= 1
                progressed = True
        if not progressed:
            break  # every demand module saturated; caller spills to baseline
