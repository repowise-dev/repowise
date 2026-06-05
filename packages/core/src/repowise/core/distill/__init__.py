"""Distill — index-aware compaction of noisy command output.

The engine orchestrates the pure pieces (router -> filter) around the
stateful ones (omission store, savings ledger) and owns the safety
guarantees the filters rely on:

- **fallback-to-raw**: any filter exception, storage failure, or
  non-improvement returns the original output untouched;
- **reversibility**: the full raw output is persisted before a marker is
  emitted, so ``repowise expand <ref>`` always round-trips;
- **net-positive only**: distilled text (marker included) must actually be
  smaller than the raw output, with a small floor so trivial wins don't
  litter markers everywhere.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path

import structlog

from repowise.core.distill.budget import estimate_tokens, savings_pct
from repowise.core.distill.markers import parse_marker_refs, render_marker
from repowise.core.distill.registry import filter_registry
from repowise.core.distill.router import normalize_command, select_filter
from repowise.core.distill.store import OmissionStore

__all__ = [
    "DistillResult",
    "OmissionStore",
    "distill_output",
    "estimate_tokens",
    "filter_registry",
    "normalize_command",
    "parse_marker_refs",
    "render_marker",
    "savings_pct",
    "select_filter",
]

logger = structlog.get_logger(__name__)

#: Distillation must save at least this many tokens to be worth a marker.
MIN_SAVED_TOKENS = 40


@dataclass(frozen=True)
class DistillResult:
    """Outcome of one distillation attempt."""

    text: str
    distilled: bool
    filter_name: str | None
    ref: str | None
    raw_tokens: int
    distilled_tokens: int

    @property
    def savings_pct(self) -> float:
        return savings_pct(self.raw_tokens, self.distilled_tokens)


def distill_output(
    output: str,
    *,
    command: str = "",
    exit_code: int = 0,
    source: str = "cli",
    store: OmissionStore | None = None,
    store_start: Path | None = None,
    disabled_filters: tuple[str, ...] = (),
) -> DistillResult:
    """Distill *output*, falling back to the raw text on any failure.

    When *store* is None one is opened at the default sidecar location for
    *store_start* (default: cwd) and closed before returning.
    """
    raw_tokens = estimate_tokens(output)
    raw = DistillResult(
        text=output,
        distilled=False,
        filter_name=None,
        ref=None,
        raw_tokens=raw_tokens,
        distilled_tokens=raw_tokens,
    )

    try:
        chosen = select_filter(command, output, disabled=disabled_filters)
    except Exception:
        logger.debug("distill filter selection failed", exc_info=True)
        return raw
    if chosen is None:
        return raw
    if len(output.splitlines()) < chosen.min_lines:
        return raw

    try:
        kept = chosen.distill(output, command=command, exit_code=exit_code)
    except Exception:
        logger.debug("distill filter failed; falling back to raw", filter=chosen.name)
        return raw

    kept_tokens = estimate_tokens(kept)
    omitted_lines = max(0, len(output.splitlines()) - len(kept.splitlines()))

    owns_store = store is None
    try:
        if owns_store:
            store = OmissionStore.open_default(store_start)
        ref = store.put(
            output,
            source=f"{source}:{chosen.name}",
            original_tokens=raw_tokens,
            kept_tokens=kept_tokens,
        )
        marker = render_marker(ref, omitted_lines, max(0, raw_tokens - kept_tokens))
        text = kept.rstrip("\n") + "\n\n" + marker + "\n"
        distilled_tokens = estimate_tokens(text)
        # Net-positive guarantee, marker included.
        if distilled_tokens >= raw_tokens - MIN_SAVED_TOKENS:
            return raw
        store.record_saving(
            filter_name=chosen.name,
            source=source,
            command=command or None,
            raw_tokens=raw_tokens,
            distilled_tokens=distilled_tokens,
        )
        return DistillResult(
            text=text,
            distilled=True,
            filter_name=chosen.name,
            ref=ref,
            raw_tokens=raw_tokens,
            distilled_tokens=distilled_tokens,
        )
    except Exception:
        # Raw output is never recoverable without a successful store write,
        # so a storage failure means we must not drop anything.
        logger.debug("omission store unavailable; falling back to raw", exc_info=True)
        return raw
    finally:
        if owns_store and store is not None:
            with contextlib.suppress(Exception):
                store.close()
