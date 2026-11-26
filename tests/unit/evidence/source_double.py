"""A test-double ``evidence-source`` plugin: hands back the batches a test gives it.

Registered the way a real plugin is, through a ``repowise.plugins`` entry
point naming :func:`register`. It checks the credentials object the host
passes, resumes from the cursor (the index of the next batch), and fails on
request through its settings, so the contract tests can drive every branch
of the failure contract.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace

from repowise.core.evidence.sources import (
    SourceBatch,
    SourceRefused,
    SourceRequest,
    SourceUnavailable,
    evidence_sources,
)

#: The credential a test hands the source; it must never reach the store.
SECRET = "postgres://reader:s3cr3t-sentinel-9f2c@db.internal/app"


@dataclass(frozen=True)
class Credentials:
    dsn: str


class DoubleSource:
    name = "double"
    kinds = ("profile", "statement_stats", "test_timing", "benchmark")

    def __init__(self) -> None:
        self.batches: list[SourceBatch] = []
        self.fetches = 0

    def fetch(self, request: SourceRequest) -> Iterator[SourceBatch]:
        self.fetches += 1
        if request.credentials != Credentials(SECRET):
            raise SourceRefused("the database refused the login; check the read-only user")
        start = int(request.cursor or 0)
        for i in range(start, len(self.batches)):
            if request.settings.get("unavailable_at") == i:
                raise SourceUnavailable("timed out reading statement statistics")
            if request.settings.get("fail_at") == i:
                raise RuntimeError("unexpected reply")
            yield replace(self.batches[i], next_cursor=str(i + 1))


def register() -> None:
    evidence_sources.register(DoubleSource.name, DoubleSource())
