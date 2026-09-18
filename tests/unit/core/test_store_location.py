"""Global store mode: one resolution point for where a repo's index lives.

Issue #1551 asks for a mode where repowise indexes a checkout without editing
it, with the index under ``$HOME/.repowise`` instead of a ``.repowise/``
directory inside the repository. These tests cover the resolution rules that
make that work, and the two facts the promise rests on: the entry is keyed on
the checkout's absolute path, and the switch is per run.

``REPOWISE_GLOBAL_STORE_ROOT`` is set in every test that turns the mode on, so
no test here can write into a real home directory.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from repowise.core.store_location import (
    GLOBAL_STORE_ROOT_ENV,
    GLOBAL_STORE_SWITCH_ENV,
    LOCAL_STORE_DIRNAME,
    global_store_dir,
    global_store_mode,
    global_store_root,
    repo_store_key,
    resolve_store_dir,
    store_is_repo_local,
    use_global_store,
)


@pytest.fixture(autouse=True)
def _no_ambient_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test from "the user set nothing", like a real fresh shell."""
    monkeypatch.delenv(GLOBAL_STORE_SWITCH_ENV, raising=False)
    monkeypatch.delenv(GLOBAL_STORE_ROOT_ENV, raising=False)


@pytest.fixture
def store_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A store root under the test's tmp_path, never the real ``~/.repowise``."""
    root = tmp_path / "global-store"
    monkeypatch.setenv(GLOBAL_STORE_ROOT_ENV, str(root))
    return root


class TestGlobalStoreMode:
    """The switch as three states, because "unset" is not "off"."""

    def test_unset_is_auto(self) -> None:
        assert global_store_mode() == "auto"

    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on", " 1 "])
    def test_on_spellings(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        monkeypatch.setenv(GLOBAL_STORE_SWITCH_ENV, raw)
        assert global_store_mode() == "on"

    @pytest.mark.parametrize("raw", ["0", "false", "no", "NO"])
    def test_off_spellings(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        monkeypatch.setenv(GLOBAL_STORE_SWITCH_ENV, raw)
        assert global_store_mode() == "off"

    def test_anything_else_is_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A typo must not silently enable a mode that moves the index."""
        monkeypatch.setenv(GLOBAL_STORE_SWITCH_ENV, "maybe")
        assert global_store_mode() == "auto"


class TestStoreRoot:
    def test_defaults_to_home_repowise_repos(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home/example")))
        assert global_store_root() == Path("/home/example/.repowise/repos")

    def test_override_relocates_the_root(self, store_root: Path) -> None:
        """The hook tests use to stay out of a real home directory."""
        assert global_store_root() == store_root


class TestRepoStoreKey:
    """The key is what makes two checkouts two entries and one checkout one."""

    def test_same_checkout_key_is_stable(self, tmp_path: Path) -> None:
        repo = tmp_path / "checkout"
        repo.mkdir()
        assert repo_store_key(repo) == repo_store_key(repo)

    def test_same_checkout_from_another_cwd_is_the_same_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The key never depends on the process working directory.

        This is the fact the issue is really about: ``repowise status`` run from
        anywhere must find the index ``repowise init`` wrote.
        """
        repo = tmp_path / "checkout"
        (repo / "pkg").mkdir(parents=True)
        other = tmp_path / "elsewhere"
        other.mkdir()

        monkeypatch.chdir(other)
        from_elsewhere = repo_store_key(repo)
        monkeypatch.chdir(repo)
        from_inside = repo_store_key(repo)
        monkeypatch.chdir(Path.home())

        assert from_elsewhere == from_inside == repo_store_key(repo)

    def test_two_checkouts_get_two_keys(self, tmp_path: Path) -> None:
        first = tmp_path / "one" / "checkout"
        second = tmp_path / "two" / "checkout"
        first.mkdir(parents=True)
        second.mkdir(parents=True)

        assert first.name == second.name, "same basename: the digest must still differ"
        assert repo_store_key(first) != repo_store_key(second)

    def test_relative_and_absolute_paths_agree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "checkout"
        repo.mkdir()
        monkeypatch.chdir(repo)
        assert repo_store_key(".") == repo_store_key(repo)

    def test_key_is_a_legible_slug_plus_digest(self, tmp_path: Path) -> None:
        """``ls ~/.repowise/repos`` has to be readable, so the name is kept."""
        repo = tmp_path / "my-project"
        repo.mkdir()
        key = repo_store_key(repo)
        assert key.startswith("my-project-")
        assert len(key) < 80, "artifact paths have OS length limits to respect"

    def test_hostile_basename_is_slugged(self, tmp_path: Path) -> None:
        """A checkout named ``..\\weird dir`` must not escape the store root."""
        repo = tmp_path / "..weird dir"
        repo.mkdir()
        key = repo_store_key(repo)
        assert "/" not in key
        assert "\\" not in key
        assert " " not in key


class TestResolveStoreDir:
    """The one function every layer asks, so the answer cannot be half a mode."""

    def test_switch_on_is_the_global_entry(self, tmp_path: Path, store_root: Path) -> None:
        repo = tmp_path / "checkout"
        repo.mkdir()
        with use_global_store(True):
            assert resolve_store_dir(repo) == store_root / repo_store_key(repo)

    def test_switch_on_does_not_require_the_entry_to_exist(
        self, tmp_path: Path, store_root: Path
    ) -> None:
        """``init`` resolves the store before anything has created it."""
        repo = tmp_path / "checkout"
        repo.mkdir()
        with use_global_store(True):
            resolved = resolve_store_dir(repo)
        assert not resolved.exists()
        assert resolved.is_relative_to(store_root)

    def test_switch_off_is_repo_local(self, tmp_path: Path, store_root: Path) -> None:
        repo = tmp_path / "checkout"
        repo.mkdir()
        with use_global_store(False):
            assert resolve_store_dir(repo) == repo / LOCAL_STORE_DIRNAME

    def test_switch_off_ignores_an_existing_global_entry(
        self, tmp_path: Path, store_root: Path
    ) -> None:
        """Explicit off means off: no auto-detection, even with an entry present."""
        repo = tmp_path / "checkout"
        repo.mkdir()
        global_store_dir(repo).mkdir(parents=True)

        with use_global_store(False):
            assert resolve_store_dir(repo) == repo / LOCAL_STORE_DIRNAME

    def test_unset_prefers_an_existing_repo_local_store(
        self, tmp_path: Path, store_root: Path
    ) -> None:
        """Unchanged behaviour for every repository that already has one."""
        repo = tmp_path / "checkout"
        (repo / LOCAL_STORE_DIRNAME).mkdir(parents=True)
        assert resolve_store_dir(repo) == repo / LOCAL_STORE_DIRNAME

    def test_unset_follows_an_existing_global_entry(
        self, tmp_path: Path, store_root: Path
    ) -> None:
        """A plain ``update`` after a global ``init`` finds the index.

        Without this the follow-on command would create the ``.repowise/`` the
        user asked not to have, on the very next run.
        """
        repo = tmp_path / "checkout"
        repo.mkdir()
        entry = global_store_dir(repo)
        entry.mkdir(parents=True)

        assert resolve_store_dir(repo) == entry
        assert not (repo / LOCAL_STORE_DIRNAME).exists()

    def test_unset_with_nothing_indexed_stays_repo_local(
        self, tmp_path: Path, store_root: Path
    ) -> None:
        repo = tmp_path / "checkout"
        repo.mkdir()
        assert resolve_store_dir(repo) == repo / LOCAL_STORE_DIRNAME

    def test_repo_local_wins_over_a_stale_global_entry(
        self, tmp_path: Path, store_root: Path
    ) -> None:
        """A repo the user later indexed normally keeps using its own store."""
        repo = tmp_path / "checkout"
        (repo / LOCAL_STORE_DIRNAME).mkdir(parents=True)
        global_store_dir(repo).mkdir(parents=True)

        assert resolve_store_dir(repo) == repo / LOCAL_STORE_DIRNAME

    def test_switch_reads_the_environment_not_a_config_file(
        self, tmp_path: Path, store_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The switch cannot live in the store it is choosing.

        ``config.yaml`` sits inside the directory this function returns, so a
        config-file switch would have to be read from a location it decides.
        """
        repo = tmp_path / "checkout"
        repo.mkdir()
        monkeypatch.setenv(GLOBAL_STORE_SWITCH_ENV, "1")
        assert resolve_store_dir(repo) == store_root / repo_store_key(repo)


class TestStoreIsRepoLocal:
    """Callers that would edit the working tree ask this first."""

    def test_true_by_default(self, tmp_path: Path, store_root: Path) -> None:
        repo = tmp_path / "checkout"
        repo.mkdir()
        assert store_is_repo_local(repo) is True

    def test_false_under_the_switch(self, tmp_path: Path, store_root: Path) -> None:
        repo = tmp_path / "checkout"
        repo.mkdir()
        with use_global_store(True):
            assert store_is_repo_local(repo) is False

    def test_false_when_the_entry_exists_and_the_switch_is_unset(
        self, tmp_path: Path, store_root: Path
    ) -> None:
        repo = tmp_path / "checkout"
        repo.mkdir()
        global_store_dir(repo).mkdir(parents=True)
        assert store_is_repo_local(repo) is False


class TestUseGlobalStore:
    """The switch is per run: the CLI runs many commands per interpreter."""

    def test_sets_and_restores(self, tmp_path: Path, store_root: Path) -> None:
        repo = tmp_path / "checkout"
        repo.mkdir()
        assert resolve_store_dir(repo) == repo / LOCAL_STORE_DIRNAME

        with use_global_store(True):
            assert resolve_store_dir(repo) != repo / LOCAL_STORE_DIRNAME

        assert resolve_store_dir(repo) == repo / LOCAL_STORE_DIRNAME

    def test_restores_a_preexisting_value(
        self, tmp_path: Path, store_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(GLOBAL_STORE_SWITCH_ENV, "0")
        with use_global_store(True):
            assert global_store_mode() == "on"
        assert global_store_mode() == "off"

    def test_restores_after_an_exception(self, tmp_path: Path, store_root: Path) -> None:
        """Restoration lives in a ``finally``: a failed run must not move stores."""
        repo = tmp_path / "checkout"
        repo.mkdir()
        with pytest.raises(RuntimeError, match="boom"), use_global_store(True):
            raise RuntimeError("boom")
        assert resolve_store_dir(repo) == repo / LOCAL_STORE_DIRNAME

    def test_explicit_off_wins_over_the_environment(
        self, tmp_path: Path, store_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(GLOBAL_STORE_SWITCH_ENV, "1")
        with use_global_store(False):
            assert global_store_mode() == "off"

    def test_root_override_is_also_restored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(GLOBAL_STORE_ROOT_ENV, raising=False)
        elsewhere = tmp_path / "elsewhere"
        with use_global_store(True, root=elsewhere):
            assert global_store_root() == elsewhere
        assert GLOBAL_STORE_ROOT_ENV not in os.environ
