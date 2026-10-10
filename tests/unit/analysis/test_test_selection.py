"""Safe impacted-test selection: every full-run rule, the subset, runner arguments.

:func:`select_tests` is pure, so each rule is pinned on a hand-written tiers
result (the shape ``impacted-tests`` builds) without an index or git.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.test_selection import (
    MAX_INDEX_GAP,
    RUN_ALL,
    Selection,
    SelectionInput,
    TestSelectionConfig,
    doc_readers,
    format_args,
    is_runnable_test,
    left_out,
    plan_gap,
    plugin_loader,
    resolve_runner,
    runner_args,
    runner_notes,
    select_tests,
    with_rewired,
)
from repowise.core.pytest_roots import read_pytest_roots

_NONE = TestSelectionConfig()


def _tiers(covered=None, inferred=(), unknown=()):
    return {"covered": covered or {}, "inferred": list(inferred), "unknown": list(unknown)}


def _inferred(source, test, via):
    return {"source_file": source, "test_file": test, "via": via}


def _select(changed, tiers=None, config=_NONE, **kwargs):
    kwargs.setdefault("index_available", True)
    deleted = kwargs.pop("deleted", ())
    return select_tests(
        SelectionInput(changed, deleted, tiers or _tiers(), config, **kwargs)
    )


# -- full-run rules -----------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "why"),
    [
        ("uv.lock", "uv.lock changed: dependencies can change any test."),
        ("web/package-lock.json", "dependencies can change any test"),
        ("requirements-dev.txt", "dependencies can change any test"),
        ("pyproject.toml", "build or test configuration can change any test"),
        ("web/tsconfig.base.json", "build or test configuration can change any test"),
        ("docker-compose.ci.yml", "build or test configuration can change any test"),
        ("pkg/testdata/input.json", "shared test data can change any test"),
        ("tests/fixtures/user.yaml", "shared test data can change any test"),
        (".github/workflows/ci.yml", "CI configuration changed"),
        (".gitlab-ci.yml", "CI configuration changed"),
        ("services/api/.gitlab-ci.yml", "CI configuration changed"),
        (".gitlab/ci/test.yml", "CI configuration changed"),
        (".circleci/config.yml", "CI configuration changed"),
        ("Jenkinsfile", "CI configuration changed"),
        ("Jenkinsfile.release", "CI configuration changed"),
        ("azure-pipelines.yml", "CI configuration changed"),
        ("azure-pipelines-pr.yaml", "CI configuration changed"),
        (".azure-pipelines/build.yml", "CI configuration changed"),
        (".buildkite/pipeline.yml", "CI configuration changed"),
        ("bitbucket-pipelines.yml", "CI configuration changed"),
        (".travis.yml", "CI configuration changed"),
        (".drone.yml", "CI configuration changed"),
        (".woodpecker/test.yml", "CI configuration changed"),
        (".woodpecker.yaml", "CI configuration changed"),
        ("appveyor.yml", "CI configuration changed"),
        (".teamcity/settings.kts", "CI configuration changed"),
        (".repowise/config.yaml", "Repowise's configuration changed"),
    ],
)
def test_a_file_that_can_change_any_test_runs_everything(path, why) -> None:
    sel = _select([path])
    assert sel.run_all
    assert any(why in r for r in sel.reasons)
    assert sel.basis[path] == "full-run"


def test_the_configured_globs_extend_the_defaults() -> None:
    config = TestSelectionConfig(full_run_on=("schema/**",))
    assert _select(["schema/user.sql"], config=config).run_all
    assert "matches tests.full_run_on" in _select(["schema/a.sql"], config=config).reasons[0]
    # The defaults still apply beside the configured globs.
    assert _select(["uv.lock"], config=config).run_all


def test_a_deleted_lockfile_runs_everything() -> None:
    assert _select([], deleted=["poetry.lock"]).run_all


def test_an_unknown_code_file_runs_everything() -> None:
    sel = _select(["src/new.py"], _tiers(unknown=["src/new.py"]))
    assert sel.run_all
    assert sel.reasons[0].startswith("src/new.py: no coverage")
    assert sel.basis["src/new.py"] == "unknown"


def test_only_a_filename_guess_runs_everything() -> None:
    tiers = _tiers(inferred=[_inferred("src/a.py", "tests/test_a.py", "filename-pattern")])
    sel = _select(["src/a.py"], tiers)
    assert sel.run_all
    assert sel.reasons == ("src/a.py: only a filename guess names a test (tests/test_a.py).",)
    assert sel.basis["src/a.py"] == "filename-pattern"


def test_no_index_runs_everything_when_code_changed() -> None:
    sel = _select(["src/a.py"], index_available=False)
    assert sel.run_all
    assert sel.reasons[0].startswith("No index:")
    # One reason, not one per file on top of it.
    assert len(sel.reasons) == 1


@pytest.mark.parametrize(
    ("path", "skipped"),
    [
        ("LICENSE", True),
        ("LICENSE-APACHE", True),
        ("README.md", True),
        ("CHANGELOG.md", True),
        ("docs/guide.rst", True),
        ("docs/img/logo.png", True),
        # Outside docs/ a document or image may be package data a test reads.
        ("assets/logo.SVG", False),
        ("packages/web/public/logo.png", False),
        ("src/pkg/README.md", False),
        ("packages/core/templates/page.md", False),
        ("src/notice.py", False),
        ("data/users.json", False),
        # Code is a module whatever its name or place, and root data is data.
        ("docs/conf.py", False),
        ("docs/examples/snippet.py", False),
        ("history.py", False),
        ("security.go", False),
        ("changes.json", False),
    ],
)
def test_only_documentation_is_skipped(path, skipped) -> None:
    sel = _select([path], _tiers(unknown=[path]))
    assert (path in sel.skipped_files) is skipped
    assert sel.run_all is not skipped


def test_no_index_with_only_docs_changed_runs_nothing() -> None:
    sel = _select(["README.md", "docs/logo.png"], index_available=False)
    assert not sel.run_all
    assert sel.tests == ()
    assert sel.skipped_files == ("README.md", "docs/logo.png")


def test_a_deleted_code_file_with_no_known_test_runs_everything() -> None:
    sel = _select([], _tiers(unknown=["src/gone.py"]), deleted=["src/gone.py"])
    assert sel.run_all
    assert sel.reasons == ("src/gone.py was deleted and no test is known to have used it.",)


def test_a_deleted_code_file_runs_the_tests_that_imported_it() -> None:
    tiers = _tiers(inferred=[_inferred("src/gone.py", "tests/test_user.py", "import-graph")])
    sel = _select([], tiers, deleted=["src/gone.py"])
    assert not sel.run_all
    assert sel.tests == ("tests/test_user.py",)


def test_a_deleted_test_needs_no_run_of_its_own() -> None:
    tiers = _tiers(inferred=[_inferred("tests/test_old.py", "tests/test_old.py", "changed-test")])
    sel = _select([], tiers, deleted=["tests/test_old.py"], missing=["tests/test_old.py"])
    assert not sel.run_all
    assert sel.tests == ()
    assert sel.basis == {"tests/test_old.py": "deleted-test"}


def test_a_deleted_test_runs_the_tests_that_imported_it() -> None:
    tiers = _tiers(
        inferred=[
            _inferred("tests/test_base.py", "tests/test_base.py", "changed-test"),
            _inferred("tests/test_base.py", "tests/test_child.py", "import-graph"),
        ]
    )
    sel = _select([], tiers, deleted=["tests/test_base.py"], missing=["tests/test_base.py"])
    assert not sel.run_all
    assert sel.tests == ("tests/test_child.py",)


def test_an_empty_change_runs_everything() -> None:
    sel = _select([], label="origin/main...HEAD")
    assert sel.run_all
    assert sel.reasons == ("No change against origin/main...HEAD; nothing to select from.",)


def test_a_change_of_only_documentation_needs_no_tests() -> None:
    sel = _select(["docs/guide.md", "README.md"])
    assert not sel.run_all
    assert runner_args(sel, "pytest") == []


@pytest.mark.parametrize(
    "path",
    [
        "tests/helpers.py",
        "tests/utils.py",
        "tests/unit/persistence/helpers.py",
        "tests/data/users.json",
        "tests/__snapshots__/app.test.ts.snap",
        "tests/golden/report.txt",
        "tests/plugins/timing.py",
    ],
)
@pytest.mark.parametrize("deleted", [False, True])
def test_a_test_tree_file_that_is_not_a_test_runs_everything(path, deleted) -> None:
    changed, gone = ([], [path]) if deleted else ([path], [])
    sel = _select(changed, _tiers(), deleted=gone)
    assert sel.run_all
    assert any(r.startswith(f"{path} ") for r in sel.reasons), sel.reasons


@pytest.mark.parametrize("deleted", [False, True])
def test_a_production_package_init_runs_everything(deleted) -> None:
    path = "src/pkg/__init__.py"
    sel = _select([], deleted=[path]) if deleted else _select([path])
    assert sel.run_all
    assert sel.reasons[0] == (
        f"{path} changed: every import of the package runs it, and those are not all tracked."
    )


_KNOWN = (
    "tests/test_top.py",
    "tests/unit/test_a.py",
    "tests/unit/sub/test_b.py",
    "other/test_c.py",
)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/__init__.py", _KNOWN[:3]),
        ("tests/unit/__init__.py", _KNOWN[1:3]),
    ],
)
@pytest.mark.parametrize("deleted", [False, True])
def test_a_changed_test_package_init_selects_every_test_under_it(path, expected, deleted) -> None:
    changed, gone = ([], [path]) if deleted else ([path], [])
    tiers = _tiers(inferred=[_inferred(path, path, "changed-test")])
    sel = _select(changed, tiers, deleted=gone, known_tests=_KNOWN)
    assert not sel.run_all, sel.reasons
    assert sel.tests == expected
    assert sel.basis[path] == "test-package"


def test_a_route_through_a_test_package_init_selects_the_tests_under_it() -> None:
    tiers = _tiers(
        inferred=[
            _inferred("src/a.py", "tests/unit/__init__.py", "import-graph"),
            _inferred("src/a.py", "other/test_c.py", "import-graph"),
        ]
    )
    sel = _select(["src/a.py"], tiers, known_tests=_KNOWN)
    assert not sel.run_all, sel.reasons
    assert sel.tests == ("other/test_c.py", "tests/unit/test_a.py", "tests/unit/sub/test_b.py")


def test_a_route_through_another_helper_still_runs_everything() -> None:
    tiers = _tiers(
        inferred=[
            _inferred("src/a.py", "tests/unit/__init__.py", "import-graph"),
            _inferred("src/a.py", "tests/unit/helpers.py", "import-graph"),
        ]
    )
    sel = _select(["src/a.py"], tiers, known_tests=_KNOWN)
    assert sel.run_all
    assert "reached through the test helper tests/unit/helpers.py" in sel.reasons[0]


def test_a_file_whose_only_test_this_change_deletes_runs_everything() -> None:
    tiers = _tiers(inferred=[_inferred("src/a.py", "tests/test_a.py", "call-graph")])
    sel = _select(["src/a.py"], tiers, deleted=["tests/test_a.py"], missing=["tests/test_a.py"])
    assert sel.run_all
    assert "src/a.py: its only known tests are not in the checkout." in sel.reasons


def test_a_test_the_checkout_lacks_means_the_index_is_stale() -> None:
    tiers = _tiers(
        inferred=[
            _inferred("src/a.py", "tests/test_a.py", "call-graph"),
            _inferred("src/a.py", "tests/test_gone.py", "import-graph"),
        ]
    )
    sel = _select(["src/a.py"], tiers, missing=["tests/test_gone.py"])
    assert sel.run_all
    assert sel.reasons[0].startswith("tests/test_gone.py is a test the index names")


def test_a_file_reached_only_through_a_test_helper_runs_everything() -> None:
    tiers = _tiers(inferred=[_inferred("src/a.py", "tests/factories/user.py", "import-graph")])
    sel = _select(["src/a.py"], tiers)
    assert sel.run_all
    assert sel.reasons[0].startswith(
        "src/a.py is reached through the test helper tests/factories/user.py, and no test "
    )


def test_any_route_through_a_test_helper_runs_everything() -> None:
    """A real test reaching the file does not vouch for the helper's other users."""
    tiers = _tiers(
        inferred=[
            _inferred("src/a.py", "tests/test_a.py", "call-graph"),
            _inferred("src/a.py", "tests/helpers.py", "import-graph"),
        ]
    )
    sel = _select(["src/a.py"], tiers)
    assert sel.run_all
    assert "src/a.py is reached through the test helper tests/helpers.py, and no test" in sel.reasons[0]


def test_coverage_does_not_hide_a_helper_route() -> None:
    tiers = _tiers(
        covered={
            "tests/test_a.py::t|run": {"test_file": "tests/test_a.py", "source_files": ["src/a.py"]}
        },
        inferred=[_inferred("src/a.py", "tests/helpers.py", "import-graph")],
    )
    assert _select(["src/a.py"], tiers).run_all


@pytest.mark.parametrize(
    ("kwargs", "start"),
    [
        ({"graph_error": "OperationalError: locked"}, "The graph could not be read"),
        ({"map_truncated": True}, "The per-test map is at its stored row cap"),
        ({"index_problem": "The index records two commits"}, "The index records two commits"),
        ({"index_gap": None}, "Cannot tell what changed since the index was built"),
    ],
)
def test_an_index_that_cannot_be_trusted_runs_everything(kwargs, start) -> None:
    tiers = _tiers(inferred=[_inferred("src/a.py", "tests/test_a.py", "call-graph")])
    sel = _select(["src/a.py"], tiers, **kwargs)
    assert sel.run_all
    assert any(r.startswith(start) for r in sel.reasons), sel.reasons


def test_an_index_gap_no_one_traced_runs_everything() -> None:
    tiers = _tiers(inferred=[_inferred("src/a.py", "tests/test_a.py", "call-graph")])
    assert _select(["src/a.py"], tiers, index_gap=["README.md"]).run_all is False
    stale = _select(["src/a.py"], tiers, index_gap=["README.md", "src/b.py"])
    assert stale.run_all
    assert "The index predates 1 changed file(s) outside this change (e.g. src/b.py)" in (
        stale.reasons[0]
    )
    unknown = _select(["src/a.py"], tiers, index_gap=None)
    assert unknown.run_all
    assert unknown.reasons[0].startswith("Cannot tell what changed since the index was built")


# -- an index behind the base -------------------------------------------------


def _behind(gap, inferred=(), **kwargs):
    """src/a.py changed; the index predates *gap*, every candidate in it rewired."""
    tiers = _tiers(inferred=[_inferred("src/a.py", "tests/test_a.py", "call-graph"), *inferred])
    plan = plan_gap(gap, _NONE)
    plan = with_rewired(plan, plan.candidates, kwargs.pop("indexed_files", ()))
    return _select(["src/a.py"], tiers, index_gap=gap, gap=plan, **kwargs)


def test_an_index_behind_the_base_adds_the_tests_reaching_the_gap() -> None:
    sel = _behind(
        ["src/b.py", "src/c.py", "README.md"],
        inferred=[_inferred("src/b.py", "tests/test_b.py", "import-graph")],
    )
    assert sel.run_all is False
    assert sel.test_files == ("tests/test_a.py", "tests/test_b.py")
    # src/c.py has no test: it links none to the change, which is not a reason.
    assert "The index predates 2 changed file(s) outside this change" in sel.reasons[0]


@pytest.mark.parametrize(
    ("path", "why"),
    [
        ("uv.lock", "uv.lock changed after the index was built (1 such): dependencies"),
        ("web/tsconfig.json", "build or test configuration can change any test"),
        (".repowise/config.yaml", "Repowise's configuration changed"),
    ],
)
def test_configuration_changed_after_the_index_runs_everything(path, why) -> None:
    sel = _behind(["src/b.py", path])
    assert sel.run_all
    assert why in sel.reasons[0]
    assert plan_gap(["src/b.py", path], _NONE).candidates == ()


def test_nothing_is_traced_when_the_change_runs_everything_anyway() -> None:
    assert plan_gap(["src/b.py"], _NONE, ["uv.lock", "src/a.py"]).candidates == ()
    assert plan_gap(["src/b.py"], _NONE, ["src/a.py"]).candidates == ("src/b.py",)


@pytest.mark.parametrize("path", [".github/workflows/ci.yml", "tests/fixtures/user.json"])
def test_ci_config_and_test_data_after_the_index_cannot_reach_the_change(path) -> None:
    assert _behind([path]).run_all is False
    assert plan_gap([path, "src/b.py", "ui/app.css"], _NONE).candidates == ("src/b.py",)


def test_a_production_package_init_after_the_index_stands_for_its_modules() -> None:
    files = ["pkg/__init__.py", "pkg/sub/x.py", "pkg/y.py", "other/z.py"]
    plan = plan_gap(["pkg/__init__.py", "other/z.py"], _NONE)
    assert with_rewired(plan, plan.candidates, files).targets == (
        "other/z.py",
        "pkg/__init__.py",
        "pkg/sub/x.py",
        "pkg/y.py",
    )
    # An __init__.py whose imports did not move stands for nothing.
    assert with_rewired(plan, ["other/z.py"], files).targets == ("other/z.py",)
    sel = _behind(
        ["pkg/__init__.py"],
        inferred=[_inferred("pkg/sub/x.py", "tests/test_x.py", "import-graph")],
        indexed_files=files,
    )
    assert sel.run_all is False and "tests/test_x.py" in sel.test_files


def test_a_gap_too_large_to_trace_runs_everything() -> None:
    gap = [f"src/m{i}.py" for i in range(MAX_INDEX_GAP + 1)]
    sel = _behind(gap)
    assert sel.run_all
    assert f"more than the {MAX_INDEX_GAP} worth tracing" in sel.reasons[0]
    assert plan_gap(gap, _NONE).candidates == ()


def test_a_test_deleted_after_the_index_is_not_a_stale_index() -> None:
    sel = _behind(
        ["src/b.py", "tests/test_b.py"],
        inferred=[_inferred("src/b.py", "tests/test_b.py", "import-graph")],
        missing=["tests/test_b.py"],
    )
    assert sel.run_all is False
    assert sel.test_files == ("tests/test_a.py",)


def test_a_gap_route_through_an_unimported_helper_runs_everything() -> None:
    sel = _behind(
        ["src/b.py"], inferred=[_inferred("src/b.py", "tests/helpers.py", "import-graph")]
    )
    assert sel.run_all
    assert sel.reasons[0].startswith(
        "Changed after the index was built: src/b.py is reached through the test helper"
    )


def test_the_inert_gap_reasons_name_real_full_run_groups() -> None:
    from repowise.core.analysis.test_selection import _FULL_RUN_GROUPS, _GAP_INERT

    assert {why for why, _ in _FULL_RUN_GROUPS} >= _GAP_INERT


# -- the subset ---------------------------------------------------------------


def test_the_subset_is_covering_ids_and_inferred_files() -> None:
    tiers = _tiers(
        covered={
            "tests/test_a.py::test_one|run": {
                "test_file": "tests/test_a.py",
                "source_files": ["src/a.py"],
            },
            "tests/test_a.py::test_one|setup": {
                "test_file": "tests/test_a.py",
                "source_files": ["src/a.py"],
            },
        },
        inferred=[
            _inferred("src/b.py", "tests/test_b.py", "call-graph"),
            _inferred("tests/test_c.py", "tests/test_c.py", "changed-test"),
        ],
    )
    sel = _select(["src/a.py", "src/b.py", "tests/test_c.py", "docs/guide.md"], tiers)
    assert not sel.run_all
    # Phases collapse to one node id.
    assert sel.tests == ("tests/test_a.py::test_one", "tests/test_b.py", "tests/test_c.py")
    assert sel.test_files == ("tests/test_a.py", "tests/test_b.py", "tests/test_c.py")
    assert sel.skipped_files == ("docs/guide.md",)
    assert sel.basis == {
        "docs/guide.md": "no-tests-needed",
        "src/a.py": "coverage",
        "src/b.py": "call-graph",
        "tests/test_c.py": "changed-test",
    }
    assert "1 changed file(s) are documentation: no tests needed." in sel.reasons


def test_a_node_id_is_rebased_on_the_resolved_test_file() -> None:
    # The runner ran from a package directory; the map resolved its file.
    tiers = _tiers(
        covered={"tests/test_a.py::T::test_x[1]|run": {
            "test_file": "pkg/tests/test_a.py", "source_files": ["pkg/a.py"]
        }}
    )
    assert _select(["pkg/a.py"], tiers).tests == ("pkg/tests/test_a.py::T::test_x[1]",)


def test_a_node_id_inside_a_whole_selected_file_is_dropped() -> None:
    tiers = _tiers(
        covered={"tests/test_a.py::test_one|run": {
            "test_file": "tests/test_a.py", "source_files": ["src/a.py"]
        }},
        inferred=[_inferred("src/b.py", "tests/test_a.py", "import-graph")],
    )
    assert _select(["src/a.py", "src/b.py"], tiers).tests == ("tests/test_a.py",)


def test_coverage_naming_a_test_without_a_file_runs_everything() -> None:
    tiers = _tiers(covered={"suite-label": {"test_file": None, "source_files": ["src/a.py"]}})
    sel = _select(["src/a.py"], tiers)
    assert sel.run_all
    assert sel.reasons == ("Coverage names suite-label for src/a.py, but not its file.",)


def test_always_run_is_appended_and_kept_on_a_full_run() -> None:
    config = TestSelectionConfig(always_run=("tests/test_smoke.py",))
    tiers = _tiers(inferred=[_inferred("src/b.py", "tests/test_b.py", "call-graph")])
    sel = _select(["src/b.py"], tiers, config=config)
    assert runner_args(sel, "pytest") == ["tests/test_b.py", "tests/test_smoke.py"]
    full = _select(["uv.lock"], config=config)
    assert full.to_dict()["always_run"] == ["tests/test_smoke.py"]
    assert runner_args(full, "pytest") == [RUN_ALL]


def test_a_stale_map_is_stated() -> None:
    tiers = _tiers(
        covered={"t::x": {"test_file": "tests/test_a.py", "source_files": ["src/a.py"]}}
    )
    sel = _select(["src/a.py"], tiers, map_current=False)
    assert not sel.run_all
    assert sel.reasons == (
        "The per-test map was measured at another commit, so covering tests are matched "
        "by file, not by changed line.",
    )


# -- config -------------------------------------------------------------------


def test_config_reads_the_tests_block() -> None:
    cfg = TestSelectionConfig.from_repo_config(
        {"tests": {"full_run_on": ["schema/**"], "always_run": [" tests/test_smoke.py "]}}
    )
    assert cfg == TestSelectionConfig(("schema/**",), ("tests/test_smoke.py",))
    assert TestSelectionConfig.from_repo_config({}) == TestSelectionConfig()


@pytest.mark.parametrize(
    ("block", "message"),
    [
        ("all", "tests must be a mapping"),
        ({"full_run_on": "schema/**"}, "tests.full_run_on must be a list of non-empty strings"),
        ({"always_run": ["ok", ""]}, "tests.always_run must be a list of non-empty strings"),
        ({"full_run": ["x"]}, "tests.full_run is not a setting"),
    ],
)
def test_config_refuses_what_it_cannot_use(block, message) -> None:
    with pytest.raises(ValueError, match=message):
        TestSelectionConfig.from_repo_config({"tests": block})


# -- runner arguments ---------------------------------------------------------


def _subset(tests=(), files=(), always=()):
    return Selection(run_all=False, reasons=(), tests=tests, test_files=files, always_run=always)


def test_pytest_gets_node_ids_and_files() -> None:
    sel = _subset(
        tests=("tests/test_a.py::test_x[a b]", "tests/test_b.py", "web/a.test.ts"),
        files=("tests/test_a.py", "tests/test_b.py", "web/a.test.ts"),
    )
    args = runner_args(sel, "pytest")
    assert args == ["tests/test_a.py::test_x[a b]", "tests/test_b.py"]
    # Quoted for a shell: the space inside the parameter id stays one argument.
    assert format_args(args) == "'tests/test_a.py::test_x[a b]' tests/test_b.py"


def test_go_gets_each_package_directory_once() -> None:
    sel = _subset(files=("a_test.go", "pkg/x/x_test.go", "pkg/x/y_test.go", "tests/test_a.py"))
    assert runner_args(sel, "go") == [".", "./pkg/x"]


def test_go_runs_the_package_of_every_changed_go_file_with_tests() -> None:
    """Same-package tests import nothing, so the graph alone can miss them."""
    tiers = _tiers(
        inferred=[
            _inferred("pkg/x/x.go", "cmd/main_test.go", "call-graph"),
            _inferred("pkg/y/y.go", "cmd/main_test.go", "call-graph"),
        ]
    )
    sel = _select(["pkg/x/x.go", "pkg/y/y.go"], tiers, go_test_dirs={"pkg/x", "cmd"})
    assert sel.packages == ("pkg/x",)
    assert runner_args(sel, "go") == ["./cmd", "./pkg/x"]


def test_jest_and_files_get_paths() -> None:
    sel = _subset(files=("web/a.test.ts", "web/b.spec.jsx", "tests/test_a.py"))
    assert runner_args(sel, "jest") == ["web/a.test.ts", "web/b.spec.jsx"]
    assert runner_args(sel, "files") == ["web/a.test.ts", "web/b.spec.jsx", "tests/test_a.py"]


@pytest.mark.parametrize(
    ("files", "runner"),
    [
        (("tests/test_a.py",), "pytest"),
        (("a_test.go", "pkg/b_test.go"), "go"),
        (("web/a.test.ts", "web/b.test.js"), "jest"),
        (("tests/test_a.py", "a_test.go"), "files"),
        ((), "files"),
    ],
)
def test_auto_picks_the_runner_every_selected_file_belongs_to(files, runner) -> None:
    assert resolve_runner(_subset(files=files), "auto") == runner
    assert resolve_runner(_subset(files=files), "jest") == "jest"


def test_a_full_run_prints_only_the_sentinel() -> None:
    full = Selection(run_all=True, reasons=("uv.lock changed",), test_files=("a_test.go",))
    for runner in ("pytest", "go", "jest", "files"):
        assert format_args(runner_args(full, runner)) == ":all"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("conftest.py", _KNOWN),
        ("tests/conftest.py", _KNOWN[:3]),
        ("tests/unit/conftest.py", _KNOWN[1:3]),
    ],
)
@pytest.mark.parametrize("deleted", [False, True])
def test_a_changed_conftest_selects_every_test_at_or_below_it(path, expected, deleted) -> None:
    changed, gone = ([], [path]) if deleted else ([path], [])
    sel = _select(changed, _tiers(), deleted=gone, known_tests=_KNOWN)
    assert not sel.run_all, sel.reasons
    assert sel.tests == expected
    assert sel.basis[path] == "conftest"


def test_a_route_through_a_conftest_selects_the_tests_under_it() -> None:
    tiers = _tiers(
        inferred=[
            _inferred("src/a.py", "tests/unit/conftest.py", "import-graph"),
            _inferred("src/a.py", "other/test_c.py", "call-graph"),
        ]
    )
    sel = _select(["src/a.py"], tiers, known_tests=_KNOWN)
    assert not sel.run_all, sel.reasons
    assert sel.tests == ("other/test_c.py", "tests/unit/test_a.py", "tests/unit/sub/test_b.py")


def test_a_route_through_an_imported_helper_selects_its_importers() -> None:
    tiers = _tiers(
        inferred=[
            _inferred("src/a.py", "tests/helpers.py", "import-graph"),
            _inferred("src/a.py", "tests/test_x.py", "import-graph"),
        ]
    )
    tiers["helper_importers"] = {"tests/helpers.py": ["tests/test_x.py"]}
    sel = _select(["src/a.py"], tiers)
    assert not sel.run_all, sel.reasons
    assert sel.tests == ("tests/test_x.py",)


@pytest.mark.parametrize("deleted", [False, True])
def test_a_changed_helper_selects_the_tests_that_import_it(deleted) -> None:
    path = "tests/helpers.py"
    changed, gone = ([], [path]) if deleted else ([path], [])
    tiers = _tiers(
        inferred=[
            _inferred(path, path, "changed-test"),
            _inferred(path, "tests/test_x.py", "import-graph"),
            _inferred(path, "tests/test_y.py", "import-graph"),
        ]
    )
    tiers["helper_importers"] = {path: ["tests/test_x.py"], "tests/test_x.py": ["tests/test_y.py"]}
    sel = _select(changed, tiers, deleted=gone)
    assert not sel.run_all, sel.reasons
    assert sel.tests == ("tests/test_x.py", "tests/test_y.py")
    assert sel.basis[path] == "helper-importers"


# -- what the graph cannot see ------------------------------------------------


def test_a_doc_some_code_names_runs_everything() -> None:
    readers = {"docs/agent/TOOLS.md": "tests/unit/test_tools.py"}
    sel = _select(["docs/agent/TOOLS.md", "docs/guide.md"], doc_readers=readers)
    assert sel.run_all
    assert sel.reasons[0] == (
        "docs/agent/TOOLS.md is named by tests/unit/test_tools.py, so a test may read it; "
        "the tests that do are not tracked."
    )
    assert sel.basis == {"docs/agent/TOOLS.md": "full-run", "docs/guide.md": "no-tests-needed"}


def test_doc_readers_are_code_naming_the_doc_or_a_doctest_glob() -> None:
    docs = ["README.md", "docs/guide.md"]
    sources = [
        ("pyproject.toml", 'readme = "README.md"\n'),
        ("src/help.py", 'HINT = "use --doctest-glob"\n'),
        ("tests/test_readme.py", 'ROOT / "README.md"\n'),
    ]
    assert doc_readers(docs, sources) == {"README.md": "tests/test_readme.py"}
    glob = [("pytest.ini", "[pytest]\naddopts = --doctest-glob='*.md'\n")]
    assert doc_readers(docs, glob) == {"README.md": "pytest.ini", "docs/guide.md": "pytest.ini"}


@pytest.mark.parametrize(
    ("path", "text", "loads"),
    [
        ("tests/conftest.py", 'pytest_plugins = ["tests.plugins.db"]\n', True),
        ("pyproject.toml", '[tool.pytest.ini_options]\naddopts = "-p tests.plugin"\n', True),
        ("pytest.ini", "[pytest]\naddopts = -p no:cacheprovider -v\n", False),
        ("tests/test_a.py", 'pytest_plugins = ["x"]\n', False),
    ],
)
def test_plugin_loader_finds_plugins_loaded_by_name(path, text, loads) -> None:
    assert (plugin_loader([(path, text)]) == path) is loads


def test_a_helper_is_unknown_while_plugins_are_loaded_by_name() -> None:
    """A plugin's fixtures reach tests that never import it."""
    path = "tests/plugins/db.py"
    tiers = _tiers(
        inferred=[
            _inferred(path, path, "changed-test"),
            _inferred(path, "tests/test_x.py", "import-graph"),
        ]
    )
    tiers["helper_importers"] = {path: ["tests/test_x.py"]}
    assert not _select([path], tiers).run_all
    sel = _select([path], tiers, plugin_loader="tests/conftest.py")
    assert sel.run_all
    assert "tests/conftest.py loads pytest plugins by name" in sel.reasons[0]
    route = _tiers(
        inferred=[
            _inferred("src/a.py", path, "import-graph"),
            _inferred("src/a.py", "tests/test_x.py", "import-graph"),
        ]
    )
    route["helper_importers"] = {path: ["tests/test_x.py"]}
    reached = _select(["src/a.py"], route, plugin_loader="tests/conftest.py")
    assert reached.run_all
    assert reached.reasons[0].startswith("src/a.py is reached through a test helper:")


def test_a_test_the_graph_cannot_see_into_runs_with_every_subset() -> None:
    tiers = _tiers(inferred=[_inferred("src/a.py", "tests/test_a.py", "import-graph")])
    sel = _select(["src/a.py"], tiers, unplaced_tests=["tests/test_cli.py"])
    assert not sel.run_all
    assert sel.tests == ("tests/test_a.py", "tests/test_cli.py")
    assert (
        "1 test file(s) the graph cannot see into run with every selection "
        "(e.g. tests/test_cli.py)." in sel.reasons
    )
    # Documentation alone still needs no test.
    assert _select(["docs/guide.md"], unplaced_tests=["tests/test_cli.py"]).tests == ()


def test_a_test_found_walking_the_tree_runs_with_every_subset_and_says_why() -> None:
    tiers = _tiers(inferred=[_inferred("src/a.py", "tests/test_a.py", "import-graph")])
    lint = {"tests/test_no_copies.py": "it lists and reads files under a source directory"}
    sel = _select(
        ["src/a.py"],
        tiers,
        always_run_tests={**lint, "tests/test_gone.py": "it runs `tool` in a child process"},
        known_tests=["tests/test_a.py", "tests/test_no_copies.py"],
    )
    assert not sel.run_all
    # A detected test the checkout no longer has is left out.
    assert sel.tests == ("tests/test_a.py", "tests/test_no_copies.py")
    assert (
        "1 test file(s) that list source files or run the project in a child process run "
        "with every selection (e.g. tests/test_no_copies.py: it lists and reads files under "
        "a source directory)." in sel.reasons
    )
    assert sel.why == {
        "tests/test_a.py": "src/a.py changed (import-graph)",
        "tests/test_no_copies.py": (
            "runs with every subset: it lists and reads files under a source directory"
        ),
    }


def test_explain_says_what_selected_a_test_or_that_nothing_did() -> None:
    from repowise.core.analysis.test_selection import explain_test

    tiers = _tiers(inferred=[_inferred("src/a.py", "tests/test_a.py", "call-graph")])
    sel = _select(["src/a.py"], tiers, config=TestSelectionConfig(always_run=("tests/smoke",)))
    assert explain_test(sel, "tests/test_a.py::test_one") == (
        True,
        ["Selected: src/a.py changed (call-graph)."],
    )
    assert explain_test(sel, "tests/smoke")[0] is True
    selected, lines = explain_test(sel, "tests/test_b.py")
    assert not selected and lines[0].startswith("Not selected: no changed file reaches it")
    full = _select(["uv.lock"])
    assert explain_test(full, "tests/test_b.py") == (
        True,
        ["Every test runs:", "uv.lock changed: dependencies can change any test."],
    )


def test_a_test_named_module_pytest_does_not_collect_is_not_runnable() -> None:
    """``core/test_paths.py`` is test-shaped, but a bare pytest never collects it."""
    config = '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
    roots = read_pytest_roots([("pyproject.toml", config)])
    assert not is_runnable_test("src/pkg/test_paths.py", roots)
    assert is_runnable_test("tests/test_a.py", roots)
    assert is_runnable_test("src/pkg/tests/test_b.py", roots)
    # With no config the name decides, as it does for pytest.
    assert is_runnable_test("src/pkg/test_paths.py")


def test_a_runner_gets_only_its_own_always_run_tests_and_hears_of_the_rest() -> None:
    config = TestSelectionConfig(
        always_run=("tests/test_smoke.py", "apps/android/FooTest.kt", "e2e/")
    )
    tiers = _tiers(inferred=[_inferred("src/b.py", "tests/test_b.py", "call-graph")])
    sel = _select(["src/b.py"], tiers, config=config, unplaced_tests=["ui/a.test.ts"])
    assert runner_args(sel, "pytest") == ["tests/test_b.py", "tests/test_smoke.py", "e2e/"]
    assert runner_args(sel, "jest") == ["ui/a.test.ts", "e2e/"]
    assert runner_args(sel, "files") == [
        "tests/test_b.py",
        "ui/a.test.ts",
        "tests/test_smoke.py",
        "apps/android/FooTest.kt",
        "e2e/",
    ]
    assert left_out(sel, "pytest") == {"files": ["apps/android/FooTest.kt"], "jest": ["ui/a.test.ts"]}
    assert runner_notes(sel, "pytest") == [
        "Selected tests for other runners are not in these pytest arguments (1 for files, "
        "1 for jest; e.g. ui/a.test.ts); run them in a job per runner."
    ]
    # Tests the indexer found walking the tree are split the same way.
    walked = _select(
        ["src/b.py"], tiers, known_tests=["tests/test_b.py", "app/LintTest.kt"],
        always_run_tests={"app/LintTest.kt": "it lists and reads files under a source directory"},
    )
    assert left_out(walked, "pytest") == {"files": ["app/LintTest.kt"]}
    assert runner_args(walked, "pytest") == ["tests/test_b.py"]
    # Case-blind: an upper-case extension is still the runner's own.
    upper = _select(["src/b.py"], tiers, unplaced_tests=["ui/B.TEST.TS", "tests/TEST_C.PY"])
    assert runner_args(upper, "jest") == ["ui/B.TEST.TS"]
    assert runner_args(upper, "pytest") == ["tests/test_b.py", "tests/TEST_C.PY"]
    assert runner_notes(sel, "files") == []
    assert runner_notes(_select(["uv.lock"], config=config), "pytest") == []
