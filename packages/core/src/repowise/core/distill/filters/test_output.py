"""Errors-first compaction of test-runner output.

Handles pytest, go test, cargo test, and jest/vitest natively, with a
generic errors-plus-anchors fallback for anything else that smells like a
test run. The contract in every mode: all failure information survives
(failure blocks, FAILED/ERROR lines, final summary); what gets dropped is
the pass parade — dots, PASSED lines, ok lines — which is replaced by counts.
"""

from __future__ import annotations

import re
from typing import ClassVar

from repowise.core.distill.filters.base import OutputFilter, cap_block, is_error_line
from repowise.core.distill.registry import filter_registry

_COMMAND_RE = re.compile(
    r"^(pytest\b|py\.test\b|jest\b|vitest\b|cargo(?:\.exe)? (?:test|nextest)\b|"
    r"go(?:\.exe)? test\b|npm(?:\.cmd)? (?:test|run test)\b|pnpm (?:test|run test)\b|"
    r"yarn (?:test|run test)\b)"
)

# pytest --------------------------------------------------------------------
_PYTEST_SEP_RE = re.compile(r"^=+ (?P<title>.+?) =+$")
_PYTEST_SUBSEP_RE = re.compile(r"^_+ .+ _+$")
_PYTEST_FINAL_RE = re.compile(r"^=+ .*\b(passed|failed|error|errors|skipped|no tests ran)\b.* =+$")
_PYTEST_PROGRESS_FAIL_RE = re.compile(r"\b(FAILED|ERROR)\b")

# go test -------------------------------------------------------------------
_GO_RUN_RE = re.compile(r"^=== (?:RUN|PAUSE|CONT|NAME)\b")
_GO_PASS_RE = re.compile(r"^\s*--- PASS:|^PASS$|^ok\s+\S+")
_GO_FAIL_RE = re.compile(r"^\s*--- FAIL:|^FAIL\b")

# cargo test ----------------------------------------------------------------
_CARGO_OK_RE = re.compile(r"^test \S+ \.\.\. ok$")
_CARGO_RESULT_RE = re.compile(r"^test result:")

# jest / vitest -------------------------------------------------------------
_JEST_PASS_SUITE_RE = re.compile(r"^\s*(PASS|√|✓)\s")
_JEST_SUMMARY_RE = re.compile(
    r"^\s*(Tests?(?: Suites)?|Test Files|Snapshots|Time|Duration|Ran all)\b"
)

#: Per-failure-block cap (head keeps the test id, tail keeps the assertion).
_BLOCK_HEAD = 10
_BLOCK_TAIL = 30


@filter_registry.register
class TestOutputFilter(OutputFilter):
    name: ClassVar[str] = "test_output"
    priority: ClassVar[int] = 20
    min_lines: ClassVar[int] = 10

    def matches_command(self, command: str) -> bool:
        return bool(_COMMAND_RE.match(command))

    def matches_content(self, output: str) -> bool:
        return _detect_format(output) is not None

    def distill(self, output: str, *, command: str = "", exit_code: int = 0) -> str:
        fmt = _detect_format(output)
        lines = output.splitlines()
        if fmt == "pytest":
            return _distill_pytest(lines)
        if fmt == "go":
            return _distill_go(lines)
        if fmt == "cargo":
            return _distill_cargo(lines)
        if fmt == "jest":
            return _distill_jest(lines)
        return _distill_generic(lines)


def _detect_format(output: str) -> str | None:
    if "test session starts" in output:
        return "pytest"
    if re.search(r"^test result:", output, re.MULTILINE):
        return "cargo"
    if re.search(r"^(=== RUN|--- (PASS|FAIL):|ok\s+\S+\s+[\d.]+s)", output, re.MULTILINE):
        return "go"
    # jest/vitest: the summary block always counts "N total"; prose that merely
    # mentions "Tests:" (e.g. commit bodies) must not sniff as a test run.
    if re.search(r"^\s*(Tests?(?: Suites)?|Test Files):.*\btotal\b", output, re.MULTILINE) or (
        re.search(r"^\s*(PASS|FAIL)\s+\S+\.(test|spec)\.[jt]sx?", output, re.MULTILINE)
    ):
        return "jest"
    return None


# -- pytest ------------------------------------------------------------------


def _distill_pytest(lines: list[str]) -> str:
    out: list[str] = []
    section = "header"
    section_buf: list[str] = []
    dropped_progress = 0
    warning_lines = 0

    def flush_failure_section() -> None:
        """Emit buffered FAILURES/ERRORS sub-blocks, individually capped."""
        block: list[str] = []
        for ln in section_buf:
            if _PYTEST_SUBSEP_RE.match(ln) and block:
                out.extend(cap_block(block, _BLOCK_HEAD, _BLOCK_TAIL))
                block = [ln]
            else:
                block.append(ln)
        if block:
            out.extend(cap_block(block, _BLOCK_HEAD, _BLOCK_TAIL))

    for line in lines:
        sep = _PYTEST_SEP_RE.match(line)
        if sep:
            if section in ("failures", "errors"):
                flush_failure_section()
            elif section == "warnings" and warning_lines:
                out.append(f"(warnings summary omitted: {warning_lines} lines)")
            section_buf = []
            title = sep.group("title").lower()
            if "test session starts" in title:
                section = "header"
                out.append(line)
            elif title in ("failures", "errors"):
                section = title
                out.append(line)
            elif "warnings summary" in title:
                section = "warnings"
            elif "short test summary info" in title:
                section = "summary"
                out.append(line)
            elif _PYTEST_FINAL_RE.match(line):
                section = "done"
                if dropped_progress:
                    out.append(f"({dropped_progress} progress lines omitted)")
                    dropped_progress = 0
                out.append(line)
            else:
                section = "other"
            continue

        if section == "header":
            if (
                line.startswith(("platform ", "collected ", "rootdir:"))
                or _PYTEST_PROGRESS_FAIL_RE.search(line)
                or is_error_line(line)
            ):
                out.append(line)
            elif line.strip():
                dropped_progress += 1
        elif section in ("failures", "errors"):
            section_buf.append(line)
        elif section == "warnings":
            if line.strip():
                warning_lines += 1
        elif section == "summary" or (section in ("other", "done") and is_error_line(line)):
            out.append(line)

    if section in ("failures", "errors"):
        flush_failure_section()
    if not any(_PYTEST_SEP_RE.match(ln) for ln in out):
        raise ValueError("pytest output without recognizable sections")
    return "\n".join(out)


# -- go test -----------------------------------------------------------------


def _distill_go(lines: list[str]) -> str:
    out: list[str] = []
    passes = 0
    runs = 0
    in_fail_block = False
    for line in lines:
        if _GO_RUN_RE.match(line):
            runs += 1
            in_fail_block = False
        elif _GO_FAIL_RE.match(line):
            in_fail_block = line.lstrip().startswith("--- FAIL")
            out.append(line)
        elif _GO_PASS_RE.match(line):
            in_fail_block = False
            if line.startswith(("ok", "PASS")):
                out.append(line)
            else:
                passes += 1
        elif line.startswith("exit status ") or in_fail_block or is_error_line(line):
            out.append(line)
    if not out and passes == 0 and runs == 0:
        raise ValueError("not go test output")
    if passes or runs:
        out.insert(0, f"({passes or runs} passing tests omitted)")
    return "\n".join(out)


# -- cargo test --------------------------------------------------------------


def _distill_cargo(lines: list[str]) -> str:
    out: list[str] = []
    ok_count = 0
    in_failures = False
    for line in lines:
        if _CARGO_OK_RE.match(line):
            ok_count += 1
        elif line.strip() == "failures:":
            in_failures = True
            out.append(line)
        elif _CARGO_RESULT_RE.match(line):
            in_failures = False
            out.append(line)
        elif (
            (line.startswith("running ") and line.endswith(("tests", "test")))
            or in_failures
            or is_error_line(line)
        ):
            out.append(line)
    if ok_count:
        out.insert(0, f"({ok_count} passing tests omitted)")
    if not any(_CARGO_RESULT_RE.match(ln) for ln in out):
        raise ValueError("cargo test output without a result line")
    return "\n".join(out)


# -- jest / vitest -----------------------------------------------------------


def _distill_jest(lines: list[str]) -> str:
    out: list[str] = []
    passing = 0
    pass_suites = 0
    in_failure_block = False
    block: list[str] = []

    def flush() -> None:
        nonlocal block
        if block:
            out.extend(cap_block(block, _BLOCK_HEAD, _BLOCK_TAIL))
            block = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("●") or stripped.startswith("⎯") or is_error_line(line):
            in_failure_block = True
            block.append(line)
        elif _JEST_SUMMARY_RE.match(line):
            in_failure_block = False
            flush()
            out.append(line)
        elif _JEST_PASS_SUITE_RE.match(line):
            in_failure_block = False
            flush()
            if stripped.startswith("PASS"):
                pass_suites += 1
            else:
                passing += 1
        elif in_failure_block:
            block.append(line)
    flush()
    if passing or pass_suites:
        parts = []
        if pass_suites:
            parts.append(f"{pass_suites} passing suites")
        if passing:
            parts.append(f"{passing} passing tests")
        out.insert(0, f"({' and '.join(parts)} omitted)")
    if not out:
        raise ValueError("not jest/vitest output")
    return "\n".join(out)


# -- generic -----------------------------------------------------------------


def _distill_generic(lines: list[str]) -> str:
    """Errors + anchors: head, tail, and every error line with light context."""
    keep = set(range(min(5, len(lines))))
    keep.update(range(max(0, len(lines) - 10), len(lines)))
    for i, line in enumerate(lines):
        if is_error_line(line):
            keep.update((max(0, i - 1), i, min(len(lines) - 1, i + 1)))
    out: list[str] = []
    last = -1
    for i in sorted(keep):
        if i != last + 1:
            out.append(f"  ... ({i - last - 1} lines elided) ...")
        out.append(lines[i])
        last = i
    return "\n".join(out)
