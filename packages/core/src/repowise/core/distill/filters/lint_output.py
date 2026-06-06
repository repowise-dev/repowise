"""Rule-grouped compaction of linter output.

Handles eslint (stylish), ruff (full and concise), flake8/pylint-style
single-line diagnostics, mypy, cargo clippy, and golangci-lint. Lint output
is dominated by near-identical violations, so the rendering groups by rule
id — count, a sample message, and file:line anchors — with errors before
warnings. The errors-first invariant holds: every error-severity diagnostic
is kept verbatim; only warning-severity findings collapse into groups. The
tool's own summary lines (problem totals, fixable counts) always survive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import ClassVar

from repowise.core.distill.filters.base import OutputFilter, is_error_line
from repowise.core.distill.registry import filter_registry

_COMMAND_RE = re.compile(
    r"^(eslint\b|biome (?:check|lint)\b|ruff\b(?!\s+format)|flake8\b|pylint\b|mypy\b|"
    r"cargo(?:\.exe)? clippy\b|golangci-lint\b|"
    r"npm(?:\.cmd)? run lint\b|pnpm (?:run )?lint\b|yarn (?:run )?lint\b|next(?:\.cmd)? lint\b)"
)

# eslint stylish: indented "line:col  severity  message  rule" rows under a
# file-path header line. Message and rule are separated by 2+ spaces.
_ESLINT_ROW_RE = re.compile(
    r"^\s+(?P<line>\d+):(?P<col>\d+)\s+(?P<sev>error|warning)\s+(?P<msg>.+?)\s{2,}(?P<rule>\S+)$"
)
_ESLINT_SUMMARY_RE = re.compile(r"^[✖✗x] \d+ problems?|potentially fixable")

# ruff/flake8/pylint concise: path:line:col: CODE [*] message
_CONCISE_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+):\s"
    r"(?P<rule>[A-Z]{1,4}\d{2,5})(?P<fix> \[\*\])?\s(?P<msg>.+)$"
)

# ruff full (rustc-style): "CODE [*] message" head + "--> path:line:col" frame.
_RUFF_BLOCK_RE = re.compile(r"^(?P<rule>[A-Z]{1,4}\d{2,5})(?P<fix> \[\*\])? (?P<msg>.+)$")

# clippy / rustc: "warning: message" or "error[E0308]: message" head + frame.
_CLIPPY_BLOCK_RE = re.compile(r"^(?P<sev>warning|error)(?:\[(?P<code>E\d{4})\])?: (?P<msg>.+)$")
_CLIPPY_RULE_RE = re.compile(r"clippy::[a-z_]+|#\[warn\(([a-z_:]+)\)\]")
_CLIPPY_SUMMARY_RE = re.compile(
    r"^(warning|error): .* generated \d+ warnings?|^error: could not compile"
)

_ARROW_RE = re.compile(r"^\s*-->\s(?P<file>.+?):(?P<line>\d+)(?::\d+)?\s*$")

# mypy: path:line: severity: message  [rule]
_MYPY_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+)(?::\d+)?:\s(?P<sev>error|warning|note):\s"
    r"(?P<msg>.+?)(?:\s+\[(?P<rule>[\w.-]+)\])?$"
)

# golangci-lint: path:line:col: message (linter)
_GOLANGCI_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+):\s(?P<msg>.+)\s\((?P<rule>[\w-]+)\)$"
)

_SUMMARY_RE = re.compile(r"^Found \d+ error|^\[\*\] \d+ fixable|^No errors found|^\d+ issues?[:.]")

#: Anchors shown per rule group before "+N more".
_MAX_ANCHORS = 5
#: Sample-message cap on group lines (anchors carry the location detail).
_MAX_GROUP_MSG = 140


@dataclass
class _Diag:
    rule: str
    anchor: str  # file:line
    severity: str  # "error" | "warning"
    message: str
    lines: list[str] = field(default_factory=list)
    fixable: bool = False
    file_header: str | None = None  # eslint: path line giving the rows context

    @property
    def is_error(self) -> bool:
        """Tool-declared error severity.

        Deliberately NOT ``is_error_line``-based: linter messages incidentally
        containing words like "Exception" would drag whole warning blocks into
        the verbatim section. Incidental matches are preserved line-by-line by
        the rescue tail in :func:`_render` instead.
        """
        return self.severity == "error"


@filter_registry.register
class LintOutputFilter(OutputFilter):
    name: ClassVar[str] = "lint_output"
    priority: ClassVar[int] = 25
    min_lines: ClassVar[int] = 10

    def matches_command(self, command: str) -> bool:
        return bool(_COMMAND_RE.match(command))

    def matches_content(self, output: str) -> bool:
        return _detect_format(output) is not None

    def distill(self, output: str, *, command: str = "", exit_code: int = 0) -> str:
        fmt = _detect_format(output)
        if fmt is None:
            raise ValueError("unrecognized lint output")
        lines = output.splitlines()
        diags, trailing = _PARSERS[fmt](lines)
        if not diags:
            raise ValueError(f"no diagnostics parsed from {fmt} output")
        return _render(lines, diags, trailing)


def _detect_format(output: str) -> str | None:
    lines = output.splitlines()[:400]

    def count(pattern: re.Pattern[str]) -> int:
        return sum(1 for ln in lines if pattern.match(ln))

    if count(_ESLINT_ROW_RE) >= 2:
        return "eslint"
    has_arrows = any(_ARROW_RE.match(ln) for ln in lines)
    if has_arrows and count(_CLIPPY_BLOCK_RE) >= 2:
        return "clippy"
    if has_arrows and count(_RUFF_BLOCK_RE) >= 2:
        return "ruff_full"
    if count(_MYPY_RE) >= 2:
        return "mypy"
    if count(_CONCISE_RE) >= 2:
        return "concise"
    if count(_GOLANGCI_RE) >= 2:
        return "golangci"
    return None


# -- parsers: each returns (diags, trailing summary lines) --------------------


def _parse_eslint(lines: list[str]) -> tuple[list[_Diag], list[str]]:
    diags: list[_Diag] = []
    trailing: list[str] = []
    current_file = ""
    for line in lines:
        row = _ESLINT_ROW_RE.match(line)
        if row:
            diags.append(
                _Diag(
                    rule=row.group("rule"),
                    anchor=f"{current_file}:{row.group('line')}",
                    severity=row.group("sev"),
                    message=row.group("msg"),
                    lines=[line],
                    file_header=current_file,
                )
            )
        elif _ESLINT_SUMMARY_RE.search(line.strip()):
            trailing.append(line)
        elif line.strip() and not line.startswith((" ", "\t")):
            current_file = line.strip()
    return diags, trailing


def _parse_concise(lines: list[str]) -> tuple[list[_Diag], list[str]]:
    """ruff --output-format=concise, flake8, pylint-parseable single lines."""
    diags: list[_Diag] = []
    trailing: list[str] = []
    for line in lines:
        m = _CONCISE_RE.match(line)
        if m:
            diags.append(
                _Diag(
                    rule=m.group("rule"),
                    anchor=f"{m.group('file')}:{m.group('line')}",
                    severity="warning",
                    message=m.group("msg"),
                    lines=[line],
                    fixable=bool(m.group("fix")),
                )
            )
        elif _SUMMARY_RE.match(line.strip()):
            trailing.append(line)
    return diags, trailing


def _parse_golangci(lines: list[str]) -> tuple[list[_Diag], list[str]]:
    """golangci-lint text format: diagnostic line + source echo + caret."""
    diags: list[_Diag] = []
    trailing: list[str] = []
    for line in lines:
        m = _GOLANGCI_RE.match(line)
        if m:
            diags.append(
                _Diag(
                    rule=m.group("rule"),
                    anchor=f"{m.group('file')}:{m.group('line')}",
                    severity="warning",
                    message=m.group("msg"),
                    lines=[line],
                )
            )
        elif _SUMMARY_RE.match(line.strip()):
            trailing.append(line)
        elif diags and line.strip() and not _GOLANGCI_RE.match(line):
            # source echo / caret lines attach to the open diagnostic
            diags[-1].lines.append(line)
    return diags, trailing


def _parse_mypy(lines: list[str]) -> tuple[list[_Diag], list[str]]:
    diags: list[_Diag] = []
    trailing: list[str] = []
    for line in lines:
        m = _MYPY_RE.match(line)
        if m:
            sev = m.group("sev")
            if sev == "note" and diags:
                # notes elaborate on the previous diagnostic
                diags[-1].lines.append(line)
                continue
            diags.append(
                _Diag(
                    rule=m.group("rule") or "(uncoded)",
                    anchor=f"{m.group('file')}:{m.group('line')}",
                    severity="error" if sev == "error" else "warning",
                    message=m.group("msg"),
                    lines=[line],
                )
            )
        elif _SUMMARY_RE.match(line.strip()):
            trailing.append(line)
    return diags, trailing


def _parse_blocks(
    lines: list[str], head_re: re.Pattern[str], *, clippy: bool
) -> tuple[list[_Diag], list[str]]:
    """rustc-style blocks: head line, --> location, code frame, notes/help."""
    diags: list[_Diag] = []
    trailing: list[str] = []
    open_diag: _Diag | None = None
    for line in lines:
        if _SUMMARY_RE.match(line.strip()) or (clippy and _CLIPPY_SUMMARY_RE.match(line)):
            open_diag = None
            trailing.append(line)
            continue
        head = head_re.match(line)
        if head:
            groups = head.groupdict()
            open_diag = _Diag(
                rule=groups.get("code") or groups.get("rule") or "",
                anchor="",
                severity=groups.get("sev") or "warning",
                message=groups["msg"],
                lines=[line],
                fixable=bool(groups.get("fix")),
            )
            diags.append(open_diag)
            continue
        if open_diag is None:
            continue
        arrow = _ARROW_RE.match(line)
        if arrow and not open_diag.anchor:
            open_diag.anchor = f"{arrow.group('file')}:{arrow.group('line')}"
        if clippy and not open_diag.rule.startswith("clippy::"):
            rule_m = _CLIPPY_RULE_RE.search(line)
            if rule_m:
                open_diag.rule = (
                    rule_m.group(0)
                    if rule_m.group(0).startswith("clippy")
                    else (rule_m.group(1) or open_diag.rule)
                )
        if line.strip():
            open_diag.lines.append(line)
        else:
            open_diag = None  # blank line closes the block
    for d in diags:
        if not d.rule:
            d.rule = "(uncoded)"
    return diags, trailing


_PARSERS = {
    "eslint": _parse_eslint,
    "concise": _parse_concise,
    "golangci": _parse_golangci,
    "mypy": _parse_mypy,
    "ruff_full": lambda lines: _parse_blocks(lines, _RUFF_BLOCK_RE, clippy=False),
    "clippy": lambda lines: _parse_blocks(lines, _CLIPPY_BLOCK_RE, clippy=True),
}


# -- rendering -----------------------------------------------------------------


def _render(raw_lines: list[str], diags: list[_Diag], trailing: list[str]) -> str:
    errors, incidental, grouped = [], [], []
    for d in diags:
        if d.is_error:
            errors.append(d)
        elif any(is_error_line(ln) for ln in d.lines):
            # Warning-severity diags that *incidentally* contain
            # error-classified lines (an errcheck message, a rule about
            # exceptions) surface those lines verbatim instead of being
            # grouped — grouping them would emit the sample message AND the
            # rescued raw lines, a net loss.
            incidental.append(d)
        else:
            grouped.append(d)

    groups: dict[str, list[_Diag]] = {}
    for d in grouped:
        groups.setdefault(d.rule, []).append(d)

    out: list[str] = []
    summary = f"lint: {len(diags)} findings"
    parts = []
    if errors or incidental:
        parts.append(f"{len(errors) + len(incidental)} kept verbatim")
    if grouped:
        parts.append(f"{len(grouped)} collapsed into {len(groups)} rules")
    if parts:
        summary += " — " + ", ".join(parts)
    out.append(summary)
    out.append("")

    # Errors first, verbatim (identical repeats collapse to one annotated
    # copy — the original line survives as the prefix), with their eslint
    # file-header context.
    last_header: str | None = None
    seen: dict[str, int] = {}  # error line -> index in out
    repeats: dict[int, int] = {}  # index in out -> extra occurrences
    for d in errors:
        if d.file_header and d.file_header != last_header:
            out.append(d.file_header)
            last_header = d.file_header
        for ln in d.lines:
            if len(d.lines) == 1 and ln in seen:
                repeats[seen[ln]] = repeats.get(seen[ln], 0) + 1
            else:
                seen[ln] = len(out)
                out.append(ln)
    # Incidental error-classified lines: only the tripping lines, deduped.
    for d in incidental:
        for ln in d.lines:
            if not is_error_line(ln):
                continue
            if ln in seen:
                repeats[seen[ln]] = repeats.get(seen[ln], 0) + 1
            else:
                seen[ln] = len(out)
                out.append(ln)
    for idx, extra in repeats.items():
        out[idx] = f"{out[idx]}  (×{extra + 1})"  # noqa: RUF001
    if errors or incidental:
        out.append("")

    # Warning-severity findings grouped by rule, most frequent first.
    for rule, ds in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        fixable = sum(1 for d in ds if d.fixable)
        fix_note = f", {fixable} fixable" if fixable else ""
        msg = ds[0].message
        if len(msg) > _MAX_GROUP_MSG:
            msg = msg[: _MAX_GROUP_MSG - 1] + "…"
        anchors = [d.anchor for d in ds if d.anchor]
        shown = anchors[:_MAX_ANCHORS]
        more = f", +{len(anchors) - len(shown)} more" if len(anchors) > len(shown) else ""
        loc = f"  [{', '.join(shown)}{more}]" if shown else ""
        out.append(f"  {rule} ×{len(ds)}{fix_note} — {msg}{loc}")  # noqa: RUF001

    if trailing:
        out.append("")
        out.extend(trailing)

    # Belt-and-braces for the cardinal invariant: any raw error-classified
    # line that did not survive the grouping is appended verbatim (identical
    # repeats collapse to one annotated copy).
    rendered = "\n".join(out)
    rescued: dict[str, int] = {}
    for ln in raw_lines:
        if ln.strip() and is_error_line(ln) and ln not in rendered:
            rescued[ln] = rescued.get(ln, 0) + 1
    if rescued:
        out.append("")
        out.extend(ln if n == 1 else f"{ln}  (×{n})" for ln, n in rescued.items())  # noqa: RUF001
    return "\n".join(out)
