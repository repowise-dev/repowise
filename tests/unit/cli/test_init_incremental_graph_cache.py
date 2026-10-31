from __future__ import annotations

import asyncio
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.cli.commands.init_cmd import _finish


@pytest.mark.parametrize("enabled", [False, True])
def test_init_graph_caches_follow_the_switch(monkeypatch, tmp_path: Path, enabled: bool) -> None:
    if enabled:
        monkeypatch.setenv("REPOWISE_INCREMENTAL_GRAPH", "1")
    else:
        monkeypatch.setenv("REPOWISE_INCREMENTAL_GRAPH", "0")

    cache_calls: list[str] = []
    import repowise.core.pipeline.incremental.graph_seed as graph_seed

    monkeypatch.setattr(
        graph_seed,
        "invalidate_seed_edge_cache",
        lambda _path: cache_calls.append("invalidate"),
    )
    async def save(_path, _builder):
        cache_calls.append("save")

    monkeypatch.setattr(graph_seed, "save_seed_edge_cache", save)

    class Steps:
        def step(self, _name):
            return nullcontext()

    class Callback:
        def __init__(self) -> None:
            self.warnings: list[str] = []
            self.table = SimpleNamespace(stop=lambda _phase: None)

        def on_phase_start(self, *_args):
            pass

        def on_phase_done(self, *_args):
            pass

    class Recorder:
        def __init__(self) -> None:
            self.table = SimpleNamespace(stop=lambda _phase: None)
            self.timings: dict[str, float] = {}

        def rebind(self, _steps):
            return Callback()

    @contextmanager
    def progress_steps(*_args, **_kwargs):
        yield Steps()

    monkeypatch.setattr(_finish, "print_stage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_finish, "progress_steps", progress_steps)

    def run_async(awaitable):
        return asyncio.run(awaitable)

    async def persist_result(*_args):
        return None

    monkeypatch.setattr(_finish, "run_async", run_async)
    monkeypatch.setattr(_finish, "persist_result", persist_result)
    monkeypatch.setattr(_finish, "catch_up_savings", lambda *_args: None)
    monkeypatch.setattr(_finish, "save_run_config", lambda _run: None)

    run = SimpleNamespace(
        total_phases=1,
        recorder=Recorder(),
        repo_path=tmp_path,
        result=SimpleNamespace(graph_builder=object()),
        run_warnings=[],
        phase_timings={},
    )
    _finish.persist_index(run)

    assert cache_calls == (["invalidate", "save"] if enabled else [])
