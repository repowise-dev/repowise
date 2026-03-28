"""Unit tests for FileTraverser."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion.traverser import FileTraverser, _detect_language

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

    def test_skips_oversized_files(self, tmp_path: Path) -> None:
        big = tmp_path / "big.py"
        big.write_bytes(b"x = 1\n" * 200_000)  # ~1.2 MB
        traverser = FileTraverser(tmp_path, max_file_size_kb=500)
        paths = [f.path for f in traverser.traverse()]
        assert not any("big.py" in p for p in paths)

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
