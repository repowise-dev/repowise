"""Native clone-pair detection over tree-sitter tokens.

Pipeline:

1. Tokenize every parsed file with the duplication ``tokenizer``.
2. Rolling-hash each token stream into fixed-size windows.
3. Bucket windows by hash; for each multi-window bucket, verify token
   equality (hash collision-proof) and emit a ``ClonePair``.
4. Merge adjacent windows in the same (file_a, file_b) pair into a
   single contiguous clone region.
5. Weight active vs dormant clone pairs using
   ``git_meta_map[path]['co_change_partners_json']`` — when two files
   that contain a clone also frequently change together, the clone is
   *actively maintained duplication* and should be rated higher.

The Phase-3 plan calls for co-change correlation from day one so that
the ``dry_violation`` biomarker can rank clones by activity, not just
size.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from repowise.core.cancellation import check_cancelled

from .limits import DuplicationDiagnostics, DuplicationLimits, looks_minified
from .rabin_karp import WindowHash, index_by_hash, rolling_hashes
from .tokenizer import tokenize_file

log = structlog.get_logger(__name__)


# Tunables — locked in for v1, may move to ``HealthConfig`` later.
DEFAULT_WINDOW_TOKENS = 50
DEFAULT_MIN_LINES = 6


@dataclass
class ClonePair:
    """One verified clone region between two files (or two regions in
    the same file)."""

    file_a: str
    file_b: str
    a_start_line: int
    a_end_line: int
    b_start_line: int
    b_end_line: int
    token_count: int
    co_change_count: int = 0  # 0 when files don't share co-change history

    @property
    def is_intra_file(self) -> bool:
        return self.file_a == self.file_b

    @property
    def a_line_count(self) -> int:
        return self.a_end_line - self.a_start_line + 1

    @property
    def b_line_count(self) -> int:
        return self.b_end_line - self.b_start_line + 1


@dataclass
class DuplicationReport:
    pairs: list[ClonePair] = field(default_factory=list)
    # Per-file duplication percent: ratio of duplicated lines vs file's
    # total non-blank line count. Used by the dry_violation biomarker
    # and surfaced on HealthFileMetric.duplication_pct.
    duplication_pct: dict[str, float] = field(default_factory=dict)
    # Per-file pair index for fast lookups by biomarker.
    pairs_by_file: dict[str, list[ClonePair]] = field(default_factory=dict)
    # Flat counters describing how the resource guards behaved (files
    # skipped as minified, degenerate buckets dropped, deadline hit, …).
    # Empty on the no-op fallback path. See ``limits.DuplicationDiagnostics``.
    diagnostics: dict[str, int | bool] = field(default_factory=dict)


def _read_source(abs_path: str) -> bytes | None:
    try:
        return Path(abs_path).read_bytes()
    except OSError:
        return None


def _parse_co_change_partners(meta: dict[str, Any]) -> dict[str, int]:
    raw = meta.get("co_change_partners_json")
    if not raw:
        return {}
    try:
        partners = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    out: dict[str, int] = {}
    for p in partners:
        if not isinstance(p, dict):
            continue
        path = p.get("file_path") or p.get("path")
        count = p.get("co_change_count") or p.get("count") or 0
        if not path:
            continue
        try:
            out[str(path)] = int(count)
        except (TypeError, ValueError):
            continue
    return out


def _co_change_score(
    file_a: str,
    file_b: str,
    git_meta_map: dict[str, dict[str, Any]],
) -> int:
    """Bidirectional max — co-change matrices are stored per file, but
    the same pair shows up from both sides, sometimes with slightly
    different counts depending on the window. Take the max."""
    a_meta = git_meta_map.get(file_a, {}) or {}
    b_meta = git_meta_map.get(file_b, {}) or {}
    from_a = _parse_co_change_partners(a_meta).get(file_b, 0)
    from_b = _parse_co_change_partners(b_meta).get(file_a, 0)
    return max(from_a, from_b)


def _tokens_equal(
    a_kinds: list[str],
    b_kinds: list[str],
    a_start: int,
    b_start: int,
    window: int,
) -> bool:
    """Verify hash-collision by comparing token ``kind`` sequences.

    Operates on the per-file kind list (all the verifier ever compared of
    the full ``Token`` records) so cached token streams round-trip without
    rebuilding Token objects.
    """
    if a_start + window > len(a_kinds) or b_start + window > len(b_kinds):
        return False
    return a_kinds[a_start : a_start + window] == b_kinds[b_start : b_start + window]


def _merge_adjacent_pairs(raw: list[ClonePair]) -> list[ClonePair]:
    """Merge overlapping/adjacent clone windows in the same (a, b) pair.

    Two pairs are merged when:
      - they share file_a and file_b
      - their A-side and B-side ranges both touch or overlap (within 1
        line of slack for token-level windows that don't perfectly
        align with statement boundaries).
    """
    if not raw:
        return []

    def _key(p: ClonePair) -> tuple[str, str, int, int]:
        return (p.file_a, p.file_b, p.a_start_line, p.b_start_line)

    raw = sorted(raw, key=_key)
    merged: list[ClonePair] = []
    for p in raw:
        if not merged:
            merged.append(p)
            continue
        last = merged[-1]
        same_pair = last.file_a == p.file_a and last.file_b == p.file_b
        a_touch = p.a_start_line <= last.a_end_line + 1
        b_touch = p.b_start_line <= last.b_end_line + 1
        if same_pair and a_touch and b_touch:
            merged[-1] = ClonePair(
                file_a=last.file_a,
                file_b=last.file_b,
                a_start_line=last.a_start_line,
                a_end_line=max(last.a_end_line, p.a_end_line),
                b_start_line=last.b_start_line,
                b_end_line=max(last.b_end_line, p.b_end_line),
                token_count=last.token_count + p.token_count,
                co_change_count=max(last.co_change_count, p.co_change_count),
            )
        else:
            merged.append(p)
    return merged


def detect_clones(
    parsed_files: Iterable[Any],
    git_meta_map: dict[str, dict[str, Any]] | None = None,
    *,
    window_tokens: int = DEFAULT_WINDOW_TOKENS,
    min_lines: int = DEFAULT_MIN_LINES,
    limits: DuplicationLimits | None = None,
    cache_dir: Path | None = None,
) -> DuplicationReport:
    """Run the duplication pipeline over the supplied parsed files.

    Thin orchestrator over four bounded stages:

    1. :func:`_collect_windows` — read + tokenize each file, skipping
       minified/generated content and over-budget files.
    2. ``index_by_hash`` — group candidate windows by rolling hash.
    3. :func:`_pairs_from_buckets` — verify collisions into clone pairs,
       capping degenerate buckets and honouring a wall-clock deadline.
    4. :func:`_finalize_pairs` / :func:`_aggregate` — merge, filter by
       size, weight by co-change, and roll up per-file metrics.

    Every stage is bounded by :class:`~.limits.DuplicationLimits` so no
    repo shape (minified bundles, generated tables) can wedge the run —
    see issue #341.
    """
    meta_map = git_meta_map or {}
    lim = limits or DuplicationLimits()
    diag = DuplicationDiagnostics()

    cache = None
    if cache_dir is not None:
        from .token_cache import DuplicationTokenCache

        cache = DuplicationTokenCache(cache_dir, window_tokens)
        cache.load()

    per_file_kinds, per_file_nloc, all_windows = _collect_windows(
        parsed_files, window_tokens, lim, diag, cache
    )
    if cache is not None:
        cache.save()
        log.debug(
            "duplication_token_cache",
            hits=cache.hits,
            misses=cache.misses,
        )
    if not all_windows:
        return DuplicationReport(diagnostics=diag.as_log_fields())

    bucket = index_by_hash(all_windows)
    raw_pairs = _pairs_from_buckets(bucket, per_file_kinds, window_tokens, lim, diag)

    final = _finalize_pairs(_merge_adjacent_pairs(raw_pairs), min_lines, meta_map)
    pairs_by_file, duplication_pct = _aggregate(final, per_file_nloc)

    return DuplicationReport(
        pairs=final,
        duplication_pct=duplication_pct,
        pairs_by_file=pairs_by_file,
        diagnostics=diag.as_log_fields(),
    )


# ---------------------------------------------------------------------------
# Stage 1 — windowing
# ---------------------------------------------------------------------------


def _collect_windows(
    parsed_files: Iterable[Any],
    window_tokens: int,
    limits: DuplicationLimits,
    diag: DuplicationDiagnostics,
    cache: Any | None = None,
) -> tuple[dict[str, list[str]], dict[str, int], list[WindowHash]]:
    """Tokenize each file once and emit its rolling-hash windows.

    Files are dropped (and counted in *diag*) when they are unreadable,
    minified/generated, shorter than one window, or exceed the per-file
    token cap. Collection stops cleanly once the repo-wide window budget
    is reached so peak memory stays bounded.

    When a :class:`~.token_cache.DuplicationTokenCache` is supplied,
    unchanged files (by content hash) skip the tokenize + rolling-hash
    work and replay their cached kind sequence and window tuples; every
    gate above still re-evaluates live against the cached lengths.
    """
    import hashlib

    per_file_kinds: dict[str, list[str]] = {}
    per_file_nloc: dict[str, int] = {}
    all_windows: list[WindowHash] = []

    for pf in parsed_files:
        check_cancelled()
        diag.files_considered += 1
        path = pf.file_info.path
        language = pf.file_info.language

        source = _read_source(pf.file_info.abs_path)
        if source is None:
            diag.skipped_unreadable += 1
            continue
        if looks_minified(source, limits):
            diag.skipped_minified += 1
            continue

        cached = None
        content_hash = ""
        if cache is not None:
            content_hash = hashlib.sha256(source).hexdigest()
            cached = cache.get(content_hash)

        if cached is not None:
            kinds, nloc, window_tuples = cached
            windows = [
                WindowHash(
                    file_path=path,
                    hash_value=h,
                    start_index=si,
                    start_line=sl,
                    end_line=el,
                )
                for h, si, sl, el in window_tuples
            ]
        else:
            toks = tokenize_file(language, source)
            if len(toks) > limits.max_tokens_per_file:
                diag.skipped_token_cap += 1
                continue
            kinds = [t.kind for t in toks]
            nloc = _nloc(source)
            windows = rolling_hashes(path, toks, window_tokens)
            if cache is not None:
                cache.put(
                    content_hash,
                    kinds,
                    nloc,
                    [(w.hash_value, w.start_index, w.start_line, w.end_line) for w in windows],
                )

        if len(kinds) < window_tokens:
            continue
        if len(kinds) > limits.max_tokens_per_file:
            diag.skipped_token_cap += 1
            continue

        if len(all_windows) + len(windows) > limits.max_total_windows:
            diag.window_budget_hit = True
            break

        per_file_kinds[path] = kinds
        per_file_nloc[path] = nloc
        all_windows.extend(windows)
        diag.files_tokenized += 1

    diag.total_windows = len(all_windows)
    return per_file_kinds, per_file_nloc, all_windows


# ---------------------------------------------------------------------------
# Stage 2 — bucket verification
# ---------------------------------------------------------------------------


def _pairs_from_buckets(
    bucket: dict[int, list[WindowHash]],
    per_file_kinds: dict[str, list[str]],
    window_tokens: int,
    limits: DuplicationLimits,
    diag: DuplicationDiagnostics,
) -> list[ClonePair]:
    """Verify each hash bucket into clone pairs.

    Buckets larger than ``limits.max_bucket_windows`` are degenerate
    repetition (boilerplate, generated code) — emitting their O(k²) pairs
    is pure noise, so they are skipped and counted. A soft wall-clock
    deadline guards against any unanticipated pathology: on expiry we
    return the pairs found so far rather than spinning indefinitely.
    """
    raw_pairs: list[ClonePair] = []
    seen: set[tuple[str, int, str, int]] = set()
    deadline = (time.monotonic() + limits.time_budget_secs) if limits.time_budget_secs else None

    for i, windows in enumerate(bucket.values()):
        if len(windows) < 2:
            continue
        if len(windows) > limits.max_bucket_windows:
            diag.degenerate_buckets += 1
            continue
        # Bail promptly on Ctrl-C, and check the clock occasionally (cheap)
        # rather than per-pair.
        check_cancelled()
        if deadline is not None and (i & 0x3FF) == 0 and time.monotonic() > deadline:
            diag.timed_out = True
            break
        _verify_bucket(windows, per_file_kinds, window_tokens, seen, raw_pairs)

    return raw_pairs


def _verify_bucket(
    windows: list[WindowHash],
    per_file_kinds: dict[str, list[str]],
    window_tokens: int,
    seen: set[tuple[str, int, str, int]],
    out: list[ClonePair],
) -> None:
    """Confirm every unordered pair in one (bounded) hash bucket.

    Hash equality is necessary but not sufficient — ``_tokens_equal``
    rejects collisions by comparing the actual token sequences.
    """
    for i in range(len(windows)):
        for j in range(i + 1, len(windows)):
            a, b = windows[i], windows[j]
            # Canonicalize so (file_a, file_b) ordering is stable.
            if (a.file_path, a.start_index) > (b.file_path, b.start_index):
                a, b = b, a
            key = (a.file_path, a.start_index, b.file_path, b.start_index)
            if key in seen:
                continue
            seen.add(key)
            if not _tokens_equal(
                per_file_kinds[a.file_path],
                per_file_kinds[b.file_path],
                a.start_index,
                b.start_index,
                window_tokens,
            ):
                continue
            out.append(
                ClonePair(
                    file_a=a.file_path,
                    file_b=b.file_path,
                    a_start_line=a.start_line,
                    a_end_line=a.end_line,
                    b_start_line=b.start_line,
                    b_end_line=b.end_line,
                    token_count=window_tokens,
                )
            )


# ---------------------------------------------------------------------------
# Stage 3 — finalize + roll up
# ---------------------------------------------------------------------------


def _finalize_pairs(
    merged: list[ClonePair],
    min_lines: int,
    meta_map: dict[str, dict[str, Any]],
) -> list[ClonePair]:
    """Drop sub-threshold pairs and attach co-change weight."""
    final: list[ClonePair] = []
    for p in merged:
        if min(p.a_line_count, p.b_line_count) < min_lines:
            continue
        score = _co_change_score(p.file_a, p.file_b, meta_map)
        if score:
            p = ClonePair(
                file_a=p.file_a,
                file_b=p.file_b,
                a_start_line=p.a_start_line,
                a_end_line=p.a_end_line,
                b_start_line=p.b_start_line,
                b_end_line=p.b_end_line,
                token_count=p.token_count,
                co_change_count=score,
            )
        final.append(p)
    return final


def _aggregate(
    final: list[ClonePair],
    per_file_nloc: dict[str, int],
) -> tuple[dict[str, list[ClonePair]], dict[str, float]]:
    """Build the per-file pair index and duplication percentages."""
    pairs_by_file: dict[str, list[ClonePair]] = defaultdict(list)
    dup_lines: dict[str, int] = defaultdict(int)
    for p in final:
        pairs_by_file[p.file_a].append(p)
        dup_lines[p.file_a] += p.a_line_count
        if not p.is_intra_file:
            pairs_by_file[p.file_b].append(p)
            dup_lines[p.file_b] += p.b_line_count

    duplication_pct: dict[str, float] = {}
    for path, dup in dup_lines.items():
        nloc = per_file_nloc.get(path, 0)
        if nloc <= 0:
            continue
        # Cap at 100% — overlapping clones can otherwise double-count
        # the same physical lines.
        duplication_pct[path] = round(min(100.0, 100.0 * dup / nloc), 2)

    return dict(pairs_by_file), duplication_pct


def _nloc(source: bytes) -> int:
    try:
        text = source.decode("utf-8", errors="replace")
    except Exception:
        return 0
    return sum(1 for line in text.splitlines() if line.strip())
