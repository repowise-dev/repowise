"""A framework pass that fails must say what it cost, not pass silently.

Both halves of the pass used to sit under one bare ``except Exception: pass``.
There are 22 framework handlers behind it, so every convention-wired edge in
the repo could vanish leaving a log-clean run and an index that looks
complete, and a framework-invoked symbol with no caller reads as dead. The
two halves also fail differently: losing the tech stack loses the recorded
frameworks as well as the edges, so each reports its own cost.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.pipeline.phases.ingestion import _run_ingestion


class _RecordingProgress:
    """Only the channel a default CLI run renders. See ``emit_warning``."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def on_message(self, level: str, text: str) -> None:
        self.messages.append((level, text))

    def __getattr__(self, _name: str):
        return lambda *a, **k: None

    def warnings(self) -> list[str]:
        return [text for level, text in self.messages if level == "warning"]


def _capture_warnings(monkeypatch) -> list[tuple[str, dict]]:
    """Collect the module logger's warning events and their fields.

    ``caplog`` sees nothing here: the structlog filtering bound logger drops
    the record before stdlib logging is reached, which is the same reason
    ``emit_warning`` exists at all. The fields are kept because an event name
    with no ``error=`` is a log nobody can act on.
    """
    from repowise.core.pipeline.phases import ingestion as module

    events: list[tuple[str, dict]] = []

    class _Recorder:
        def warning(self, event: str, **kw) -> None:
            events.append((event, kw))

        def __getattr__(self, _name: str):
            return lambda *a, **k: None

    monkeypatch.setattr(module, "logger", _Recorder())
    return events


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text("def handler():\n    return 1\n", encoding="utf-8")
    return tmp_path


async def _ingest(repo: Path, progress: _RecordingProgress):
    return await _run_ingestion(
        repo,
        exclude_patterns=None,
        skip_tests=False,
        skip_infra=False,
        progress=progress,
    )


async def test_tech_stack_failure_is_logged_and_surfaced(repo, monkeypatch):
    from repowise.core.generation.editor_files import tech_stack

    def _boom(*_a, **_k):
        raise RuntimeError("stack probe exploded")

    monkeypatch.setattr(tech_stack, "detect_tech_stack", _boom)

    progress = _RecordingProgress()
    events = _capture_warnings(monkeypatch)
    result = await _ingest(repo, progress)

    # Best-effort stays best-effort: the build completes and still parsed the
    # file, and the tech list downstream consumers read is empty, not unbound.
    parsed_files, _infos, _structure, _sources, _builder, _stats, tech_items = result
    assert len(parsed_files) == 1
    assert tech_items == []
    assert ("tech_stack_detection_failed", {"error": "stack probe exploded"}) in events
    assert any("Tech stack detection skipped" in w for w in progress.warnings())


async def test_framework_edge_failure_is_logged_and_surfaced(repo, monkeypatch):
    # Patch the mixin that declares the method, not the class that inherits it,
    # so teardown does not leave a same-valued shadow behind on GraphBuilder.
    from repowise.core.ingestion.graph._edges import EdgesMixin

    def _boom(*_a, **_k):
        raise RuntimeError("handler exploded")

    monkeypatch.setattr(EdgesMixin, "add_framework_edges", _boom)

    progress = _RecordingProgress()
    events = _capture_warnings(monkeypatch)
    result = await _ingest(repo, progress)

    assert len(result[0]) == 1
    assert ("framework_edges_failed", {"error": "handler exploded"}) in events
    assert any("Framework edge detection skipped" in w for w in progress.warnings())


async def test_a_healthy_run_adds_no_framework_warning(repo):
    progress = _RecordingProgress()
    result = await _ingest(repo, progress)

    assert result is not None
    assert not [
        w
        for w in progress.warnings()
        if "Tech stack detection skipped" in w or "Framework edge detection skipped" in w
    ]
