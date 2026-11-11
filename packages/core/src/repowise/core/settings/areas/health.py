"""Code health inputs: coverage, refactoring, assertions, gates, test selection."""

from __future__ import annotations

from repowise.core.settings.areas._common import factory
from repowise.core.settings.model import Setting

AREA = "health"
_s = factory(AREA)


def _percent(key: str, description: str) -> Setting:
    return _s(f"coverage.{key}", "float", None, description, minimum=0, maximum=100)


SETTINGS: tuple[Setting, ...] = (
    _s("coverage.auto_discover", "bool", True, "Look for coverage reports in the usual places."),
    _s(
        "coverage.artifacts",
        "list",
        [],
        "Where to look for coverage reports instead of the usual places.",
    ),
    _s("coverage.paths", "list", [], "Report paths or globs from the repo root, read without discovery. An entry "
        "may be {path, path_prefix}."),
    _s("coverage.ignore", "list", [], "Files coverage leaves out."),
    _s("coverage.format", "str", None, "Force a report format instead of detecting it."),
    _s("coverage.strip_prefix", "str", None, "Path prefix to strip from report paths."),
    _s("coverage.path_prefix", "str", None, "Path prefix to add to report paths."),
    _s(
        "coverage.reingest_on_update",
        "bool",
        None,
        "Re-read reports on every update. Unset re-reads only newer reports.",
    ),
    _percent("fail_under", "The coverage gate fails below this percent."),
    _percent("fail_under_risky", "The coverage gate fails when risky files are below this percent."),
    _percent("max_drop", "The coverage gate fails when coverage falls from the base by more than this many points."),
    _percent("fail_under_branches", "The coverage gate fails below this branch percent."),
    _s(
        "coverage.min_coverable_lines",
        "int",
        None,
        "Changes with fewer coverable lines than this pass the gate.",
        minimum=0,
    ),
    _s("coverage.gates", "list", [], "Coverage gates scoped to paths.", on_invalid="error"),
    _s("refactoring.enabled", "bool", True, "Work out refactoring plans in the health pass."),
    _s("refactoring.detectors.disabled", "list", [], "Refactoring detectors to silence, such as extract_class or split_file."),
    _s(
        "refactoring.min_confidence",
        "str",
        None,
        "Least confidence a refactoring plan needs to show.",
        choices=("low", "medium", "high"),
    ),
    _s(
        "refactoring.llm.enabled",
        "bool",
        False,
        "Let a model write refactoring code when asked; indexing never calls it.",
        writer="repowise.server.services.refactoring_settings:save_llm_enabled",
    ),
    _s(
        "assertions.extra_names",
        "list",
        [],
        "Your own assertion helpers, by exact name, counted as assertions.",
    ),
    _s(
        "tests.full_run_on",
        "list",
        [],
        "Gitignore patterns whose change makes impacted-tests select every test.",
        on_invalid="error",
    ),
    _s("tests.always_run", "list", [], "Tests added to every impacted-tests selection, as written.", on_invalid="error"),
    _s(
        "security.patterns",
        "list",
        [],
        "Your own secret shapes for security check, each {name, regex, severity}.",
        on_invalid="error",
    ),
    _s(
        "changes.outcome_fix_days",
        "int",
        30,
        "Days after a change in which fix commits to its files are counted.",
        minimum=1,
        maximum=365,
    ),
    _s(
        "REPOWISE_COMMIT_HEALTH_BACKGROUND",
        "bool",
        True,
        "Finish the commit health scan in the background after update; 0 keeps it inline.",
        env_false=frozenset({"0"}),
        internal=True,
    ),
    _s(
        "REPOWISE_COMMIT_HEALTH_BUDGET_SECONDS",
        "float",
        300.0,
        "Time limit for one commit health scan.",
        minimum=0,
        internal=True,
    ),
    _s(
        "REPOWISE_COMMIT_HEALTH_COMMITS",
        "int",
        150,
        "How many recent commits get a health scan.",
        minimum=0,
        internal=True,
    ),
    _s(
        "REPOWISE_COMMIT_HEALTH_MAX_FILES",
        "int",
        300,
        "Skip the health scan of a commit touching more files than this; 0 means no cap.",
        minimum=0,
        internal=True,
    ),
    _s(
        "REPOWISE_FULL_RESCORE_INTERVAL_DAYS",
        "float",
        7.0,
        "Days between full health rescores on update.",
        minimum=0,
        internal=True,
    ),
    _s(
        "REPOWISE_GATE_SUMMARY_DIR",
        "str",
        None,
        "Folder where each CI gate leaves its result row. The GitHub Action sets it.",
        internal=True,
    ),
)
