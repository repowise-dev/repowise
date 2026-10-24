"""Runtime-downloaded grammars (COBOL, Apex) load lazily and fail loudly."""

from __future__ import annotations

import subprocess
from typing import ClassVar

import pytest

from repowise.core.ingestion.parser import grammars


@pytest.fixture
def fresh_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(grammars, "_LANGUAGE_REGISTRY", {})
    monkeypatch.setattr(grammars, "_GRAMMAR_FAILURES", {})


@pytest.fixture
def offline_pack(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The language pack as an offline machine sees it: every load raises."""
    import tree_sitter_language_pack as pack

    calls: list[str] = []

    def _offline(name: str) -> object:
        calls.append(name)
        raise RuntimeError("Download error: Failed to fetch manifest")

    monkeypatch.setattr(pack, "get_language", _offline)
    return calls


def test_loading_one_grammar_does_not_load_the_language_pack(
    fresh_registry: None, offline_pack: list[str]
) -> None:
    """A Python-only index must never reach the runtime download."""
    assert grammars._get_language("python") is not None
    assert offline_pack == []
    assert "cobol" not in grammars._GRAMMAR_FAILURES


def test_an_unloadable_runtime_grammar_is_reported_with_its_reason(
    fresh_registry: None, offline_pack: list[str]
) -> None:
    result = grammars.unavailable_runtime_grammars(["python", "cobol", "cobol"])

    assert result == [("cobol", "Download error: Failed to fetch manifest")]


def test_a_failed_grammar_is_not_retried_per_file(
    fresh_registry: None, offline_pack: list[str]
) -> None:
    """One attempt per process, not one network timeout per COBOL file."""
    for _ in range(5):
        assert grammars._get_language("cobol") is None

    assert offline_pack == ["cobol"]


def test_the_grammar_dir_setting_points_the_pack_at_a_staged_cache(
    fresh_registry: None, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import tree_sitter_language_pack as pack

    configured: list[object] = []
    monkeypatch.setattr(pack, "configure", configured.append)
    monkeypatch.setattr(pack, "get_language", lambda name: (_ for _ in ()).throw(OSError()))
    monkeypatch.setenv(grammars.GRAMMAR_DIR_ENV, str(tmp_path))

    grammars._get_language("cobol")

    assert len(configured) == 1
    assert configured[0].cache_dir == str(tmp_path)


def test_the_ingestion_preflight_names_the_language_and_file_count(
    fresh_registry: None, offline_pack: list[str]
) -> None:
    from repowise.core.pipeline.phases.ingestion import _report_missing_grammars

    class _Stats:
        lang_counts: ClassVar[dict] = {"cobol": 106, "python": 3}

    class _Progress:
        def __init__(self) -> None:
            self.messages: list[tuple[str, str]] = []

        def on_message(self, level: str, text: str) -> None:
            self.messages.append((level, text))

    progress = _Progress()
    _report_missing_grammars(_Stats(), progress)

    assert len(progress.messages) == 1
    level, text = progress.messages[0]
    assert level == "warning"
    assert "COBOL grammar unavailable: 106 files indexed without symbols" in text
    assert grammars.GRAMMAR_DIR_ENV in text


def test_doctor_reports_an_uncached_grammar_without_downloading_it(
    tmp_path, monkeypatch: pytest.MonkeyPatch, offline_pack: list[str]
) -> None:
    from repowise.cli.commands.doctor_cmd._checks import core

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "PAYROLL.cbl").write_text("       IDENTIFICATION DIVISION.\n")
    (tmp_path / "app.py").write_text("")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    monkeypatch.setattr(grammars, "runtime_grammar_cache", lambda tag: (False, "/cache"))

    rows = core._runtime_grammar_rows(tmp_path)

    assert [(r.name, r.ok) for r in rows] == [("COBOL grammar", False)]
    assert "1 files would index without symbols" in rows[0].detail
    assert offline_pack == []


def test_doctor_counts_untracked_files_ingestion_would_index(
    tmp_path, monkeypatch: pytest.MonkeyPatch, offline_pack: list[str]
) -> None:
    from repowise.cli.commands.doctor_cmd._checks import core

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("build/\n")
    (tmp_path / "PAYROLL.cbl").write_text("       IDENTIFICATION DIVISION.\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "GEN.cbl").write_text("")
    monkeypatch.setattr(grammars, "runtime_grammar_cache", lambda tag: (False, "/cache"))

    rows = core._runtime_grammar_rows(tmp_path)

    assert [(r.name, r.ok) for r in rows] == [("COBOL grammar", False)]
    assert "1 files would index without symbols" in rows[0].detail
