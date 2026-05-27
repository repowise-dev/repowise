"""Façade-parity + characterization tests for the split ``cli/ui`` package.

Guards the extraction of the former ``ui.py`` monolith into the ``ui/`` package:
the façade must still expose every public name, and the decomposed
``interactive_advanced_config`` must return the same dict shape it always did.
"""

from __future__ import annotations

import pytest

import repowise.cli.ui as ui
from repowise.cli.ui import mode_selection
from repowise.cli.ui.repo_scanner import RepoScanInfo


def test_facade_reexports_public_surface() -> None:
    expected = {
        "BRAND",
        "BRAND_STYLE",
        "MaybeCountColumn",
        "RichProgressCallback",
        "RepoScanInfo",
        "load_dotenv",
        "interactive_provider_select",
        "interactive_advanced_config",
        "interactive_mode_select",
        "interactive_fast_mode_offer",
        "interactive_repo_select",
        "interactive_primary_select",
        "should_offer_fast_mode",
        "print_banner",
        "print_phase_header",
        "print_scan_summary",
        "print_index_only_intro",
        "quick_repo_scan",
        "build_analysis_summary_panel",
        "build_completion_panel",
        "build_contextual_next_steps",
        "format_elapsed",
        "LARGE_REPO_FILE_THRESHOLD",
    }
    missing = {name for name in expected if not hasattr(ui, name)}
    assert not missing


def test_format_elapsed_unchanged() -> None:
    assert ui.format_elapsed(5.0) == "5.0s"
    assert ui.format_elapsed(125.0) == "2m 5s"


def test_should_offer_fast_mode_threshold() -> None:
    assert ui.should_offer_fast_mode(None) is False
    assert ui.should_offer_fast_mode(RepoScanInfo(total_files=100)) is False
    big = RepoScanInfo(total_files=ui.LARGE_REPO_FILE_THRESHOLD + 1)
    assert ui.should_offer_fast_mode(big) is True


def _drive_advanced_config(
    monkeypatch: pytest.MonkeyPatch,
    *,
    scan: RepoScanInfo | None,
    allow_fast: bool,
) -> dict:
    """Run interactive_advanced_config with all prompts answered by their defaults."""

    # click.confirm -> always its `default`; click.prompt -> always its `default`.
    monkeypatch.setattr(mode_selection.click, "confirm", lambda *a, **k: k.get("default", False))

    # First positional arg of prompt is the empty-exclude case which returns ""
    # to break the loop; all others return their `default`.
    def _prompt(text, *a, **k):
        return k.get("default", "")

    monkeypatch.setattr(mode_selection.click, "prompt", _prompt)

    class _NullConsole:
        def print(self, *a, **k) -> None:
            pass

    return mode_selection.interactive_advanced_config(
        _NullConsole(), scan=scan, allow_fast=allow_fast
    )


def test_advanced_config_default_keys_no_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _drive_advanced_config(monkeypatch, scan=None, allow_fast=False)
    assert result == {
        "skip_tests": False,
        "skip_infra": False,
        "include_submodules": False,
        "run_mode": "standard",
        "exclude": (),
        "commit_limit": 500,
        "follow_renames": False,
        "concurrency": 5,
        "reasoning": "auto",
        "embedder": "mock",
        "test_run": False,
        "tier1_top_n": None,
    }


def test_advanced_config_allow_fast_small_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    scan = RepoScanInfo(total_files=100)
    result = _drive_advanced_config(monkeypatch, scan=scan, allow_fast=True)
    # Small repo: run mode defaults to standard, tier cap default 0 -> None.
    assert result["run_mode"] == "standard"
    assert result["tier1_top_n"] is None
    assert result["commit_limit"] == 1000  # <500 files -> deeper history default
    assert result["concurrency"] == 8  # <200 files -> higher concurrency default
