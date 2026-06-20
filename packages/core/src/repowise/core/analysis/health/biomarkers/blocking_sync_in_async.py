"""Blocking-sync-in-async — a synchronous blocking call inside ``async def``.

A synchronous ``time.sleep`` / ``requests.get`` / ``subprocess.run`` /
``os.system`` / bare ``open`` inside an ``async def`` blocks the whole event
loop for its entire duration, stalling every other coroutine. A ``performance``
dimension signal mirroring ruff's ASYNC210 / ASYNC230 / ASYNC251.

Precision-first: the walker's perf pass uses a small known-API allowlist of
*always-synchronous* calls and fires only when the call is NOT awaited, so an
``await`` on an async client never trips it. Python only in v1 (its async-fn
node type is unambiguous); this detector lifts the pre-collected hits into
findings.
"""

from __future__ import annotations

from ..models import Severity
from .base import BiomarkerResult, FileContext

_KIND = "blocking_sync_in_async"


class BlockingSyncInAsyncDetector:
    name = _KIND
    category = "performance"

    def detect(self, ctx: FileContext) -> list[BiomarkerResult]:
        out: list[BiomarkerResult] = []
        for hit in ctx.perf_hits:
            if hit.kind != _KIND:
                continue
            api = hit.detail or "a blocking call"
            out.append(
                BiomarkerResult(
                    biomarker_type=self.name,
                    severity=Severity.MEDIUM,
                    function_name=hit.function,
                    line_start=hit.line,
                    line_end=hit.line,
                    details={"api": hit.detail},
                    reason=f"{api} blocks the event loop inside an async function",
                )
            )
        return out


BIOMARKER = BlockingSyncInAsyncDetector()
