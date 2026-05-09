"""Tests for the post-commit hook installer's legacy-block migration.

Pre-marker repowise versions wrote a hook body without start/end markers
that ended in `exit 0`, which made any later marker-based block dead code.
On Windows the legacy invocation also tended to fail every commit because
`uv run repowise update` would rebuild the venv from scratch. The
installer must detect those bodies and excise them before appending the
new marker block.
"""

from __future__ import annotations

import subprocess

import pytest

from repowise.cli.hooks import install, status, _strip_legacy_block


@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


_LEGACY_BODY = """\
#!/bin/sh
# post-commit hook: auto-update repowise wiki after each commit
# Runs in the background so it doesn't block your terminal

echo "[repowise] Triggering incremental wiki update..."

(
  cd "$(git rev-parse --show-toplevel)" || exit 1
  powershell.exe -Command "uv run repowise update" > /tmp/repowise-update.log 2>&1
) &

exit 0
"""


def test_strip_legacy_block_removes_only_legacy_section():
    after_block = "# my own custom hook\necho 'kept'\n"
    content = _LEGACY_BODY + "\n" + after_block

    cleaned, stripped = _strip_legacy_block(content)
    assert stripped is True
    assert "Triggering incremental wiki update" not in cleaned
    assert "/tmp/repowise-update.log" not in cleaned
    assert "echo 'kept'" in cleaned


def test_strip_legacy_block_noop_for_clean_content():
    content = "#!/bin/sh\necho 'fine'\n"
    cleaned, stripped = _strip_legacy_block(content)
    assert stripped is False
    assert cleaned == content


class TestInstallMigratesLegacyHook:
    def test_install_over_legacy_replaces_with_marker_block(self, git_repo):
        hook = git_repo / ".git" / "hooks" / "post-commit"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(_LEGACY_BODY, encoding="utf-8")

        result = install(git_repo)
        assert result == "installed"

        content = hook.read_text(encoding="utf-8")
        assert "# repowise-hook-start" in content
        assert "# repowise-hook-end" in content
        # Legacy fingerprints must be gone
        assert "Triggering incremental wiki update" not in content
        assert "/tmp/repowise-update.log" not in content
        # And there must be no leftover `exit 0` that would shadow the new
        # marker block.
        assert "\nexit 0\n" not in content

    def test_install_over_legacy_plus_marker_returns_migrated(self, git_repo):
        # Existing file already has both: legacy block (unreachable) AND
        # a marker block. Installer should strip the legacy half and
        # report the migration.
        from repowise.cli.hooks import _HOOK_SCRIPT

        hook = git_repo / ".git" / "hooks" / "post-commit"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(
            _LEGACY_BODY + "\n" + _HOOK_SCRIPT, encoding="utf-8"
        )

        result = install(git_repo)
        assert result == "migrated legacy hook"

        content = hook.read_text(encoding="utf-8")
        assert "Triggering incremental wiki update" not in content
        assert "# repowise-hook-start" in content

    def test_install_already_marker_only_is_idempotent(self, git_repo):
        install(git_repo)  # First install — fresh
        result = install(git_repo)  # Second install — should be a no-op
        assert result == "already installed"
        assert status(git_repo) == "installed"

    def test_install_preserves_unrelated_hook_content(self, git_repo):
        hook = git_repo / ".git" / "hooks" / "post-commit"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(
            "#!/bin/sh\n# my project's lint hook\necho 'lint'\n",
            encoding="utf-8",
        )

        install(git_repo)

        content = hook.read_text(encoding="utf-8")
        assert "echo 'lint'" in content
        assert "# repowise-hook-start" in content
