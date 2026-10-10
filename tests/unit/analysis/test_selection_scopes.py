"""Scopes: what a manifest, a package ``__init__.py`` or an asset runs instead of every test."""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from repowise.core.analysis.selection_scopes import (
    ECOSYSTEMS,
    MAX_NAMERS,
    needs_namers,
    trigger_scopes,
)
from repowise.core.analysis.test_selection import (
    SelectionInput,
    TestSelectionConfig,
    file_namers,
    full_run_reason,
    select_tests,
)

_NONE = TestSelectionConfig()
_TRACKED = (
    "package.json",
    "pyproject.toml",
    "web/package.json",
    "web/src/app.ts",
    "web/src/app.test.ts",
    "web/src/styles/layout.css",
    "web/e2e/login.spec.ts",
    "svc/go.mod",
    "svc/main.go",
    "svc/main_test.go",
    "src/pkg/__init__.py",
    "src/pkg/core.py",
    "src/pkg/sub/deep.py",
    "src/other.py",
    "tests/test_core.py",
    "tests/fixtures/package.json",
    "crate/Cargo.toml",
    "assets/logo.svg",
)
_TESTS = ["web/src/app.test.ts", "web/e2e/login.spec.ts", "svc/main_test.go", "tests/test_core.py"]


def _scopes(paths, namers=None, config=_NONE):
    return trigger_scopes(paths, _TRACKED, namers or {}, config)


def _tiers(*rows, importers=None):
    inferred = [{"source_file": s, "test_file": t, "via": "import-graph"} for s, t in rows]
    return {"covered": {}, "inferred": inferred, "unknown": [], "helper_importers": importers or {}}


def _select(paths, tiers=None, namers=None, **kwargs):
    kwargs.setdefault("known_tests", _TESTS)
    inp = SelectionInput(paths, (), tiers or _tiers(), _NONE, scopes=_scopes(paths, namers), **kwargs)
    return select_tests(inp)


def test_every_ecosystem_file_is_also_a_full_run_trigger() -> None:
    """A file no scope covers (in a test tree, say) must still run everything."""
    for _, _, _, globs in ECOSYSTEMS:
        for glob in globs:
            sample = glob.replace("**/", "").replace("**", "x.txt").replace("*", "x")
            for path in (sample, f"deep/dir/{sample}"):
                assert full_run_reason(path), path


@pytest.mark.parametrize(
    ("path", "root", "suffix", "reach"),
    [
        ("web/package.json", "web", ".ts", ("web/src/app.ts",)),
        ("web/package-lock.json", "web", ".ts", ("web/src/app.ts",)),
        ("package.json", ".", ".ts", ()),
        ("web/tsconfig.json", ".", ".ts", ()),
        ("pnpm-workspace.yaml", ".", ".ts", ()),
        (".nvmrc", ".", ".js", ()),
        ("uv.lock", ".", ".py", ()),
        ("services/api/pyproject.toml", ".", ".py", ()),
        (".python-version", ".", ".py", ()),
        ("requirements/base.txt", ".", ".py", ()),
        ("services/api/deps/constraints-3.txt", ".", ".py", ()),
        ("requirements-dev.in", ".", ".py", ()),
        ("services/api/requirements/test.in", ".", ".py", ()),
        ("svc/go.sum", "svc", ".go", ("svc/main.go",)),
        ("go.work", ".", ".go", ()),
        ("Gemfile.lock", ".", ".rb", ()),
        ("app/build.gradle.kts", ".", ".kt", ()),
    ],
)
def test_a_manifest_or_lockfile_scopes_to_its_ecosystem(path, root, suffix, reach) -> None:
    scope = _scopes([path])[path]
    assert (scope.basis, scope.root, scope.reach) == ("ecosystem", root, reach)
    assert suffix in scope.suffixes


@pytest.mark.parametrize(
    "path",
    [
        "crate/Cargo.toml",
        "Cargo.lock",
        "Makefile",
        "Dockerfile",
        ".tool-versions",
        ".github/workflows/ci.yml",
        "tests/fixtures/package.json",
        "tests/data/pyproject.toml",
        "__init__.py",
        # Tool configuration, not an asset any code reads.
        ".env",
        ".editorconfig",
        "web/.eslintrc.json",
        "renovate.json",
        "ruff.toml",
        "web/config.toml",
        "scripts/release.sh",
        "web/deps/pins.txt",
    ],
)
def test_what_can_change_any_test_keeps_its_full_run(path) -> None:
    assert path not in _scopes([path], {path: ["web/src/app.ts"]})


@pytest.mark.parametrize("path", ["web/src/data/users.json", "web/src/queries/find.scm"])
def test_a_data_or_query_file_nothing_names_keeps_its_full_run(path) -> None:
    """A test anywhere may glob it, so its package's tests are not enough."""
    assert _scopes([path]) == {}
    assert _scopes([path], {path: ["web/src/app.ts"]})[path].basis == "named-by"


def test_a_configured_full_run_trigger_is_never_scoped() -> None:
    config = TestSelectionConfig(full_run_on=("web/package.json",))
    assert _scopes(["web/package.json"], config=config) == {}
    assert not needs_namers("web/package.json", config)


def test_only_scopable_files_ask_who_names_them() -> None:
    asked = ["web/package.json", "docs/guide.md", "web/src/styles/layout.css", "a/b.json"]
    assert all(needs_namers(p, _NONE) for p in asked)
    for path in ["src/a.py", ".env", "Makefile", "renovate.json", "tests/data/x.json"]:
        assert not needs_namers(path, _NONE), path


def test_a_production_package_init_scopes_to_the_modules_under_it() -> None:
    scope = _scopes(["src/pkg/__init__.py"])["src/pkg/__init__.py"]
    assert (scope.basis, scope.root) == ("package-importers", "src/pkg")
    assert scope.reach == ("src/pkg/__init__.py", "src/pkg/core.py", "src/pkg/sub/deep.py")


def test_an_asset_runs_what_names_it_or_its_package() -> None:
    css = "web/src/styles/layout.css"
    named = _scopes([css], {css: ["web/src/app.ts"]})[css]
    assert (named.basis, named.root, named.namers) == ("named-by", None, ("web/src/app.ts",))
    # A name written only outside the package may be another file's.
    elsewhere = _scopes([css], {css: ["src/other.py"]})[css]
    assert (elsewhere.basis, elsewhere.root) == ("owner-package", "web")
    assert elsewhere.namers == ("src/other.py",) and elsewhere.reach == ("web/src/app.ts",)
    owned = _scopes([css])[css]
    assert (owned.basis, owned.root, owned.suffixes) == ("owner-package", "web", ())
    # The repository root is no package; a doc no code names needs nothing.
    assert _scopes(["assets/logo.svg", "docs/guide.md"]) == {}
    changelog = _scopes(["CHANGELOG.md"], {"CHANGELOG.md": ["scripts/lanes.mjs"]})
    assert changelog["CHANGELOG.md"].namers == ("scripts/lanes.mjs",)
    # A doctest glob makes the doc a test.
    assert _scopes(["README.md"], {"README.md": ["pytest.ini"]}) == {}


def test_a_query_file_runs_the_tests_of_the_spec_naming_it() -> None:
    query = "src/pkg/queries/python.scm"
    scope = _scopes([query], {query: ["src/pkg/specs/python.py"]})[query]
    assert (scope.basis, scope.namers) == ("named-by", ("src/pkg/specs/python.py",))


def test_a_file_named_by_too_many_files_runs_everything() -> None:
    namers = [f"src/m{i}.py" for i in range(MAX_NAMERS + 1)]
    for path in ["web/src/styles/layout.css", "web/package.json"]:
        sel = _select([path], namers={path: namers})
        assert sel.run_all
        assert sel.reasons[0] == (
            f"{path} changed: {MAX_NAMERS + 1} non-test files name it, more than the "
            f"{MAX_NAMERS} worth tracing."
        )
    # Tests naming it are selected as they stand and do not count.
    tests = [f"tests/test_m{i}.py" for i in range(MAX_NAMERS + 1)]
    assert _scopes(["web/package.json"], {"web/package.json": tests})["web/package.json"].run_all is None


def test_namers_an_ecosystem_scope_already_runs_do_not_count() -> None:
    """A root manifest runs every JS test, so JS code naming it adds nothing to trace."""
    js = [f"web/src/m{i}.ts" for i in range(MAX_NAMERS + 5)]
    scope = _scopes(["package.json"], {"package.json": [*js, "tests/test_versions.py"]})
    assert scope["package.json"].run_all is None
    assert scope["package.json"].namers == ("tests/test_versions.py",)
    # Under a nested manifest, only its own directory is held.
    nested = _scopes(["web/package.json"], {"web/package.json": ["web/src/app.ts", "ui/x.ts"]})
    assert nested["web/package.json"].namers == ("ui/x.ts",)
    helper = _scopes(["web/package.json"], {"web/package.json": ["web/test/helpers/fake.ts"]})
    assert helper["web/package.json"].namers == ("web/test/helpers/fake.ts",)


def test_file_namers_lists_every_file_naming_one() -> None:
    sources = [
        ("web/src/app.ts", 'import "./styles/layout.css";\n'),
        ("src/other.py", 'CSS = "layout.css"\n'),
        ("pyproject.toml", 'x = "layout.css"\n'),
        ("web/src/more.ts", '"layout.css"\n'),
    ]
    files = ["web/src/styles/layout.css", "other/layout.css"]
    # An asset may be included by another asset, so its name alone names it.
    assert file_namers(files, sources)["other/layout.css"] == [
        "web/src/app.ts",
        "src/other.py",
        "web/src/more.ts",
    ]
    # A manifest is named only where a written directory can lead to it.
    manifests = ["web/package.json", "other/package.json"]
    code = [("web/src/app.ts", "import p from '../package.json'"), ("x.py", '"package.json"')]
    exact = file_namers(manifests, code, lambda f: f.endswith("package.json"))
    assert exact == {
        "web/package.json": ["web/src/app.ts", "x.py"],
        "other/package.json": ["x.py"],
    }


def test_a_js_package_manifest_runs_its_tests_and_those_reaching_its_code() -> None:
    sel = _select(["web/package.json"], _tiers(("web/src/app.ts", "tests/test_core.py")))
    assert not sel.run_all, sel.reasons
    assert sel.test_files == ("web/src/app.test.ts", "web/e2e/login.spec.ts", "tests/test_core.py")
    assert sel.basis == {"web/package.json": "ecosystem"}
    assert sel.why["web/e2e/login.spec.ts"].startswith("web/package.json changed (ecosystem: ")
    assert sel.reasons[0].startswith("web/package.json changed: it can change the JavaScript")


def test_a_test_reading_a_manifest_as_data_runs_with_its_ecosystem() -> None:
    reader = "tests/test_versions.py"
    sel = _select(["package.json"], _tiers((reader, reader)), namers={"package.json": [reader]})
    assert not sel.run_all, sel.reasons
    assert sel.test_files == ("web/src/app.test.ts", "web/e2e/login.spec.ts", reader)


def test_a_python_lockfile_runs_only_python_tests_and_needs_no_graph() -> None:
    sel = _select(["uv.lock"], index_available=False)
    assert not sel.run_all, sel.reasons
    assert sel.test_files == ("tests/test_core.py",)


def test_a_scope_with_no_test_runs_everything() -> None:
    sel = _select(["svc/go.mod"], known_tests=[])
    assert sel.run_all
    assert sel.reasons[0].startswith("svc/go.mod changed: it can change the Go tests under svc/")
    assert sel.reasons[0].endswith("and no such test is known.")


def test_a_namer_no_test_reaches_runs_everything() -> None:
    css = "web/src/styles/layout.css"
    sel = _select([css], namers={css: ["web/src/app.ts"]})
    assert sel.run_all
    assert sel.reasons[0] == f"{css} is named by web/src/app.ts, and no test is known to reach it."
    reached = _select(
        [css], _tiers(("web/src/app.ts", "web/src/app.test.ts")), namers={css: ["web/src/app.ts"]}
    )
    assert not reached.run_all and reached.test_files == ("web/src/app.test.ts",)
    assert reached.basis == {css: "named-by"}


def test_a_changed_package_init_runs_the_tests_that_import_the_package_itself() -> None:
    """``import pkg`` reaches only ``__init__.py``; its rows come from the change's own walk."""
    tiers = _tiers(("src/pkg/__init__.py", "tests/test_imports_pkg.py"), ("src/pkg/core.py", "tests/test_core.py"))
    sel = _select(["src/pkg/__init__.py"], tiers)
    assert not sel.run_all, sel.reasons
    assert sel.test_files == ("tests/test_imports_pkg.py", "tests/test_core.py")


def test_a_route_through_an_unimported_helper_still_runs_everything() -> None:
    sel = _select(["src/pkg/__init__.py"], _tiers(("src/pkg/core.py", "tests/helpers.py")))
    assert sel.run_all
    assert sel.reasons[0].startswith(
        "src/pkg/__init__.py changed; src/pkg/core.py is reached through the test helper"
    )


def test_a_route_shared_by_files_is_decided_once() -> None:
    css, svg = "web/src/styles/layout.css", "web/src/styles/icon.svg"
    tiers = _tiers(("web/src/app.ts", "tests/helpers.py"))
    sel = _select([css, svg], tiers, namers={css: ["web/src/app.ts"], svg: ["web/src/app.ts"]})
    helper = [r for r in sel.reasons if "tests/helpers.py" in r]
    assert len(helper) == 2 and {r.split()[0] for r in helper} == {css, svg}


def test_a_scope_that_walks_the_graph_needs_the_index() -> None:
    sel = _select(["web/package.json"], index_available=False)
    assert sel.run_all and sel.reasons[0].startswith("No index")


def test_unplaced_tests_run_with_a_scoped_selection() -> None:
    sel = _select(["uv.lock"], unplaced_tests=["tests/test_cli.py"])
    assert sel.test_files == ("tests/test_core.py", "tests/test_cli.py")


def test_scope_paths_are_posix() -> None:
    assert all(PurePosixPath(p).as_posix() == p for p in _TRACKED)


def _namer_checkout(tmp_path):
    """A git checkout where one changed name sits inside another (``a.md`` in ``data.md``)."""
    import subprocess

    from repowise.core.analysis.changed_lines import ChangeSet, FileDiff
    from repowise.core.analysis.test_collection import read_checkout

    files = {
        "web/package.json": "{}",
        "web/src/app.ts": "import pkg from '../package.json'\n",
        "web/src/app.test.ts": "readFileSync('package.json')\n",
        "tools/build.py": "print('web/package.json', 'README.md')\n",
        "tools/data.py": "open('docs/data.md')\n",
        "docs/a.md": "# a\n",
        "docs/data.md": "# data\n",
        "src/mod.py": "x = 1\n",
        "pytest.ini": "[pytest]\naddopts = --doctest-glob=*.md\n",
        "README.md": "readme\n",
    }
    for path, text in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    changed = ("web/package.json", "README.md", "docs/a.md", "docs/data.md")
    change = ChangeSet(
        files={p: FileDiff(path=p) for p in changed},
        deleted=set(),
        label="t",
        base=None,
        head=None,
    )
    return change, read_checkout(tmp_path)


def test_plan_scopes_names_the_same_files_from_git_grep_as_from_reading_them(tmp_path) -> None:
    """The grep only skips reading files that hold no changed name; nested names still count."""
    from dataclasses import replace

    from repowise.core.analysis.test_collection import plan_scopes

    change, checkout = _namer_checkout(tmp_path)
    assert checkout.holding is not None
    grepped = plan_scopes(change, _NONE, checkout)
    assert grepped == plan_scopes(change, _NONE, replace(checkout, holding=None))
    assert grepped.namers["web/package.json"] == [
        "tools/build.py",
        "web/src/app.test.ts",
        "web/src/app.ts",
    ]
    # "data.md" holds "a.md": a doc keeps name-only matching, so both are named.
    assert grepped.namers["docs/a.md"][-1] == "tools/data.py"
    assert grepped.namers["docs/data.md"][-1] == "tools/data.py"


def test_a_grep_that_cannot_answer_falls_back_to_reading_every_file(
    tmp_path, monkeypatch
) -> None:
    from dataclasses import replace

    from repowise.core.analysis import test_collection
    from repowise.core.analysis.doc_drift import suggest

    change, checkout = _namer_checkout(tmp_path)
    read = test_collection.plan_scopes(change, _NONE, replace(checkout, holding=None))
    monkeypatch.setattr(suggest, "git_run", lambda *_a, **_k: None)  # timed out
    assert test_collection.plan_scopes(change, _NONE, checkout) == read


def test_a_blob_read_that_stops_early_does_not_read_as_unmoved(tmp_path) -> None:
    """A path left out of a blob read counts as unread only where the file exists."""
    import subprocess

    from repowise.core.analysis.test_collection import _unread

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "kept.py").write_text("import os\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "one")
    old = git("rev-parse", "HEAD")
    (tmp_path / "added.py").write_text("import sys\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "two")
    new = git("rev-parse", "HEAD")

    specs = [(old, "kept.py"), (new, "kept.py"), (old, "added.py"), (new, "added.py")]
    whole = {(old, "kept.py"): b"x", (new, "kept.py"): b"x", (new, "added.py"): b"y"}
    assert _unread(tmp_path, specs, whole) == set()  # added.py is absent at old
    assert _unread(tmp_path, specs, {(old, "kept.py"): b"x"}) == {"kept.py", "added.py"}
    assert _unread(tmp_path, specs, None) == set()  # nothing read: the caller fails closed
