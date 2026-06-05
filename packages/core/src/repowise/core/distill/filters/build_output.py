"""Compaction of build/compile output (npm, tsc, cargo, go, bundlers).

One line-classifying pass works across toolchains: errors and warnings are
kept verbatim (with file-location lines), progress spam collapses to verb
counts, asset/route tables are capped, and the head/tail anchors preserve
the banner and the summary. Errors-first, same as test output.
"""

from __future__ import annotations

import re
from typing import ClassVar

from repowise.core.distill.filters.base import OutputFilter, is_error_line
from repowise.core.distill.registry import filter_registry

_COMMAND_RE = re.compile(
    r"^(npm(?:\.cmd)? run b|npm(?:\.cmd)? run-script b|pnpm (?:run )?b|yarn (?:run )?b|"
    r"tsc\b|cargo(?:\.exe)? (?:build|check|clippy)\b|go(?:\.exe)? (?:build|vet)\b|"
    r"make\b|vite build|webpack\b|next(?:\.cmd)? build|dotnet(?:\.exe)? build\b|"
    r"npm(?:\.cmd)? run (?:type-check|typecheck|lint|compile)\b|gradle|mvn\b)"
)

# Progress verbs collapsed to "<verb> ... (xN)".
_PROGRESS_RE = re.compile(
    r"^\s*(?P<verb>Compiling|Downloading|Downloaded|Fetching|Fetched|Installing|"
    r"Resolving|Resolved|Checking|Building|Bundling|Documenting|Generating|"
    r"Creating|transforming|rendering chunks|computing gzip size|Linting)\b"
)

# Warnings are kept (deduped); is_error_line covers errors.
_WARNING_RE = re.compile(r"(?i)\bwarn(ing)?\b")

# file(line,col) / file:line:col locations attached to diagnostics.
_LOCATION_RE = re.compile(r"^\s*\S+\.\w{1,4}[(:]\d+[,:)]")

# Asset/route table rows (bundler output): sizes, tree glyphs, pipes.
_TABLE_RE = re.compile(r"(│|├|└|┌|\|\s*$|\d+(\.\d+)?\s*(B|kB|KB|MB|kb)\b)")

_MAX_TABLE_ROWS = 10
_HEAD_ANCHOR = 4
_TAIL_ANCHOR = 12


@filter_registry.register
class BuildOutputFilter(OutputFilter):
    name: ClassVar[str] = "build_output"
    priority: ClassVar[int] = 30
    min_lines: ClassVar[int] = 15

    def matches_command(self, command: str) -> bool:
        return bool(_COMMAND_RE.match(command))

    def matches_content(self, output: str) -> bool:
        sample = output.splitlines()[:50]
        progress = sum(1 for ln in sample if _PROGRESS_RE.match(ln))
        return progress >= 5

    def distill(self, output: str, *, command: str = "", exit_code: int = 0) -> str:
        lines = output.splitlines()
        n = len(lines)
        head_end = min(_HEAD_ANCHOR, n)
        tail_start = max(head_end, n - _TAIL_ANCHOR)

        out: list[str] = []
        repeats = 0  # consecutive duplicates of the last emitted line
        last_emitted: str | None = None
        progress_counts: dict[str, int] = {}
        progress_first: dict[str, int] = {}
        table_rows = 0
        dropped = 0

        def emit(line: str) -> None:
            """Append, collapsing consecutive duplicate lines into one + count."""
            nonlocal repeats, last_emitted
            if line.strip() and line.strip() == last_emitted:
                repeats += 1
                return
            flush_repeats()
            last_emitted = line.strip()
            out.append(line)

        def flush_repeats() -> None:
            nonlocal repeats
            if repeats:
                out[-1] = f"{out[-1]}  (x{repeats + 1})"
                repeats = 0

        for i, line in enumerate(lines):
            anchored = i < head_end or i >= tail_start
            if is_error_line(line) or _WARNING_RE.search(line) or _LOCATION_RE.match(line):
                emit(line)
            elif m := _PROGRESS_RE.match(line):
                verb = m.group("verb")
                progress_counts[verb] = progress_counts.get(verb, 0) + 1
                if verb not in progress_first:
                    flush_repeats()
                    last_emitted = None
                    progress_first[verb] = len(out)
                    out.append(line)  # placeholder; annotated with count below
            elif _TABLE_RE.search(line) and line.strip():
                table_rows += 1
                if table_rows <= _MAX_TABLE_ROWS or anchored:
                    emit(line)
            elif anchored:
                emit(line)
            elif line.strip():
                dropped += 1
        flush_repeats()

        for verb, idx in progress_first.items():
            count = progress_counts[verb]
            if count > 1:
                out[idx] = f"{out[idx]}  (x{count}, further {verb} lines collapsed)"
        if table_rows > _MAX_TABLE_ROWS:
            out.append(f"({table_rows - _MAX_TABLE_ROWS} table rows omitted)")
        if dropped:
            out.append(f"({dropped} build-output lines omitted)")
        return "\n".join(out)
