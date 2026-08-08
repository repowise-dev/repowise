"""``module`` is the package boundary, and every write path agrees on it.

``module`` used to be ``community_label or top_level_dir``. Two namespaces in
one field, and only the full-index path ever supplied the community map — the
incremental, re-score and ``repowise health`` paths did not — so a file's module
depended on which code path last wrote its row. Measured on this repo before the
change: 1,355 of 3,263 files reported a module they do not live in.
"""

from __future__ import annotations

from repowise.core.analysis.health.engine import (
    _PACKAGE_MANIFESTS,
    _fallback_module,
    _module_for,
    _package_roots,
)


def test_package_roots_are_read_from_manifests_in_the_file_list():
    roots = _package_roots(
        {
            "packages/core/pyproject.toml",
            "packages/core/src/thing.py",
            "packages/ui/package.json",
            "packages/ui/src/thing.ts",
            "docs/guide.md",
        }
    )
    assert roots == {"packages/core", "packages/ui"}


def test_every_listed_manifest_is_one_the_traverser_actually_emits():
    """The list is bounded by what reaches ``parsed_files``, not by what exists.

    The analyzer only ever sees traversed files, and the traverser drops any
    file whose language it cannot detect — ``go.mod``, ``pom.xml``,
    ``build.gradle``, ``Gemfile`` and ``setup.cfg`` among them. Listing those
    would be dead entries that read as support for Go, Maven and Ruby
    monorepos. This pins the constraint so the list cannot quietly grow past it.
    """
    import tempfile
    from pathlib import Path

    from repowise.core.ingestion.traverser import FileTraverser

    root = Path(tempfile.mkdtemp())
    for i, name in enumerate(sorted(_PACKAGE_MANIFESTS)):
        pkg = root / f"pkg{i}"
        pkg.mkdir()
        (pkg / name).write_text("{}\n" if name.endswith("json") else "x = 1\n", encoding="utf-8")

    emitted = {p.path.replace("\\", "/").rsplit("/", 1)[-1] for p in FileTraverser(root).traverse()}
    assert emitted >= _PACKAGE_MANIFESTS, sorted(_PACKAGE_MANIFESTS - emitted)


def test_a_repo_root_manifest_is_not_a_package_root():
    """Otherwise every file in a single-package repo lands in one bucket.

    That is the degenerate case the package-root rule exists to avoid, and it
    is the common shape: a plain app repo with one ``package.json`` at the top.
    """
    assert _package_roots({"package.json", "src/a.ts", "src/b.ts"}) == set()


def test_module_is_the_package_not_the_top_level_directory():
    """The monorepo case. ``packages`` is a container, not a module.

    Taking the first path segment put 69% of this repo in one bucket, which is
    what made per-module averages and ``module:`` targeting useless here.
    """
    roots = {"packages/core", "packages/ui"}
    assert _module_for("packages/core/src/deep/nested/thing.py", roots) == "packages/core"
    assert _module_for("packages/ui/src/thing.ts", roots) == "packages/ui"


def test_the_deepest_enclosing_package_wins():
    roots = {"packages/core", "packages/core/vendor/inner"}
    assert _module_for("packages/core/vendor/inner/x.py", roots) == "packages/core/vendor/inner"
    assert _module_for("packages/core/src/x.py", roots) == "packages/core"


def test_a_file_outside_every_package_falls_back_to_the_top_level_directory():
    roots = {"packages/core"}
    assert _module_for("tests/unit/test_x.py", roots) == "tests"
    assert _module_for("docs/guide.md", roots) == "docs"


def test_no_manifests_anywhere_is_exactly_the_old_behaviour():
    """Repos with no nested packages must not move.

    Measured on the two sibling repos in this workspace (a FastAPI backend and
    a Next.js frontend): zero nested manifests, so both keep the attribution
    they already had.
    """
    for path in ("app/routers/files.py", "src/components/x.tsx", "tests/unit/a.py"):
        assert _module_for(path, set()) == _fallback_module(path)


def test_root_level_files_have_no_module():
    """``None``, not ``""`` — the rollup must not grow a phantom empty bucket."""
    assert _module_for("README.md", {"packages/core"}) is None
    assert _module_for("Makefile", set()) is None


def test_windows_separators_resolve_to_the_same_module():
    roots = {"packages/core"}
    assert _module_for("packages\\core\\src\\thing.py", roots) == "packages/core"


def _analyze(tmp_path, *, module_map):
    """Run the real analyzer over a two-package tree and return {path: module}."""
    from repowise.core.analysis.health import HealthAnalyzer
    from repowise.core.ingestion.parser import parse_file
    from repowise.core.ingestion.traverser import FileTraverser

    (tmp_path / "packages" / "core").mkdir(parents=True, exist_ok=True)
    (tmp_path / "packages" / "ui").mkdir(parents=True, exist_ok=True)
    (tmp_path / "packages" / "core" / "pyproject.toml").write_text(
        '[project]\nname = "core"\n', encoding="utf-8"
    )
    (tmp_path / "packages" / "core" / "thing.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "packages" / "ui" / "pyproject.toml").write_text(
        '[project]\nname = "ui"\n', encoding="utf-8"
    )
    (tmp_path / "packages" / "ui" / "widget.py").write_text("b = 2\n", encoding="utf-8")

    parsed = [
        parse_file(f, (tmp_path / f.path).read_bytes())
        for f in FileTraverser(tmp_path).traverse()
    ]
    report = HealthAnalyzer(None, parsed_files=parsed, module_map=module_map).analyze()
    return {m.file_path: m.module for m in report.metrics}


def test_the_engine_wires_real_package_roots_through_to_the_metric(tmp_path):
    """End to end, not just the helper: the stored ``module`` is the package.

    The helpers can be right while the call site still reads the old value, so
    this asserts on what the analyzer actually writes.
    """
    modules = _analyze(tmp_path, module_map=None)
    assert modules["packages/core/thing.py"] == "packages/core"
    assert modules["packages/ui/widget.py"] == "packages/ui"


def test_a_community_map_can_no_longer_change_the_answer(tmp_path):
    """The regression that motivated this, as an executable invariant.

    Only the full-index path ever passed a ``module_map``; the incremental,
    re-score and ``repowise health`` paths did not, so a row's namespace
    depended on which one last wrote it. Supplying a hostile map must now be
    inert — this is what makes the four paths agree.
    """
    poisoned = {
        "packages/core/thing.py": "tests/unit",
        "packages/ui/widget.py": "repowise/distill (7)",
    }
    assert _analyze(tmp_path, module_map=poisoned) == _analyze(tmp_path, module_map=None)
