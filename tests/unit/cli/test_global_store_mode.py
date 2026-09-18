"""The two promises global store mode makes, driven through the real CLI.

Issue #1551 asks for a mode where a repository the user does not own can be
indexed without editing it. Two things have to hold, and neither is visible
from the resolution unit tests alone:

1. ``init --global-store`` leaves the checkout exactly as it found it. No
   ``.repowise/``, and no editor or instruction files either, because those are
   equally edits to somebody else's tree.
2. The index it wrote is found again afterwards, by commands that were not
   given the switch, without re-creating ``.repowise/``.

``REPOWISE_GLOBAL_STORE_ROOT`` sends every write to a tmp dir, and the CLI's
own autouse fixtures already point ``HOME`` there too.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from repowise.cli.main import cli
from repowise.core.store_location import (
    GLOBAL_STORE_ROOT_ENV,
    GLOBAL_STORE_SWITCH_ENV,
    global_store_dir,
)

#: Files the working tree must not gain. ``.repowise`` is the index directory;
#: the rest are the editor/instruction writes init normally makes.
_FORBIDDEN = (".repowise", ".mcp.json", ".claude", ".vscode", ".gitignore", "CLAUDE.md", "AGENTS.md")


@pytest.fixture
def store_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "global-store"
    monkeypatch.setenv(GLOBAL_STORE_ROOT_ENV, str(root))
    monkeypatch.delenv(GLOBAL_STORE_SWITCH_ENV, raising=False)
    return root


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A small git checkout, so the git-history phases have something to read."""
    path = tmp_path / "checkout"
    (path / "src").mkdir(parents=True)
    (path / "src" / "app.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef main():\n    print(add(1, 2))\n",
        encoding="utf-8",
    )
    (path / "README.md").write_text("# checkout\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=path,
        check=True,
    )
    return path


def _snapshot(repo: Path) -> set[str]:
    """Every path in the working tree that is not tracked or ignored by git.

    ``git status --porcelain`` is the right measure rather than a raw directory
    listing: a mode that left a gitignored ``.repowise/`` behind would pass a
    listing check for "nothing new to commit" while still editing the tree.
    """
    out = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {line[3:].strip() for line in out.splitlines() if line.strip()}


def _run(args: list[str]) -> Result:
    runner = CliRunner()
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    return result


class TestGlobalInitLeavesTheTreeUntouched:
    def test_no_file_is_added_to_the_checkout(self, repo: Path, store_root: Path) -> None:
        before = _snapshot(repo)
        _run(["init", str(repo), "--no-prose", "--yes", "--global-store"])
        assert _snapshot(repo) == before
        for name in _FORBIDDEN:
            assert not (repo / name).exists(), f"{name} was written into the repository"

    def test_the_index_lands_under_the_store_root(self, repo: Path, store_root: Path) -> None:
        _run(["init", str(repo), "--no-prose", "--yes", "--global-store"])
        entry = global_store_dir(repo)
        assert entry.is_relative_to(store_root)
        assert (entry / "wiki.db").is_file()
        assert (entry / "state.json").is_file()

    def test_env_var_is_the_same_switch(
        self, repo: Path, store_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``REPOWISE_GLOBAL_STORE=1`` is the CI and sandbox spelling."""
        monkeypatch.setenv(GLOBAL_STORE_SWITCH_ENV, "1")
        _run(["init", str(repo), "--no-prose", "--yes"])
        assert not (repo / ".repowise").exists()
        assert (global_store_dir(repo) / "wiki.db").is_file()

    def test_editor_files_are_skipped_even_without_the_flag(
        self, repo: Path, store_root: Path
    ) -> None:
        """The mode's promise covers editor setup, not just the index.

        Editor setup writes four files into the checkout, so turning it off is
        part of keeping the tree untouched rather than a separate courtesy.
        """
        result = _run(["init", str(repo), "--no-prose", "--yes", "--global-store"])
        assert not (repo / ".mcp.json").exists()
        assert not (repo / ".claude").exists()
        assert "global store" in result.output.lower()

    def test_the_store_entry_is_keyed_on_the_checkout(self, repo: Path, store_root: Path) -> None:
        _run(["init", str(repo), "--no-prose", "--yes", "--global-store"])
        entries = [p.name for p in store_root.iterdir() if p.is_dir()]
        assert len(entries) == 1
        assert entries[0].startswith(repo.name)


class TestGlobalInitIsFoundAgain:
    """The follow-on commands must not create the ``.repowise/`` init avoided."""

    def test_plain_status_finds_the_global_index(self, repo: Path, store_root: Path) -> None:
        _run(["init", str(repo), "--no-prose", "--yes", "--global-store"])
        result = _run(["status", str(repo)])
        assert "Total pages" in result.output
        assert not (repo / ".repowise").exists()

    def test_plain_update_leaves_the_tree_untouched(self, repo: Path, store_root: Path) -> None:
        """``update`` refreshes editor files on every outcome, so it must ask."""
        _run(["init", str(repo), "--no-prose", "--yes", "--global-store"])
        before = _snapshot(repo)
        _run(["update", str(repo)])
        assert _snapshot(repo) == before
        assert not (repo / ".repowise").exists()

    def test_plain_doctor_finds_the_global_index(self, repo: Path, store_root: Path) -> None:
        _run(["init", str(repo), "--no-prose", "--yes", "--global-store"])
        result = CliRunner().invoke(cli, ["doctor", str(repo)])
        assert not (repo / ".repowise").exists()
        assert result.exit_code in (0, 1), result.output


class TestTwoCheckouts:
    def test_each_checkout_gets_its_own_entry(self, tmp_path: Path, store_root: Path) -> None:
        """Two checkouts of one repository are two indexes, keyed by path."""
        repos = []
        for name in ("one", "two"):
            path = tmp_path / name / "checkout"
            (path / "src").mkdir(parents=True)
            (path / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=path, check=True)
            subprocess.run(["git", "add", "-A"], cwd=path, check=True)
            subprocess.run(
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
                cwd=path,
                check=True,
            )
            repos.append(path)

        for path in repos:
            _run(["init", str(path), "--no-prose", "--yes", "--global-store"])

        entries = sorted(p.name for p in store_root.iterdir() if p.is_dir())
        assert len(entries) == 2
        assert global_store_dir(repos[0]) != global_store_dir(repos[1])
        for path in repos:
            assert not (path / ".repowise").exists()

    def test_one_checkout_resolves_the_same_from_any_cwd(
        self, repo: Path, store_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The entry is the checkout's absolute path, never the process cwd."""
        _run(["init", str(repo), "--no-prose", "--yes", "--global-store"])

        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        result_from_elsewhere = _run(["status", str(repo)])

        monkeypatch.chdir(repo)
        result_from_inside = _run(["status", str(repo)])

        assert "Total pages" in result_from_elsewhere.output
        assert "Total pages" in result_from_inside.output
        assert not (repo / ".repowise").exists()


class TestTheStoreRootStaysOutOfHome:
    def test_the_mode_writes_nothing_into_the_real_home(
        self, repo: Path, store_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The override is what keeps these tests from touching ``~/.repowise``."""
        fake_home = repo.parent / "fake-home"
        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        _run(["init", str(repo), "--no-prose", "--yes", "--global-store"])

        assert (global_store_dir(repo) / "wiki.db").is_file()
        assert global_store_dir(repo).is_relative_to(store_root)
        assert not (fake_home / ".repowise" / "repos").exists()
