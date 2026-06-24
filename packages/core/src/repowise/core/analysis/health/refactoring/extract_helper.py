"""Extract Helper detector — clone dedup as a shared helper (Phase 2).

When the same block of code is duplicated across two or more sites, the
fix is to extract it once into a shared helper. The duplication is not a
heuristic: it *is* the verified clone pairs the health pass already
computed (``duplication.detect_clones`` → ``ctx.clones``), so the
suggestion is deterministic and matches the ``dry_violation`` biomarker.

The detector runs in the existing per-file pass, but a clone block spans
files, so two safeguards keep one block from producing one nag per site:

- **Clone-set clustering.** A file's clone pairs are grouped by the block
  they touch (overlapping line ranges on this file's side), and each
  group's *occurrences* are the block's region here plus every partner's
  region. Because the duplication detector emits a clone bucket pairwise,
  the lexicographically-smallest member of a clone set pairs with every
  other member, so that one anchor file sees the whole set (A↔B, A↔C ⇒
  the set ``{A, B, C}`` is visible from ``A``).
- **Canonical anchor.** Each block is emitted only from the pass of its
  anchor file — the smallest occurrence path — so ``{A, B, C}`` yields one
  suggestion (from ``A``), never three.

Precision-first, the gate demands a block genuinely worth a helper:

- the shared span is at least ``_MIN_HELPER_LINES`` real lines (a helper,
  not a two-line idiom);
- after dropping test-file and generated occurrences (DB migrations,
  vendored bundles) the block still duplicates across ``>= 2`` sites — test
  fixtures and migration boilerplate duplicate constantly but a shared
  helper is the wrong fix for them;
- the recovered impact is read off the file's ``dry_violation`` finding
  when it overlaps the block, else ``0`` (same posture as Extract Class).

Confidence rides the co-change signal: a clone whose sites are actively
co-modified is real, maintained duplication (``high``); a dormant clone is
still worth extracting but ranks ``medium``.
"""

from __future__ import annotations

from typing import Any

from .models import RefactoringContext, RefactoringSuggestion
from .registry import RefactoringDetector, effort_bucket, register

# The biomarker this detector answers — the recovered impact is read off it.
_SOURCE_BIOMARKER = "dry_violation"

# Minimum shared-line span for a clone to be worth a helper. The clone
# pipeline already floors regions at 6 lines (``DEFAULT_MIN_LINES``); a
# helper is a real unit of behaviour, so we ask for a little more and reject
# the smallest window-sized matches that read as incidental similarity.
_MIN_HELPER_LINES = 8

# Co-change count at/above which duplication counts as actively maintained
# (mirrors ``dry_violation._ACTIVE_CO_CHANGE``) — the strong, high-confidence
# smell rather than a dormant clone.
_ACTIVE_CO_CHANGE = 3

# Slack (lines) for treating two clone regions on this file's side as the
# same block — matches the duplication merger's one-line window slack.
_REGION_SLACK = 1


def _is_test_path(path: str) -> bool:
    """Conservative test-file check (mirrors the engine's ``_is_test_file``).

    Kept local so the detector stays self-contained — duplication among test
    fixtures is common and low value, so test occurrences are dropped before
    a suggestion is formed.
    """
    p = path.lower().replace("\\", "/")
    return (
        "/test/" in p
        or "/tests/" in p
        or "/__tests__/" in p
        or p.rsplit("/", 1)[-1].startswith("test_")
        or p.endswith("_test.py")
        or p.endswith("_test.go")
        or p.endswith(".test.ts")
        or p.endswith(".test.tsx")
        or p.endswith(".test.js")
        or p.endswith(".test.mts")
        or p.endswith(".test.cts")
        or p.endswith(".spec.ts")
        or p.endswith(".spec.js")
        or p.endswith(".spec.mts")
        or p.endswith(".spec.cts")
    )


def _is_generated_path(path: str) -> bool:
    """Non-refactorable generated/append-only code (DB migrations, vendored
    bundles). Their boilerplate duplicates heavily but extracting a shared
    helper is the wrong advice — a migration must stay self-contained — so
    these occurrences are dropped like test ones (plan's "no generated-file
    noise" gate)."""
    p = path.lower().replace("\\", "/")
    return (
        "/migrations/versions/" in p
        or "/alembic/versions/" in p
        or "/migrations/" in p
        or "/node_modules/" in p
        or "/vendor/" in p
        or "/__generated__/" in p
        or p.endswith(".min.js")
    )


def _is_skippable_occurrence(path: str) -> bool:
    return _is_test_path(path) or _is_generated_path(path)


class _Block:
    """One duplicated code block as seen from the anchor file: the region on
    this file's side plus the geometry needed to gather every occurrence."""

    __slots__ = ("anchor_end", "anchor_start", "co_change", "occurrences", "token_count")

    def __init__(self, start: int, end: int) -> None:
        self.anchor_start = start
        self.anchor_end = end
        # (file, line_start, line_end) — de-duplicated, sorted at emit time.
        self.occurrences: set[tuple[str, int, int]] = set()
        self.token_count = 0
        self.co_change = 0

    def touches(self, start: int, end: int) -> bool:
        """Overlap (within slack) on the anchor side — same physical block."""
        return start <= self.anchor_end + _REGION_SLACK and end >= self.anchor_start - _REGION_SLACK

    def absorb(self, start: int, end: int, pair: Any, *, anchor: str) -> None:
        self.anchor_start = min(self.anchor_start, start)
        self.anchor_end = max(self.anchor_end, end)
        self.token_count = max(self.token_count, int(getattr(pair, "token_count", 0)))
        self.co_change = max(self.co_change, int(getattr(pair, "co_change_count", 0)))
        # The anchor-side region of this pair is always an occurrence; add it
        # plus the partner region(s). Intra-file pairs contribute both regions.
        self.occurrences.add((anchor, start, end))
        if pair.file_a == anchor and pair.file_b == anchor:
            self.occurrences.add((anchor, pair.b_start_line, pair.b_end_line))
        elif pair.file_a == anchor:
            self.occurrences.add((pair.file_b, pair.b_start_line, pair.b_end_line))
        else:
            self.occurrences.add((pair.file_a, pair.a_start_line, pair.a_end_line))


@register
class ExtractHelperDetector(RefactoringDetector):
    name = "extract_helper"

    def detect(self, ctx: RefactoringContext) -> list[RefactoringSuggestion]:
        if not ctx.clones:
            return []

        blocks = self._cluster_blocks(ctx)
        if not blocks:
            return []

        impact_lookup = self._impact_for_dry_violation(ctx)
        out: list[RefactoringSuggestion] = []
        for block in blocks:
            suggestion = self._build_suggestion(ctx, block, impact_lookup)
            if suggestion is not None:
                out.append(suggestion)

        # Stable order: biggest recovery first, then — because dry_violation
        # deductions are near-uniform, so impact rarely separates clones — the
        # plan's "co_change x span" priority (actively co-modified, larger
        # blocks first), then target for a fully deterministic tie-break.
        out.sort(
            key=lambda s: (
                -s.impact_delta,
                -int(s.evidence.get("co_change_count", 0)),
                -int(s.evidence.get("duplicated_lines", 0)),
                s.target_symbol,
            )
        )
        return out

    def _cluster_blocks(self, ctx: RefactoringContext) -> list[_Block]:
        """Group this file's clone pairs into blocks by their anchor-side
        region. Pairs are processed in a deterministic order so block
        boundaries (and therefore the output) never depend on dict order."""
        anchor = ctx.file_path

        def _anchor_region(pair: Any) -> tuple[int, int]:
            # For an inter-file pair, the region on *this* file's side; for an
            # intra-file pair, the earlier of the two regions.
            if pair.file_a == anchor and pair.file_b == anchor:
                return min(
                    (pair.a_start_line, pair.a_end_line),
                    (pair.b_start_line, pair.b_end_line),
                )
            if pair.file_a == anchor:
                return pair.a_start_line, pair.a_end_line
            return pair.b_start_line, pair.b_end_line

        ordered = sorted(ctx.clones, key=lambda p: _anchor_region(p))
        blocks: list[_Block] = []
        for pair in ordered:
            start, end = _anchor_region(pair)
            target = next((b for b in blocks if b.touches(start, end)), None)
            if target is None:
                target = _Block(start, end)
                blocks.append(target)
            target.absorb(start, end, pair, anchor=anchor)
        return blocks

    def _build_suggestion(
        self,
        ctx: RefactoringContext,
        block: _Block,
        impact_lookup: list[tuple[int, int, float]],
    ) -> RefactoringSuggestion | None:
        # Drop test-file occurrences — duplication among fixtures is noise —
        # then coalesce the overlapping windows the clone detector emits for one
        # physical block into a single site per region (without merging, the
        # same import/parse block reads as "5 sites" when it is really one).
        kept = [o for o in block.occurrences if not _is_skippable_occurrence(o[0])]
        occurrences = _merge_ranges_per_file(kept)
        if len(occurrences) < 2:
            return None

        occ_files = sorted({o[0] for o in occurrences})
        # Canonical anchor: emit each block exactly once, from its smallest
        # occurrence path. (Also guarantees ``ctx.file_path`` is non-test.)
        if ctx.file_path != occ_files[0]:
            return None

        duplicated_lines = max(end - start + 1 for _f, start, end in occurrences)
        if duplicated_lines < _MIN_HELPER_LINES:
            return None

        # Anchor region = this file's merged region overlapping the block (its
        # largest, deterministically) so the headline line range is the real one.
        anchor_region = next(
            ((s, e) for f, s, e in occurrences if f == ctx.file_path),
            (block.anchor_start, block.anchor_end),
        )
        impact = self._impact_for_block(anchor_region, impact_lookup)
        is_intra = len(occ_files) == 1

        plan = {
            "occurrences": [{"file": f, "line_start": s, "line_end": e} for f, s, e in occurrences],
            "suggested_site": self._suggested_site(ctx, occ_files),
            "duplicated_lines": duplicated_lines,
        }
        evidence = {
            "occurrence_count": len(occurrences),
            "duplicated_lines": duplicated_lines,
            "token_count": block.token_count,
            "co_change_count": block.co_change,
            "is_intra_file": is_intra,
        }
        other_files = [f for f in occ_files if f != ctx.file_path]
        blast_radius = {
            "files": other_files,
            "file_count": len(other_files),
            "co_change_count": block.co_change,
        }
        basename = ctx.file_path.replace("\\", "/").rsplit("/", 1)[-1]
        return RefactoringSuggestion(
            refactoring_type=self.name,
            file_path=ctx.file_path,
            target_symbol=f"{basename}:{anchor_region[0]}-{anchor_region[1]}",
            line_start=anchor_region[0],
            line_end=anchor_region[1],
            plan=plan,
            evidence=evidence,
            impact_delta=round(float(impact), 3),
            effort_bucket=effort_bucket(duplicated_lines),
            blast_radius=blast_radius,
            confidence="high" if block.co_change >= _ACTIVE_CO_CHANGE else "medium",
            source_biomarker=_SOURCE_BIOMARKER,
        )

    def _suggested_site(
        self, ctx: RefactoringContext, occ_files: list[str]
    ) -> dict[str, str | None]:
        """Where the shared helper should live: the community centroid of the
        occurrences (the module most of them belong to), with the shared
        directory as a fallback when no community labels exist."""
        module = None
        if ctx.module_map:
            counts: dict[str, int] = {}
            for f in occ_files:
                mod = ctx.module_map.get(f)
                if mod:
                    counts[mod] = counts.get(mod, 0) + 1
            if counts:
                # Centroid = most-shared module; ties broken lexicographically
                # so the site is deterministic.
                module = min(counts, key=lambda m: (-counts[m], m))
        return {"module": module, "directory": _common_directory(occ_files)}

    @staticmethod
    def _impact_for_dry_violation(ctx: RefactoringContext) -> list[tuple[int, int, float]]:
        """The file's ``dry_violation`` findings as (line_start, line_end,
        impact) so a block can claim the impact of the clone it overlaps."""
        out: list[tuple[int, int, float]] = []
        for f in ctx.findings:
            if getattr(f, "biomarker_type", "") != _SOURCE_BIOMARKER:
                continue
            start = getattr(f, "line_start", None)
            end = getattr(f, "line_end", None)
            impact = float(getattr(f, "health_impact", 0.0) or 0.0)
            if start is None or end is None:
                # No region — attribute to the whole file (matches any block).
                out.append((0, 1_000_000_000, impact))
            else:
                out.append((int(start), int(end), impact))
        return out

    @staticmethod
    def _impact_for_block(
        region: tuple[int, int], impact_lookup: list[tuple[int, int, float]]
    ) -> float:
        """Recovered impact for the block whose anchor region overlaps a
        ``dry_violation`` finding; 0 when none does (precision-first)."""
        start, end = region
        best = 0.0
        for f_start, f_end, impact in impact_lookup:
            if start <= f_end and end >= f_start and impact > best:
                best = impact
        return best


def _merge_ranges_per_file(
    occurrences: list[tuple[str, int, int]],
) -> list[tuple[str, int, int]]:
    """Collapse overlapping/adjacent line ranges within each file into one
    region. The clone detector emits a block as several offset windows
    (8-35, 8-36, 9-36, 22-28 …); left unmerged those read as separate sites,
    so a single duplicated block looks far more spread out than it is.

    Returns the merged ``(file, start, end)`` tuples, sorted — so the output
    is deterministic regardless of the input order.
    """
    by_file: dict[str, list[tuple[int, int]]] = {}
    for f, start, end in occurrences:
        by_file.setdefault(f, []).append((start, end))
    out: list[tuple[str, int, int]] = []
    for f, ranges in by_file.items():
        cur_start, cur_end = None, None
        for start, end in sorted(ranges):
            if cur_start is None:
                cur_start, cur_end = start, end
            elif start <= cur_end + _REGION_SLACK:
                cur_end = max(cur_end, end)
            else:
                out.append((f, cur_start, cur_end))
                cur_start, cur_end = start, end
        if cur_start is not None:
            out.append((f, cur_start, cur_end))
    return sorted(out)


def _common_directory(paths: list[str]) -> str | None:
    """Longest shared directory prefix of *paths* (POSIX), or ``None`` when
    they share no directory. Component-wise, never mid-segment."""
    seg_lists = [p.replace("\\", "/").split("/")[:-1] for p in paths]
    if not seg_lists or any(not segs for segs in seg_lists):
        return None
    common: list[str] = []
    for parts in zip(*seg_lists, strict=False):
        if len(set(parts)) == 1:
            common.append(parts[0])
        else:
            break
    return "/".join(common) or None
