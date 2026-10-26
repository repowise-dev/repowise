from unittest.mock import AsyncMock, Mock

import pytest

from repowise.core.pipeline import orchestrator
from repowise.core.registry import RepositoryPreprocessors, preprocessors


@pytest.mark.asyncio
async def test_preprocessing_finishes_before_index_phase(monkeypatch, tmp_path):
    registry = RepositoryPreprocessors()
    monkeypatch.setattr(preprocessors, "repository_preprocessors", registry)

    def prepare(root):
        (root / "manual.md").write_text("Converted PDF")
        return ["manual.md"]

    registry.register("documents", prepare)

    async def index(run):
        assert (run.repo_path / "manual.md").read_text() == "Converted PDF"
        raise StopIterationError

    monkeypatch.setattr(orchestrator, "run_index_phase", index)
    with pytest.raises(StopIterationError):
        await orchestrator.run_pipeline(tmp_path)


class StopIterationError(Exception):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("outputs", [[], ["manual.md"]])
async def test_managed_outputs_disable_saved_phase_reuse(monkeypatch, tmp_path, outputs):
    registry = RepositoryPreprocessors()
    monkeypatch.setattr(preprocessors, "repository_preprocessors", registry)
    registry.register("documents", lambda root: outputs)
    controller = Mock()

    async def index(run):
        assert run.resume is (not bool(outputs))
        assert run.resume_controller is (None if outputs else controller)
        raise StopIterationError

    monkeypatch.setattr(orchestrator, "run_index_phase", index)
    with pytest.raises(StopIterationError):
        await orchestrator.run_pipeline(tmp_path, resume=True, resume_controller=controller)


@pytest.mark.asyncio
async def test_preprocessing_failure_aborts_before_index_phase(monkeypatch, tmp_path):
    registry = RepositoryPreprocessors()
    monkeypatch.setattr(preprocessors, "repository_preprocessors", registry)

    def broken(root):
        raise ValueError("Broken PDF")

    registry.register("documents", broken)
    index = AsyncMock()
    monkeypatch.setattr(orchestrator, "run_index_phase", index)
    with pytest.raises(RuntimeError, match="Broken PDF"):
        await orchestrator.run_pipeline(tmp_path)
    index.assert_not_awaited()
