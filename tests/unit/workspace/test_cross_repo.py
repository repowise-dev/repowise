"""Tests for repowise.core.workspace.cross_repo — cross-repo intelligence."""

from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from repowise.core.workspace.cross_repo import (
    CROSS_REPO_EDGES_FILENAME,
    CrossRepoCoChange,
    CrossRepoOverlay,
    CrossRepoPackageDep,
    _GitCommit,
    _parse_git_log,
    detect_cross_repo_co_changes,
    detect_package_dependencies,
    load_overlay,
    save_overlay,
)


# ---------------------------------------------------------------------------
# _parse_git_log
# ---------------------------------------------------------------------------


class TestParseGitLog:
    def test_parses_standard_output(self, tmp_path: Path) -> None:
        """Mock subprocess to return a known git log and verify parsing."""
        fake_output = (
            "\x00alice@co.com|1712900000\n"
            "src/app.py\n"
            "src/utils.py\n"
            "\x00bob@co.com|1712890000\n"
            "src/main.py\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("R", (), {
                "returncode": 0, "stdout": fake_output, "stderr": ""
            })()
            commits = _parse_git_log(tmp_path)

        assert len(commits) == 2
        assert commits[0].author_email == "alice@co.com"
        assert commits[0].timestamp == 1712900000
        assert commits[0].files == ["src/app.py", "src/utils.py"]
        assert commits[1].author_email == "bob@co.com"
        assert commits[1].files == ["src/main.py"]

    def test_handles_empty_repo(self, tmp_path: Path) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("R", (), {
                "returncode": 0, "stdout": "", "stderr": ""
            })()
            commits = _parse_git_log(tmp_path)
        assert commits == []

    def test_handles_subprocess_failure(self, tmp_path: Path) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("R", (), {
                "returncode": 128, "stdout": "", "stderr": "fatal: not a git repo"
            })()
            commits = _parse_git_log(tmp_path)
        assert commits == []

    def test_handles_exception(self, tmp_path: Path) -> None:
        with patch("subprocess.run", side_effect=OSError("no git")):
            commits = _parse_git_log(tmp_path)
        assert commits == []


# ---------------------------------------------------------------------------
# Cross-repo co-change detection
# ---------------------------------------------------------------------------


def _make_commits(alias: str, entries: list[tuple[str, int, list[str]]]) -> list[_GitCommit]:
    """Helper: build _GitCommit list from (email, ts, files) tuples."""
    return [_GitCommit(author_email=e, timestamp=ts, files=files)
            for e, ts, files in entries]


class TestCrossRepoCoChanges:
    def _detect_with_mocked_logs(
        self, repo_commits: dict[str, list[_GitCommit]], **kwargs
    ) -> list[CrossRepoCoChange]:
        """Run detect_cross_repo_co_changes with mocked _parse_git_log."""
        repo_paths = {alias: Path(f"/fake/{alias}") for alias in repo_commits}

        def fake_parse(repo_path, commit_limit=500):
            for alias, path in repo_paths.items():
                if str(repo_path) == str(path):
                    return repo_commits[alias]
            return []

        with patch("repowise.core.workspace.cross_repo._parse_git_log", side_effect=fake_parse):
            return detect_cross_repo_co_changes(repo_paths, **kwargs)

    def test_same_author_within_window(self) -> None:
        """Two repos, same author, commits <24h apart → edges found."""
        now = int(time.time())
        results = self._detect_with_mocked_logs({
            "backend": _make_commits("backend", [
                ("alice@co.com", now - 3600, ["src/api.py"]),  # 1 hour ago
            ]),
            "frontend": _make_commits("frontend", [
                ("alice@co.com", now - 7200, ["src/client.ts"]),  # 2 hours ago
            ]),
        }, min_score=0.0)
        assert len(results) >= 1
        edge = results[0]
        assert {edge.source_repo, edge.target_repo} == {"backend", "frontend"}

    def test_different_authors_no_match(self) -> None:
        """Same timestamps but different authors → no edges."""
        now = int(time.time())
        results = self._detect_with_mocked_logs({
            "backend": _make_commits("backend", [
                ("alice@co.com", now - 3600, ["src/api.py"]),
            ]),
            "frontend": _make_commits("frontend", [
                ("bob@co.com", now - 3600, ["src/client.ts"]),
            ]),
        }, min_score=0.0)
        assert len(results) == 0

    def test_outside_time_window(self) -> None:
        """Same author, commits >24h apart → no edges."""
        now = int(time.time())
        results = self._detect_with_mocked_logs({
            "backend": _make_commits("backend", [
                ("alice@co.com", now, ["src/api.py"]),
            ]),
            "frontend": _make_commits("frontend", [
                ("alice@co.com", now - 100000, ["src/client.ts"]),  # ~28h ago
            ]),
        }, min_score=0.0)
        assert len(results) == 0

    def test_temporal_decay(self) -> None:
        """Recent co-changes should have higher strength than old ones."""
        now = int(time.time())
        # Recent pair
        recent = self._detect_with_mocked_logs({
            "a": _make_commits("a", [("x@co.com", now - 100, ["f1.py"])]),
            "b": _make_commits("b", [("x@co.com", now - 200, ["f2.py"])]),
        }, min_score=0.0)

        # Old pair (6 months ago)
        old = self._detect_with_mocked_logs({
            "a": _make_commits("a", [("x@co.com", now - 15_000_000, ["f1.py"])]),
            "b": _make_commits("b", [("x@co.com", now - 15_000_100, ["f2.py"])]),
        }, min_score=0.0)

        assert recent and old
        assert recent[0].strength > old[0].strength

    def test_min_score_filter(self) -> None:
        """Low-strength edges are filtered by min_score."""
        now = int(time.time())
        # Very old commit → low weight
        results = self._detect_with_mocked_logs({
            "a": _make_commits("a", [("x@co.com", now - 30_000_000, ["f1.py"])]),
            "b": _make_commits("b", [("x@co.com", now - 30_000_100, ["f2.py"])]),
        }, min_score=5.0)
        assert len(results) == 0

    def test_single_repo_returns_empty(self) -> None:
        """Only one repo → no cross-repo edges."""
        now = int(time.time())
        results = self._detect_with_mocked_logs({
            "only": _make_commits("only", [
                ("alice@co.com", now, ["f1.py", "f2.py"]),
            ]),
        })
        assert results == []

    def test_multiple_files_per_commit(self) -> None:
        """N files in commit A × M files in commit B = N*M file pairs."""
        now = int(time.time())
        results = self._detect_with_mocked_logs({
            "a": _make_commits("a", [
                ("x@co.com", now - 100, ["f1.py", "f2.py"]),
            ]),
            "b": _make_commits("b", [
                ("x@co.com", now - 200, ["g1.py", "g2.py", "g3.py"]),
            ]),
        }, min_score=0.0)
        # 2 × 3 = 6 unique file pairs
        assert len(results) == 6


# ---------------------------------------------------------------------------
# Manifest scanning
# ---------------------------------------------------------------------------


class TestManifestScanning:
    def test_npm_file_reference(self, tmp_path: Path) -> None:
        """package.json with file: reference to sibling repo."""
        backend = tmp_path / "backend"
        shared = tmp_path / "shared"
        backend.mkdir()
        shared.mkdir()

        pkg = {"dependencies": {"@myorg/shared": "file:../shared"}}
        (backend / "package.json").write_text(json.dumps(pkg))

        repo_paths = {"backend": backend, "shared": shared}
        deps = detect_package_dependencies(repo_paths)
        assert len(deps) == 1
        assert deps[0].source_repo == "backend"
        assert deps[0].target_repo == "shared"
        assert deps[0].kind == "npm_local_path"

    def test_cargo_path_dep(self, tmp_path: Path) -> None:
        """Cargo.toml with path dependency to sibling repo."""
        app = tmp_path / "app"
        lib = tmp_path / "lib"
        app.mkdir()
        lib.mkdir()

        cargo_content = '[dependencies]\nmylib = { path = "../lib" }\n'
        (app / "Cargo.toml").write_text(cargo_content)

        # Need tomllib for parsing
        try:
            import tomllib  # noqa: F401
        except ImportError:
            try:
                import tomli  # noqa: F401
            except ImportError:
                pytest.skip("No TOML parser available")

        repo_paths = {"app": app, "lib": lib}
        deps = detect_package_dependencies(repo_paths)
        assert any(d.kind == "cargo_path" and d.target_repo == "lib" for d in deps)

    def test_go_replace_directive(self, tmp_path: Path) -> None:
        """go.mod with replace pointing to sibling repo."""
        svc = tmp_path / "service"
        proto = tmp_path / "proto"
        svc.mkdir()
        proto.mkdir()

        go_mod = "module example.com/service\n\nreplace example.com/proto => ../proto\n"
        (svc / "go.mod").write_text(go_mod)

        repo_paths = {"service": svc, "proto": proto}
        deps = detect_package_dependencies(repo_paths)
        assert any(d.kind == "go_replace" and d.target_repo == "proto" for d in deps)

    def test_no_manifests(self, tmp_path: Path) -> None:
        """Repo with no manifest files → no deps."""
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        deps = detect_package_dependencies({"a": a, "b": b})
        assert deps == []

    def test_reference_outside_workspace(self, tmp_path: Path) -> None:
        """References to dirs not in repo_paths are ignored."""
        backend = tmp_path / "backend"
        backend.mkdir()
        pkg = {"dependencies": {"external": "file:../../outside"}}
        (backend / "package.json").write_text(json.dumps(pkg))

        deps = detect_package_dependencies({"backend": backend})
        assert deps == []


# ---------------------------------------------------------------------------
# Overlay persistence
# ---------------------------------------------------------------------------


class TestOverlayPersistence:
    def test_save_and_load_round_trip(self, tmp_path: Path) -> None:
        overlay = CrossRepoOverlay(
            version=1,
            generated_at="2026-04-12T12:00:00Z",
            co_changes=[
                CrossRepoCoChange(
                    source_repo="a", source_file="f1.py",
                    target_repo="b", target_file="f2.py",
                    strength=3.5, frequency=4, last_date="2026-04-10",
                ),
            ],
            package_deps=[
                CrossRepoPackageDep(
                    source_repo="b", target_repo="a",
                    source_manifest="package.json", kind="npm_local_path",
                ),
            ],
            repo_summaries={"a": {"cross_repo_edge_count": 1}},
        )
        save_overlay(overlay, tmp_path)
        loaded = load_overlay(tmp_path)
        assert loaded is not None
        assert loaded.version == 1
        assert len(loaded.co_changes) == 1
        assert loaded.co_changes[0].strength == 3.5
        assert len(loaded.package_deps) == 1
        assert loaded.package_deps[0].kind == "npm_local_path"

    def test_load_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert load_overlay(tmp_path) is None

    def test_load_corrupt_json_returns_none(self, tmp_path: Path) -> None:
        data_dir = tmp_path / ".repowise-workspace"
        data_dir.mkdir()
        (data_dir / CROSS_REPO_EDGES_FILENAME).write_text("not json!!!")
        assert load_overlay(tmp_path) is None

    def test_save_creates_data_dir(self, tmp_path: Path) -> None:
        overlay = CrossRepoOverlay()
        path = save_overlay(overlay, tmp_path)
        assert path.is_file()
        assert (tmp_path / ".repowise-workspace").is_dir()
