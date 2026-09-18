"""Per-event pricing projections; history is never repriced.

Two halves. :func:`price_tokens` projects a token count at a rate already
captured on an event. :func:`resolve_pricing_snapshot` produces the rate to
capture, at write time, so a later price change cannot rewrite what a past
saving was worth.

Why the snapshot is cached on disk rather than resolved per event:
:func:`repowise.core.distill.session_model.resolve_session_model` scans the
local agent transcripts, and its Codex branch reads every session file end to
end whenever the repository has no Codex history -- the common case, and
measured at around six seconds on a developer machine with 700 sessions. That
is impossible on the MCP path and worse on the hook, which is a fresh
short-lived process per tool call and could not amortize it at all. So the
resolution runs at most once per TTL per repository, is memoized in-process on
top of that, and the hook never triggers one.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Cache lifetime. A developer changes coding model far less often than they
#: run a tool call, and a rate that is a day stale is a much smaller error than
#: either alternative: a six-second stall per event, or no price at all.
_TTL_SECONDS = 24 * 60 * 60

#: Cache file format. Bump when the payload shape changes; an unreadable or
#: unrecognised file is treated as absent, never as an error.
_CACHE_VERSION = 1

_CACHE_NAME = "pricing-snapshot.json"


def price_tokens(tokens: int, usd_per_million: float | None) -> float | None:
    """Price one token dimension from its captured event-time rate."""
    if usd_per_million is None:
        return None
    if not math.isfinite(usd_per_million) or usd_per_million < 0:
        raise ValueError("pricing rate must be finite and nonnegative")
    return max(tokens, 0) * usd_per_million / 1_000_000


@dataclass(frozen=True, slots=True)
class PricingSnapshot:
    """The rates an event was priced at, and where they came from."""

    model: str
    pricing_source: str
    pricing_version: str
    input_rate_usd_per_million: float
    output_rate_usd_per_million: float
    currency: str = "USD"

    def as_payload(self) -> dict[str, Any]:
        """The event fields this snapshot supplies, ready to merge."""
        return {
            "model": self.model,
            "currency": self.currency,
            "pricing_source": self.pricing_source,
            "pricing_version": self.pricing_version,
            "input_rate_usd_per_million": self.input_rate_usd_per_million,
            "output_rate_usd_per_million": self.output_rate_usd_per_million,
        }


#: repository root -> (snapshot, monotonic deadline). Bounds the disk read as
#: well as the scan for a long-lived process such as the MCP server.
_memo: dict[str, tuple[PricingSnapshot | None, float]] = {}


def resolve_pricing_snapshot(
    repo_root: str | Path | None, *, allow_scan: bool = True
) -> PricingSnapshot | None:
    """The pricing snapshot to stamp on an event, or ``None`` when unpriced.

    *allow_scan* is the hot-path switch. With it false the cache is read but
    never refreshed, so the hook pays one small file read and writes an
    unpriced event rather than triggering a transcript scan. Whichever surface
    next runs with scanning allowed refills the cache for it.

    Never raises: an unpriced event is a correct event, and the report reports
    priced and unpriced tokens separately for exactly this reason.
    """
    if repo_root is None:
        return None
    try:
        return _resolve(Path(repo_root), allow_scan=allow_scan)
    except Exception:
        return None


def clear_pricing_cache() -> None:
    """Drop the in-process memo. For tests and long-lived process teardown."""
    _memo.clear()


def _resolve(repo_root: Path, *, allow_scan: bool) -> PricingSnapshot | None:
    key = str(repo_root)
    now = time.monotonic()
    memoized = _memo.get(key)
    if memoized is not None and memoized[1] > now:
        return memoized[0]
    cached = _read_cache(_cache_path(repo_root))
    if cached is not None:
        _memo[key] = (cached, now + _TTL_SECONDS)
        return cached
    if not allow_scan:
        # Deliberately no memo entry: the next scanning surface should refill
        # the cache promptly rather than wait out a TTL this process set.
        return None
    snapshot = _scan(repo_root)
    _write_cache(_cache_path(repo_root), snapshot)
    _memo[key] = (snapshot, now + _TTL_SECONDS)
    return snapshot


def _cache_path(repo_root: Path) -> Path:
    return repo_root / ".repowise" / "omissions" / _CACHE_NAME


def _scan(repo_root: Path) -> PricingSnapshot:
    """Detect the coding agent's model and freeze its rates."""
    from repowise.core.distill.session_model import resolve_session_model
    from repowise.core.generation.cost_tracker import get_model_pricing, pricing_table_version

    resolved = resolve_session_model(repo_root)
    rates = get_model_pricing(resolved.model)
    return PricingSnapshot(
        model=resolved.model,
        # Machine-readable provenance rather than the detector's human label,
        # so a consumer can tell a detected rate from the default without
        # parsing prose. ``unknown`` is the detector's own word for "nothing
        # found", and stays distinguishable from a real agent.
        pricing_source=f"session_model:{resolved.agent}",
        pricing_version=pricing_table_version(),
        input_rate_usd_per_million=float(rates["input"]),
        output_rate_usd_per_million=float(rates["output"]),
    )


def _read_cache(path: Path) -> PricingSnapshot | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("version") != _CACHE_VERSION:
        return None
    resolved_at = raw.get("resolved_at")
    if not isinstance(resolved_at, int | float) or isinstance(resolved_at, bool):
        return None
    if time.time() - float(resolved_at) > _TTL_SECONDS:
        return None
    try:
        return PricingSnapshot(
            model=str(raw["model"]),
            pricing_source=str(raw["pricing_source"]),
            pricing_version=str(raw["pricing_version"]),
            input_rate_usd_per_million=float(raw["input_rate_usd_per_million"]),
            output_rate_usd_per_million=float(raw["output_rate_usd_per_million"]),
            currency=str(raw.get("currency", "USD")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _write_cache(path: Path, snapshot: PricingSnapshot) -> None:
    """Write the cache atomically, or not at all.

    A torn file reads back as absent, which costs another six-second scan, so
    the temporary-then-replace is about latency here as much as correctness.
    """
    payload = {"version": _CACHE_VERSION, "resolved_at": time.time(), **snapshot.as_payload()}
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        with contextlib.suppress(OSError):
            temporary.unlink()
