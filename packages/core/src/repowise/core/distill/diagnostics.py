"""Structured diagnostics for ``repowise distill --format json``.

Reuses the lint filter's parsers so a compiler, type-checker or linter run
becomes ``file/line/col/severity/code/source/message`` records plus a
top-level ``status`` (clean | errors | unavailable | unparsed).
"""

from __future__ import annotations

import re
from typing import Any

from repowise.core.distill.filters.lint_output import _MYPY_RE, _PARSERS, _detect_format

_FIELDS = ("file", "line", "col", "severity", "code", "source", "message")

# MSBuild / tsc: path(line,col): error CODE: message [project]
_MSBUILD_RE = re.compile(
    r"^(?P<file>\S.*?)\((?P<line>\d+),(?P<col>\d+)\):\s"
    r"(?P<sev>error|warning)\s(?P<code>[A-Za-z]+\d+):\s(?P<msg>.+?)"
    r"(?:\s+\[[^\]]*proj[^\]]*\])?\s*$"
)
# gcc / clang / javac / go: [C:]path:line[:col]: [severity:] message [-Wcode]
_GNU_RE = re.compile(
    r"^(?P<file>(?:[A-Za-z]:)?[^\s:]+):(?P<line>\d+)(?::(?P<col>\d+))?:\s"
    r"(?:(?P<sev>fatal error|error|warning|note):\s)?(?P<msg>.+?)"
    r"(?:\s+\[(?P<code>[^\]\s]+)\])?\s*$"
)
# The shell's own "no such program" line, not the phrase quoted inside other output.
_NOT_FOUND_RE = re.compile(
    r"^(?:\S+: (?:line \d+: |\d+: )?\S+: command not found"  # bash / sh
    r"|zsh: command not found: \S+"
    r"|'\S+' is not recognized as an internal or external command"  # cmd.exe
    r"|\S+ : The term '\S+' is not recognized)"  # PowerShell
)


def parse_diagnostics(output: str, exit_code: int) -> dict[str, Any]:
    """Parse *output* into ``{status, source, exit_code, [reason], diagnostics}``."""
    lint = _lint_records(output)
    seen = {(r["file"], r["line"]) for r in lint}
    # Two tools in one run (tsc + eslint, mypy + gcc): keep both, once each.
    records = lint + [r for r in _fallback_records(output) if (r["file"], r["line"]) not in seen]
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
    return [
        dict(
            zip(
                _FIELDS,
                (
                    d.file,
                    d.line,
                    d.col,
                    d.severity,
                    None if d.rule == "(uncoded)" else d.rule,
                    fmt,
                    d.message,
                ),
                strict=True,
            )
        )
        for d in diags
    ]


def _fallback_records(output: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ln in output.splitlines():
        m = _MSBUILD_RE.match(ln)
        if m:
            code = m.group("code")
            source = "tsc" if code.startswith("TS") else "msbuild"
            values: tuple[Any, ...] = (
                m["file"],
                int(m["line"]),
                int(m["col"]),
                m["sev"],
                code,
                source,
                m["msg"],
            )
            out.append(dict(zip(_FIELDS, values, strict=True)))
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
            if out:
                out[-1]["message"] += "\n" + ln
            continue
        values = (
            m["file"],
            int(m["line"]),
            int(col) if col else None,
            "error" if sev == "fatal error" else sev,
            m["code"],
            "gnu",
            m["msg"],
        )
        out.append(dict(zip(_FIELDS, values, strict=True)))
    # dotnet prints every diagnostic twice: once in the build log, once in the summary.
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for rec in out:
        unique.setdefault(tuple(rec.values()), rec)
    return list(unique.values())
