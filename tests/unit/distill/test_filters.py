"""Fixture-driven tests for the seven output filters.

Every fixture under tests/fixtures/distill/ is real output captured from this
repository (git/pytest/npm/find/ls) or a hand-written but format-faithful
sample for toolchains not present here (go, cargo, jest, tsc).

Two invariants run across the board:
- **zero error-line loss** on test/build output: every raw line classified by
  ``is_error_line`` appears verbatim in the distilled rendering;
- **savings assertions** per fixture, plus a >=60% median over the
  test/build bench set.
"""

from __future__ import annotations

import re
import statistics

import pytest

from repowise.core.distill.budget import estimate_tokens, savings_pct
from repowise.core.distill.filters.base import is_error_line
from repowise.core.distill.registry import filter_registry


def _distill(load_fixture, fixture: str, filter_name: str, command: str) -> tuple[str, str]:
    raw = load_fixture(fixture)
    f = filter_registry.get(filter_name)
    assert f is not None
    return raw, f.distill(raw, command=command)


def _pct(raw: str, distilled: str) -> float:
    return savings_pct(estimate_tokens(raw), estimate_tokens(distilled))


def _assert_no_error_line_lost(raw: str, distilled: str) -> None:
    for line in raw.splitlines():
        if is_error_line(line):
            assert line in distilled, f"error line dropped: {line!r}"


# -- test_output ---------------------------------------------------------------

#: (fixture, command) pairs for the test/build savings bench.
BENCH_CASES = [
    ("pytest_fail.txt", "test_output", "pytest"),
    ("pytest_pass_verbose.txt", "test_output", "pytest tests/unit"),
    ("go_test_fail.txt", "test_output", "go test ./..."),
    ("cargo_test_fail.txt", "test_output", "cargo test"),
    ("jest_fail.txt", "test_output", "npm test"),
    ("npm_build.txt", "build_output", "npm run build"),
]


@pytest.mark.parametrize(("fixture", "filter_name", "command"), BENCH_CASES)
def test_no_error_line_lost(load_fixture, fixture, filter_name, command) -> None:
    raw, distilled = _distill(load_fixture, fixture, filter_name, command)
    _assert_no_error_line_lost(raw, distilled)


@pytest.mark.parametrize(("fixture", "filter_name", "command"), BENCH_CASES)
def test_individual_savings_floor(load_fixture, fixture, filter_name, command) -> None:
    raw, distilled = _distill(load_fixture, fixture, filter_name, command)
    assert _pct(raw, distilled) >= 40.0, f"{fixture} saved less than 40%"


def test_bench_median_savings_at_least_60pct(load_fixture) -> None:
    """Plan exit criterion: >=60% median token reduction on test/build fixtures.

    tsc_errors.txt participates as 0%: it is all signal, the filter keeps
    everything, and the engine correctly passes it through — that behavior is
    asserted separately below.
    """
    pcts = []
    for fixture, filter_name, command in BENCH_CASES:
        raw, distilled = _distill(load_fixture, fixture, filter_name, command)
        pcts.append(_pct(raw, distilled))
    pcts.append(0.0)  # tsc_errors.txt: all-error output, no savings possible
    assert statistics.median(pcts) >= 60.0, f"median savings {pcts}"


def test_pytest_failures_and_summary_survive(load_fixture) -> None:
    raw, distilled = _distill(load_fixture, "pytest_fail.txt", "test_output", "pytest")
    assert "FAILURES" in distilled
    assert "short test summary info" in distilled
    assert "test_assertion_failure" in distilled
    # The final counts line is the single most useful line of a test run.
    assert "4 failed" in distilled
    # The pass parade is gone.
    assert "[ 55%]" not in distilled or distilled.count("%]") < raw.count("%]")


def test_pytest_pass_run_collapses_to_header_and_summary(load_fixture) -> None:
    _raw, distilled = _distill(
        load_fixture, "pytest_pass_verbose.txt", "test_output", "pytest tests/unit"
    )
    assert "test session starts" in distilled
    assert "passed" in distilled
    assert len(distilled.splitlines()) < 20


def test_go_panic_traceback_survives(load_fixture) -> None:
    _raw, distilled = _distill(load_fixture, "go_test_fail.txt", "test_output", "go test ./...")
    assert "--- FAIL: TestResolveImports" in distilled
    assert "panic: runtime error: index out of range" in distilled
    assert "blame_test.go:57" in distilled
    assert "exit status 2" in distilled
    # Passing subtests are summarized away.
    assert "TestParserPython3/basic" not in distilled


def test_cargo_failures_section_survives(load_fixture) -> None:
    _raw, distilled = _distill(load_fixture, "cargo_test_fail.txt", "test_output", "cargo test")
    assert "store::tests::test_ttl_prune" in distilled
    assert "test result: FAILED. 22 passed; 2 failed" in distilled
    assert "test graph::tests::test_pagerank_sums_to_one ... ok" not in distilled


def test_jest_failure_blocks_survive(load_fixture) -> None:
    _raw, distilled = _distill(load_fixture, "jest_fail.txt", "test_output", "npm test")
    assert "● ChatInterface › renders streaming tokens incrementally" in distilled  # noqa: RUF001
    assert "chat-interface.test.tsx:47:20" in distilled
    assert "Tests:       2 failed, 117 passed, 119 total" in distilled
    assert "✓ renders without crashing" not in distilled


# -- build_output ----------------------------------------------------------------


def test_build_errors_survive_and_spam_collapses(load_fixture) -> None:
    raw, distilled = _distill(load_fixture, "npm_build.txt", "build_output", "npm run build")
    _assert_no_error_line_lost(raw, distilled)
    assert len(distilled.splitlines()) < len(raw.splitlines())


def test_tsc_all_error_output_is_kept_whole(load_fixture) -> None:
    """All-signal build output: the filter must not beat the raw by dropping errors."""
    raw = load_fixture("tsc_errors.txt")
    f = filter_registry.get("build_output")
    distilled = f.distill(raw, command="npm run type-check")
    _assert_no_error_line_lost(raw, distilled)
    assert "Found 6 errors in 4 files." in distilled


# -- lint_output ---------------------------------------------------------------

#: (fixture, command) pairs for the lint savings bench. ruff fixtures are real
#: output from this repo (`--select ALL` over core/persistence); eslint is real
#: output from packages/web; clippy/golangci/mypy are format-faithful samples
#: for toolchains not installed here. eslint and mypy are error-heavy by
#: construction — they participate in the median as the all-signal end.
LINT_BENCH_CASES = [
    ("ruff_check_full.txt", "ruff check packages"),
    ("ruff_check_concise.txt", "ruff check --output-format=concise packages"),
    ("eslint_fail.txt", "npm run lint"),
    ("clippy_warnings.txt", "cargo clippy"),
    ("golangci_lint.txt", "golangci-lint run ./..."),
    ("mypy_errors.txt", "mypy packages"),
]


@pytest.mark.parametrize(("fixture", "command"), LINT_BENCH_CASES)
def test_lint_no_error_line_lost(load_fixture, fixture, command) -> None:
    raw, distilled = _distill(load_fixture, fixture, "lint_output", command)
    _assert_no_error_line_lost(raw, distilled)


def test_lint_bench_median_savings_at_least_60pct(load_fixture) -> None:
    """Exit criterion: >=60% median token reduction across the lint fixtures."""
    pcts = []
    for fixture, command in LINT_BENCH_CASES:
        raw, distilled = _distill(load_fixture, fixture, "lint_output", command)
        pcts.append(_pct(raw, distilled))
    assert statistics.median(pcts) >= 60.0, f"median savings {pcts}"


def test_ruff_full_groups_by_rule_and_keeps_summary(load_fixture) -> None:
    raw, distilled = _distill(load_fixture, "ruff_check_full.txt", "lint_output", "ruff check")
    # Rule groups carry counts and file:line anchors.
    assert re.search(r"D\d{3} ×\d+", distilled)  # noqa: RUF001
    assert re.search(r"coordinator\.py:\d+", distilled)
    # The tool's own totals and fixable summary survive verbatim.
    assert re.search(r"^Found \d+ errors\.$", distilled, re.MULTILINE)
    assert "fixable with the `--fix` option" in distilled
    assert _pct(raw, distilled) >= 75.0


def test_ruff_concise_groups_by_rule(load_fixture) -> None:
    raw, distilled = _distill(load_fixture, "ruff_check_concise.txt", "lint_output", "ruff check")
    assert re.search(r"[A-Z]+\d+ ×\d+, \d+ fixable", distilled)  # noqa: RUF001
    assert _pct(raw, distilled) >= 70.0


def test_eslint_errors_verbatim_warnings_grouped(load_fixture) -> None:
    raw, distilled = _distill(load_fixture, "eslint_fail.txt", "lint_output", "npm run lint")
    # Every error row survives verbatim (the hooks error among them).
    assert 'React Hook "useEffect" is called conditionally' in distilled
    # Identical repeated error rows collapse to one annotated copy.
    assert distilled.count("no-html-link-for-pages") < raw.count("no-html-link-for-pages")
    assert "(×5)" in distilled  # noqa: RUF001
    # Warnings are grouped by rule.
    assert re.search(r"@next/next/no-img-element ×\d+", distilled)  # noqa: RUF001
    # The problems summary survives.
    assert "✖ 19 problems (9 errors, 10 warnings)" in distilled


def test_clippy_error_block_survives_warnings_group(load_fixture) -> None:
    raw, distilled = _distill(load_fixture, "clippy_warnings.txt", "lint_output", "cargo clippy")
    # The compile error block is verbatim, frame included.
    assert "error[E0308]: mismatched types" in distilled
    assert "expected `usize`, found `Option<usize>`" in distilled
    # Warning blocks collapse into clippy rule groups.
    assert re.search(r"clippy::redundant_clone ×\d+", distilled)  # noqa: RUF001
    assert "error: could not compile `gitscan`" in distilled
    assert _pct(raw, distilled) >= 60.0


def test_golangci_errcheck_lines_survive(load_fixture) -> None:
    raw, distilled = _distill(load_fixture, "golangci_lint.txt", "lint_output", "golangci-lint run")
    # Error-classified messages survive verbatim, not as a lossy group.
    for line in raw.splitlines():
        if "Error return value" in line:
            assert line in distilled
    assert re.search(r"wsl ×8", distilled)  # noqa: RUF001


def test_mypy_all_signal_output_keeps_every_error(load_fixture) -> None:
    """mypy output is mostly errors — nothing to win, nothing to lose."""
    raw, distilled = _distill(load_fixture, "mypy_errors.txt", "lint_output", "mypy packages")
    _assert_no_error_line_lost(raw, distilled)
    assert "Found 7 errors in 3 files" in distilled


def test_lint_unrecognized_raises() -> None:
    f = filter_registry.get("lint_output")
    with pytest.raises(ValueError, match="lint"):
        f.distill("random text\n" * 50, command="npm run lint")


# -- git family -------------------------------------------------------------------


def test_git_status_compact(load_fixture) -> None:
    raw, distilled = _distill(load_fixture, "git_status_dirty.txt", "git_status", "git status")
    assert distilled.splitlines()[0].startswith("## feat/distill")
    # Every path in the raw status survives, with porcelain-style codes.
    assert "A  packages/core/src/repowise/core/distill/store.py" in distilled
    assert " M README.md" in distilled
    assert "?? tests/fixtures/distill/" in distilled
    # The hint boilerplate is gone.
    assert "git restore" not in distilled
    assert _pct(raw, distilled) >= 15.0


def test_git_status_clean_tree() -> None:
    raw = (
        "On branch main\n"
        "Your branch is up to date with 'origin/main'.\n\n"
        "nothing to commit, working tree clean\n"
        "and some\nmore lines\nto pass min_lines\n"
    )
    f = filter_registry.get("git_status")
    distilled = f.distill(raw, command="git status")
    assert "## main...origin/main" in distilled
    assert "clean" in distilled


def test_git_log_subjects_only(load_fixture) -> None:
    raw, distilled = _distill(load_fixture, "git_log_full.txt", "git_log", "git log -40")
    lines = distilled.splitlines()
    assert "40 commits" in lines[0]
    # Most recent commit subject is present with its short sha.
    assert any("2ef3f76c" in ln for ln in lines)
    assert _pct(raw, distilled) >= 80.0


def test_git_diff_keeps_structure(load_fixture) -> None:
    raw, distilled = _distill(
        load_fixture, "git_diff_large.txt", "git_diff", "git diff HEAD~6..HEAD"
    )
    assert distilled.splitlines()[0].startswith("diff:")
    # Every changed file is accounted for: full hunks or a +/- summary line.
    import re

    for m in re.finditer(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+)$", raw, re.MULTILINE):
        assert m.group("b") in distilled, f"file {m.group('b')} unaccounted for"
    assert _pct(raw, distilled) >= 50.0


def test_git_diff_unrecognized_raises() -> None:
    f = filter_registry.get("git_diff")
    with pytest.raises(ValueError):
        f.distill("random text\n" * 50, command="git diff")


# -- git_diff_stat ------------------------------------------------------------------


def _stat_output(n_files: int = 100) -> str:
    lines = []
    for i in range(n_files):
        churn = (i * 7) % 250 + 1
        bars = "+" * min(churn, 20)
        lines.append(f" pkg/module_{i:03d}.py | {churn:4d} {bars}")
    lines.append(" img/logo.png | Bin 1024 -> 2048 bytes")
    lines.append(f" {n_files + 1} files changed, 12345 insertions(+), 6789 deletions(-)")
    return "\n".join(lines) + "\n"


def test_git_diff_stat_keeps_top_by_churn() -> None:
    f = filter_registry.get("git_diff_stat")
    assert f is not None
    raw = _stat_output(100)
    distilled = f.distill(raw, command="git diff --stat HEAD~50..HEAD")
    head = distilled.splitlines()
    assert head[0].startswith("diff --stat: 101 files changed")
    assert "81 more files omitted" in head[1]  # 101 rows - 20 kept
    # The highest-churn row survives; a tiny row does not.
    max_line = max(
        (ln for ln in raw.splitlines() if "|" in ln and "Bin" not in ln and "changed" not in ln),
        key=lambda ln: int(ln.split("|")[1].strip().split()[0]),
    )
    assert max_line in distilled
    assert _pct(raw, distilled) >= 50.0


def test_git_diff_stat_command_routing() -> None:
    stat = filter_registry.get("git_diff_stat")
    hunks = filter_registry.get("git_diff")
    assert stat.matches_command("git diff --stat HEAD~5")
    assert not stat.matches_command("git diff HEAD~5")
    assert not hunks.matches_command("git diff --stat HEAD~5")


def test_git_diff_stat_content_sniff() -> None:
    stat = filter_registry.get("git_diff_stat")
    assert stat.matches_content(_stat_output(50))
    assert not stat.matches_content("random text\n" * 50)


def test_git_diff_stat_mixed_with_hunks_raises() -> None:
    f = filter_registry.get("git_diff_stat")
    mixed = _stat_output(60) + "diff --git a/x.py b/x.py\n+++ b/x.py\n+new line\n"
    with pytest.raises(ValueError):
        f.distill(mixed, command="git diff --stat -p")


def test_git_diff_stat_small_diff_raises() -> None:
    # <= KEEP_STAT_FILES rows: nothing to save, fall back to raw.
    f = filter_registry.get("git_diff_stat")
    with pytest.raises(ValueError):
        f.distill(_stat_output(10), command="git diff --stat")


# -- file_listing -------------------------------------------------------------------


def test_find_paths_grouped_by_directory(load_fixture) -> None:
    raw, distilled = _distill(
        load_fixture, "find_paths.txt", "file_listing", "find packages/core/src -name *.py"
    )
    assert "directories:" in distilled.splitlines()[0]
    assert "packages/core/src/repowise/core/distill/" in distilled
    assert _pct(raw, distilled) >= 60.0


def test_ls_long_compacts_columns(load_fixture) -> None:
    raw, distilled = _distill(load_fixture, "ls_la.txt", "file_listing", "ls -la")
    assert "entries:" in distilled.splitlines()[0]
    assert _pct(raw, distilled) >= 60.0


# -- logs ------------------------------------------------------------------------


def test_logs_collapse_templates_keep_errors(load_fixture) -> None:
    raw, distilled = _distill(load_fixture, "generic_logs.txt", "logs", "tail -200 server.log")
    # Every severe line survives verbatim.
    for line in raw.splitlines():
        if "ERROR" in line:
            assert line in distilled
    # Repeated event templates collapse with a count annotation.
    assert "similar)" in distilled
    assert _pct(raw, distilled) >= 50.0


# -- registry/meta -----------------------------------------------------------------


def test_all_core_filters_registered() -> None:
    names = {f.name for f in filter_registry.filters()}
    assert names >= {
        "test_output",
        "build_output",
        "lint_output",
        "git_status",
        "git_log",
        "git_diff",
        "file_listing",
        "logs",
    }
