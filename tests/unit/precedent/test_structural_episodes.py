"""Unit tests for structural episode derivation.

Silence is the behaviour under test as much as the facts are: a repo where
none of these hold must produce an empty store, and a check that cannot answer
must produce nothing rather than a partial answer.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from repowise.core.ingestion.traverser import FileTraverser
from repowise.core.precedent import EpisodeStore
from repowise.core.precedent.structural import (
    FREE_KINDS,
    KIND_CONFIG_OVERRIDE,
    KIND_EDITABLE_SHADOW,
    KIND_FORMATTER_DRIFT,
    KIND_NESTED_REPOS,
    derive_structural_episodes,
    record_structural_episodes,
)


def _repo(tmp_path: Path) -> Path:
    (tmp_path / ".repowise").mkdir()
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "app.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path


def _traverser(tmp_path: Path) -> FileTraverser:
    traverser = FileTraverser(tmp_path)
    list(traverser._walk())  # the derivation reads what the walk left behind
    return traverser


def _kinds(episodes: list) -> set[str]:
    return {ep.kind for ep in episodes}


class TestSilence:
    def test_plain_repo_emits_nothing(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        episodes = derive_structural_episodes(
            repo, _traverser(repo), allow_formatter_check=False
        )
        assert episodes == []

    def test_plain_repo_leaves_an_empty_store(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        record_structural_episodes(repo, _traverser(repo), allow_formatter_check=False)
        with EpisodeStore.open_for_repo(repo) as store:
            assert store.count() == 0

    def test_uninitialised_repo_is_never_given_a_repowise_dir(self, tmp_path: Path) -> None:
        (tmp_path / "pkg").mkdir()
        written = record_structural_episodes(
            tmp_path, _traverser(tmp_path), allow_formatter_check=False
        )
        assert written == 0
        assert not (tmp_path / ".repowise").exists()


class TestNestedRepos:
    def test_names_the_separate_checkouts(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        for name in ("backend", "frontend"):
            (repo / name / ".git").mkdir(parents=True)
        episodes = derive_structural_episodes(
            repo, _traverser(repo), allow_formatter_check=False
        )
        [nested] = [ep for ep in episodes if ep.kind == KIND_NESTED_REPOS]
        assert set(nested.nodes) == {"backend", "frontend"}
        assert "backend" in nested.body and "frontend" in nested.body

    def test_git_file_form_counts(self, tmp_path: Path) -> None:
        """A repo whose gitdir lives elsewhere is still an independent repo."""
        repo = _repo(tmp_path)
        (repo / "linked").mkdir()
        (repo / "linked" / ".git").write_text(
            "gitdir: C:/elsewhere/other-project/.git", encoding="utf-8"
        )
        episodes = derive_structural_episodes(
            repo, _traverser(repo), allow_formatter_check=False
        )
        assert KIND_NESTED_REPOS in _kinds(episodes)

    def test_linked_worktree_is_not_a_separate_repo(self, tmp_path: Path) -> None:
        """A worktree of THIS repo is the same repository, not an independent one."""
        repo = _repo(tmp_path)
        (repo / "wt-feature").mkdir()
        (repo / "wt-feature" / ".git").write_text(
            "gitdir: C:/repo/.git/worktrees/wt-feature\n", encoding="utf-8"
        )
        episodes = derive_structural_episodes(
            repo, _traverser(repo), allow_formatter_check=False
        )
        assert KIND_NESTED_REPOS not in _kinds(episodes)

    def test_nested_submodule_is_not_a_separate_repo(self, tmp_path: Path) -> None:
        """A submodule declared in a nested .gitmodules is absent from the root one."""
        repo = _repo(tmp_path)
        (repo / "vendored").mkdir()
        (repo / "vendored" / ".git").write_text(
            "gitdir: ../.git/modules/outer/modules/vendored\n", encoding="utf-8"
        )
        episodes = derive_structural_episodes(
            repo, _traverser(repo), allow_formatter_check=False
        )
        assert KIND_NESTED_REPOS not in _kinds(episodes)

    def test_declared_submodule_is_not_a_separate_repo(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        (repo / "vendored").mkdir()
        (repo / "vendored" / ".git").write_text("gitdir: ../.git/modules/vendored", "utf-8")
        (repo / ".gitmodules").write_text(
            '[submodule "vendored"]\n\tpath = vendored\n\turl = https://example.invalid/x\n',
            encoding="utf-8",
        )
        episodes = derive_structural_episodes(
            repo, _traverser(repo), allow_formatter_check=False
        )
        assert KIND_NESTED_REPOS not in _kinds(episodes)


class TestEditableShadow:
    def _venv_with_editable_install(self, repo: Path, *, script: str) -> None:
        (repo / "pyproject.toml").write_text(
            f'[project]\nname = "x"\nversion = "0"\n\n'
            f'[project.scripts]\n{script} = "x.cli:main"\n',
            encoding="utf-8",
        )
        site = repo / ".venv" / "Lib" / "site-packages"
        site.mkdir(parents=True)
        (site / "__editable__.x-0.pth").write_text("/src", encoding="utf-8")

    def test_launcher_beside_an_editable_pth(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        self._venv_with_editable_install(repo, script="mytool")
        scripts = repo / ".venv" / "Scripts"
        scripts.mkdir()
        (scripts / "mytool.exe").write_bytes(b"MZ")
        episodes = derive_structural_episodes(
            repo, _traverser(repo), allow_formatter_check=False
        )
        [shadow] = [ep for ep in episodes if ep.kind == KIND_EDITABLE_SHADOW]
        assert shadow.subject == "mytool"
        assert "__editable__.x-0.pth" in shadow.evidence

    def test_no_launcher_means_no_episode(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        self._venv_with_editable_install(repo, script="mytool")
        episodes = derive_structural_episodes(
            repo, _traverser(repo), allow_formatter_check=False
        )
        assert KIND_EDITABLE_SHADOW not in _kinds(episodes)

    def test_editable_install_of_another_distribution_means_no_episode(
        self, tmp_path: Path
    ) -> None:
        """An unrelated dependency installed -e does not shadow this checkout."""
        repo = _repo(tmp_path)
        (repo / "pyproject.toml").write_text(
            '[project]\nname = "my-tool"\nversion = "0"\n\n'
            '[project.scripts]\nmytool = "my_tool.cli:main"\n',
            encoding="utf-8",
        )
        site = repo / ".venv" / "Lib" / "site-packages"
        site.mkdir(parents=True)
        (site / "__editable__.some_dependency-2.1.pth").write_text("/elsewhere", encoding="utf-8")
        scripts = repo / ".venv" / "Scripts"
        scripts.mkdir()
        (scripts / "mytool.exe").write_bytes(b"MZ")
        episodes = derive_structural_episodes(
            repo, _traverser(repo), allow_formatter_check=False
        )
        assert KIND_EDITABLE_SHADOW not in _kinds(episodes)

    def test_distribution_spelling_does_not_matter(self, tmp_path: Path) -> None:
        """my-tool, my_tool and My.Tool are one distribution."""
        repo = _repo(tmp_path)
        (repo / "pyproject.toml").write_text(
            '[project]\nname = "My.Tool"\nversion = "0"\n\n'
            '[project.scripts]\nmytool = "my_tool.cli:main"\n',
            encoding="utf-8",
        )
        site = repo / ".venv" / "Lib" / "site-packages"
        site.mkdir(parents=True)
        (site / "__editable__.my_tool-0.1.pth").write_text("/src", encoding="utf-8")
        scripts = repo / ".venv" / "Scripts"
        scripts.mkdir()
        (scripts / "mytool.exe").write_bytes(b"MZ")
        episodes = derive_structural_episodes(
            repo, _traverser(repo), allow_formatter_check=False
        )
        assert KIND_EDITABLE_SHADOW in _kinds(episodes)

    def test_launcher_without_editable_install_means_no_episode(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        (repo / "pyproject.toml").write_text(
            '[project]\nname = "x"\nversion = "0"\n\n[project.scripts]\nmytool = "x.cli:main"\n',
            encoding="utf-8",
        )
        scripts = repo / ".venv" / "Scripts"
        scripts.mkdir(parents=True)
        (scripts / "mytool.exe").write_bytes(b"MZ")
        episodes = derive_structural_episodes(
            repo, _traverser(repo), allow_formatter_check=False
        )
        assert KIND_EDITABLE_SHADOW not in _kinds(episodes)


class TestConfigOverrides:
    def test_exclude_patterns_are_reported(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        (repo / ".repowise" / "config.yaml").write_text(
            "exclude_patterns:\n  - vendor/**\n", encoding="utf-8"
        )
        episodes = derive_structural_episodes(
            repo, _traverser(repo), allow_formatter_check=False
        )
        [cfg] = [ep for ep in episodes if ep.kind == KIND_CONFIG_OVERRIDE]
        assert cfg.subject == "exclude_patterns"
        assert "vendor/**" in cfg.evidence

    def test_disabled_switch_is_reported_once_per_block(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        (repo / ".repowise" / "config.yaml").write_text(
            "hooks:\n  read_skeleton: false\n  search_digest: false\n", encoding="utf-8"
        )
        episodes = derive_structural_episodes(
            repo, _traverser(repo), allow_formatter_check=False
        )
        [cfg] = [ep for ep in episodes if ep.kind == KIND_CONFIG_OVERRIDE]
        assert cfg.subject == "hooks"
        assert "read_skeleton" in cfg.body and "search_digest" in cfg.body

    def test_default_config_says_nothing(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        (repo / ".repowise" / "config.yaml").write_text(
            "provider: openai\nhooks:\n  read_skeleton: true\n", encoding="utf-8"
        )
        episodes = derive_structural_episodes(
            repo, _traverser(repo), allow_formatter_check=False
        )
        assert KIND_CONFIG_OVERRIDE not in _kinds(episodes)


class TestFormatterDrift:
    """The one check that costs a subprocess, and the one that must stay silent
    whenever it cannot answer."""

    def _declaring_repo(self, tmp_path: Path) -> Path:
        repo = _repo(tmp_path)
        (repo / "pyproject.toml").write_text(
            '[project]\nname = "x"\nversion = "0"\n\n[tool.ruff.format]\nquote-style = "double"\n',
            encoding="utf-8",
        )
        return repo

    def test_not_run_when_the_repo_declares_no_formatter(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        repo = _repo(tmp_path)
        monkeypatch.setattr(
            "repowise.core.precedent.structural._ruff_executable",
            lambda _root: (_ for _ in ()).throw(AssertionError("must not resolve")),
        )
        episodes = derive_structural_episodes(repo, _traverser(repo), allow_formatter_check=True)
        assert KIND_FORMATTER_DRIFT not in _kinds(episodes)

    def test_drift_is_reported_with_the_true_count(self, tmp_path: Path, monkeypatch) -> None:
        repo = self._declaring_repo(tmp_path)
        monkeypatch.setattr(
            "repowise.core.precedent.structural._ruff_executable", lambda _root: "ruff"
        )
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(
                returncode=1,
                stdout="Would reformat: a.py\nWould reformat: b.py\n2 files would be reformatted\n",
                stderr="",
            ),
        )
        episodes = derive_structural_episodes(repo, _traverser(repo), allow_formatter_check=True)
        [drift] = [ep for ep in episodes if ep.kind == KIND_FORMATTER_DRIFT]
        assert "2 files" in drift.body
        assert "ruff format --check ." in drift.evidence
        assert "2 files would be reformatted" in drift.evidence

    def test_a_repo_that_formats_with_something_else_is_silent(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Ruff-as-linter beside another formatter must not be read as ruff-as-formatter."""
        repo = _repo(tmp_path)
        (repo / "pyproject.toml").write_text(
            '[project]\nname = "x"\nversion = "0"\n\n'
            '[tool.ruff.lint]\nselect = ["E"]\n\n[tool.black]\nline-length = 88\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run ruff")),
        )
        episodes = derive_structural_episodes(repo, _traverser(repo), allow_formatter_check=True)
        assert KIND_FORMATTER_DRIFT not in _kinds(episodes)

    def test_ruff_as_linter_only_is_silent(self, tmp_path: Path, monkeypatch) -> None:
        """A pyproject mentioning ruff and the word format is not a declaration."""
        repo = _repo(tmp_path)
        (repo / "pyproject.toml").write_text(
            '[project]\nname = "x"\nversion = "0"\ndescription = "formats things"\n\n'
            '[tool.ruff.lint]\nselect = ["E"]\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run ruff")),
        )
        episodes = derive_structural_episodes(repo, _traverser(repo), allow_formatter_check=True)
        assert KIND_FORMATTER_DRIFT not in _kinds(episodes)

    def test_a_pre_commit_declaration_counts(self, tmp_path: Path, monkeypatch) -> None:
        repo = _repo(tmp_path)
        (repo / "pyproject.toml").write_text(
            '[project]\nname = "x"\nversion = "0"\n', encoding="utf-8"
        )
        (repo / ".pre-commit-config.yaml").write_text(
            "repos:\n  - hooks:\n      - id: ruff-format\n", encoding="utf-8"
        )
        monkeypatch.setattr(
            "repowise.core.precedent.structural._ruff_executable", lambda _root: "ruff"
        )
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(
                returncode=1, stdout="Would reformat: a.py\n", stderr=""
            ),
        )
        episodes = derive_structural_episodes(repo, _traverser(repo), allow_formatter_check=True)
        assert KIND_FORMATTER_DRIFT in _kinds(episodes)

    def test_clean_tree_produces_no_episode(self, tmp_path: Path, monkeypatch) -> None:
        repo = self._declaring_repo(tmp_path)
        monkeypatch.setattr(
            "repowise.core.precedent.structural._ruff_executable", lambda _root: "ruff"
        )
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(returncode=0, stdout="12 files already formatted\n", stderr=""),
        )
        episodes = derive_structural_episodes(repo, _traverser(repo), allow_formatter_check=True)
        assert KIND_FORMATTER_DRIFT not in _kinds(episodes)

    def test_timeout_produces_no_episode(self, tmp_path: Path, monkeypatch) -> None:
        """An exceeded budget must produce nothing, never a partial count."""
        repo = self._declaring_repo(tmp_path)
        monkeypatch.setattr(
            "repowise.core.precedent.structural._ruff_executable", lambda _root: "ruff"
        )

        def _timeout(*_a, **_k):
            raise subprocess.TimeoutExpired(cmd="ruff", timeout=1)

        monkeypatch.setattr(subprocess, "run", _timeout)
        episodes = derive_structural_episodes(repo, _traverser(repo), allow_formatter_check=True)
        assert KIND_FORMATTER_DRIFT not in _kinds(episodes)

    def test_crash_produces_no_episode(self, tmp_path: Path, monkeypatch) -> None:
        repo = self._declaring_repo(tmp_path)
        monkeypatch.setattr(
            "repowise.core.precedent.structural._ruff_executable", lambda _root: "ruff"
        )
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(returncode=2, stdout="", stderr="boom"),
        )
        episodes = derive_structural_episodes(repo, _traverser(repo), allow_formatter_check=True)
        assert KIND_FORMATTER_DRIFT not in _kinds(episodes)

    def test_update_path_never_runs_it(self, tmp_path: Path, monkeypatch) -> None:
        repo = self._declaring_repo(tmp_path)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("no subprocess on update")),
        )
        derive_structural_episodes(repo, _traverser(repo), allow_formatter_check=False)
        assert KIND_FORMATTER_DRIFT not in FREE_KINDS


class TestDegradation:
    def test_a_broken_traverser_costs_only_its_own_fact(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        (repo / ".repowise" / "config.yaml").write_text(
            "exclude_patterns:\n  - vendor/**\n", encoding="utf-8"
        )
        broken = SimpleNamespace()  # no stats, no console-script names
        episodes = derive_structural_episodes(repo, broken, allow_formatter_check=False)
        assert _kinds(episodes) == {KIND_CONFIG_OVERRIDE}


class TestPersistence:
    def test_recording_twice_is_idempotent(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        (repo / "backend" / ".git").mkdir(parents=True)
        for _ in range(2):
            record_structural_episodes(repo, _traverser(repo), allow_formatter_check=False)
        with EpisodeStore.open_for_repo(repo) as store:
            assert store.count() == 1

    def test_a_fact_that_stops_holding_is_dropped(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        (repo / "backend" / ".git").mkdir(parents=True)
        record_structural_episodes(repo, _traverser(repo), allow_formatter_check=False)
        (repo / "backend" / ".git").rmdir()
        record_structural_episodes(repo, _traverser(repo), allow_formatter_check=False)
        with EpisodeStore.open_for_repo(repo) as store:
            assert store.count() == 0

    def test_episodes_are_tiered_in_the_payload(self, tmp_path: Path) -> None:
        """The tier is the line between this layer and a per-user memory."""
        repo = _repo(tmp_path)
        (repo / "backend" / ".git").mkdir(parents=True)
        record_structural_episodes(repo, _traverser(repo), allow_formatter_check=False)
        with EpisodeStore.open_for_repo(repo) as store:
            assert {row["tier"] for row in store.list_episodes()} == {"structural"}
