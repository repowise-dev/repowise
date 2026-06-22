"""Tests for the CLI release-news presenter."""

from __future__ import annotations

from rich.console import Console

import repowise.cli.whats_new as wn
from repowise.cli.update_check import UpdateCheck
from repowise.core.upgrade.changelog import parse_changelog

SAMPLE = """## [0.21.0] - 2026-06-19
### Added
- New thing.

## [0.20.0] - 2026-06-16
### Fixed
- Old fix.
"""


def _redirect_global_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(wn, "user_global_dir", lambda: tmp_path)


def test_find_changelog_honours_env_override(monkeypatch, tmp_path):
    cl = tmp_path / "MY_CHANGELOG.md"
    cl.write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setenv("REPOWISE_CHANGELOG", str(cl))
    assert wn.find_changelog_path() == cl


def test_find_changelog_ignores_cwd(monkeypatch, tmp_path):
    """A foreign docs/CHANGELOG.md in the cwd must not shadow repowise's own."""
    monkeypatch.delenv("REPOWISE_CHANGELOG", raising=False)
    foreign = tmp_path / "docs" / "CHANGELOG.md"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("## [99.0.0] - x\n### Added\n- not repowise\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    found = wn.find_changelog_path()
    # Either the real source changelog or the bundled copy, never the cwd one.
    assert found != foreign
    assert found is not None


def test_load_changelog_entries_from_override(monkeypatch, tmp_path):
    cl = tmp_path / "CHANGELOG.md"
    cl.write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setenv("REPOWISE_CHANGELOG", str(cl))
    entries = wn.load_changelog_entries()
    assert [e.version for e in entries] == ["0.21.0", "0.20.0"]


def test_last_seen_roundtrip(monkeypatch, tmp_path):
    _redirect_global_dir(monkeypatch, tmp_path)
    assert wn.read_last_seen_version() is None
    wn.write_last_seen_version("0.21.0")
    assert wn.read_last_seen_version() == "0.21.0"


def test_render_whats_new_returns_false_when_nothing_new():
    import io

    entries = parse_changelog(SAMPLE)
    console = Console(file=io.StringIO(), width=100)
    assert wn.render_whats_new(console, entries, since_version="0.21.0") is False


def test_render_whats_new_renders_new_entries():
    entries = parse_changelog(SAMPLE)
    import io

    buf = io.StringIO()
    console = Console(file=buf, width=100)
    rendered = wn.render_whats_new(entries=entries, console=console, since_version="0.20.0")
    assert rendered is True
    assert "0.21.0" in buf.getvalue()
    assert "New thing" in buf.getvalue()


def test_render_whats_new_empty_entries_shows_fallback_link():
    import io

    buf = io.StringIO()
    console = Console(file=buf, width=100)
    assert wn.render_whats_new(console, [], since_version=None) is False
    assert wn.RELEASES_URL in buf.getvalue()


def _check(update_available, latest="9.9.9"):
    return UpdateCheck(
        current_version="0.21.0",
        latest_version=latest,
        resolved_executable=None,
        running_executable="repowise",
        python="python",
        update_available=update_available,
        suggested_command="pipx upgrade repowise",
        install_hint="pipx",
    )


def test_advisory_prints_when_update_available():
    import io

    buf = io.StringIO()
    console = Console(file=buf, width=100)
    assert wn.render_update_advisory(console, _check(True)) is True
    assert "9.9.9" in buf.getvalue()
    assert "pipx upgrade repowise" in buf.getvalue()


def test_advisory_silent_when_up_to_date():
    import io

    buf = io.StringIO()
    console = Console(file=buf, width=100)
    assert wn.render_update_advisory(console, _check(False)) is False
    assert buf.getvalue() == ""


def test_advisory_silent_when_latest_unknown():
    import io

    buf = io.StringIO()
    console = Console(file=buf, width=100)
    assert wn.render_update_advisory(console, _check(None, latest=None)) is False
