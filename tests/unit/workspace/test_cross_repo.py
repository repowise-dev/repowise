"""Tests for repowise.core.workspace.cross_repo — cross-repo intelligence."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from repowise.core.workspace.cross_repo import (
    CROSS_REPO_EDGES_FILENAME,
    CrossRepoCoChange,
    CrossRepoOverlay,
    CrossRepoPackageDep,
    _GitCommit,
    _is_noise_path,
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
            "\x00alice@co.com\x01Alice\x011712900000\n"
            "src/app.py\n"
            "src/utils.py\n"
            "\x00bob@co.com\x01Bob B\x011712890000\n"
            "src/main.py\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type(
                "R", (), {"returncode": 0, "stdout": fake_output, "stderr": ""}
            )()
            commits = _parse_git_log(tmp_path)

        assert len(commits) == 2
        assert commits[0].author_email == "alice@co.com"
        assert commits[0].author_name == "Alice"
        assert commits[0].timestamp == 1712900000
        assert commits[0].files == ["src/app.py", "src/utils.py"]
        assert commits[1].author_email == "bob@co.com"
        assert commits[1].files == ["src/main.py"]

    def test_author_identity_bridges_email_variants(self) -> None:
        """Same human, different git configs → one identity."""
        a = _GitCommit(
            author_email="12345+jdoe@users.noreply.github.com",
            timestamp=0,
            author_name="Jane Doe",
        )
        b = _GitCommit(
            author_email="jane@personal.dev",
            timestamp=0,
            author_name="JaneDoe",
        )
        c = _GitCommit(author_email="ci@bot.dev", timestamp=0, author_name="")
        assert a.author_identity == b.author_identity == "janedoe"
        assert c.author_identity == "ci@bot.dev"

    def test_handles_empty_repo(self, tmp_path: Path) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
            commits = _parse_git_log(tmp_path)
        assert commits == []

    def test_handles_subprocess_failure(self, tmp_path: Path) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = type(
                "R", (), {"returncode": 128, "stdout": "", "stderr": "fatal: not a git repo"}
            )()
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
    return [_GitCommit(author_email=e, timestamp=ts, files=files) for e, ts, files in entries]


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
        results = self._detect_with_mocked_logs(
            {
                "backend": _make_commits(
                    "backend",
                    [
                        ("alice@co.com", now - 3600, ["src/api.py"]),  # 1 hour ago
                    ],
                ),
                "frontend": _make_commits(
                    "frontend",
                    [
                        ("alice@co.com", now - 7200, ["src/client.ts"]),  # 2 hours ago
                    ],
                ),
            },
            min_score=0.0,
            min_sessions=1,
        )
        assert len(results) >= 1
        edge = results[0]
        assert {edge.source_repo, edge.target_repo} == {"backend", "frontend"}

    def test_different_authors_no_match(self) -> None:
        """Same timestamps but different authors → no edges."""
        now = int(time.time())
        results = self._detect_with_mocked_logs(
            {
                "backend": _make_commits(
                    "backend",
                    [
                        ("alice@co.com", now - 3600, ["src/api.py"]),
                    ],
                ),
                "frontend": _make_commits(
                    "frontend",
                    [
                        ("bob@co.com", now - 3600, ["src/client.ts"]),
                    ],
                ),
            },
            min_score=0.0,
            min_sessions=1,
        )
        assert len(results) == 0

    def test_outside_time_window(self) -> None:
        """Same author, commits >24h apart → no edges."""
        now = int(time.time())
        results = self._detect_with_mocked_logs(
            {
                "backend": _make_commits(
                    "backend",
                    [
                        ("alice@co.com", now, ["src/api.py"]),
                    ],
                ),
                "frontend": _make_commits(
                    "frontend",
                    [
                        ("alice@co.com", now - 100000, ["src/client.ts"]),  # ~28h ago
                    ],
                ),
            },
            min_score=0.0,
            min_sessions=1,
        )
        assert len(results) == 0

    def test_temporal_decay(self) -> None:
        """Recent co-changes should have higher strength than old ones."""
        now = int(time.time())
        # Recent pair
        recent = self._detect_with_mocked_logs(
            {
                "a": _make_commits("a", [("x@co.com", now - 100, ["f1.py"])]),
                "b": _make_commits("b", [("x@co.com", now - 200, ["f2.py"])]),
            },
            min_score=0.0,
            min_sessions=1,
        )

        # Old pair (6 months ago)
        old = self._detect_with_mocked_logs(
            {
                "a": _make_commits("a", [("x@co.com", now - 15_000_000, ["f1.py"])]),
                "b": _make_commits("b", [("x@co.com", now - 15_000_100, ["f2.py"])]),
            },
            min_score=0.0,
            min_sessions=1,
        )

        assert recent and old
        assert recent[0].strength > old[0].strength

    def test_min_score_filter(self) -> None:
        """Low-strength edges are filtered by min_score."""
        now = int(time.time())
        # Strength is bounded below 1, so a floor of 1.0 filters everything.
        results = self._detect_with_mocked_logs(
            {
                "a": _make_commits("a", [("x@co.com", now - 100, ["f1.py"])]),
                "b": _make_commits("b", [("x@co.com", now - 200, ["f2.py"])]),
            },
            min_score=1.0,
            min_sessions=1,
        )
        assert len(results) == 0

    def test_single_repo_returns_empty(self) -> None:
        """Only one repo → no cross-repo edges."""
        now = int(time.time())
        results = self._detect_with_mocked_logs(
            {
                "only": _make_commits(
                    "only",
                    [
                        ("alice@co.com", now, ["f1.py", "f2.py"]),
                    ],
                ),
            }
        )
        assert results == []

    def test_multiple_files_per_commit(self) -> None:
        """N files in commit A x M files in commit B = N*M file pairs."""
        now = int(time.time())
        results = self._detect_with_mocked_logs(
            {
                "a": _make_commits(
                    "a",
                    [
                        ("x@co.com", now - 100, ["f1.py", "f2.py"]),
                    ],
                ),
                "b": _make_commits(
                    "b",
                    [
                        ("x@co.com", now - 200, ["g1.py", "g2.py", "g3.py"]),
                    ],
                ),
            },
            min_score=0.0,
            min_sessions=1,
        )
        # 2 x 3 = 6 unique file pairs
        assert len(results) == 6

    def test_strength_bounded_below_one(self) -> None:
        """Strength is a share: always in (0, 1) no matter the volume."""
        now = int(time.time())
        # Many co-sessions of the same pair, 2 days apart each
        day = 86400
        entries_a = [("x@co.com", now - i * 2 * day, ["api.py"]) for i in range(10)]
        entries_b = [("x@co.com", now - i * 2 * day + 60, ["client.ts"]) for i in range(10)]
        results = self._detect_with_mocked_logs(
            {
                "a": _make_commits("a", entries_a),
                "b": _make_commits("b", entries_b),
            },
            min_score=0.0,
        )
        assert results
        for r in results:
            assert 0.0 < r.strength < 1.0

    def test_session_collapses_burst_of_commits(self) -> None:
        """A day of back-to-back commits is one session: frequency 1, not NxM."""
        now = int(time.time())
        results = self._detect_with_mocked_logs(
            {
                "a": _make_commits(
                    "a",
                    [
                        ("x@co.com", now - 3600 * 1, ["api.py"]),
                        ("x@co.com", now - 3600 * 2, ["api.py"]),
                        ("x@co.com", now - 3600 * 3, ["api.py"]),
                    ],
                ),
                "b": _make_commits(
                    "b",
                    [
                        ("x@co.com", now - 3000, ["client.ts"]),
                        ("x@co.com", now - 5000, ["client.ts"]),
                        ("x@co.com", now - 9000, ["client.ts"]),
                    ],
                ),
            },
            min_score=0.0,
            min_sessions=1,
        )
        assert len(results) == 1
        assert results[0].frequency == 1

    def test_single_session_pair_dropped_by_default(self) -> None:
        """One shared afternoon proves nothing: default needs >=2 co-sessions."""
        now = int(time.time())
        results = self._detect_with_mocked_logs(
            {
                "a": _make_commits("a", [("x@co.com", now - 100, ["f1.py"])]),
                "b": _make_commits("b", [("x@co.com", now - 200, ["f2.py"])]),
            },
            min_score=0.0,
        )
        assert results == []

    def test_ubiquitous_file_filtered(self) -> None:
        """A diary file touched in most commits never becomes an edge."""
        now = int(time.time())
        day = 86400
        # 30 backend sessions 2 days apart (enough history for the ubiquity
        # filter to engage); a diary rides along in every one.
        backend = []
        for i in range(30):
            files = ["docs/PROGRESS.md", f"src/other_{i}.py"]
            if i in (0, 2):
                files.append("src/api.py")
            backend.append(("x@co.com", now - i * 2 * day, files))
        frontend = [
            ("x@co.com", now - 0 * 2 * day + 60, ["src/client.ts"]),
            ("x@co.com", now - 2 * 2 * day + 60, ["src/client.ts"]),
        ]
        results = self._detect_with_mocked_logs(
            {
                "backend": _make_commits("backend", backend),
                "frontend": _make_commits("frontend", frontend),
            }
        )
        assert results, "the real api.py <-> client.ts coupling must survive"
        for r in results:
            assert "PROGRESS.md" not in r.source_file
            assert "PROGRESS.md" not in r.target_file
        pair_files = {(r.source_file, r.target_file) for r in results}
        assert ("src/api.py", "src/client.ts") in pair_files

    def test_sustained_coupling_outranks_coincidence(self) -> None:
        """5-of-6 sessions must outrank a 2-of-2 coincidence (smoothing)."""
        now = int(time.time())
        day = 86400
        a_entries = []
        b_entries = []
        # Sustained pair: co-occurs in 5 sessions, plus one solo session
        for i in range(5):
            a_entries.append(("x@co.com", now - i * 2 * day, ["sustained.py"]))
            b_entries.append(("x@co.com", now - i * 2 * day + 60, ["sustained.ts"]))
        a_entries.append(("x@co.com", now - 5 * 2 * day, ["sustained.py"]))
        # Coincidental pair: exists in exactly 2 sessions, both shared
        for i in range(2):
            a_entries.append(("y@co.com", now - i * 2 * day, ["coincidence.py"]))
            b_entries.append(("y@co.com", now - i * 2 * day + 60, ["coincidence.ts"]))
        results = self._detect_with_mocked_logs(
            {
                "a": _make_commits("a", a_entries),
                "b": _make_commits("b", b_entries),
            },
            min_score=0.0,
        )
        by_pair = {(r.source_file, r.target_file): r.strength for r in results}
        assert (
            by_pair[("sustained.py", "sustained.ts")]
            > by_pair[("coincidence.py", "coincidence.ts")]
        )

    def test_per_repo_pair_cap(self) -> None:
        """One hyperactive repo pair cannot starve others out of the results."""
        import repowise.core.workspace.cross_repo as cr

        now = int(time.time())
        day = 86400
        # a<->b: 4x4 files co-occurring in 2 recent sessions → 16 strong pairs
        ab_a = [("x@co.com", now - i * 2 * day, [f"a{n}.py" for n in range(4)]) for i in range(2)]
        ab_b = [
            ("x@co.com", now - i * 2 * day + 60, [f"b{n}.ts" for n in range(4)]) for i in range(2)
        ]
        # a<->c: one weaker (older) pair
        ac_a = [("x@co.com", now - (100 + i * 2) * day, ["shared.py"]) for i in range(2)]
        ac_c = [("x@co.com", now - (100 + i * 2) * day + 60, ["consumer.go"]) for i in range(2)]
        with patch.object(cr, "_MAX_EDGES_PER_REPO_PAIR", 5):
            results = self._detect_with_mocked_logs(
                {
                    "a": _make_commits("a", ab_a + ac_a),
                    "b": _make_commits("b", ab_b),
                    "c": _make_commits("c", ac_c),
                },
                min_score=0.0,
            )
        ab_edges = [r for r in results if {r.source_repo, r.target_repo} == {"a", "b"}]
        ac_edges = [r for r in results if {r.source_repo, r.target_repo} == {"a", "c"}]
        assert len(ab_edges) == 5, "hyperactive pair capped"
        assert len(ac_edges) == 1, "weaker pair still present"


# ---------------------------------------------------------------------------
# Noise-file filtering
# ---------------------------------------------------------------------------


class TestIsNoisePath:
    @pytest.mark.parametrize(
        "path",
        [
            ".github/workflows/deploy.yml",
            "sub/.github/workflows/build.yaml",
            "package-lock.json",
            "frontend/yarn.lock",
            "Cargo.lock",
            "go.sum",
            "poetry.lock",
            "uv.lock",
            "app/static/bundle.min.js",
            "styles/site.min.css",
            "dist/index.js",
            "node_modules/left-pad/index.js",
            "src/generated/client.ts",
            "Assets/Localization/en.json",
            "web/locales/de.json",
            "i18n/messages.fr.json",
            "Assets/UI/Panels/InventoryPanel.prefab",
            "api/proto/service.pb.go",
            "svc/pb/thing_pb2.py",
            "messages.po",
            "CHANGELOG.md",
            "docs/CHANGELOG.md",
        ],
    )
    def test_noise_paths_detected(self, path: str) -> None:
        assert _is_noise_path(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "src/api.py",
            "src/client.ts",
            "backend/src/domain/manager.rs",
            "config.json",
            "README.md",
            "packages/core/index.ts",
        ],
    )
    def test_signal_paths_kept(self, path: str) -> None:
        assert _is_noise_path(path) is False

    def test_windows_separators_normalized(self) -> None:
        assert _is_noise_path("frontend\\dist\\app.js") is True


class TestCrossRepoNoiseFiltering:
    def _detect_with_mocked_logs(
        self, repo_commits: dict[str, list[_GitCommit]], **kwargs
    ) -> list[CrossRepoCoChange]:
        repo_paths = {alias: Path(f"/fake/{alias}") for alias in repo_commits}

        def fake_parse(repo_path, commit_limit=500):
            for alias, path in repo_paths.items():
                if str(repo_path) == str(path):
                    return repo_commits[alias]
            return []

        with patch("repowise.core.workspace.cross_repo._parse_git_log", side_effect=fake_parse):
            return detect_cross_repo_co_changes(repo_paths, **kwargs)

    def test_noise_files_excluded_from_pairs(self) -> None:
        """Workflow/lockfile noise never appears in co-change results."""
        now = int(time.time())
        results = self._detect_with_mocked_logs(
            {
                "backend": _make_commits(
                    "backend",
                    [
                        (
                            "alice@co.com",
                            now - 3600,
                            ["src/api.py", ".github/workflows/deploy.yml"],
                        ),
                    ],
                ),
                "frontend": _make_commits(
                    "frontend",
                    [
                        ("alice@co.com", now - 7200, ["src/client.ts", "yarn.lock"]),
                    ],
                ),
            },
            min_score=0.0,
            min_sessions=1,
        )
        # Only the real src/api.py <-> src/client.ts pair survives.
        assert len(results) == 1
        files = {results[0].source_file, results[0].target_file}
        assert files == {"src/api.py", "src/client.ts"}

    def test_commit_with_only_noise_dropped(self) -> None:
        """A commit left empty after filtering produces no edges."""
        now = int(time.time())
        results = self._detect_with_mocked_logs(
            {
                "backend": _make_commits(
                    "backend",
                    [
                        ("alice@co.com", now - 3600, [".github/workflows/deploy.yml"]),
                    ],
                ),
                "frontend": _make_commits(
                    "frontend",
                    [
                        ("alice@co.com", now - 7200, ["src/client.ts"]),
                    ],
                ),
            },
            min_score=0.0,
            min_sessions=1,
        )
        assert results == []

    def test_wide_session_capped_per_side(self) -> None:
        """A sprawling release session pairs at most 20 files per side."""
        now = int(time.time())
        wide_files = [f"wide_b{n:02d}.py" for n in range(40)]
        results = self._detect_with_mocked_logs(
            {
                "a": _make_commits(
                    "a",
                    [
                        ("x@co.com", now - 100, ["main.py"]),
                    ],
                ),
                "b": _make_commits(
                    "b",
                    [
                        ("x@co.com", now - 120, wide_files),
                    ],
                ),
            },
            min_score=0.0,
            min_sessions=1,
        )
        # 1 x min(40, 20) = 20 pairs, not 40
        assert len(results) == 20


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
            generated_at="2026-04-12T12:00:00Z",
            co_changes=[
                CrossRepoCoChange(
                    source_repo="a",
                    source_file="f1.py",
                    target_repo="b",
                    target_file="f2.py",
                    strength=0.35,
                    frequency=4,
                    last_date="2026-04-10",
                ),
            ],
            package_deps=[
                CrossRepoPackageDep(
                    source_repo="b",
                    target_repo="a",
                    source_manifest="package.json",
                    kind="npm_local_path",
                ),
            ],
            repo_summaries={"a": {"cross_repo_edge_count": 1}},
        )
        save_overlay(overlay, tmp_path)
        loaded = load_overlay(tmp_path)
        assert loaded is not None
        assert loaded.version == overlay.version
        assert len(loaded.co_changes) == 1
        assert loaded.co_changes[0].strength == 0.35
        assert len(loaded.package_deps) == 1
        assert loaded.package_deps[0].kind == "npm_local_path"

    def test_load_stale_version_returns_none(self, tmp_path: Path) -> None:
        """v1 overlays carry unbounded strengths — treated as absent."""
        overlay = CrossRepoOverlay(version=1)
        save_overlay(overlay, tmp_path)
        assert load_overlay(tmp_path) is None

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
