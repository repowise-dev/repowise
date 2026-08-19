"""The decisions phase says which sources failed, separately from which were empty.

``_run_decision_extraction`` renders two lists. A source that found nothing
belongs in the informational one; a source that raised belongs in the warning
one, and must not appear in both — "Nothing found in: pull requests" is exactly
the reassuring sentence a failed source used to produce.
"""

from __future__ import annotations

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


@pytest.fixture
def _patched(monkeypatch, tmp_path):
    import repowise.core.analysis.decision_extractor as de

    monkeypatch.setattr(de, "DecisionExtractor", _StubExtractor)
    monkeypatch.setattr(
        de,
        "enabled_source_names",
        lambda _cfg: ("inline_marker", "git_archaeology", "adr", "pr", "comment"),
    )
    return tmp_path


async def test_failed_source_warns_and_is_kept_out_of_the_empty_list(_patched, monkeypatch):
    progress = _RecordingProgress()

    # The session miner is a separate pass; keep it out of this assertion.
    import repowise.core.sessions.miners.decisions as miners

    monkeypatch.setattr(miners, "session_mining_enabled", lambda _cfg: False)

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
