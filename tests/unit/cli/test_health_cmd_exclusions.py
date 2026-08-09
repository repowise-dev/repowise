"""``repowise health`` must analyze the file set the index analyzed.

It persists into the same ``health_file_metrics`` rows the indexer writes, so
the two have to agree on which files exist. It built its traverser with the
submodule and nested-repo flags from ``state.json`` but *not* the config's
``exclude_patterns``, so on a repo with exclusions it scored — and overwrote
rows for — files the index had deliberately dropped. On a repo excluding a
directory that holds a package manifest it could also write a different
``module`` than the index did, which is the "whichever path last wrote the row"
defect module attribution exists to end.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from repowise.cli.commands.health_cmd import command as health_cmd
from repowise.cli.main import cli


class _StopHere(RuntimeError):
    """Raised once the traverser has been constructed — nothing after it is
    under test, and everything after it wants a real index."""


def _traverser_spy(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Capture FileTraverser's kwargs, then abort the command."""
    import repowise.core.ingestion as ingestion

    seen: dict = {}

    class _Recorder:
        def __init__(self, repo_path, **kwargs):
            seen.update(kwargs)
            raise _StopHere

    monkeypatch.setattr(ingestion, "FileTraverser", _Recorder)
    return seen


def _invoke(monkeypatch: pytest.MonkeyPatch, tmp_path, config: dict) -> dict:
    seen = _traverser_spy(monkeypatch)
    monkeypatch.setattr(health_cmd, "load_state", lambda _p: {})
    monkeypatch.setattr(health_cmd, "load_config", lambda _p: config)

    result = CliRunner().invoke(cli, ["health", str(tmp_path)])

    assert isinstance(result.exception, _StopHere), result.output
    return seen


def test_the_config_exclusions_reach_the_traverser(monkeypatch, tmp_path) -> None:
    seen = _invoke(monkeypatch, tmp_path, {"exclude_patterns": ["vendor/**", "*.gen.ts"]})
    assert seen["extra_exclude_patterns"] == ["vendor/**", "*.gen.ts"]


def test_no_exclusions_configured_passes_none(monkeypatch, tmp_path) -> None:
    """``None`` is the traverser's documented "nothing to add" value; an empty
    list would be equivalent but reads as "exclusions were configured"."""
    seen = _invoke(monkeypatch, tmp_path, {})
    assert seen["extra_exclude_patterns"] is None


def test_the_state_flags_still_reach_it(monkeypatch, tmp_path) -> None:
    """Guard against the exclusion fix displacing what was already passed."""
    seen = _traverser_spy(monkeypatch)
    monkeypatch.setattr(
        health_cmd,
        "load_state",
        lambda _p: {"include_submodules": True, "include_nested_repos": True},
    )
    monkeypatch.setattr(health_cmd, "load_config", lambda _p: {"exclude_patterns": ["x/**"]})

    result = CliRunner().invoke(cli, ["health", str(tmp_path)])

    assert isinstance(result.exception, _StopHere), result.output
    assert seen == {
        "include_submodules": True,
        "include_nested_repos": True,
        "extra_exclude_patterns": ["x/**"],
    }
