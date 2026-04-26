"""Tests for repowise.core.workspace.scanner — repo discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.workspace.scanner import (
    DiscoveredRepo,
    ScanResult,
    _generate_aliases,
    _is_git_repo,
    _is_submodule,
    scan_for_repos,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: Path, rel: str) -> Path:
    """Create a fake git repo at ``tmp_path / rel`` (with a .git directory)."""
    p = tmp_path / rel
    p.mkdir(parents=True, exist_ok=True)
    (p / ".git").mkdir()
    return p


def _make_submodule(tmp_path: Path, rel: str) -> Path:
    """Create a fake submodule at ``tmp_path / rel`` (.git is a file)."""
    p = tmp_path / rel
    p.mkdir(parents=True, exist_ok=True)
    (p / ".git").write_text("gitdir: ../.git/modules/sub")
    return p


def _make_indexed_repo(tmp_path: Path, rel: str) -> Path:
    """Create a fake repo with an existing .repowise/ directory."""
    p = _make_repo(tmp_path, rel)
    (p / ".repowise").mkdir()
    return p


# ---------------------------------------------------------------------------
# scan_for_repos — basic cases
# ---------------------------------------------------------------------------


class TestScanForRepos:
    def test_empty_dir(self, tmp_path: Path) -> None:
        result = scan_for_repos(tmp_path)
        assert result.repos == []
        assert result.root == tmp_path.resolve()

    def test_single_repo_at_root(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        result = scan_for_repos(tmp_path)
        assert len(result.repos) == 1
        assert result.repos[0].path == tmp_path.resolve()
        assert result.repos[0].is_submodule is False

    def test_multiple_repos_at_depth_1(self, tmp_path: Path) -> None:
        _make_repo(tmp_path, "backend")
        _make_repo(tmp_path, "frontend")
        _make_repo(tmp_path, "shared-libs")

        result = scan_for_repos(tmp_path)
        aliases = {r.alias for r in result.repos}
        assert aliases == {"backend", "frontend", "shared-libs"}
        assert len(result.repos) == 3

    def test_repos_sorted_alphabetically(self, tmp_path: Path) -> None:
        _make_repo(tmp_path, "zebra")
        _make_repo(tmp_path, "alpha")
        _make_repo(tmp_path, "middle")

        result = scan_for_repos(tmp_path)
        names = [r.name for r in result.repos]
        assert names == ["alpha", "middle", "zebra"]

    def test_root_is_repo_with_sub_repos(self, tmp_path: Path) -> None:
        """When root itself is a git repo AND has sub-repos, all are returned."""
        (tmp_path / ".git").mkdir()  # root is a git repo
        _make_repo(tmp_path, "backend")
        _make_repo(tmp_path, "frontend")

        result = scan_for_repos(tmp_path)
        aliases = {r.alias for r in result.repos}
        assert len(result.repos) == 3
        assert tmp_path.name in aliases or "." in aliases or tmp_path.resolve() in {r.path for r in result.repos}
        assert "backend" in aliases
        assert "frontend" in aliases

    def test_root_is_repo_no_sub_repos(self, tmp_path: Path) -> None:
        """When root is a git repo with no sub-repos, single-repo result."""
        (tmp_path / ".git").mkdir()
        result = scan_for_repos(tmp_path)
        assert len(result.repos) == 1
        assert result.repos[0].path == tmp_path.resolve()

    def test_nested_repos_outer_wins(self, tmp_path: Path) -> None:
        """If repo A contains repo B, only A is found (B is not descended into)."""
        outer = _make_repo(tmp_path, "monorepo")
        # Inner repo would be found if we descended, but we shouldn't
        (outer / "packages" / "sub").mkdir(parents=True)
        (outer / "packages" / "sub" / ".git").mkdir()

        result = scan_for_repos(tmp_path)
        assert len(result.repos) == 1
        assert result.repos[0].name == "monorepo"

    def test_repos_at_depth_2(self, tmp_path: Path) -> None:
        _make_repo(tmp_path, "org/backend")
        _make_repo(tmp_path, "org/frontend")

        result = scan_for_repos(tmp_path)
        assert len(result.repos) == 2
        aliases = {r.alias for r in result.repos}
        assert aliases == {"backend", "frontend"}

    def test_respects_max_depth(self, tmp_path: Path) -> None:
        _make_repo(tmp_path, "a/b/c/deep-repo")

        # Default max_depth=3 should NOT find it (depth of deep-repo is 3,
        # but we check children at depth 2 which means depth 3 is the boundary)
        result = scan_for_repos(tmp_path, max_depth=2)
        assert len(result.repos) == 0

        result = scan_for_repos(tmp_path, max_depth=4)
        assert len(result.repos) == 1


# ---------------------------------------------------------------------------
# scan_for_repos — skip directories
# ---------------------------------------------------------------------------


class TestScanSkipDirs:
    def test_skips_node_modules(self, tmp_path: Path) -> None:
        nm = tmp_path / "node_modules" / "some-pkg"
        nm.mkdir(parents=True)
        (nm / ".git").mkdir()

        result = scan_for_repos(tmp_path)
        assert len(result.repos) == 0
        assert any("node_modules" in s for s in result.skipped_dirs)

    def test_skips_venv(self, tmp_path: Path) -> None:
        venv = tmp_path / ".venv" / "lib"
        venv.mkdir(parents=True)

        result = scan_for_repos(tmp_path)
        assert len(result.repos) == 0

    def test_skips_hidden_dirs(self, tmp_path: Path) -> None:
        _make_repo(tmp_path, ".hidden-project")

        result = scan_for_repos(tmp_path)
        assert len(result.repos) == 0

    def test_does_not_skip_repowise_dir(self, tmp_path: Path) -> None:
        """The .repowise directory itself should not be skipped during scanning."""
        _make_repo(tmp_path, "myrepo")
        (tmp_path / "myrepo" / ".repowise").mkdir()

        result = scan_for_repos(tmp_path)
        assert len(result.repos) == 1
        assert result.repos[0].has_repowise is True


# ---------------------------------------------------------------------------
# scan_for_repos — submodules
# ---------------------------------------------------------------------------


class TestScanSubmodules:
    def test_submodule_excluded_by_default(self, tmp_path: Path) -> None:
        _make_repo(tmp_path, "main-repo")
        _make_submodule(tmp_path, "sub-repo")

        result = scan_for_repos(tmp_path)
        assert len(result.repos) == 1
        assert result.repos[0].name == "main-repo"

    def test_submodule_included_with_flag(self, tmp_path: Path) -> None:
        _make_repo(tmp_path, "main-repo")
        _make_submodule(tmp_path, "sub-repo")

        result = scan_for_repos(tmp_path, include_submodules=True)
        assert len(result.repos) == 2
        sub = next(r for r in result.repos if r.name == "sub-repo")
        assert sub.is_submodule is True

    def test_submodule_at_root_excluded(self, tmp_path: Path) -> None:
        """Root itself is a submodule and include_submodules=False."""
        (tmp_path / ".git").write_text("gitdir: ../.git/modules/x")

        result = scan_for_repos(tmp_path, include_submodules=False)
        assert len(result.repos) == 0


# ---------------------------------------------------------------------------
# scan_for_repos — metadata
# ---------------------------------------------------------------------------


class TestScanMetadata:
    def test_has_repowise_detected(self, tmp_path: Path) -> None:
        _make_indexed_repo(tmp_path, "indexed-repo")
        _make_repo(tmp_path, "new-repo")

        result = scan_for_repos(tmp_path)
        indexed = next(r for r in result.repos if r.name == "indexed-repo")
        new = next(r for r in result.repos if r.name == "new-repo")
        assert indexed.has_repowise is True
        assert new.has_repowise is False

    def test_paths_are_absolute(self, tmp_path: Path) -> None:
        _make_repo(tmp_path, "myrepo")

        result = scan_for_repos(tmp_path)
        assert result.repos[0].path.is_absolute()
        assert result.root.is_absolute()


# ---------------------------------------------------------------------------
# alias generation
# ---------------------------------------------------------------------------


class TestAliasGeneration:
    def test_unique_names(self, tmp_path: Path) -> None:
        _make_repo(tmp_path, "backend")
        _make_repo(tmp_path, "frontend")

        result = scan_for_repos(tmp_path)
        aliases = [r.alias for r in result.repos]
        assert len(set(aliases)) == len(aliases)

    def test_collision_suffixed(self, tmp_path: Path) -> None:
        _make_repo(tmp_path, "org1/api")
        _make_repo(tmp_path, "org2/api")

        result = scan_for_repos(tmp_path)
        aliases = sorted(r.alias for r in result.repos)
        assert aliases == ["api", "api-2"]

    def test_triple_collision(self, tmp_path: Path) -> None:
        _make_repo(tmp_path, "a/svc")
        _make_repo(tmp_path, "b/svc")
        _make_repo(tmp_path, "c/svc")

        result = scan_for_repos(tmp_path)
        aliases = sorted(r.alias for r in result.repos)
        assert aliases == ["svc", "svc-2", "svc-3"]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_is_git_repo_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        assert _is_git_repo(tmp_path) is True

    def test_is_git_repo_file(self, tmp_path: Path) -> None:
        (tmp_path / ".git").write_text("gitdir: something")
        assert _is_git_repo(tmp_path) is True

    def test_is_not_git_repo(self, tmp_path: Path) -> None:
        assert _is_git_repo(tmp_path) is False

    def test_is_submodule(self, tmp_path: Path) -> None:
        (tmp_path / ".git").write_text("gitdir: ../.git/modules/x")
        assert _is_submodule(tmp_path) is True

    def test_is_not_submodule(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        assert _is_submodule(tmp_path) is False

    def test_generate_aliases_no_collision(self) -> None:
        root = Path("/ws")
        pairs = [(Path("/ws/a"), root), (Path("/ws/b"), root)]
        aliases = _generate_aliases(pairs)
        assert aliases == {Path("/ws/a"): "a", Path("/ws/b"): "b"}

    def test_generate_aliases_collision(self) -> None:
        root = Path("/ws")
        pairs = [(Path("/ws/x/api"), root), (Path("/ws/y/api"), root)]
        aliases = _generate_aliases(pairs)
        assert aliases[Path("/ws/x/api")] == "api"
        assert aliases[Path("/ws/y/api")] == "api-2"
