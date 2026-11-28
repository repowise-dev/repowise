"""Structured diagnostics for ``repowise distill --format json``.

Reuses the lint filter's parsers so a compiler, type-checker or linter run
becomes ``file/line/col/severity/code/source/message`` records plus a
top-level ``status`` (clean | errors | unavailable | unparsed).
"""

from __future__ import annotations

import re
from typing import Any

from repowise.core.distill.filters.lint_output import _PARSERS, _detect_format

# MSBuild / tsc: path(line,col): error CODE: message
_MSBUILD_RE = re.compile(
    r"^(?P<file>[^\s(][^(]*?)\((?P<line>\d+),(?P<col>\d+)\):\s"
    r"(?P<sev>error|warning)\s(?P<code>[A-Za-z]+\d+):\s(?P<msg>.+?)\s*$"
)
# gcc / clang / javac / go: path:line[:col]: [severity:] message
# shortcut: rejects Windows drive paths (C:\...), widen when a Windows toolchain needs it.
_GNU_RE = re.compile(
    r"^(?P<file>[^\s:]+):(?P<line>\d+)(?::(?P<col>\d+))?:\s"
    r"(?:(?P<sev>fatal error|error|warning|note):\s)?(?P<msg>.+?)\s*$"
)

_NOT_FOUND = ("command not found", "is not recognized")


def parse_diagnostics(output: str, exit_code: int) -> dict[str, Any]:
    """Parse *output* into ``{status, source, exit_code, [reason], diagnostics}``."""
    records = _lint_records(output) or _fallback_records(output)
    lines = [ln for ln in output.splitlines() if ln.strip()]
    not_found = exit_code != 0 and not records and any(s in output for s in _NOT_FOUND)
    reason = None
    if exit_code in (127, 9009) or not_found:
        status, reason = "unavailable", (lines[-1].strip() if lines else "")
    elif records:
        status = "errors"
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


def _record(
    file: str,
    line: int | None,
    col: int | None,
    severity: str,
    code: str | None,
    source: str,
    message: str,
) -> dict[str, Any]:
    return {
        "file": file,
        "line": line,
        "col": col,
        "severity": severity,
        "code": code,
        "source": source,
        "message": message,
    }


def _lint_records(output: str) -> list[dict[str, Any]]:
    fmt = _detect_format(output, min_hits=1)
    if fmt is None:
        return []
    # shortcut: concise/golangci severity is always "warning", upgrade when the parsers read real severities.
    # shortcut: GNU fatal-error lines mixed into mypy-detected output are dropped, fold them in if seen.
    diags, _trailing = _PARSERS[fmt](output.splitlines())
    out = []
    for d in diags:
        source = fmt
        if fmt == "mypy":
            # _MYPY_RE also matches gcc/clang/javac lines.
            source = "mypy" if d.file.endswith((".py", ".pyi")) else "gnu"
        code = None if d.rule == "(uncoded)" else d.rule
        out.append(_record(d.file, d.line, d.col, d.severity, code, source, d.message))
    return out


def _fallback_records(output: str) -> list[dict[str, Any]]:
    # shortcut: dotnet prints each error twice (build log + summary), dedupe if users complain.
    out: list[dict[str, Any]] = []
    for ln in output.splitlines():
        m = _MSBUILD_RE.match(ln)
        if m:
            code = m.group("code")
            source = "tsc" if code.startswith("TS") else "msbuild"
            out.append(
                _record(
                    m.group("file"),
                    int(m.group("line")),
                    int(m.group("col")),
                    m.group("sev"),
                    code,
                    source,
                    m.group("msg"),
                )
            )
            continue
        m = _GNU_RE.match(ln)
        if not m:
            continue
        sev = m.group("sev") or "error"  # go vet / go build print no severity
        if sev == "note":
            if out:
                out[-1]["message"] += "\n" + ln
            continue
        col = m.group("col")
        out.append(
            _record(
                m.group("file"),
                int(m.group("line")),
                int(col) if col else None,
                "error" if sev == "fatal error" else sev,
                None,
                "gnu",
                m.group("msg"),
            )
        )
    return out
