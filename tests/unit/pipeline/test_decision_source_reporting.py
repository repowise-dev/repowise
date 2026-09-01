"""The decisions phase says which sources failed, separately from which were empty.

``_run_decision_extraction`` renders two lists. A source that found nothing
belongs in the informational one; a source that raised belongs in the warning
one, and must not appear in both — "Nothing found in: pull requests" is exactly
the reassuring sentence a failed source used to produce.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.core.pipeline.phases.analysis import _run_decision_extraction


class _RecordingProgress:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def on_message(self, level: str, text: str) -> None:
        self.messages.append((level, text))

    def on_phase_start(self, *_a: object, **_k: object) -> None: ...
    def on_item_done(self, *_a: object, **_k: object) -> None: ...
    def on_phase_done(self, *_a: object, **_k: object) -> None: ...

    def text_at(self, level: str) -> str:
        return " ".join(t for lvl, t in self.messages if lvl == level)


class _StubExtractor:
    """Stands in for DecisionExtractor: pr raised, git/comment found nothing."""

    def __init__(self, *_a: object, **_k: object) -> None: ...

    async def extract_all(self, on_step=None, enabled_sources=None):
        return SimpleNamespace(
            total_found=1,
            decisions=[SimpleNamespace(source="inline_marker")],
            by_source={
                "inline_marker": 1,
                "git_archaeology": 0,
                "adr": 0,
                "pr": 0,
                "comment": 0,
            },
            failures={"pr": "RuntimeError: provider exploded"},
        )


def _write_policy(repo: Path, **sources: bool) -> None:
    """Switch capture sources through the config the pipeline actually reads."""
    lines = ["decisions:", "  sources:"]
    lines += [f"    {key}: {str(value).lower()}" for key, value in sources.items()]
    cfg = repo / ".repowise"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def _patched(monkeypatch, tmp_path):
    """A repo whose whole capture policy is on, with a stubbed extractor.

    The session source is switched off through the policy rather than by
    patching a gate function: the pipeline reads one resolved policy, so a
    patch on anything else silently stops isolating the session miner and the
    test starts reading the developer's real transcripts.
    """
    import repowise.core.analysis.decision_extractor as de

    monkeypatch.setattr(de, "DecisionExtractor", _StubExtractor)
    _write_policy(tmp_path, session=False)
    return tmp_path


async def test_failed_source_warns_and_is_kept_out_of_the_empty_list(_patched):
    progress = _RecordingProgress()

    await _run_decision_extraction(
        _patched,
        llm_client=SimpleNamespace(),
        graph_builder=SimpleNamespace(graph=lambda: None),
        git_meta_map={},
        parsed_files=[],
        progress=progress,
    )

    warnings = progress.text_at("warning")
    info = progress.text_at("info")

    # The failure is reported, names the source, and carries the reason.
    assert "pull requests" in warnings
    assert "provider exploded" in warnings
    # And it is NOT also reported as an honest zero.
    assert "Nothing found in" in info
    nothing_line = next(
        t for lvl, t in progress.messages if lvl == "info" and "Nothing found" in t
    )
    assert "pull requests" not in nothing_line
    # Sources that genuinely found nothing still appear there.
    assert "git history" in nothing_line
    assert "ADR files" in nothing_line


async def test_a_source_that_never_ran_is_not_an_honest_zero(_patched):
    """The keyless default path: three model-only sources return [] at once.

    Reporting those as "nothing found" is the same confusion the failed/empty
    split exists to remove, so they belong on the "not run" line instead.
    """
    progress = _RecordingProgress()

    await _run_decision_extraction(
        _patched,
        llm_client=None,
        graph_builder=SimpleNamespace(graph=lambda: None),
        git_meta_map={},
        parsed_files=[],
        progress=progress,
    )

    info = progress.text_at("info")
    assert "Not run" in info
    not_run = next(t for lvl, t in progress.messages if lvl == "info" and "Not run" in t)
    assert "pull requests" in not_run
    assert "git history" in not_run
    assert "No LLM provider" in not_run

    nothing = [t for lvl, t in progress.messages if lvl == "info" and "Nothing found" in t]
    for line in nothing:
        assert "pull requests" not in line
        assert "git history" not in line


async def test_a_disabled_source_is_reported_as_switched_off(tmp_path, monkeypatch):
    import repowise.core.analysis.decision_extractor as de

    monkeypatch.setattr(de, "DecisionExtractor", _StubExtractor)
    _write_policy(tmp_path, session=False, comment=False)
    progress = _RecordingProgress()

    await _run_decision_extraction(
        tmp_path,
        llm_client=SimpleNamespace(),
        graph_builder=SimpleNamespace(graph=lambda: None),
        git_meta_map={},
        parsed_files=[],
        progress=progress,
    )

    not_run = next(t for lvl, t in progress.messages if lvl == "info" and "Not run" in t)
    assert "comments" in not_run
    assert "agent sessions" in not_run
    assert "switched off" in not_run
