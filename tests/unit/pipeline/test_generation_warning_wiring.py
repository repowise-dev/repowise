"""A generation-time embed failure must reach the progress callback.

Regression anchor for #1369: the orchestrator warned only through structlog,
and the CLI pins structlog to ERROR unless -v, so a dead Ollama mid-run
produced no visible degradation — the run's ``degraded`` list (persisted to
state.json) stayed empty. ``run_generation`` now threads an ``on_warning``
callback down to the embed-batch failure handler, routing it through
``progress.on_message("warning", ...)`` so the Rich callback records it in
its ``warnings`` list (which init persists as ``degraded``).

This test pins the seam: a stub generator captures the ``on_warning`` it is
handed, and invoking that callback must surface the text through the
progress callback's ``on_message``.
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

import pytest

from repowise.core.pipeline.phases.generation import run_generation


class _WarningRecorder:
    """A progress callback that captures on_message(warning) the way
    RichProgressCallback does."""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def on_phase_start(self, phase: str, total: int | None) -> None:
        pass

    def on_item_done(self, phase: str) -> None:
        pass

    def on_phase_done(self, phase: str) -> None:
        pass

    def on_stage(self, stage: str) -> None:
        pass

    def on_message(self, level: str, text: str) -> None:
        if level == "warning":
            self.warnings.append(text)


class _StubGenerator:
    """Records the on_warning callback it was handed, then returns pages."""

    def __init__(self) -> None:
        self.on_warning: Callable[[str], None] | None = None

    async def generate_all(
        self,
        parsed_files: list,
        *args: object,
        **kwargs: object,
    ) -> list:
        callback = kwargs.get("on_warning")
        assert callback is not None, "run_generation must thread on_warning through"
        self.on_warning = callback  # type: ignore[assignment]
        return []


@pytest.fixture(autouse=True)
def _stub_generator(monkeypatch: pytest.MonkeyPatch) -> _StubGenerator:
    stub = _StubGenerator()
    monkeypatch.setattr(
        "repowise.core.generation.PageGenerator",
        lambda *a, **k: stub,
    )
    return stub


def _parsed_files(n: int) -> list:
    return [SimpleNamespace(file_info=SimpleNamespace(path=f"pkg/mod_{i}.py")) for i in range(n)]


async def test_embed_failure_handled_through_progress_warnings(
    _stub_generator: _StubGenerator, tmp_path: pytest.TempPathFactory
) -> None:
    progress = _WarningRecorder()
    await run_generation(
        repo_path=tmp_path,
        parsed_files=_parsed_files(1),
        source_map={},
        graph_builder=SimpleNamespace(),
        repo_structure=SimpleNamespace(),
        git_meta_map={},
        llm_client=SimpleNamespace(),
        embedder=None,
        vector_store=None,
        concurrency=1,
        progress=progress,
    )
    # The orchestrator's embed-batch handler calls on_warning on failure;
    # wherever it fires, the text must land in the progress warnings.
    assert _stub_generator.on_warning is not None
    _stub_generator.on_warning(
        "Embedding failed for 3 page(s) (RuntimeError: boom) — semantic search "
        "will miss them; run `repowise reindex` to repair."
    )
    assert len(progress.warnings) == 1
    assert "Embedding failed for 3 page(s)" in progress.warnings[0]
