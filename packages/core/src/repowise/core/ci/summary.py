"""One summary over every gate a CI run judged: a sticky pull-request comment,
a job-summary table and a combined verdict.

Each gate maps its own result to a :class:`GateSummary` (a ``gate_summary``
function beside it); this module renders any list of them. Everything is a
pure function over in-memory values, so a caller that builds gate results
without a checkout renders the same comment the Action posts.

Every finding line in the comment ends in a hidden marker naming the gate and
the finding's line-free fingerprint, so a later run, or any other reader of
the comment, can tell which findings are gone (:func:`fingerprints_in`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .markdown import ROW_LIMIT, cell, code, more_line, plural

PASS, WARN, FAIL = "pass", "warn", "fail"
#: The gate could not run (exit code 2); the combined verdict counts it as a fail.
ERROR = "error"
#: The gate exited 0 but left no result to read; the combined verdict counts it as a warning.
NO_RESULT = "no_result"
VERDICTS = (PASS, WARN, FAIL, ERROR, NO_RESULT)
#: Verdicts of a gate that judged nothing this run.
_NOT_JUDGED = frozenset({ERROR, NO_RESULT})

#: Finding levels, as SARIF names them.
LEVELS = ("error", "warning", "note")

#: Opens the sticky comment; a run finds its earlier comment by it.
COMMENT_MARKER = "<!-- repowise-ci-summary -->"
#: Kept out of a comment key, so a shell can derive the same key with ``tr``.
_KEY_UNSAFE = re.compile(r"[^A-Za-z0-9_./]")
#: Longest message or headline shown. With :data:`ROW_LIMIT` rows per gate this
#: keeps a comment far under GitHub's 65,536 characters.
MESSAGE_LIMIT = 300
_FINGERPRINT_TAG = "repowise-fingerprint:v1:"
_FINGERPRINT_RE = re.compile(r"<!-- " + re.escape(_FINGERPRINT_TAG) + r"([A-Za-z0-9_.:-]+) -->")
#: Kept out of a marker: anything that could end the HTML comment early.
_UNSAFE = re.compile(r"[^A-Za-z0-9_.:-]")

_TITLES = {
    "coverage": "Coverage",
    "doc-drift": "Doc drift",
    "security": "Security",
    "health": "Health",
    "risk": "Change risk",
}
_VERDICT_WORDS = {
    PASS: "Pass",
    WARN: "Warn",
    FAIL: "Fail",
    ERROR: "Could not evaluate",
    NO_RESULT: "Could not evaluate",
}


@dataclass(frozen=True)
class SummaryFinding:
    """One finding as the summary lists it."""

    level: str
    path: str
    line: int | None
    message: str
    #: Line-free, as the gate's SARIF ``partialFingerprints`` carries it.
    fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "file_path": self.path,
            "line_number": self.line,
            "message": self.message,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> SummaryFinding:
        line = d.get("line_number")
        return cls(
            str(d["level"]),
            str(d["file_path"]),
            int(line) if line else None,
            str(d["message"]),
            str(d["fingerprint"]),
        )


@dataclass(frozen=True)
class GateSummary:
    """One gate's verdict, a one-line headline, and the findings worth listing."""

    gate: str
    verdict: str
    headline: str
    findings: tuple[SummaryFinding, ...] = ()

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(f"unknown gate verdict {self.verdict!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "verdict": self.verdict,
            "headline": self.headline,
            "findings": [f.to_dict() for f in self.findings],
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> GateSummary:
        return cls(
            str(d["gate"]),
            str(d["verdict"]),
            str(d.get("headline") or ""),
            tuple(SummaryFinding.from_dict(f) for f in d.get("findings") or ()),
        )


def with_exit_code(summary: GateSummary | None, gate: str, exit_code: int) -> GateSummary:
    """*summary* reconciled with the gate's exit code, which decides fail and error.

    A gate that exits 1 failed even when its own result reads as a pass (a check
    layered on top, such as a grown baseline); exit 2, or no summary at all,
    means it could not be evaluated. A gate that exits 0 with no summary (an
    older release) reads as not evaluated too, but warns rather than fails.
    """
    if exit_code == 0 and summary is None:
        return GateSummary(gate, NO_RESULT, "Exited 0 but left no result to read.")
    if exit_code not in (0, 1) or summary is None:
        headline = summary.headline if summary else "See the job log for why."
        findings = summary.findings if summary else ()
        return GateSummary(gate, ERROR, headline, findings)
    if exit_code == 1 and summary.verdict != FAIL:
        headline = f"{summary.headline} The gate exited 1; see the job log for why.".lstrip()
        return GateSummary(gate, FAIL, headline, summary.findings)
    if exit_code == 0 and summary.verdict in (FAIL, *_NOT_JUDGED):
        return GateSummary(gate, WARN, summary.headline, summary.findings)
    return summary


def combined_verdict(gates: Iterable[GateSummary]) -> str:
    """``fail`` when any gate failed or could not run, else ``warn`` on any warning
    or a gate that left no result, else ``pass``."""
    verdicts = {g.verdict for g in gates}
    if verdicts & {FAIL, ERROR}:
        return FAIL
    return WARN if verdicts & {WARN, NO_RESULT} else PASS


def comment_marker(key: str = "") -> str:
    """The marker opening the comment; *key* (a working directory) keeps two
    uses of the Action on one pull request apart."""
    key = _KEY_UNSAFE.sub("_", key)
    return f"<!-- repowise-ci-summary:{key} -->" if key else COMMENT_MARKER


def fingerprint_marker(gate: str, fingerprint: str) -> str:
    """The hidden ``<!-- repowise-fingerprint:v1:<gate>:<id> -->`` marker for one finding."""
    return f"<!-- {_FINGERPRINT_TAG}{marker_key(gate, fingerprint)} -->"


def marker_key(gate: str, fingerprint: str) -> str:
    """``<gate>:<id>``, the key :func:`fingerprints_in` returns, safe inside a marker."""
    return f"{_safe(gate)}:{_safe(fingerprint)}"


def _safe(text: str) -> str:
    return re.sub(r"-{2,}", "-", _UNSAFE.sub("_", text))


def fingerprints_in(body: str) -> frozenset[str]:
    """The ``<gate>:<id>`` keys an earlier comment's finding markers carry."""
    return frozenset(_FINGERPRINT_RE.findall(body or ""))


def render_comment(
    gates: Sequence[GateSummary], *, previous: str | None = None, key: str = ""
) -> str:
    """The sticky comment: its marker (:func:`comment_marker`), the verdict
    table, then each gate's findings.

    Each gate lists its first :data:`~.markdown.ROW_LIMIT` findings, errors
    first. With *previous* (the comment this replaces), findings it listed
    that a gate judged this run no longer reports are counted as resolved; a
    gate that judged nothing resolves nothing.
    """
    lines = [comment_marker(key), *_table(gates)]
    for g in gates:
        if g.findings:
            lines += ["", f"#### {cell(_title(g.gate))}", *_finding_lines(g)]
    if previous is not None:
        judged = {_safe(g.gate) for g in gates if g.verdict not in _NOT_JUDGED}
        current = {marker_key(g.gate, f.fingerprint) for g in gates for f in g.findings}
        earlier = {k for k in fingerprints_in(previous) if k.split(":", 1)[0] in judged}
        resolved = len(earlier - current)
        if resolved:
            lines += ["", f"Resolved since the last run: {plural(resolved, 'finding')}."]
    return "\n".join(lines) + "\n"


def render_job_summary(gates: Sequence[GateSummary]) -> str:
    """The verdict table alone; each gate writes its own details to the job summary."""
    return "\n".join(_table(gates)) + "\n"


def _table(gates: Sequence[GateSummary]) -> list[str]:
    verdict = combined_verdict(gates)
    lines = [f"### Repowise gates: {_VERDICT_WORDS[verdict].lower()}", ""]
    if not gates:
        return [*lines, "No gate ran."]
    lines += ["| Gate | Result | Summary |", "| --- | --- | --- |"]
    lines += [
        f"| {cell(_title(g.gate))} | {_VERDICT_WORDS[g.verdict]} | {_text(g.headline)} |"
        for g in gates
    ]
    return lines


_LEVEL_ORDER = {level: i for i, level in enumerate(LEVELS)}


def _finding_lines(g: GateSummary) -> list[str]:
    ordered = sorted(
        g.findings, key=lambda f: (_LEVEL_ORDER.get(f.level, len(LEVELS)), f.path, f.line or 0)
    )
    out = [
        f"- {f.level} {code(f'{f.path}:{f.line}' if f.line else f.path)} {_text(f.message)} "
        f"{fingerprint_marker(g.gate, f.fingerprint)}"
        for f in ordered[:ROW_LIMIT]
    ]
    if rest := more_line(len(ordered) - ROW_LIMIT, "findings"):
        out.append(f"- {rest[0].upper()}{rest[1:]} See the job summary.")
    return out


def _text(text: str) -> str:
    """*text* from the change itself made inert: one line, capped, escaped, no mentions.

    A zero-width space after ``@`` stops a team or user mention, and after ``[``
    a link or an image.
    """
    text = " ".join(text.split())
    if len(text) > MESSAGE_LIMIT:
        text = text[: MESSAGE_LIMIT - 1].rstrip() + "\u2026"
    return cell(text).replace("@", "@\u200b").replace("[", "[\u200b")


def _title(gate: str) -> str:
    return _TITLES.get(gate, gate)
