"""Unit tests for the shared pruned filesystem walk (``repowise.core.fs_walk``)
plus the regression guard that keeps new unpruned walk call sites (``rglob``,
``os.walk``, recursive ``glob``) out of packages/core, cli, and server.

Why this exists: unpruned ``Path.rglob`` over a repo that physically contains
sibling/vendored repos (or ``node_modules`` / ``.venv``) has caused two
separate multi-minute perf incidents AND leaks foreign manifests
(``hugo/go.mod``, ``eShop/*.sln``) into the current repo's resolver context.
Every repo-wide scan must go through ``fs_walk``.
"""

from __future__ import annotations

import re
from pathlib import Path

from repowise.core.fs_walk import PRUNED_DIRS, PRUNED_DIRS_DERIVED, iter_glob, walk_repo


def _write(root: Path, rel: str, content: str = "x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# iter_glob — basic rglob parity
# ---------------------------------------------------------------------------


class TestIterGlobParity:
    def test_matches_rglob_on_clean_tree(self, tmp_path: Path) -> None:
        """On a tree with no junk dirs, iter_glob == rglob exactly."""
        _write(tmp_path, "go.mod")
        _write(tmp_path, "services/foo/go.mod")
        _write(tmp_path, "libs/bar/baz/go.mod")
        _write(tmp_path, "libs/bar/baz/main.go")
        expected = sorted(tmp_path.rglob("go.mod"))
        assert sorted(iter_glob(tmp_path, "go.mod")) == expected

    def test_yields_matching_directories_too(self, tmp_path: Path) -> None:
        """rglob semantics: directories with a matching basename are yielded."""
        (tmp_path / "src" / "META-INF" / "services").mkdir(parents=True)
        results = list(iter_glob(tmp_path, "services"))
        assert results == [tmp_path / "src" / "META-INF" / "services"]

    def test_slash_pattern_matches_path_tail(self, tmp_path: Path) -> None:
        """Patterns with '/' mirror rglob("META-INF/services") semantics."""
        (tmp_path / "a" / "META-INF" / "services").mkdir(parents=True)
        (tmp_path / "b" / "other" / "services").mkdir(parents=True)
        expected = sorted(tmp_path.rglob("META-INF/services"))
        assert sorted(iter_glob(tmp_path, "META-INF/services")) == expected
        assert (tmp_path / "b" / "other" / "services") not in set(
            iter_glob(tmp_path, "META-INF/services")
        )

    def test_slash_pattern_with_glob_component(self, tmp_path: Path) -> None:
        _write(tmp_path, "x/META-INF/spring/auto.imports")
        _write(tmp_path, "x/META-INF/spring/readme.txt")
        expected = sorted(tmp_path.rglob("META-INF/spring/*.imports"))
        assert sorted(iter_glob(tmp_path, "META-INF/spring/*.imports")) == expected

    def test_slash_pattern_matches_at_root(self, tmp_path: Path) -> None:
        """rglob("META-INF/services") also matches directly under the root."""
        (tmp_path / "META-INF" / "services").mkdir(parents=True)
        expected = sorted(tmp_path.rglob("META-INF/services"))
        assert sorted(iter_glob(tmp_path, "META-INF/services")) == expected

    def test_multiple_patterns_single_walk(self, tmp_path: Path) -> None:
        _write(tmp_path, "app/tsconfig.json")
        _write(tmp_path, "app/jsconfig.json")
        _write(tmp_path, "app/package.json")
        found = set(iter_glob(tmp_path, ("tsconfig*.json", "jsconfig.json")))
        assert found == {tmp_path / "app" / "tsconfig.json", tmp_path / "app" / "jsconfig.json"}

    def test_missing_root_yields_nothing(self, tmp_path: Path) -> None:
        assert list(iter_glob(tmp_path / "nope", "*.go")) == []


# ---------------------------------------------------------------------------
# Pruning — junk dirs and nested git repos
# ---------------------------------------------------------------------------


class TestPruning:
    def test_junk_dirs_pruned(self, tmp_path: Path) -> None:
        _write(tmp_path, "real/go.mod")
        _write(tmp_path, "node_modules/dep/go.mod")
        _write(tmp_path, ".venv/lib/go.mod")
        _write(tmp_path, "sub/__pycache__/go.mod")
        assert list(iter_glob(tmp_path, "go.mod")) == [tmp_path / "real" / "go.mod"]

    def test_nested_git_dir_pruned(self, tmp_path: Path) -> None:
        """A subdirectory with its own .git directory is another repo — skip it."""
        _write(tmp_path, "go.mod")
        (tmp_path / "sibling-repo" / ".git").mkdir(parents=True)
        _write(tmp_path, "sibling-repo/go.mod")
        _write(tmp_path, "sibling-repo/deep/go.mod")
        assert list(iter_glob(tmp_path, "go.mod")) == [tmp_path / "go.mod"]

    def test_nested_git_file_pruned(self, tmp_path: Path) -> None:
        """Worktrees and submodules mark themselves with a .git FILE."""
        _write(tmp_path, "main.go")
        _write(tmp_path, "submodule/.git", "gitdir: ../.git/modules/submodule")
        _write(tmp_path, "submodule/sub.go")
        assert list(iter_glob(tmp_path, "*.go")) == [tmp_path / "main.go"]

    def test_walk_root_with_git_not_pruned(self, tmp_path: Path) -> None:
        """The walk root IS the repo being scanned — its own .git is exempt."""
        (tmp_path / ".git").mkdir()
        _write(tmp_path, "src/go.mod")
        assert list(iter_glob(tmp_path, "go.mod")) == [tmp_path / "src" / "go.mod"]

    def test_nested_git_pruning_can_be_disabled(self, tmp_path: Path) -> None:
        (tmp_path / "vendored" / ".git").mkdir(parents=True)
        _write(tmp_path, "vendored/go.mod")
        found = list(iter_glob(tmp_path, "go.mod", prune_nested_git=False))
        assert found == [tmp_path / "vendored" / "go.mod"]

    def test_custom_prune_dirs(self, tmp_path: Path) -> None:
        """Callers may pass their own prune set (narrower or broader)."""
        _write(tmp_path, "dist/package.json")
        _write(tmp_path, "node_modules/x/package.json")
        narrow = frozenset({"node_modules"})
        found = set(iter_glob(tmp_path, "package.json", prune_dirs=narrow))
        assert found == {tmp_path / "dist" / "package.json"}

    def test_default_keeps_possible_source_dirs(self, tmp_path: Path) -> None:
        """The DEFAULT set must not prune dirs real projects use for source —
        coveragepy's main package is literally ``coverage/``; a module rooted
        at ``build/go.mod`` must stay discoverable."""
        _write(tmp_path, "coverage/__init__.py")
        _write(tmp_path, "build/go.mod")
        _write(tmp_path, "Library/app.py")
        _write(tmp_path, "Logs/worker.py")
        assert list(iter_glob(tmp_path, "__init__.py")) == [tmp_path / "coverage" / "__init__.py"]
        assert list(iter_glob(tmp_path, "go.mod")) == [tmp_path / "build" / "go.mod"]
        assert list(iter_glob(tmp_path, "app.py")) == [tmp_path / "Library" / "app.py"]
        assert list(iter_glob(tmp_path, "worker.py")) == [tmp_path / "Logs" / "worker.py"]

    def test_derived_set_prunes_output_dirs(self, tmp_path: Path) -> None:
        """PRUNED_DIRS_DERIVED (dynamic hints) prunes derived-output names."""
        _write(tmp_path, "dist/settings.py")
        _write(tmp_path, "src/settings.py")
        found = list(iter_glob(tmp_path, "settings.py", prune_dirs=PRUNED_DIRS_DERIVED))
        assert found == [tmp_path / "src" / "settings.py"]

    def test_default_prune_set_contents(self) -> None:
        """Canary: the dirs behind past incidents stay pruned."""
        for d in ("node_modules", ".venv", "__pycache__", ".git", ".repowise", ".next"):
            assert d in PRUNED_DIRS
        for d in ("Library", "Temp", "Logs", "UserSettings", "MemoryCaptures"):
            assert d not in PRUNED_DIRS
        for d in ("dist", "build", "out", "coverage"):
            assert d not in PRUNED_DIRS  # possible source dirs — derived set only
            assert d in PRUNED_DIRS_DERIVED


# ---------------------------------------------------------------------------
# walk_repo — low-level walk
# ---------------------------------------------------------------------------


class TestWalkRepo:
    def test_caller_can_bound_depth_via_dirnames(self, tmp_path: Path) -> None:
        _write(tmp_path, "a/b/c/d/deep.txt")
        _write(tmp_path, "a/shallow.txt")
        seen: list[str] = []
        for dirpath, dirnames, filenames in walk_repo(tmp_path):
            if len(dirpath.relative_to(tmp_path).parts) >= 1:
                dirnames[:] = []
            seen.extend(filenames)
        assert "shallow.txt" in seen
        assert "deep.txt" not in seen

    def test_nested_git_subtree_never_yielded(self, tmp_path: Path) -> None:
        _write(tmp_path, "ours.txt")
        (tmp_path / "other" / ".git").mkdir(parents=True)
        _write(tmp_path, "other/theirs.txt")
        yielded_dirs = [d for d, _, _ in walk_repo(tmp_path)]
        assert tmp_path / "other" not in yielded_dirs


# ---------------------------------------------------------------------------
# --include-submodules plumbing — scanners must honor prune_nested_git=False
# ---------------------------------------------------------------------------


class TestSubmodulePlumbing:
    def test_read_go_modules_can_include_submodules(self, tmp_path: Path) -> None:
        from repowise.core.ingestion.resolvers.go import read_go_modules

        _write(tmp_path, "go.mod", "module example.com/root\n")
        _write(tmp_path, "vendored/.git", "gitdir: ../.git/modules/vendored")
        _write(tmp_path, "vendored/go.mod", "module example.com/vendored\n")

        default = dict(read_go_modules(tmp_path))
        assert default == {"": "example.com/root"}

        included = dict(read_go_modules(tmp_path, prune_nested_git=False))
        assert included == {"": "example.com/root", "vendored": "example.com/vendored"}

    def test_graph_builder_threads_include_submodules(self, tmp_path: Path) -> None:
        """Either traverser flag that indexes .git-bearing subdirs must also
        stop resolver scans from pruning them."""
        from repowise.core.ingestion import GraphBuilder

        assert GraphBuilder(tmp_path)._prune_nested_git is True
        assert GraphBuilder(tmp_path, include_submodules=True)._prune_nested_git is False
        assert GraphBuilder(tmp_path, include_nested_repos=True)._prune_nested_git is False

    def test_resolver_context_default(self) -> None:
        from repowise.core.ingestion.resolvers.context import ResolverContext

        ctx = ResolverContext(path_set=set(), stem_map={}, graph=None)
        assert ctx.prune_nested_git is True


# ---------------------------------------------------------------------------
# Traverser monorepo detection — must share _walk's boundary semantics
# ---------------------------------------------------------------------------


class TestMonorepoDetectionBoundaries:
    def test_nested_repo_not_reported_as_package(self, tmp_path: Path) -> None:
        """A sibling/vendored repo inside the tree is not a package of THIS
        repo — and must not be rglob-scanned for language/entry points
        (that scan ran for minutes per sibling on multi-repo directories)."""
        from repowise.core.ingestion.traverser import FileTraverser

        _write(tmp_path, "packages/app/package.json", "{}")
        _write(tmp_path, "packages/app/index.ts", "export {}")
        (tmp_path / "sibling" / ".git").mkdir(parents=True)
        _write(tmp_path, "sibling/package.json", "{}")
        _write(tmp_path, "node_modules/dep/package.json", "{}")

        traverser = FileTraverser(tmp_path)
        packages, _ = traverser._detect_monorepo()
        paths = {p.path for p in packages}
        assert "packages/app" in paths
        assert "sibling" not in paths
        assert all(not p.startswith("node_modules") for p in paths)

    def test_nested_repo_reported_when_opted_in(self, tmp_path: Path) -> None:
        from repowise.core.ingestion.traverser import FileTraverser

        (tmp_path / "sibling" / ".git").mkdir(parents=True)
        _write(tmp_path, "sibling/package.json", "{}")

        traverser = FileTraverser(tmp_path, include_nested_repos=True)
        packages, _ = traverser._detect_monorepo()
        assert "sibling" in {p.path for p in packages}


# ---------------------------------------------------------------------------
# external_systems._discover — depth bound must match the historical rglob
# ---------------------------------------------------------------------------


class TestExternalSystemsDiscoverDepth:
    def test_depth_bound_matches_old_rglob_semantics(self, tmp_path: Path) -> None:
        """Old code: rglob("*") + skip files with >4 relative parts. The
        walk_repo rewrite bounds descent by clearing dirnames at dir-depth 3;
        files at exactly 4 relative parts must stay IN, 5 must stay OUT."""
        from repowise.core.ingestion.external_systems import _discover

        _write(tmp_path, "package.json", "{}")  # 1 part
        _write(tmp_path, "a/package.json", "{}")  # 2 parts
        _write(tmp_path, "a/b/c/package.json", "{}")  # 4 parts — boundary, kept
        _write(tmp_path, "a/b/c/d/package.json", "{}")  # 5 parts — too deep
        found = {p.relative_to(tmp_path).as_posix() for p in _discover(tmp_path)}
        assert "package.json" in found
        assert "a/package.json" in found
        assert "a/b/c/package.json" in found
        assert "a/b/c/d/package.json" not in found


# ---------------------------------------------------------------------------
# Regression guard — no new direct filesystem-walk call sites
# ---------------------------------------------------------------------------
#
# Three call shapes, three histories:
#   .rglob(        two multi-minute init stalls plus foreign-manifest leaks
#                  (see module docstring)
#   os.walk(       every hand-rolled walk re-invented (and drifted) its own
#                  prune list, and none carried the nested-git or
#                  junction-cycle guards
#   .glob("...**   recursive Path.glob descends into node_modules/.venv and
#                  nested repos even when matches are filtered afterwards;
#                  coverage discovery shipped exactly this bug
#
# Allowlist additions require a justification comment: the walk root is a
# tightly-bounded directory that cannot contain junk trees or nested repos,
# the file IS the shared implementation, or migration onto fs_walk is a
# tracked follow-up. Matching is on comment-stripped lines, so prose
# mentions of these names in ``#`` comments do not trip the guard
# (docstring mentions still do — keep call syntax out of docstrings).

_GUARD_KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("rglob", re.compile(r"\.rglob\(")),
    ("os.walk", re.compile(r"\bos\.walk\(")),
    ("recursive-glob", re.compile(r"""\.glob\(\s*[rbuf]*['"][^'"]*\*\*""")),
)

_WALK_ALLOWLIST: dict[tuple[str, str], frozenset[str]] = {
    # The shared implementation (uses os.walk; docstrings reference rglob).
    ("core", "fs_walk.py"): frozenset({"rglob", "os.walk"}),
    # The gitignore-aware index walk; migration onto walk_repo is a tracked
    # follow-up (it must keep its pathspec layer on top).
    ("core", "ingestion/traverser.py"): frozenset({"os.walk"}),
    # Repo DISCOVERY: exists to find nested .git dirs, prunes in-place;
    # migration onto walk_repo(prune_nested_git=False) is a tracked follow-up.
    ("core", "workspace/scanner.py"): frozenset({"os.walk"}),
    # Decision mining: pruned in-place walks + an rglob bounded to docs/;
    # migration is a tracked follow-up (sequenced after the source_map reuse).
    ("core", "analysis/decisions/extractor.py"): frozenset({"rglob", "os.walk"}),
    # Sizes .repowise/ only — repowise-owned, cannot contain junk trees.
    ("cli", "commands/status_cmd.py"): frozenset({"rglob"}),
    # Scans repowise's own packaged web/ui source dirs, not the user repo.
    ("cli", "commands/serve_cmd.py"): frozenset({"rglob"}),
    # Sizes .repowise/ only — repowise-owned, cannot contain junk trees.
    ("server", "routers/overview.py"): frozenset({"rglob"}),
}


def _guarded_src_roots() -> dict[str, Path]:
    # tests/unit/ingestion/ → repo root → packages/<pkg>/src/repowise/<pkg>
    repo_root = Path(__file__).resolve().parents[3]
    return {
        "core": repo_root / "packages" / "core" / "src" / "repowise" / "core",
        "cli": repo_root / "packages" / "cli" / "src" / "repowise" / "cli",
        "server": repo_root / "packages" / "server" / "src" / "repowise" / "server",
    }


def _strip_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


class TestNoUnprunedWalkRegression:
    def test_no_new_walk_call_sites(self) -> None:
        offenders: list[str] = []
        for tree, root in _guarded_src_roots().items():
            assert root.is_dir(), f"layout changed? {root}"
            for py in root.rglob("*.py"):  # tests may rglob; src may not.
                rel = py.relative_to(root).as_posix()
                allowed = _WALK_ALLOWLIST.get((tree, rel), frozenset())
                text = _strip_comments(py.read_text(encoding="utf-8", errors="ignore"))
                for kind, pattern in _GUARD_KINDS:
                    if kind in allowed:
                        continue
                    if pattern.search(text):
                        offenders.append(f"{tree}:{rel} [{kind}]")
        assert not offenders, (
            "Direct filesystem-walk calls found — use "
            "repowise.core.fs_walk.iter_glob/walk_repo/WalkSnapshot instead "
            "(prunes node_modules/.venv AND nested git repos, guards against "
            "junction cycles). Unpruned walks have caused multi-minute init "
            f"stalls and cross-repo manifest leaks. Offenders: {offenders}"
        )

    def test_allowlist_entries_still_needed(self) -> None:
        """An allowlist entry whose file no longer matches its kinds is stale —
        prune it so the exemption cannot silently cover future code."""
        roots = _guarded_src_roots()
        kind_res = dict(_GUARD_KINDS)
        stale: list[str] = []
        for (tree, rel), kinds in _WALK_ALLOWLIST.items():
            py = roots[tree] / rel
            if not py.is_file():
                stale.append(f"{tree}:{rel} (file gone)")
                continue
            text = _strip_comments(py.read_text(encoding="utf-8", errors="ignore"))
            for kind in kinds:
                if not kind_res[kind].search(text):
                    stale.append(f"{tree}:{rel} [{kind}]")
        assert not stale, f"Stale allowlist entries — remove them: {stale}"
