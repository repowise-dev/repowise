"""#3185: size-skip notices are surfaced but not persisted as degradations.

``_emit_traversal_summary`` names each skipped source file so a dropped entry
point is not silent (#1237). Those names are progress notices, not analysis
degradations, so they must not be emitted as ``warning`` — ``RichProgressCallback``
collects ``warning`` messages into ``warnings``, and ``init`` then persists that
list as ``state["degraded"]``, which is served to agents on every MCP reply.
"""

from __future__ import annotations

from types import SimpleNamespace

from repowise.core.pipeline.phases.ingestion import _emit_traversal_summary


class _RecordingProgress:
    """Mirrors the warning-collection semantics of ``RichProgressCallback``:
    only ``warning``/``error`` messages become degradations; notices and info
    are surfaced but not persisted.
    """

    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.messages: list[tuple[str, str]] = []

    def on_phase_start(self, phase: str, total: int | None) -> None: ...

    def on_item_done(self, phase: str) -> None: ...

    def on_phase_done(self, phase: str) -> None: ...

    def on_stage(self, stage: str) -> None: ...

    def on_message(self, level: str, text: str) -> None:
        self.messages.append((level, text))
        if level in ("warning", "error"):
            self.warnings.append(text)


def _stats(skipped_source_files: list, truncated: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        total_paths_walked=10,
        skipped_source_files=skipped_source_files,
        skipped_source_files_truncated=truncated,
        lang_counts={"py": 1},
    )


def test_size_skip_notice_not_collected_as_degradation():
    progress = _RecordingProgress()
    skipped = [
        SimpleNamespace(path="src/big.h", size_kb=520, reason="looks minified"),
        SimpleNamespace(path="src/huge.cs", size_kb=9745, reason="over_limit"),
    ]
    _emit_traversal_summary(progress, _stats(skipped), included=8)
    # The notice is still surfaced to the user ...
    assert any("Not indexed" in text for _, text in progress.messages)
    # ... but it is NOT collected as a degradation/warning, so it can never
    # reach ``state["degraded"]`` (#3185).
    assert not any("Not indexed" in w for w in progress.warnings)


def test_truncated_size_skip_notice_not_collected_as_degradation():
    progress = _RecordingProgress()
    skipped = [SimpleNamespace(path="src/big.h", size_kb=520, reason="looks minified")]
    _emit_traversal_summary(progress, _stats(skipped, truncated=True), included=8)
    assert any("more source files skipped" in text for _, text in progress.messages)
    assert not any("more source files skipped" in w for w in progress.warnings)
