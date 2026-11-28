"""A test plugin that declares settings and an ``evidence-source`` that reads them.

Loaded the way a real plugin is, through a ``repowise.plugins`` entry point
naming :func:`register` (the :func:`installed` fixture). The source returns
statement batches from its cursor and fails on request (``SyncSource.fail``).
"""

from __future__ import annotations

import importlib.metadata
from collections.abc import Iterator
from typing import Any

import pytest

from repowise.core.evidence.sources import (
    SourceBatch,
    SourceRefused,
    SourceRequest,
    SourceUnavailable,
    evidence_sources,
)
from repowise.core.registry import plugins
from repowise.core.settings import registry
from repowise.core.settings.plugins import plugin_setting, register_settings

DB = plugin_setting("synctest", "db", "str", None, "Database to read statements from.")
MODE = plugin_setting(
    "synctest", "mode", "str", "fast", "How hard to read.", choices=("fast", "full")
)
TOKEN = plugin_setting("synctest", "token", "str", None, "Read-only database token.", secret=True)

#: The token a test sets; it must never reach a file, a log or the output.
SECRET = "tok-s3cr3t-sentinel-5b1e"

_STATEMENTS = [
    {"statement_fp": "a0b1c2d3e4f50617", "query": "select 1", "calls": 5, "total_ms": 3.0}
]


class SyncSource:
    name = "synctest"
    kinds = ("statement_stats",)
    settings = (DB, MODE, TOKEN)

    def __init__(self) -> None:
        self.requests: list[SourceRequest] = []
        self.batches = 2
        #: ``None``, ``"unavailable"``, ``"refused"`` or ``"leaky"`` (an error quoting the token).
        self.fail: str | None = None

    def fetch(self, request: SourceRequest) -> Iterator[SourceBatch]:
        self.requests.append(request)
        if self.fail == "unavailable":
            raise SourceUnavailable("timed out")
        if self.fail == "refused":
            raise SourceRefused("the database refused the token")
        if self.fail == "leaky":
            raise ConnectionError(f"cannot connect with {request.credentials[TOKEN.key]}")
        for i in range(int(request.cursor or 0), self.batches):
            yield SourceBatch(
                kind="statement_stats",
                workload=f"prod db {i}",
                workload_class=None,
                commit=None,
                entries=_STATEMENTS,
                document={
                    "window": {"start": "2026-10-01T00:00:00Z", "end": "2026-10-02T00:00:00Z"}
                },
                next_cursor=str(i + 1),
            )


def register() -> None:
    register_settings(DB, MODE, TOKEN)
    evidence_sources.register(SyncSource.name, SyncSource())


def _tables() -> tuple[dict, dict, list]:
    registry._load()
    assert registry._by_key is not None
    return dict(registry._by_key), dict(registry._by_env), list(registry._plugin_settings)


def _restore(saved: tuple[dict, dict, list]) -> None:
    by_key, by_env, plugin_settings = saved
    assert registry._by_key is not None
    registry._by_key.clear()
    registry._by_key.update(by_key)
    registry._by_env.clear()
    registry._by_env.update(by_env)
    registry._plugin_settings[:] = plugin_settings


@pytest.fixture
def no_plugins(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No plugin installed; the settings and source tables put back afterwards."""
    yield from _with_entry_points(monkeypatch, [])


@pytest.fixture
def installed(monkeypatch: pytest.MonkeyPatch) -> Iterator[SyncSource]:
    """This module loaded as the only plugin; yields its source."""
    eps = [
        importlib.metadata.EntryPoint(
            "synctest", "tests.unit.settings.plugin_double:register", plugins.ENTRY_POINT_GROUP
        )
    ]
    for _ in _with_entry_points(monkeypatch, eps):
        yield evidence_sources.get("synctest")


def _with_entry_points(monkeypatch: pytest.MonkeyPatch, eps: list[Any]) -> Iterator[None]:
    from repowise.core.issues import issue_sources

    saved = _tables()
    seams = (evidence_sources, issue_sources)
    kept = [(dict(seam._impls), dict(seam._owners)) for seam in seams]
    # Only this test's plugins: a source an earlier load registered, or the
    # built-in forge issue source, is gone too.
    for seam in seams:
        seam._impls.clear()
        seam._owners.clear()
    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda **kw: eps if kw.get("group") == plugins.ENTRY_POINT_GROUP else [],
    )
    monkeypatch.delenv(plugins.ENV_VAR, raising=False)
    for s in (DB, MODE, TOKEN):
        monkeypatch.delenv(s.env.name, raising=False)
    plugins.reset()
    try:
        plugins.load_plugins()
        yield None
    finally:
        plugins.reset()
        for seam, (impls, owners) in zip(seams, kept, strict=True):
            seam._impls.clear()
            seam._impls.update(impls)
            seam._owners.clear()
            seam._owners.update(owners)
        _restore(saved)
