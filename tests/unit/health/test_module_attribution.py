"""``module`` is the package boundary, and every write path agrees on it.

``module`` used to be ``community_label or top_level_dir``. Two namespaces in
one field, and only the full-index path ever supplied the community map — the
incremental, re-score and ``repowise health`` paths did not — so a file's module
depended on which code path last wrote its row. Measured on this repo before the
change: 1,355 of 3,263 files reported a module they do not live in.
"""

from __future__ import annotations

from repowise.core.analysis.health.engine import (
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


def test_the_file_list_fallback_only_sees_manifests_the_traverser_emits():
    """Why the file list is a fallback and the disk scan is the real answer.

    The analyzer's ``parsed_files`` only carry manifests the traverser could
    language-detect, and it drops 18 of them — ``go.mod``, ``pom.xml``,
    ``build.gradle``, ``Gemfile``, ``build.sbt`` among them. Deriving roots
    from that list therefore cannot see a Go, Maven, Groovy-Gradle, Ruby or
    Scala package, which is exactly the gap ``scan_package_roots`` closes.
    """
    from repowise.core.ingestion.package_roots import package_manifest_names

    dropped = {"go.mod", "pom.xml", "build.gradle", "Gemfile", "build.sbt"}
    # They are package manifests...
    assert dropped <= package_manifest_names()
    # ...but a file list can only offer what the traverser emitted, and these
    # never arrive, so the fallback yields nothing for them.
    assert _package_roots({f"svc/{name}" for name in dropped}) == {"svc"}
    assert _package_roots({"svc/main.go", "libs/Main.java"}) == set()


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
    for path, top_level in (
        ("app/routers/files.py", "app"),
        ("src/components/x.tsx", "src"),
        ("tests/unit/a.py", "tests"),
    ):
        assert _module_for(path, set()) == top_level


def test_root_level_files_have_no_module():
    """``None``, not ``""`` — the rollup must not grow a phantom empty bucket."""
    assert _module_for("README.md", {"packages/core"}) is None
    assert _module_for("Makefile", set()) is None


def test_windows_separators_resolve_to_the_same_module():
    roots = {"packages/core"}
    assert _module_for("packages\\core\\src\\thing.py", roots) == "packages/core"


def _analyze(tmp_path, *, community_label_map):
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
    report = HealthAnalyzer(
        None, parsed_files=parsed, community_label_map=community_label_map
    ).analyze()
    return {m.file_path: m.module for m in report.metrics}


def test_the_engine_wires_real_package_roots_through_to_the_metric(tmp_path):
    """End to end, not just the helper: the stored ``module`` is the package.

    The helpers can be right while the call site still reads the old value, so
    this asserts on what the analyzer actually writes.
    """
    modules = _analyze(tmp_path, community_label_map=None)
    assert modules["packages/core/thing.py"] == "packages/core"
    assert modules["packages/ui/widget.py"] == "packages/ui"


def test_the_engine_reads_package_roots_off_disk_not_the_file_list(tmp_path):
    """The case the whole change exists for, and the only one that proves it.

    A ``pyproject.toml`` fixture cannot: the traverser emits it, so the file
    list and the disk scan agree and the analyzer looks correct either way.
    ``go.mod`` is dropped as a file, so its package is visible only to the
    scan — if the engine falls back to ``parsed_files`` this returns
    ``services``, the top-level container, which is the bug.
    """
    from repowise.core.analysis.health import HealthAnalyzer
    from repowise.core.ingestion.parser import parse_file
    from repowise.core.ingestion.traverser import FileTraverser

    for rel, body in (
        ("services/api/go.mod", "module example.com/api\n\ngo 1.22\n"),
        ("services/api/main.go", "package main\n\nfunc main() {}\n"),
        # Nested module inside another: the deepest-first loop's reason to exist.
        ("services/api/internal/tools/go.mod", "module example.com/api/tools\n"),
        ("services/api/internal/tools/tool.go", "package tools\n\nfunc T() {}\n"),
        # No manifest anywhere above it -> top-level fallback, unchanged.
        ("scripts/deploy.py", "def deploy():\n    return 1\n"),
    ):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    parsed = [
        parse_file(f, (tmp_path / f.path).read_bytes()) for f in FileTraverser(tmp_path).traverse()
    ]
    report = HealthAnalyzer(None, parsed_files=parsed, repo_root=tmp_path).analyze()
    modules = {m.file_path: m.module for m in report.metrics}

    assert modules["services/api/main.go"] == "services/api"
    assert modules["services/api/internal/tools/tool.go"] == "services/api/internal/tools"
    assert modules["scripts/deploy.py"] == "scripts"


def test_without_a_repo_root_the_engine_still_produces_the_old_answer(tmp_path):
    """The fallback is a degradation, not a failure.

    In-memory callers and hosted paths that cannot supply a checkout must keep
    working; they just cannot see a manifest the traverser dropped.
    """
    from repowise.core.analysis.health import HealthAnalyzer
    from repowise.core.ingestion.parser import parse_file
    from repowise.core.ingestion.traverser import FileTraverser

    for rel, body in (
        ("services/api/go.mod", "module example.com/api\n"),
        ("services/api/main.go", "package main\n\nfunc main() {}\n"),
    ):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    parsed = [
        parse_file(f, (tmp_path / f.path).read_bytes()) for f in FileTraverser(tmp_path).traverse()
    ]
    report = HealthAnalyzer(None, parsed_files=parsed).analyze()
    modules = {m.file_path: m.module for m in report.metrics}

    assert modules["services/api/main.go"] == "services"


def test_every_analyzer_call_site_supplies_a_repo_root():
    """The convergence claim rests on this, and nothing else asserts it.

    ``module`` is written by four paths — full index, incremental update,
    ``_rescore_health_from_db`` and ``repowise health`` — and corrected by a
    fifth, the ``repowise update`` repair. They agree only because all five
    scan the same repo off disk. Drop ``repo_root`` at one analyzer call site
    and that path silently reverts to the file-list fallback: on a Go monorepo
    it writes ``services`` while the repair writes ``services/api``, so every
    update from then on reports files corrected and the two never settle.

    Asserted on the call sites because the failure is an *omitted* keyword —
    there is no value to observe, and a test that constructs the analyzer
    directly (as the rest of this file does) cannot see the omission at all.
    """
    import ast
    from pathlib import Path as _Path

    import repowise.cli.commands.health_cmd.command as health_cmd
    import repowise.cli.commands.update_cmd.persistence as rescore
    import repowise.core.pipeline.incremental as incremental
    import repowise.core.pipeline.phases.analysis as full_index

    for module in (full_index, incremental, rescore, health_cmd):
        source = _Path(module.__file__).read_text(encoding="utf-8")
        calls = [
            node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "HealthAnalyzer"
        ]
        assert calls, f"no HealthAnalyzer call found in {module.__name__}"
        for call in calls:
            kwargs = {kw.arg for kw in call.keywords}
            assert "repo_root" in kwargs, f"{module.__name__}:{call.lineno} omits repo_root"


def test_a_community_map_can_no_longer_change_the_answer(tmp_path):
    """The regression that motivated this, as an executable invariant.

    Only the full-index path ever passed a ``community_label_map``; the incremental,
    re-score and ``repowise health`` paths did not, so a row's namespace
    depended on which one last wrote it. Supplying a hostile map must now be
    inert — this is what makes the four paths agree.
    """
    poisoned = {
        "packages/core/thing.py": "tests/unit",
        "packages/ui/widget.py": "repowise/distill (7)",
    }
    assert _analyze(tmp_path, community_label_map=poisoned) == _analyze(
        tmp_path, community_label_map=None
    )


def test_module_is_always_a_path_never_a_community_label():
    """Why the dashboard no longer strips a `` (N)`` suffix off ``module``.

    Community detection appends that suffix when two clusters collide on a
    label, and the rollups used to sanitize it because ``module`` once carried
    those labels. ``_module_for`` returns a path segment or ``None``, so the
    suffix cannot reach the column and the sanitizer had nothing left to strip.
    """
    roots = {"packages/core", "packages/ui"}
    for path in (
        "packages/core/src/thing.py",
        "packages/ui/src/thing.ts",
        "docs/guide.md",
        "README.md",
    ):
        module = _module_for(path, roots)
        assert module is None or path.startswith(module + "/")
