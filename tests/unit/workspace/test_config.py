"""Tests for repowise.core.workspace.config — workspace configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.workspace.config import (
    WORKSPACE_CONFIG_FILENAME,
    RepoEntry,
    WorkspaceConfig,
    find_workspace_root,
)


# ---------------------------------------------------------------------------
# RepoEntry
# ---------------------------------------------------------------------------


class TestRepoEntry:
    def test_to_dict_minimal(self) -> None:
        entry = RepoEntry(path="backend", alias="backend")
        d = entry.to_dict()
        assert d == {"path": "backend", "alias": "backend"}
        assert "is_primary" not in d
        assert "indexed_at" not in d
        assert "last_commit_at_index" not in d

    def test_to_dict_with_timestamps(self) -> None:
        entry = RepoEntry(
            path="backend",
            alias="backend",
            indexed_at="2026-04-12T10:30:00+00:00",
            last_commit_at_index="abc1234",
        )
        d = entry.to_dict()
        assert d["indexed_at"] == "2026-04-12T10:30:00+00:00"
        assert d["last_commit_at_index"] == "abc1234"

    def test_from_dict_with_timestamps(self) -> None:
        entry = RepoEntry.from_dict({
            "path": "be",
            "alias": "be",
            "indexed_at": "2026-04-12T10:30:00+00:00",
            "last_commit_at_index": "abc1234",
        })
        assert entry.indexed_at == "2026-04-12T10:30:00+00:00"
        assert entry.last_commit_at_index == "abc1234"

    def test_from_dict_backward_compat(self) -> None:
        """Old YAML without timestamp fields should still load fine."""
        entry = RepoEntry.from_dict({"path": "lib", "alias": "lib"})
        assert entry.indexed_at is None
        assert entry.last_commit_at_index is None

    def test_to_dict_primary(self) -> None:
        entry = RepoEntry(path="backend", alias="backend", is_primary=True)
        d = entry.to_dict()
        assert d["is_primary"] is True

    def test_from_dict(self) -> None:
        entry = RepoEntry.from_dict({"path": "fe", "alias": "frontend", "is_primary": True})
        assert entry.path == "fe"
        assert entry.alias == "frontend"
        assert entry.is_primary is True

    def test_from_dict_defaults(self) -> None:
        entry = RepoEntry.from_dict({"path": "lib", "alias": "lib"})
        assert entry.is_primary is False


# ---------------------------------------------------------------------------
# WorkspaceConfig — serialization
# ---------------------------------------------------------------------------


class TestWorkspaceConfigSerialization:
    def test_to_dict_structure(self) -> None:
        config = WorkspaceConfig(
            version=1,
            repos=[
                RepoEntry(path="backend", alias="backend", is_primary=True),
                RepoEntry(path="frontend", alias="frontend"),
            ],
            default_repo="backend",
        )
        d = config.to_dict()
        assert d["version"] == 1
        assert d["default_repo"] == "backend"
        assert len(d["repos"]) == 2
        assert d["repos"][0]["alias"] == "backend"

    def test_from_dict_valid(self) -> None:
        data = {
            "version": 1,
            "default_repo": "be",
            "repos": [
                {"path": "be", "alias": "be", "is_primary": True},
                {"path": "fe", "alias": "fe"},
            ],
        }
        config = WorkspaceConfig.from_dict(data)
        assert config.version == 1
        assert config.default_repo == "be"
        assert len(config.repos) == 2

    def test_from_dict_missing_fields(self) -> None:
        config = WorkspaceConfig.from_dict({})
        assert config.version == 1
        assert config.repos == []
        assert config.default_repo is None

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        original = WorkspaceConfig(
            version=1,
            repos=[
                RepoEntry(path="backend", alias="backend", is_primary=True),
                RepoEntry(path="libs/shared", alias="shared"),
            ],
            default_repo="backend",
        )
        original.save(tmp_path)

        loaded = WorkspaceConfig.load(tmp_path)
        assert loaded.version == original.version
        assert loaded.default_repo == original.default_repo
        assert len(loaded.repos) == len(original.repos)
        assert loaded.repos[0].path == "backend"
        assert loaded.repos[0].alias == "backend"
        assert loaded.repos[0].is_primary is True
        assert loaded.repos[1].path == "libs/shared"
        assert loaded.repos[1].alias == "shared"
        assert loaded.repos[1].is_primary is False

    def test_save_load_roundtrip_with_timestamps(self, tmp_path: Path) -> None:
        original = WorkspaceConfig(
            version=1,
            repos=[
                RepoEntry(
                    path="backend",
                    alias="backend",
                    is_primary=True,
                    indexed_at="2026-04-12T10:30:00+00:00",
                    last_commit_at_index="abc1234567890",
                ),
                RepoEntry(path="frontend", alias="frontend"),
            ],
            default_repo="backend",
        )
        original.save(tmp_path)

        loaded = WorkspaceConfig.load(tmp_path)
        assert loaded.repos[0].indexed_at == "2026-04-12T10:30:00+00:00"
        assert loaded.repos[0].last_commit_at_index == "abc1234567890"
        assert loaded.repos[1].indexed_at is None
        assert loaded.repos[1].last_commit_at_index is None

    def test_load_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            WorkspaceConfig.load(tmp_path)

    def test_save_creates_file(self, tmp_path: Path) -> None:
        config = WorkspaceConfig(repos=[], default_repo=None)
        path = config.save(tmp_path)
        assert path.exists()
        assert path.name == WORKSPACE_CONFIG_FILENAME

    def test_posix_paths_stored(self, tmp_path: Path) -> None:
        """Paths in YAML should use forward slashes even on Windows."""
        config = WorkspaceConfig(
            repos=[RepoEntry(path="libs/shared", alias="shared")],
        )
        config.save(tmp_path)
        text = (tmp_path / WORKSPACE_CONFIG_FILENAME).read_text(encoding="utf-8")
        assert "libs/shared" in text
        assert "\\" not in text  # no backslashes


# ---------------------------------------------------------------------------
# WorkspaceConfig — query helpers
# ---------------------------------------------------------------------------


class TestWorkspaceConfigQueries:
    def _make_config(self) -> WorkspaceConfig:
        return WorkspaceConfig(
            repos=[
                RepoEntry(path="backend", alias="backend", is_primary=True),
                RepoEntry(path="frontend", alias="frontend"),
                RepoEntry(path="libs/shared", alias="shared"),
            ],
            default_repo="backend",
        )

    def test_get_repo_found(self) -> None:
        config = self._make_config()
        repo = config.get_repo("frontend")
        assert repo is not None
        assert repo.alias == "frontend"

    def test_get_repo_missing(self) -> None:
        config = self._make_config()
        assert config.get_repo("nonexistent") is None

    def test_get_primary(self) -> None:
        config = self._make_config()
        primary = config.get_primary()
        assert primary is not None
        assert primary.alias == "backend"

    def test_get_primary_fallback_to_first(self) -> None:
        config = WorkspaceConfig(
            repos=[RepoEntry(path="a", alias="a"), RepoEntry(path="b", alias="b")],
            default_repo=None,
        )
        primary = config.get_primary()
        assert primary is not None
        assert primary.alias == "a"

    def test_get_primary_empty(self) -> None:
        config = WorkspaceConfig(repos=[])
        assert config.get_primary() is None

    def test_repo_paths(self, tmp_path: Path) -> None:
        config = self._make_config()
        paths = config.repo_paths(tmp_path)
        assert len(paths) == 3
        assert all(p.is_absolute() for p in paths)
        assert paths[0] == (tmp_path / "backend").resolve()
        assert paths[2] == (tmp_path / "libs" / "shared").resolve()

    def test_repo_aliases(self) -> None:
        config = self._make_config()
        assert config.repo_aliases() == ["backend", "frontend", "shared"]


# ---------------------------------------------------------------------------
# WorkspaceConfig — mutation helpers
# ---------------------------------------------------------------------------


class TestWorkspaceConfigMutation:
    def test_add_repo(self) -> None:
        config = WorkspaceConfig(repos=[], default_repo=None)
        config.add_repo(RepoEntry(path="new", alias="new"))
        assert len(config.repos) == 1

    def test_add_repo_duplicate_alias(self) -> None:
        config = WorkspaceConfig(
            repos=[RepoEntry(path="a", alias="myalias")],
        )
        with pytest.raises(ValueError, match="already exists"):
            config.add_repo(RepoEntry(path="b", alias="myalias"))

    def test_remove_repo(self) -> None:
        config = WorkspaceConfig(
            repos=[
                RepoEntry(path="a", alias="a"),
                RepoEntry(path="b", alias="b"),
            ],
            default_repo="a",
        )
        removed = config.remove_repo("a")
        assert removed is not None
        assert removed.alias == "a"
        assert len(config.repos) == 1
        # Default should shift to first remaining repo
        assert config.default_repo == "b"

    def test_remove_repo_not_found(self) -> None:
        config = WorkspaceConfig(repos=[])
        assert config.remove_repo("nope") is None


# ---------------------------------------------------------------------------
# find_workspace_root
# ---------------------------------------------------------------------------


class TestFindWorkspaceRoot:
    def test_found_at_start(self, tmp_path: Path) -> None:
        (tmp_path / WORKSPACE_CONFIG_FILENAME).write_text("version: 1")
        assert find_workspace_root(tmp_path) == tmp_path.resolve()

    def test_found_in_parent(self, tmp_path: Path) -> None:
        (tmp_path / WORKSPACE_CONFIG_FILENAME).write_text("version: 1")
        child = tmp_path / "backend" / "src"
        child.mkdir(parents=True)
        assert find_workspace_root(child) == tmp_path.resolve()

    def test_not_found(self, tmp_path: Path) -> None:
        child = tmp_path / "some" / "deep" / "path"
        child.mkdir(parents=True)
        # No config file anywhere — should return None (eventually hits fs root)
        # This test may be slow on deep paths, so we use tmp_path which is shallow
        result = find_workspace_root(child)
        assert result is None

    def test_stops_at_correct_level(self, tmp_path: Path) -> None:
        """Config at one level, deeper path should find it."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / WORKSPACE_CONFIG_FILENAME).write_text("version: 1")
        deep = ws / "repo" / "src" / "pkg"
        deep.mkdir(parents=True)
        assert find_workspace_root(deep) == ws.resolve()
