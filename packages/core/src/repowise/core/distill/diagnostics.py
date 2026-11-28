"""Structured diagnostics for ``repowise distill --format json``.

Reuses the lint filter's parsers so a compiler, type-checker or linter run
becomes ``file/line/col/severity/code/source/message`` records plus a
top-level ``status`` (clean | errors | unavailable | unparsed).
"""

from __future__ import annotations

import re
from typing import Any

from repowise.core.distill.filters.lint_output import (
    _CONCISE_RE,
    _MYPY_RE,
    _PARSERS,
    _detect_format,
)

# MSBuild / tsc: path(line,col): error CODE: message [project]
_MSBUILD_RE = re.compile(
    r"^(?P<file>\S.*?)\((?P<line>\d+),(?P<col>\d+)\):\s"
    r"(?P<sev>error|warning)\s(?P<code>[A-Za-z]+\d+):\s(?P<msg>.+?)"
    r"(?:\s+\[[^\]]*\.\w*proj\])?\s*$"
)
# gcc / clang / javac / go: [C:]path:line[:col]: [severity:] message [-Wcode]
# shortcut: paths with spaces are not matched, widen if Swift/Windows users report misses.
_GNU_RE = re.compile(
    r"^(?P<file>(?:[A-Za-z]:)?[^\s:]+):(?P<line>\d+)(?::(?P<col>\d+))?:\s"
    r"(?:(?P<sev>fatal error|error|warning|note):\s)?(?P<msg>.+?)"
    r"(?:\s+\[(?P<code>[^\]\s]+)\])?\s*$"
)
# The shell's own "no such program" line, not the phrase quoted inside other output.
_NOT_FOUND_RE = re.compile(
    r"^(?:\S+: (?:line \d+: |\d+: )?\S+: (?:command )?not found\s*$"  # bash / sh (dash omits "command")
    r"|zsh: command not found: \S+"
    r"|'\S+' is not recognized as an internal or external command"  # cmd.exe
    r"|\S+ : The term '\S+' is not recognized)"  # PowerShell
)


def parse_diagnostics(output: str, exit_code: int) -> dict[str, Any]:
    """Parse *output* into ``{status, source, exit_code, [reason], diagnostics}``."""
    lint = _lint_records(output)
    # Two tools in one run (tsc + eslint, ruff + mypy): keep both. A fallback record
    # repeats a lint one only when it is the same line read as gcc or as the same format.
    seen = {(r["file"], r["line"], src) for r in lint for src in ("gnu", r["source"])}
    fallback = _fallback_records(output)
    records = lint + [r for r in fallback if (r["file"], r["line"], r["source"]) not in seen]
    not_found = next((ln.strip() for ln in output.splitlines() if _NOT_FOUND_RE.match(ln)), None)
    reason = None
    if records:
        status = "errors"
    elif exit_code in (127, 9009) or (exit_code != 0 and not_found):
        lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
        status, reason = "unavailable", not_found or (lines[-1] if lines else "")
    elif exit_code == 0:
        status = "clean"
    else:
        status = "unparsed"
    result: dict[str, Any] = {
        "status": status,
        "source": records[0]["source"] if records else None,
        "exit_code": exit_code,
    }
    if reason is not None:
        result["reason"] = reason
    result["diagnostics"] = records
    return result


def _lint_records(output: str) -> list[dict[str, Any]]:
    fmt = _detect_format(output, min_hits=1)
    if fmt is None:
        return []
    diags, _trailing = _PARSERS[fmt](output.splitlines())
    if fmt == "mypy":
        # gcc/clang/javac lines also match _MYPY_RE; the GNU fallback reads those.
        # A note with no diagnostic before it is not a finding (mypy exits 0).
        diags = [
            d
            for d in diags
            if d.file.endswith((".py", ".pyi"))
            and (m := _MYPY_RE.match(d.lines[0])) is not None
            and m.group("sev") != "note"
        ]
    # ruff/flake8/golangci print no severity, so their parsers report "warning".
    # No file: a run summary (rustc "aborting due to 2 previous errors"), not a finding.
    return [
        {
            "file": d.file,
            "line": d.line,
            "col": d.col,
            "severity": d.severity,
            "code": None if d.rule == "(uncoded)" else d.rule,
            "source": fmt,
            "message": d.message,
        }
        for d in diags
        if d.file
    ]


def _fallback_records(output: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ln in output.splitlines():
        m = _MSBUILD_RE.match(ln)
        if m:
            code = m["code"]
            source = "tsc" if code.startswith("TS") else "msbuild"
            out.append(_record(m["file"], m["line"], m["col"], m["sev"], code, source, m["msg"]))
            continue
        m = _CONCISE_RE.match(ln)
        if m:
            # ruff/flake8 beside another linter, or past the 400-line detection window.
            out.append(
                _record(m["file"], m["line"], m["col"], "warning", m["rule"], "concise", m["msg"])
            )
            continue
        m = _GNU_RE.match(ln)
        if not m:
            continue
        sev, col = m.group("sev"), m.group("col")
        if sev is None:
            # Only go build / go vet print no severity, always with a column;
            # anything else (log.Lshortfile, timestamps, Python warnings) is noise.
            if not (m["file"].endswith(".go") and col):
                continue
            sev = "error"
        if sev == "note":
            if out and out[-1]["source"] == "gnu":
                out[-1]["message"] += "\n" + ln
            continue
        sev = "error" if sev == "fatal error" else sev
        out.append(_record(m["file"], m["line"], col, sev, m["code"], "gnu", m["msg"]))
    # dotnet prints every diagnostic twice: once in the build log, once in the summary.
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for rec in out:
        unique.setdefault(tuple(rec.values()), rec)
    return list(unique.values())


def _record(
    file: str, line: str, col: str | None, sev: str, code: str | None, source: str, msg: str
) -> dict[str, Any]:
    return {
        "file": file,
        "line": int(line),
        "col": int(col) if col else None,
        "severity": sev,
        "code": code,
        "source": source,
        "message": msg,
    }
