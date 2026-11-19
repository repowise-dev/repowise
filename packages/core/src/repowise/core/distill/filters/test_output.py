"""Errors-first compaction of test-runner output.

Handles pytest, go test, cargo test, and jest/vitest natively, with a
generic errors-plus-anchors fallback for anything else that smells like a
test run. The contract in every mode: all failure information survives
(failure blocks, FAILED/ERROR lines, final summary); what gets dropped is
the pass parade — dots, PASSED lines, ok lines — which is replaced by counts.
"""

from __future__ import annotations

import re
from collections import deque
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
_GO_RUN_PARSE_RE = re.compile(r"^===\s+(?P<verb>RUN|PAUSE|CONT|NAME)(?:\s+(?P<name>\S+))?")
_GO_STATUS_PARSE_RE = re.compile(r"^\s*--- (?:FAIL|PASS|SKIP):\s+(?P<name>\S+)")
_GO_PASS_RE = re.compile(r"^\s*--- PASS:|^PASS$|^ok\s+\S+")
_GO_FAIL_RE = re.compile(r"^\s*--- FAIL:|^FAIL\b")
_GO_MAX_BUFFERED_TEST_LINES = 20

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


class _TestBuffer:
    """Bounded per-test buffer holding head and tail lines while preserving errors."""

    def __init__(
        self,
        head_lines: int = 10,
        tail_lines: int = 10,
        max_middle_errors: int = 20,
    ) -> None:
        self.head_lines = head_lines
        self.tail_lines = tail_lines
        self.max_middle_errors = max_middle_errors
        self.head: list[str] = []
        self.middle_errors: list[str] = []
        self.tail: deque[str] = deque(maxlen=tail_lines)

    def append(self, line: str) -> None:
        if len(self.head) < self.head_lines:
            self.head.append(line)
        elif is_error_line(line):
            if len(self.middle_errors) >= self.max_middle_errors:
                self.middle_errors.pop(0)
            self.middle_errors.append(line)
        else:
            self.tail.append(line)

    def drain(self) -> list[str]:
        out = list(self.head)
        out.extend(self.middle_errors)
        out.extend(self.tail)
        self.head.clear()
        self.middle_errors.clear()
        self.tail.clear()
        return out

    def drain_errors(self) -> list[str]:
        errors = [ln for ln in self.head if is_error_line(ln)]
        errors.extend(self.middle_errors)
        errors.extend(ln for ln in self.tail if is_error_line(ln))
        self.head.clear()
        self.middle_errors.clear()
        self.tail.clear()
        return errors


class _GoDistiller:
    """Stateful distiller for `go test` and `go test -v` outputs."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.out: list[str] = []
        self.passes = 0
        self.runs = 0
        self.in_fail_block = False
        self.current_test: str | None = None
        self.buffers: dict[str, _TestBuffer] = {}

    def _parse_test_name(self, line: str) -> str | None:
        m = _GO_STATUS_PARSE_RE.match(line)
        return m.group("name") if m else None

    def _active_parent(self, test_name: str | None) -> str | None:
        if test_name and "/" in test_name:
            parent = test_name.rsplit("/", 1)[0]
            if parent in self.buffers:
                return parent
        return None

    def _handle_exit_status(self, line: str) -> None:
        if self.current_test and self.current_test in self.buffers:
            self.out.extend(self.buffers.pop(self.current_test).drain())
        for buf in self.buffers.values():
            self.out.extend(buf.drain_errors())
        self.buffers.clear()
        self.out.append(line)
        self.current_test = None
        self.in_fail_block = False

    def _handle_run(self, line: str) -> bool:
        run_m = _GO_RUN_PARSE_RE.match(line)
        if not run_m:
            return False
        verb = run_m.group("verb")
        name = run_m.group("name")
        self.in_fail_block = False
        if verb == "RUN":
            self.runs += 1
            self.current_test = name
            if name:
                self.buffers[name] = _TestBuffer()
        elif verb in ("CONT", "NAME"):
            self.current_test = name
            if name and name not in self.buffers:
                self.buffers[name] = _TestBuffer()
        elif verb == "PAUSE":
            self.current_test = None
        return True

    def _handle_fail(self, line: str) -> bool:
        if not _GO_FAIL_RE.match(line):
            return False
        is_test_fail = line.lstrip().startswith("--- FAIL")
        self.in_fail_block = is_test_fail
        self.out.append(line)
        test_name = self._parse_test_name(line) if is_test_fail else self.current_test
        if test_name and test_name in self.buffers:
            self.out.extend(self.buffers.pop(test_name).drain())
        self.current_test = self._active_parent(test_name)
        return True

    def _handle_pass_or_skip(self, line: str) -> bool:
        if not (_GO_PASS_RE.match(line) or line.lstrip().startswith("--- SKIP")):
            return False
        self.in_fail_block = False
        if line.startswith(("ok", "PASS")):
            self.out.append(line)
            self.current_test = None
            return True
        if not line.lstrip().startswith("--- SKIP"):
            self.passes += 1
        test_name = self._parse_test_name(line)
        if test_name and test_name in self.buffers:
            self.out.extend(self.buffers.pop(test_name).drain_errors())
        self.current_test = self._active_parent(test_name)
        return True

    def _handle_line(self, line: str) -> None:
        if line.startswith("exit status "):
            self._handle_exit_status(line)
        elif self._handle_run(line) or self._handle_fail(line) or self._handle_pass_or_skip(line):
            return
        elif self.current_test is not None:
            self.buffers[self.current_test].append(line)
        elif self.in_fail_block or is_error_line(line):
            self.out.append(line)

    def distill(self) -> str:
        for line in self.lines:
            self._handle_line(line)
        for buf in self.buffers.values():
            self.out.extend(buf.drain_errors())

        if not self.out and self.passes == 0 and self.runs == 0:
            raise ValueError("not go test output")
        if self.passes or self.runs:
            self.out.insert(0, f"({self.passes or self.runs} passing tests omitted)")
        return "\n".join(self.out)


def _distill_go(lines: list[str]) -> str:
    return _GoDistiller(lines).distill()


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
