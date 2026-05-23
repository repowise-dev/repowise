"""Tiered doc-generation partition logic.

Splits the set of selected file pages into two tiers for large repos:

* **Tier-1** — the top ``tier1_top_n`` files by PageRank get full LLM
  generation (the existing, unchanged path).
* **Tier-2** — the long tail is rendered from a deterministic Jinja
  template and embedded for search, with no LLM call.

When ``tier1_top_n`` is ``None`` (the default) every selected file page is
tier-1, so behaviour is identical to the pre-tiering generator.
"""

from __future__ import annotations


def partition_file_tiers(
    selected_paths: set[str],
    pagerank: dict[str, float],
    tier1_top_n: int | None,
) -> tuple[set[str], set[str]]:
    """Split selected file-page paths into (tier1, tier2) sets.

    Args:
        selected_paths: Allow-set of file paths chosen for a ``file_page``.
        pagerank:       File-level PageRank scores (higher = more central).
        tier1_top_n:    Cap on the number of full-LLM (tier-1) pages. ``None``
                        or a value >= len(selected_paths) puts every page in
                        tier-1, exactly reproducing the prior behaviour.

    Returns:
        ``(tier1_paths, tier2_paths)`` — a partition of ``selected_paths``.
    """
    if tier1_top_n is None or tier1_top_n >= len(selected_paths):
        return set(selected_paths), set()
    if tier1_top_n <= 0:
        return set(), set(selected_paths)

    # Rank by PageRank desc, then path for a deterministic tie-break.
    ranked = sorted(
        selected_paths,
        key=lambda p: (-pagerank.get(p, 0.0), p),
    )
    tier1 = set(ranked[:tier1_top_n])
    tier2 = set(ranked[tier1_top_n:])
    return tier1, tier2
