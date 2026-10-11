"""Resource guards for duplication detection.

Clone detection is the one health stage whose cost is driven by *input
shape* rather than file count: a single minified/generated bundle can
emit hundreds of thousands of near-identical token windows, and the
all-pairs comparison inside a hash bucket is O(k²) in the bucket size.
Left unbounded, a repo that checks in a build artifact (``*.min.js``,
``storybook-static/``, webpack chunks) can wedge the whole pipeline for
hours — see issue #341.

This module isolates every "how much work is too much" decision so the
detector stays a thin pipeline. Three independent layers, any one of
which is sufficient on its own:

1. **Skip generated/minified files** before tokenizing them at all
   (``looks_minified`` — cheap byte scan, no parse).
2. **Cap per-file tokens** and the **repo-wide window budget** so an
   unusual-but-not-minified file (giant generated lookup table) can't
   dominate memory or bucketing cost.
3. **Cap hash-bucket size** so the O(k²) verifier loop is bounded
   regardless of how the first two layers behaved. A bucket with
   thousands of identical windows is degenerate repetition, not a
   meaningful clone — skipping it improves signal *and* safety.

A soft wall-clock deadline is the final backstop for any pathology none
of the above anticipated.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DuplicationLimits:
    """Tunable safety bounds for :func:`detect_clones`.

    Defaults are deliberately generous: they never trip on hand-written
    source (human code averages well under 100 bytes/line and rarely
    exceeds a few thousand tokens per file) but cut off generated/minified
    inputs decisively. Override per-call for tests or special repos.
    """

    # --- minified / generated detection (pre-tokenize) ---
    # Average bytes-per-line above which a file is treated as generated.
    # Hand-written code sits around 30-80; bundlers emit 1k+.
    minified_avg_line_bytes: int = 200
    # A single line longer than this flags the file even when the average
    # is dragged down by trailing blank lines (common in partially-minified
    # bundles with one giant IIFE line).
    minified_max_line_bytes: int = 2_000

    # --- per-file and repo-wide window budgets ---
    # Skip files producing more tokens than this. A file with >100k tokens
    # is not a meaningful clone signal; comparing it only adds cost.
    max_tokens_per_file: int = 100_000
    # Stop collecting once the repo-wide window count crosses this. Bounds
    # peak memory and bucketing time on very large repos.
    max_total_windows: int = 5_000_000

    # --- bucket all-pairs guard (the O(k^2) cap) ---
    # Skip hash buckets larger than this. Big buckets are ubiquitous
    # boilerplate (license headers, identical imports), never real clones.
    max_bucket_windows: int = 256

    # --- ultimate backstop ---
    # Soft wall-clock budget for the bucket-comparison phase, in seconds.
    # ``0`` disables the deadline (used by deterministic tests). On expiry
    # the detector returns the clones found so far and flags ``timed_out``.
    time_budget_secs: float = 60.0


@dataclass
class DuplicationDiagnostics:
    """Counters describing how the guards behaved on one run.

    Surfaced via :attr:`DuplicationReport.diagnostics` so the health
    engine can log *why* a repo produced fewer clone pairs than expected
    (a skipped bundle is invisible otherwise).
    """

    files_considered: int = 0
    files_tokenized: int = 0
    skipped_unreadable: int = 0
    skipped_minified: int = 0
    skipped_token_cap: int = 0
    total_windows: int = 0
    window_budget_hit: bool = False
    degenerate_buckets: int = 0
    timed_out: bool = False

    @property
    def hit_any_limit(self) -> bool:
        """True when any guard actually fired — gates optional logging."""
        return bool(
            self.skipped_minified
            or self.skipped_token_cap
            or self.window_budget_hit
            or self.degenerate_buckets
            or self.timed_out
        )

    def as_log_fields(self) -> dict[str, int | bool]:
        """Flat dict suitable for ``log.debug(**fields)``."""
        return {
            "files_considered": self.files_considered,
            "files_tokenized": self.files_tokenized,
            "skipped_unreadable": self.skipped_unreadable,
            "skipped_minified": self.skipped_minified,
            "skipped_token_cap": self.skipped_token_cap,
            "total_windows": self.total_windows,
            "window_budget_hit": self.window_budget_hit,
            "degenerate_buckets": self.degenerate_buckets,
            "timed_out": self.timed_out,
        }


def looks_minified(source: bytes, limits: DuplicationLimits) -> bool:
    """Cheap structural test for generated/minified content.

    Scans the raw bytes (no decode, no parse) and flags a file when its
    *average* line length is implausibly long, or when *any single line*
    exceeds the hard cap. Both checks short-circuit, so the cost is at
    most one linear pass and usually far less.
    """
    if not source:
        return False

    newline_count = source.count(b"\n")
    line_count = newline_count + 1
    if len(source) / line_count > limits.minified_avg_line_bytes:
        return True

    # Longest single line — walk newline to newline, bailing the moment we
    # cross the cap so a huge one-line bundle exits almost immediately.
    cap = limits.minified_max_line_bytes
    start = 0
    while True:
        nl = source.find(b"\n", start)
        if nl == -1:
            return (len(source) - start) > cap
        if (nl - start) > cap:
            return True
        start = nl + 1
