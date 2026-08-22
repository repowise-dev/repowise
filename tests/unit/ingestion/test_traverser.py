"""Unit tests for FileTraverser."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion import traverser as traverser_mod
from repowise.core.ingestion.traverser import (
    FileTraverser,
    _compile_gitignore,
    _detect_language,
    _is_generated,
)


class TestCompileGitignore:
    def test_malformed_line_is_not_fatal_and_keeps_valid(self) -> None:
        spec = _compile_gitignore([".godot\\", "build/", "*.log", "", "# note"])
        assert spec.match_file("build/x")
        assert spec.match_file("a.log")
        # A malformed line never takes down its neighbours.
        assert not spec.match_file("src/main.py")

    def test_trailing_backslash_matches_nothing(self) -> None:
        # Git parity: git treats a dangling trailing backslash (e.g. `.godot\`)
        # as an escape of nothing, so the pattern matches nothing — no error, no
        # fallback to the bare path. Dropping the line reproduces that exactly.
        # Do NOT re-add a salvage that rewrites `.godot\` -> `.godot`: that would
        # diverge from git (it would start ignoring `.godot/` that git tracks).
        spec = _compile_gitignore([".godot\\"])
        assert not spec.match_file(".godot")
        assert not spec.match_file(".godot/imported/x.res")
        assert not spec.match_file("godot.py")

    def test_unrecoverable_line_is_dropped(self) -> None:
        # A lone backslash is unparseable and empty once stripped -> drop it,
        # do not raise, and keep the valid neighbour.
        spec = _compile_gitignore(["\\", "keep-me/"])
        assert spec.match_file("keep-me/x")

    def test_all_valid_lines_preserved(self) -> None:
        spec = _compile_gitignore(["dist/", "*.tmp"])
        assert spec.match_file("dist/a")
        assert spec.match_file("x.tmp")
        assert not spec.match_file("keep.py")


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------


class TestLanguageDetection:
    def test_python_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "foo.py"
        f.write_text("x = 1")
        assert _detect_language(f) == "python"

    def test_typescript_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "bar.ts"
        f.write_text("const x = 1;")
        assert _detect_language(f) == "typescript"

    def test_tsx_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "Comp.tsx"
        f.write_text("<div />")
        assert _detect_language(f) == "typescript"

    def test_mts_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "module.mts"
        f.write_text("export const x = 1;")
        assert _detect_language(f) == "typescript"

    def test_cts_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "module.cts"
        f.write_text("export const x = 1;")
        assert _detect_language(f) == "typescript"

    def test_go_extension(self, tmp_path: Path) -> None:
        assert _detect_language(tmp_path / "main.go") == "go"

    def test_rust_extension(self, tmp_path: Path) -> None:
        assert _detect_language(tmp_path / "lib.rs") == "rust"

    def test_java_extension(self, tmp_path: Path) -> None:
        assert _detect_language(tmp_path / "Calculator.java") == "java"

    def test_cpp_extension(self, tmp_path: Path) -> None:
        assert _detect_language(tmp_path / "calc.cpp") == "cpp"

    def test_special_dockerfile(self, tmp_path: Path) -> None:
        assert _detect_language(tmp_path / "Dockerfile") == "dockerfile"

    def test_special_makefile(self, tmp_path: Path) -> None:
        assert _detect_language(tmp_path / "Makefile") == "makefile"

    def test_unknown_extension(self, tmp_path: Path) -> None:
        assert _detect_language(tmp_path / "binary.elf") == "unknown"

    def test_python_shebang(self, tmp_path: Path) -> None:
        f = tmp_path / "script"
        f.write_text("#!/usr/bin/env python3\nprint('hi')")
        assert _detect_language(f) == "python"


# ---------------------------------------------------------------------------
# File traversal
# ---------------------------------------------------------------------------


class TestFileTraverser:
    @pytest.fixture
    def simple_repo(self, tmp_path: Path) -> Path:
        """Create a minimal repo structure."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("def main(): pass")
        (tmp_path / "src" / "utils.py").write_text("def helper(): pass")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_main.py").write_text("def test_foo(): pass")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "lodash" / "index.js").mkdir(parents=True)
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "main.cpython-311.pyc").write_bytes(b"\x00")
        return tmp_path

    def test_traverses_python_files(self, simple_repo: Path) -> None:
        traverser = FileTraverser(simple_repo)
        paths = [f.path for f in traverser.traverse()]
        assert any("main.py" in p for p in paths)
        assert any("utils.py" in p for p in paths)

    def test_skips_node_modules(self, simple_repo: Path) -> None:
        traverser = FileTraverser(simple_repo)
        paths = [f.path for f in traverser.traverse()]
        assert not any("node_modules" in p for p in paths)

    def test_skips_pycache(self, simple_repo: Path) -> None:
        traverser = FileTraverser(simple_repo)
        paths = [f.path for f in traverser.traverse()]
        assert not any("__pycache__" in p for p in paths)

    def test_skips_unity_generated_dirs(self, tmp_path: Path) -> None:
        (tmp_path / "Assets" / "Scripts").mkdir(parents=True)
        (tmp_path / "Assets" / "Scripts" / "Game.cs").write_text("class Game {}")
        (tmp_path / "Library" / "PackageCache").mkdir(parents=True)
        (tmp_path / "Library" / "PackageCache" / "Fake.cs").write_text("class Fake {}")
        (tmp_path / "Temp" / "StagingArea").mkdir(parents=True)
        (tmp_path / "Temp" / "StagingArea" / "Temp.cs").write_text("class TempFile {}")
        traverser = FileTraverser(tmp_path)
        paths = [f.path for f in traverser.traverse()]
        assert "Assets/Scripts/Game.cs" in paths
        assert not any(p.startswith("Library/") for p in paths)
        assert not any(p.startswith("Temp/") for p in paths)

    def test_skips_unity_asset_extensions_before_binary_detection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "Assets").mkdir()
        (tmp_path / "Assets" / "Scene.unity").write_text("%YAML 1.1\n")
        (tmp_path / "Assets" / "Main.cs").write_text("class Main {}\n")

        def _fail_binary(_path: Path) -> bool:
            raise AssertionError("Unity asset hit binary detection")

        def _fail_shebang(_path: Path) -> str:
            raise AssertionError("Unity asset hit shebang detection")

        monkeypatch.setattr(traverser_mod, "_is_binary", _fail_binary)
        monkeypatch.setattr(traverser_mod, "_detect_by_shebang", _fail_shebang)

        traverser = FileTraverser(tmp_path)
        paths = [f.path for f in traverser.traverse()]
        assert "Assets/Main.cs" in paths
        assert "Assets/Scene.unity" not in paths

    def test_skips_binary_files(self, tmp_path: Path) -> None:
        binary = tmp_path / "binary.so"
        binary.write_bytes(b"\x00\x01\x02\x03" * 100)
        traverser = FileTraverser(tmp_path)
        paths = [f.path for f in traverser.traverse()]
        assert not any("binary.so" in p for p in paths)

    def test_respects_gitignore(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text("*.log\nsecret/\n")
        (tmp_path / "app.py").write_text("pass")
        (tmp_path / "debug.log").write_text("logs")
        (tmp_path / "secret").mkdir()
        (tmp_path / "secret" / "key.py").write_text("KEY = 'x'")
        traverser = FileTraverser(tmp_path)
        paths = [f.path for f in traverser.traverse()]
        assert any("app.py" in p for p in paths)
        assert not any("debug.log" in p for p in paths)
        assert not any("secret" in p for p in paths)

    def test_respects_git_info_exclude(self, tmp_path: Path) -> None:
        # .git/info/exclude is git's local-only ignore file — scratch dirs
        # excluded there are invisible to git status and must be equally
        # invisible to the index (they leaked into blast-radius lists).
        info = tmp_path / ".git" / "info"
        info.mkdir(parents=True)
        (info / "exclude").write_text("local-stash/\n*.scratch\n")
        (tmp_path / "app.py").write_text("pass")
        (tmp_path / "notes.scratch").write_text("pass")
        (tmp_path / "local-stash").mkdir()
        (tmp_path / "local-stash" / "probe.py").write_text("pass")
        traverser = FileTraverser(tmp_path)
        paths = [f.path for f in traverser.traverse()]
        assert any("app.py" in p for p in paths)
        assert not any("local-stash" in p for p in paths)
        assert not any("notes.scratch" in p for p in paths)

    def test_malformed_gitignore_line_does_not_abort(self, tmp_path: Path) -> None:
        # Git tolerates patterns that pathspec rejects (e.g. a trailing
        # backslash like ``.godot\``). One such line must not crash the whole
        # traversal; the remaining valid patterns must still apply.
        (tmp_path / ".gitignore").write_text(".godot\\\n*.log\napp_ok.py\n")
        (tmp_path / "app.py").write_text("pass")
        (tmp_path / "app_ok.py").write_text("pass")
        (tmp_path / "debug.log").write_text("logs")
        traverser = FileTraverser(tmp_path)
        paths = [f.path for f in traverser.traverse()]
        # Did not raise, and the well-formed patterns after the bad line held.
        assert any("app.py" in p for p in paths)
        assert not any("app_ok.py" in p for p in paths)
        assert not any("debug.log" in p for p in paths)

    def test_skips_oversized_files_without_a_parser(self, tmp_path: Path) -> None:
        # No AST parser, so size is the only signal and the default cap holds.
        big = tmp_path / "fixture.json"
        big.write_bytes(b'{"k": 1}\n' * 200_000)  # ~1.7 MB
        traverser = FileTraverser(tmp_path, max_file_size_kb=500)
        paths = [f.path for f in traverser.traverse()]
        assert not any("fixture.json" in p for p in paths)
        assert traverser.stats.skipped_oversized == 1
        # Blobs stay in the aggregate — naming them would drown the signal.
        assert traverser.stats.skipped_source_files == []

    def test_indexes_source_past_the_default_cap(self, tmp_path: Path) -> None:
        # The #1237 regression: a repo's biggest hand-written module was
        # dropped before any language check ran.
        big = tmp_path / "big.py"
        big.write_bytes(b"x = 1\n" * 200_000)  # ~1.2 MB
        traverser = FileTraverser(tmp_path, max_file_size_kb=500)
        paths = [f.path for f in traverser.traverse()]
        assert any("big.py" in p for p in paths)
        assert traverser.stats.skipped_oversized == 0

    def test_skips_source_past_the_source_ceiling(self, tmp_path: Path) -> None:
        # The ceiling is a memory budget: tree-sitter peaks at ~95 MB of RSS
        # per MB of source and the parse pool runs 8 workers.
        big = tmp_path / "huge.py"
        big.write_bytes(b"x = 1\n" * 500_000)  # ~2.9 MB, over the 2 MB ceiling
        traverser = FileTraverser(tmp_path, max_file_size_kb=500)
        paths = [f.path for f in traverser.traverse()]
        assert not any("huge.py" in p for p in paths)
        assert traverser.stats.skipped_oversized == 1
        # ...and it is named, because a silently dropped module is the bug.
        assert [s.path for s in traverser.stats.skipped_source_files] == ["huge.py"]
        assert traverser.stats.skipped_source_files[0].reason == "over_max_size"

    def test_skips_minified_bundle_under_the_ceiling(self, tmp_path: Path) -> None:
        # Real parser, source extension, under the ceiling — only the line
        # shape distinguishes it from hand-written code.
        bundle = tmp_path / "vendor.js"
        bundle.write_bytes(b"!function(){" + b";".join(b"var a=1" for _ in range(90_000)) + b"}();")
        assert bundle.stat().st_size > 500 * 1024
        traverser = FileTraverser(tmp_path, max_file_size_kb=500)
        paths = [f.path for f in traverser.traverse()]
        assert not any("vendor.js" in p for p in paths)
        assert traverser.stats.skipped_oversized == 1
        assert traverser.stats.skipped_source_files[0].reason == "minified"

    def test_source_ceiling_cannot_be_raised_by_max_file_size_kb(self, tmp_path: Path) -> None:
        # The source ceiling is a memory budget (~95 MB RSS per MB of source,
        # times an 8-worker pool), not a preference. Raising the caller-facing
        # knob must not be able to lift it, or the OOM guard is optional.
        big = tmp_path / "huge.py"
        big.write_bytes(b"x = 1\n" * 500_000)  # ~2.9 MB, over the 2 MB ceiling
        traverser = FileTraverser(tmp_path, max_file_size_kb=100_000)  # 100 MB
        paths = [f.path for f in traverser.traverse()]
        assert not any("huge.py" in p for p in paths)
        assert traverser.stats.skipped_oversized == 1

    def test_max_file_size_kb_still_governs_parserless_files(self, tmp_path: Path) -> None:
        # ...but it remains the knob for everything with no parser.
        blob = tmp_path / "fixture.json"
        blob.write_bytes(b'{"k": 1}\n' * 200_000)  # ~1.7 MB
        traverser = FileTraverser(tmp_path, max_file_size_kb=100_000)
        paths = [f.path for f in traverser.traverse()]
        assert any("fixture.json" in p for p in paths)

    def test_normal_source_is_not_mistaken_for_minified(self, tmp_path: Path) -> None:
        # Long-ish but ordinary lines must survive the guard: the threshold is
        # 200 bytes/line and real source measured 25-60.
        wide = tmp_path / "wide.py"
        line = b"result = compute(" + b"argument_name, " * 8 + b"final)\n"  # ~140 bytes
        wide.write_bytes(line * 5_000)
        assert wide.stat().st_size > 500 * 1024
        traverser = FileTraverser(tmp_path, max_file_size_kb=500)
        paths = [f.path for f in traverser.traverse()]
        assert any("wide.py" in p for p in paths)

    def test_deterministic_ordering(self, simple_repo: Path) -> None:
        traverser = FileTraverser(simple_repo)
        run1 = [f.path for f in traverser.traverse()]
        run2 = [f.path for f in traverser.traverse()]
        assert run1 == run2

    def test_is_test_flag(self, simple_repo: Path) -> None:
        traverser = FileTraverser(simple_repo)
        files = {f.path: f for f in traverser.traverse()}
        test_file = next(p for p in files if "test_main" in p)
        assert files[test_file].is_test is True
        main_file = next(p for p in files if p.endswith("main.py"))
        assert files[main_file].is_test is False

    def test_file_info_fields(self, tmp_path: Path) -> None:
        (tmp_path / "calc.py").write_text("class Calc: pass")
        traverser = FileTraverser(tmp_path)
        files = list(traverser.traverse())
        assert len(files) == 1
        fi = files[0]
        assert fi.language == "python"
        assert fi.size_bytes > 0
        assert fi.abs_path.endswith("calc.py")


# ---------------------------------------------------------------------------
# Extra exclude patterns (CLI --exclude / settings["exclude_patterns"])
# ---------------------------------------------------------------------------


class TestExtraExcludePatterns:
    def test_extra_exclude_vendor_dir(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("pass")
        (tmp_path / "vendor").mkdir()
        (tmp_path / "vendor" / "utils.py").write_text("pass")
        traverser = FileTraverser(tmp_path, extra_exclude_patterns=["vendor/"])
        paths = [f.path for f in traverser.traverse()]
        assert any("main.py" in p for p in paths)
        assert not any("vendor" in p for p in paths)

    def test_extra_exclude_nested_glob(self, tmp_path: Path) -> None:
        (tmp_path / "src" / "generated").mkdir(parents=True)
        (tmp_path / "src" / "generated" / "proto.py").write_text("pass")
        (tmp_path / "src" / "real.py").write_text("pass")
        traverser = FileTraverser(tmp_path, extra_exclude_patterns=["src/generated/**"])
        paths = [f.path for f in traverser.traverse()]
        assert any("real.py" in p for p in paths)
        assert not any("proto.py" in p for p in paths)

    def test_extra_exclude_dir_pattern(self, tmp_path: Path) -> None:
        (tmp_path / "src" / "generated").mkdir(parents=True)
        (tmp_path / "src" / "generated" / "types.ts").write_text("export type T = string;")
        (tmp_path / "src" / "app.ts").write_text("const x = 1;")
        traverser = FileTraverser(tmp_path, extra_exclude_patterns=["src/generated/"])
        paths = [f.path for f in traverser.traverse()]
        assert any("app.ts" in p for p in paths)
        assert not any("types.ts" in p for p in paths)

    def test_extra_exclude_multiple_patterns(self, tmp_path: Path) -> None:
        (tmp_path / "vendor").mkdir()
        (tmp_path / "vendor" / "dep.py").write_text("pass")
        (tmp_path / "dist").mkdir()
        (tmp_path / "dist" / "bundle.js").write_text("// built")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("pass")
        traverser = FileTraverser(tmp_path, extra_exclude_patterns=["vendor/", "dist/"])
        paths = [f.path for f in traverser.traverse()]
        assert any("main.py" in p for p in paths)
        assert not any("vendor" in p for p in paths)
        assert not any("dist" in p for p in paths)

    def test_no_extra_patterns_behaves_normally(self, tmp_path: Path) -> None:
        (tmp_path / "src" / "app.py").mkdir(parents=True)
        # Ensure passing None or empty list doesn't break anything
        for patterns in (None, []):
            traverser = FileTraverser(tmp_path, extra_exclude_patterns=patterns)
            list(traverser.traverse())  # Should not raise


# ---------------------------------------------------------------------------
# Per-directory .repowiseIgnore
# ---------------------------------------------------------------------------


class TestPerDirectoryrepowiseIgnore:
    def test_subdir_repowise_ignore_excludes_dir(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / ".repowiseIgnore").write_text("generated/\n")
        (src / "generated").mkdir()
        (src / "generated" / "types.py").write_text("pass")
        (src / "real.py").write_text("pass")
        traverser = FileTraverser(tmp_path)
        paths = [f.path for f in traverser.traverse()]
        assert any("real.py" in p for p in paths)
        assert not any("types.py" in p for p in paths)

    def test_subdir_repowise_ignore_excludes_files(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / ".repowiseIgnore").write_text("*.test.ts\n")
        (src / "app.ts").write_text("const x = 1;")
        (src / "app.test.ts").write_text("test('ok', () => {})")
        traverser = FileTraverser(tmp_path)
        paths = [f.path for f in traverser.traverse()]
        assert any("app.ts" in p and "test" not in p for p in paths)
        assert not any("app.test.ts" in p for p in paths)

    def test_root_repowise_ignore_still_respected(self, tmp_path: Path) -> None:
        (tmp_path / ".repowiseIgnore").write_text("secret/\n")
        (tmp_path / "secret").mkdir()
        (tmp_path / "secret" / "key.py").write_text("KEY = 'x'")
        (tmp_path / "app.py").write_text("pass")
        traverser = FileTraverser(tmp_path)
        paths = [f.path for f in traverser.traverse()]
        assert any("app.py" in p for p in paths)
        assert not any("secret" in p for p in paths)

    def test_subdir_repowise_ignore_does_not_affect_sibling_dirs(self, tmp_path: Path) -> None:
        api = tmp_path / "api"
        api.mkdir()
        (api / ".repowiseIgnore").write_text("internal/\n")
        (api / "internal").mkdir()
        (api / "internal" / "secret.py").write_text("pass")
        (api / "public.py").write_text("pass")
        other = tmp_path / "other"
        other.mkdir()
        (other / "internal").mkdir()
        (other / "internal" / "visible.py").write_text("pass")
        traverser = FileTraverser(tmp_path)
        paths = [f.path for f in traverser.traverse()]
        # api/internal should be excluded
        assert not any("api/internal" in p for p in paths)
        # other/internal should NOT be excluded (different parent's ignore)
        assert any("visible.py" in p for p in paths)


# ---------------------------------------------------------------------------
# Nested (per-directory) .gitignore
# ---------------------------------------------------------------------------


class TestNestedGitignore:
    """Git reads a ``.gitignore`` in every directory, not just the repo root.
    A workspace/monorepo package with its own ``.gitignore`` must be honoured.
    """

    def test_nested_gitignore_excludes_dir(self, tmp_path: Path) -> None:
        # Mirrors the #341 case: a yarn-workspace `frontend/` with its own
        # .gitignore excluding generated bundle output.
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (frontend / ".gitignore").write_text("storybook-static/\n")
        (frontend / "storybook-static").mkdir()
        (frontend / "storybook-static" / "bundle.js").write_text("/* minified */")
        (frontend / "app.ts").write_text("const x = 1;")
        traverser = FileTraverser(tmp_path)
        paths = [f.path for f in traverser.traverse()]
        assert any("app.ts" in p for p in paths)
        assert not any("storybook-static" in p for p in paths)

    def test_nested_gitignore_excludes_files(self, tmp_path: Path) -> None:
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / ".gitignore").write_text("*.generated.ts\n")
        (pkg / "real.ts").write_text("const x = 1;")
        (pkg / "types.generated.ts").write_text("export type T = string;")
        traverser = FileTraverser(tmp_path)
        paths = [f.path for f in traverser.traverse()]
        assert any("real.ts" in p for p in paths)
        assert not any("types.generated.ts" in p for p in paths)

    def test_nested_gitignore_does_not_affect_sibling_dirs(self, tmp_path: Path) -> None:
        a = tmp_path / "a"
        a.mkdir()
        (a / ".gitignore").write_text("artifacts/\n")
        (a / "artifacts").mkdir()
        (a / "artifacts" / "out.py").write_text("pass")
        b = tmp_path / "b"
        (b / "artifacts").mkdir(parents=True)
        (b / "artifacts" / "keep.py").write_text("pass")
        traverser = FileTraverser(tmp_path)
        paths = [f.path for f in traverser.traverse()]
        assert not any("a/artifacts" in p for p in paths)
        # b/artifacts is not excluded — different directory, no .gitignore there
        assert any("keep.py" in p for p in paths)

    def test_nested_gitignore_and_repowise_ignore_merge(self, tmp_path: Path) -> None:
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / ".gitignore").write_text("bundles/\n")
        (pkg / ".repowiseIgnore").write_text("*.snap\n")
        (pkg / "bundles").mkdir()
        (pkg / "bundles" / "bundle.js").write_text("// built")
        (pkg / "comp.tsx").write_text("<div />")
        (pkg / "comp.snap").write_text("snapshot")
        traverser = FileTraverser(tmp_path)
        paths = [f.path for f in traverser.traverse()]
        assert any("comp.tsx" in p for p in paths)
        assert not any("bundles" in p for p in paths)
        assert not any("comp.snap" in p for p in paths)


# ---------------------------------------------------------------------------
# Monorepo detection
# ---------------------------------------------------------------------------


class TestMonorepoDetection:
    def test_detects_monorepo(self, tmp_path: Path) -> None:
        # Create two packages with manifests
        pkg_a = tmp_path / "packages" / "core"
        pkg_a.mkdir(parents=True)
        (pkg_a / "pyproject.toml").write_text("[project]\nname='core'")
        (pkg_a / "main.py").write_text("pass")

        pkg_b = tmp_path / "packages" / "cli"
        pkg_b.mkdir(parents=True)
        (pkg_b / "pyproject.toml").write_text("[project]\nname='cli'")
        (pkg_b / "main.py").write_text("pass")

        traverser = FileTraverser(tmp_path)
        structure = traverser.get_repo_structure()
        assert structure.is_monorepo is True
        pkg_names = [p.name for p in structure.packages]
        assert "core" in pkg_names
        assert "cli" in pkg_names

    def test_single_package_not_monorepo(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\nname='myapp'")
        (tmp_path / "app.py").write_text("pass")
        traverser = FileTraverser(tmp_path)
        structure = traverser.get_repo_structure()
        # Root manifest doesn't count — only manifests at depth 1+
        assert structure.is_monorepo is False

    def test_language_distribution(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("pass")
        (tmp_path / "b.py").write_text("pass")
        (tmp_path / "c.ts").write_text("const x = 1;")
        traverser = FileTraverser(tmp_path)
        structure = traverser.get_repo_structure()
        assert "python" in structure.root_language_distribution
        assert "typescript" in structure.root_language_distribution
        assert (
            structure.root_language_distribution["python"]
            > structure.root_language_distribution["typescript"]
        )


# ---------------------------------------------------------------------------
# TraversalStats
# ---------------------------------------------------------------------------


class TestTraversalStats:
    def test_stats_counts_included_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("pass")
        (tmp_path / "b.py").write_text("pass")
        (tmp_path / "c.ts").write_text("const x = 1;")
        traverser = FileTraverser(tmp_path)
        list(traverser.traverse())
        assert traverser.stats.included == 3
        assert traverser.stats.total_paths_walked >= 3

    def test_stats_counts_gitignore_skips(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text("*.log\n")
        (tmp_path / "app.py").write_text("pass")
        (tmp_path / "debug.log").write_text("log data")
        traverser = FileTraverser(tmp_path)
        list(traverser.traverse())
        assert traverser.stats.skipped_gitignore >= 1
        assert traverser.stats.included >= 1

    def test_stats_counts_oversized_skips(self, tmp_path: Path) -> None:
        # A parserless blob past the cap: the counter must still fire, or the
        # size guard has been removed rather than narrowed.
        big = tmp_path / "big.json"
        big.write_bytes(b'{"k": 1}\n' * 200_000)
        (tmp_path / "small.py").write_text("pass")
        traverser = FileTraverser(tmp_path, max_file_size_kb=500)
        list(traverser.traverse())
        assert traverser.stats.skipped_oversized == 1
        assert traverser.stats.included == 1

    def test_stats_counts_blocked_extension(self, tmp_path: Path) -> None:
        (tmp_path / "lib.so").write_bytes(b"\x00" * 100)
        (tmp_path / "app.py").write_text("pass")
        traverser = FileTraverser(tmp_path)
        list(traverser.traverse())
        assert traverser.stats.skipped_blocked_extension >= 1

    def test_stats_lang_counts(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("pass")
        (tmp_path / "b.py").write_text("pass")
        (tmp_path / "c.ts").write_text("const x = 1;")
        traverser = FileTraverser(tmp_path)
        list(traverser.traverse())
        assert traverser.stats.lang_counts.get("python") == 2
        assert traverser.stats.lang_counts.get("typescript") == 1

    def test_stats_extra_exclude(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("pass")
        (tmp_path / "vendor").mkdir()
        (tmp_path / "vendor" / "lib.py").write_text("pass")
        traverser = FileTraverser(tmp_path, extra_exclude_patterns=["vendor/"])
        list(traverser.traverse())
        # vendor/ is pruned at directory level, not file level
        assert traverser.stats.included == 1


# ---------------------------------------------------------------------------
# Submodule handling
# ---------------------------------------------------------------------------


class TestSubmoduleHandling:
    def test_skips_submodule_dirs(self, tmp_path: Path) -> None:
        (tmp_path / ".gitmodules").write_text(
            '[submodule "libs/foo"]\n'
            "    path = libs/foo\n"
            "    url = https://github.com/example/foo.git\n"
        )
        (tmp_path / "libs" / "foo").mkdir(parents=True)
        (tmp_path / "libs" / "foo" / "main.py").write_text("pass")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("pass")
        traverser = FileTraverser(tmp_path)
        paths = [f.path for f in traverser.traverse()]
        assert any("app.py" in p for p in paths)
        assert not any("libs/foo" in p for p in paths)
        assert traverser.stats.skipped_submodule >= 1

    def test_include_submodules_flag(self, tmp_path: Path) -> None:
        (tmp_path / ".gitmodules").write_text(
            '[submodule "libs/foo"]\n'
            "    path = libs/foo\n"
            "    url = https://github.com/example/foo.git\n"
        )
        (tmp_path / "libs" / "foo").mkdir(parents=True)
        (tmp_path / "libs" / "foo" / "main.py").write_text("pass")
        traverser = FileTraverser(tmp_path, include_submodules=True)
        paths = [f.path for f in traverser.traverse()]
        assert any("libs/foo" in p for p in paths)

    def test_include_submodules_with_initialized_submodule(self, tmp_path: Path) -> None:
        """An *initialized* submodule carries a `.git` file — the nested-git
        boundary check must not override the explicit opt-in.

        Regression: ``include_submodules=True`` previously skipped parsing
        ``.gitmodules`` entirely, so initialized submodules fell through to
        the nested-git skip and were silently dropped anyway.
        """
        (tmp_path / ".gitmodules").write_text(
            '[submodule "libs/foo"]\n'
            "    path = libs/foo\n"
            "    url = https://github.com/example/foo.git\n"
        )
        (tmp_path / "libs" / "foo").mkdir(parents=True)
        (tmp_path / "libs" / "foo" / ".git").write_text("gitdir: ../../.git/modules/libs/foo\n")
        (tmp_path / "libs" / "foo" / "main.py").write_text("pass")
        traverser = FileTraverser(tmp_path, include_submodules=True)
        paths = [f.path for f in traverser.traverse()]
        assert any("libs/foo/main.py" in p for p in paths)
        assert traverser.stats.skipped_nested_repo == 0

    def test_initialized_submodule_skipped_by_default(self, tmp_path: Path) -> None:
        (tmp_path / ".gitmodules").write_text(
            '[submodule "libs/foo"]\n'
            "    path = libs/foo\n"
            "    url = https://github.com/example/foo.git\n"
        )
        (tmp_path / "libs" / "foo").mkdir(parents=True)
        (tmp_path / "libs" / "foo" / ".git").write_text("gitdir: ../../.git/modules/libs/foo\n")
        (tmp_path / "libs" / "foo" / "main.py").write_text("pass")
        traverser = FileTraverser(tmp_path)
        paths = [f.path for f in traverser.traverse()]
        assert not any("libs/foo" in p for p in paths)
        assert traverser.stats.skipped_submodule >= 1

    def test_include_submodules_keeps_other_nested_repos_skipped(self, tmp_path: Path) -> None:
        """The submodule opt-in must not widen to arbitrary nested repos."""
        (tmp_path / ".gitmodules").write_text(
            '[submodule "libs/foo"]\n'
            "    path = libs/foo\n"
            "    url = https://github.com/example/foo.git\n"
        )
        (tmp_path / "libs" / "foo").mkdir(parents=True)
        (tmp_path / "libs" / "foo" / ".git").write_text("gitdir: ../../.git/modules/libs/foo\n")
        (tmp_path / "libs" / "foo" / "main.py").write_text("pass")
        (tmp_path / "sibling_repo").mkdir()
        (tmp_path / "sibling_repo" / ".git").mkdir()
        (tmp_path / "sibling_repo" / "inner.py").write_text("pass")
        traverser = FileTraverser(tmp_path, include_submodules=True)
        paths = [f.path for f in traverser.traverse()]
        assert any("libs/foo/main.py" in p for p in paths)
        assert not any("sibling_repo" in p for p in paths)
        assert traverser.stats.skipped_nested_repo >= 1

    def test_no_gitmodules_file(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("pass")
        traverser = FileTraverser(tmp_path)
        paths = [f.path for f in traverser.traverse()]
        assert any("app.py" in p for p in paths)

    def test_multiple_submodules(self, tmp_path: Path) -> None:
        (tmp_path / ".gitmodules").write_text(
            '[submodule "libs/foo"]\n'
            "    path = libs/foo\n"
            "    url = https://github.com/example/foo.git\n"
            '[submodule "libs/bar"]\n'
            "    path = libs/bar\n"
            "    url = https://github.com/example/bar.git\n"
        )
        (tmp_path / "libs" / "foo").mkdir(parents=True)
        (tmp_path / "libs" / "foo" / "main.py").write_text("pass")
        (tmp_path / "libs" / "bar").mkdir(parents=True)
        (tmp_path / "libs" / "bar" / "index.ts").write_text("export const x = 1;")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("pass")
        traverser = FileTraverser(tmp_path)
        paths = [f.path for f in traverser.traverse()]
        assert any("app.py" in p for p in paths)
        assert not any("libs/foo" in p for p in paths)
        assert not any("libs/bar" in p for p in paths)


# ---------------------------------------------------------------------------
# Nested git repo handling
# ---------------------------------------------------------------------------


class TestNestedGitRepoHandling:
    """A parent repo may physically contain other independent git repos as
    subdirectories (common when a workspace root is itself versioned).
    Those subdirs must be treated as traversal boundaries — not walked into
    as if they were part of the parent's working tree.
    """

    def _make_repo(self, path: Path, gitdir_is_file: bool = False) -> None:
        path.mkdir(parents=True, exist_ok=True)
        git_marker = path / ".git"
        if gitdir_is_file:
            git_marker.write_text("gitdir: /elsewhere/.git\n")
        else:
            git_marker.mkdir()

    def test_skips_nested_git_repo_dir(self, tmp_path: Path) -> None:
        self._make_repo(tmp_path / "child_repo")
        (tmp_path / "child_repo" / "inner.py").write_text("pass")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("pass")

        traverser = FileTraverser(tmp_path)
        paths = [f.path for f in traverser.traverse()]

        assert any("app.py" in p for p in paths)
        assert not any("child_repo" in p for p in paths)
        assert traverser.stats.skipped_nested_repo >= 1

    def test_skips_nested_git_repo_when_gitdir_is_file(self, tmp_path: Path) -> None:
        # `.git` as a file (submodule / worktree / external gitdir) still
        # marks the directory as an independent repo and must be skipped.
        self._make_repo(tmp_path / "linked_repo", gitdir_is_file=True)
        (tmp_path / "linked_repo" / "inner.py").write_text("pass")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("pass")

        traverser = FileTraverser(tmp_path)
        paths = [f.path for f in traverser.traverse()]

        assert not any("linked_repo" in p for p in paths)
        assert traverser.stats.skipped_nested_repo >= 1

    def test_skips_multiple_nested_repos(self, tmp_path: Path) -> None:
        for name in ("backend", "frontend", "shared"):
            self._make_repo(tmp_path / name)
            (tmp_path / name / f"{name}.py").write_text("pass")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("pass")

        traverser = FileTraverser(tmp_path)
        paths = [f.path for f in traverser.traverse()]

        assert any("app.py" in p for p in paths)
        for name in ("backend", "frontend", "shared"):
            assert not any(name in p for p in paths)
        assert traverser.stats.skipped_nested_repo == 3

    def test_root_itself_being_a_git_repo_is_fine(self, tmp_path: Path) -> None:
        # The root .git must NOT cause the traverser to skip the root.
        (tmp_path / ".git").mkdir()
        (tmp_path / "app.py").write_text("pass")

        traverser = FileTraverser(tmp_path)
        paths = [f.path for f in traverser.traverse()]

        assert any("app.py" in p for p in paths)
        assert traverser.stats.skipped_nested_repo == 0

    def test_include_nested_repos_flag_opts_in(self, tmp_path: Path) -> None:
        self._make_repo(tmp_path / "child_repo")
        (tmp_path / "child_repo" / "inner.py").write_text("pass")

        traverser = FileTraverser(tmp_path, include_nested_repos=True)
        paths = [f.path for f in traverser.traverse()]

        assert any("inner.py" in p for p in paths)
        assert traverser.stats.skipped_nested_repo == 0

    def test_deeply_nested_repo_is_still_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "a" / "b" / "c").mkdir(parents=True)
        self._make_repo(tmp_path / "a" / "b" / "c" / "vendored")
        (tmp_path / "a" / "b" / "c" / "vendored" / "lib.py").write_text("pass")
        (tmp_path / "a" / "ok.py").write_text("pass")

        traverser = FileTraverser(tmp_path)
        paths = [f.path for f in traverser.traverse()]

        assert any("ok.py" in p for p in paths)
        assert not any("vendored" in p for p in paths)


# ---------------------------------------------------------------------------
# Entry-point flag (registry-derived conventions)
# ---------------------------------------------------------------------------


class TestEntryPointFlag:
    def _flagged(self, tmp_path: Path) -> set[str]:
        return {f.path for f in FileTraverser(tmp_path).traverse() if f.is_entry_point}

    def test_new_language_conventions_flag_entry_points(self, tmp_path: Path) -> None:
        files = {
            "src/Application.kt": "fun main() {}",
            "config.ru": "run App",
            "myapp/src/myapp_app.erl": "-module(myapp_app).",
            "lib/shop/application.ex": "defmodule Shop.Application do\nend",
            "shop/core.clj": "(defn -main [])",
            "cli/Program.fs": "[<EntryPoint>]\nlet main argv = 0",
            "artisan": "#!/usr/bin/env php\n<?php",
        }
        for rel, content in files.items():
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        flagged = self._flagged(tmp_path)
        assert flagged == set(files), flagged

    def test_historical_stem_parity_and_non_entries(self, tmp_path: Path) -> None:
        files = {
            "run.py": "print('x')",  # covered by the run stem (dropped pattern)
            "server.py": "print('x')",
            "pkg/helper.py": "x = 1",
            "latest_app.py": "x = 1",  # _app suffix is Erlang-only (*_app.erl)
        }
        for rel, content in files.items():
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        flagged = self._flagged(tmp_path)
        assert "run.py" in flagged
        assert "server.py" in flagged
        assert "pkg/helper.py" not in flagged
        assert "latest_app.py" not in flagged

    def test_candidacy_rejects_names_that_cannot_start_execution(self, tmp_path: Path) -> None:
        # Every one of these matches the traverser's name/stem conventions and
        # is still not where a reader enters the system. The flag is what
        # exempts a file from dead-code detection, so a name-only guess here is
        # a file nothing can ever report.
        files = {
            "index.html": "<html></html>",  # entry *filename*, non-code language
            "docs/guide/index.md": "# guide",  # entry stem, non-code language
            "pkg/resolvers/dotnet/index.ts": "export * from './x';",  # deep glue leaf
            "src/main.py": "print('x')",  # the control: a real entry
            "cli/index.ts": "export const x = 1;",  # glue near a package root survives
        }
        for rel, content in files.items():
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        assert self._flagged(tmp_path) == {"src/main.py", "cli/index.ts"}

    def test_stem_union_widens_the_flag_without_dropping_a_stem(self, tmp_path: Path) -> None:
        # The flag stems and the stems the wiki ranks by were kept by hand and
        # disagreed both ways. Union: neither side loses one it had.
        files = {
            "src/bootstrap.rb": "puts 1",  # ranking-only stem, now flagged
            "src/cli.go": "package main",
            "src/entry.ts": "export const x = 1;",
            "run.py": "print('x')",  # flag-only stems must survive
            "start.js": "console.log(1);",
        }
        for rel, content in files.items():
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        assert self._flagged(tmp_path) == set(files)

    def test_pyproject_console_scripts_flag_entry_modules(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            "[project]\n"
            'name = "demo"\n'
            "[project.scripts]\n"
            'demo-hook = "demo.cli.hook:main"\n'
            "[project.entry-points.plugins]\n"
            'demo-plugin = "demo.plugins:register"\n'
        )
        files = {
            "src/demo/cli/hook.py": "def main(): pass",
            "src/demo/plugins/__init__.py": "def register(): pass",
            "src/demo/cli/other.py": "x = 1",
        }
        for rel, content in files.items():
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        flagged = self._flagged(tmp_path)
        assert "src/demo/cli/hook.py" in flagged
        assert "src/demo/plugins/__init__.py" in flagged
        assert "src/demo/cli/other.py" not in flagged

    def test_a_console_script_target_outranks_the_candidacy_rule(self, tmp_path: Path) -> None:
        # ``pyproject.toml`` *names* the module a launcher imports, so it is
        # evidence, not a guess about the filename — the glue-leaf rule must
        # not overrule it. Same reason the post-traversal stampers
        # (graph_warmups / framework_edges) are left alone.
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\n[project.scripts]\ndemo = "demo.sub.index:main"\n'
        )
        p = tmp_path / "src" / "demo" / "sub" / "index.py"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("def main(): pass")
        assert "src/demo/sub/index.py" in self._flagged(tmp_path)

    def test_single_segment_script_target_matches_exactly(self, tmp_path: Path) -> None:
        # A bare ``main`` target must not suffix-match every ``.../main.py``.
        # The stem must be one the name conventions do *not* claim, or both
        # files flag for that reason and the test proves nothing — ``cli`` was
        # such a stem until it joined the flag set.
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\n[project.scripts]\ndemo = "tool:main"\n'
        )
        (tmp_path / "tool.py").write_text("def main(): pass")
        nested = tmp_path / "pkg" / "tool.py"
        nested.parent.mkdir(parents=True)
        nested.write_text("x = 1")
        flagged = self._flagged(tmp_path)
        assert "tool.py" in flagged
        assert "pkg/tool.py" not in flagged


# ---------------------------------------------------------------------------
# Test-file classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel_path",
    [
        "tests/conftest.py",
        "tests/type_check/typing_app.py",
        "packages/ui/__tests__/brand.test.ts",
        "packages/ui/__tests__/chat/panel.tsx",
        "src/components/Button.test.tsx",
        "src/lib/parse.spec.ts",
        "test/fixtures/app.py",
        "spec/support/helper.rb",
        "src/pkg/tests/helpers.py",
        "pkg/test_thing.py",
        "pkg/thing_test.go",
    ],
)
def test_test_files_are_recognised_including_root_level_suites(rel_path):
    """Directory and filename conventions across languages, not just Python.

    Two gaps, both of which let test files into the concept tree. ``"/tests/"
    in path`` needs a slash on both sides, so a root-level ``tests/`` — the
    usual layout — read as production code. And the stem rule knew only
    ``test_x`` / ``x_test``, so the whole JavaScript convention
    (``__tests__/`` directories, ``x.test.ts`` filenames) was invisible:
    measured on this repository, 122 test files survived into the production
    set and five concept pages were nothing but tests.
    """
    from repowise.core.test_paths import is_test_related_path

    assert is_test_related_path(rel_path)


@pytest.mark.parametrize(
    "rel_path",
    [
        "src/latest/api.py",
        "src/contest/rules.py",
        "protests/model.py",
        "src/testing_utils.py",
    ],
)
def test_production_paths_that_merely_contain_the_word_are_not_tests(rel_path):
    """Segment match, not substring: ``contest`` and ``latest`` are not tests."""
    from repowise.core.test_paths import is_test_related_path

    assert not is_test_related_path(rel_path)


class TestUnknownLanguageFileRecording:
    """Paths kept when language detection fails, for the dead-code clamp.

    The clamp reads files ingestion never parsed and refuses to call a symbol
    deletion-ready when an unread file names it. It could never be offered a
    file without a language spec, because the walk bumped a counter and threw
    the path away.
    """

    def test_records_a_reference_bearing_file(self, tmp_path: Path) -> None:
        (tmp_path / "guide.rst").write_text("literalinclude:: ../src/widget.py\n")
        traverser = FileTraverser(tmp_path)
        paths = [f.path for f in traverser.traverse()]

        # Still not indexed — recording a path registers no language.
        assert not any("guide.rst" in p for p in paths)
        assert traverser.stats.skipped_unknown_language == 1
        assert [s.path for s in traverser.stats.unknown_language_files] == ["guide.rst"]
        assert traverser.stats.unknown_language_files[0].reason == "unknown_language"

    def test_ignores_formats_that_name_no_code(self, tmp_path: Path) -> None:
        # The tail is dominated by these, and a name-matching clamp fed prose
        # suppresses findings on coincidence rather than on evidence.
        (tmp_path / "django.po").write_text('msgid "Widget"\nmsgstr ""\n')
        (tmp_path / "LICENSE.txt").write_text("Redistribution of Widget is permitted.\n")
        traverser = FileTraverser(tmp_path)
        list(traverser.traverse())

        assert traverser.stats.skipped_unknown_language == 2
        assert traverser.stats.unknown_language_files == []

    def test_does_not_consume_the_size_skip_budget(self, tmp_path: Path) -> None:
        # The two lists are separate so hundreds of unparsed docs cannot
        # truncate the oversized-source records, which are shown to the user.
        for i in range(traverser_mod._MAX_SKIPPED_SOURCE_PATHS + 5):
            (tmp_path / f"doc{i}.rst").write_text("text\n")
        big = tmp_path / "huge.py"
        big.write_bytes(b"x = 1\n" * 500_000)  # over the source ceiling

        traverser = FileTraverser(tmp_path, max_file_size_kb=500)
        list(traverser.traverse())

        assert [s.path for s in traverser.stats.skipped_source_files] == ["huge.py"]
        assert traverser.stats.skipped_source_files_truncated is False
        assert len(traverser.stats.unknown_language_files) == 55

    def test_caps_the_list_and_flags_truncation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(traverser_mod, "_MAX_UNKNOWN_LANGUAGE_PATHS", 3)
        for i in range(6):
            (tmp_path / f"doc{i}.rst").write_text("text\n")

        traverser = FileTraverser(tmp_path)
        list(traverser.traverse())

        assert len(traverser.stats.unknown_language_files) == 3
        assert traverser.stats.unknown_language_files_truncated is True
        # The count stays exact whatever the cap does to the names.
        assert traverser.stats.skipped_unknown_language == 6


class TestIsGenerated:
    """The generated-file banner check must not match ordinary prose.

    ``AUTO-GENERATED`` (and the other markers) occur naturally in docblocks
    that explain a routing or build convention. A banner is a header
    convention — it sits on the first line or two — so a marker buried in a
    paragraph must not drop a hand-written file from the index.
    """

    def test_banner_on_first_line_is_generated(self, tmp_path: Path) -> None:
        p = tmp_path / "gen.ts"
        p.write_text("// Code generated by protoc. DO NOT EDIT.\n\nexport const x = 1;\n")
        assert _is_generated(p) is True

    def test_marker_on_second_line_is_generated(self, tmp_path: Path) -> None:
        p = tmp_path / "gen.py"
        p.write_text("#!/usr/bin/env python\n# AUTO-GENERATED — do not edit.\n\nx = 1\n")
        assert _is_generated(p) is True

    def test_marker_buried_in_prose_is_not_generated(self, tmp_path: Path) -> None:
        # The exact false positive from the issue: a hand-written endpoint
        # whose docblock mentions the framework's auto-generated routes.
        p = tmp_path / "handwritten.ts"
        p.write_text(
            "/**\n"
            " * Widget endpoint.\n"
            " * Uses a /widget-ops/ prefix to avoid colliding with the\n"
            " * framework's auto-generated collection routes.\n"
            " */\n"
            "export function widgetEndpoint() { return 1 }\n"
        )
        assert _is_generated(p) is False

    def test_generated_suffix_still_wins(self, tmp_path: Path) -> None:
        p = tmp_path / "api_pb2.py"
        p.write_text("x = 1\n")
        assert _is_generated(p) is True
