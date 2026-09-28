"""CI renderings of documentation drift: markdown, GitHub annotations, SARIF.

Pure functions over finding dicts and a :class:`~.gate.GateResult`, so the CLI,
the hosted platform and the PR bot print the same thing. Two honesty rules hold
in every format: the document is named as the thing to edit (a finding is about
the prose, not the target it names), and the markdown and SARIF carry
:data:`~.constants.DETECTION_BASIS`, because a clean result from this detector
covers only the references it can resolve. Workflow-command escaping and the
annotation cap come from :mod:`repowise.core.ci.github`, and table cells, plurals
and the row cap from :mod:`repowise.core.ci.markdown`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from repowise.core.ci import github, sarif
from repowise.core.ci.markdown import ROW_LIMIT, cell, more_line, plural

from .constants import DETECTION_BASIS, HIGH_CONFIDENCE_THRESHOLD
from .gate import GateResult
from .models import DriftKind
from .serialize import fingerprint_of

SARIF_TOOL_NAME = "repowise-doc-drift"
SARIF_FINGERPRINT_KEY = "repowiseDocDrift/v1"

_RULE_TEXT: dict[DriftKind, tuple[str, str]] = {
    DriftKind.PATH: (
        "Document names a file path that does not exist",
        "A repository path written in a document resolves to no file in the tree.",
    ),
    DriftKind.LINK: (
        "Document links to a file that does not exist",
        "A relative markdown link in a document resolves to no file in the tree.",
    ),
    DriftKind.ANCHOR: (
        "Document links to a heading that does not exist",
        "A link fragment names a heading the target document no longer declares.",
    ),
    DriftKind.COMMAND: (
        "Document shows a command target that is not declared",
        "A make or npm run target shown in a document is not declared by the manifest.",
    ),
}


def _order(findings: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(
        findings,
        key=lambda f: (f["file_path"], int(f["line_number"]), str(f["kind"]), f["target"]),
    )


def _likely(finding: Mapping[str, Any]) -> str:
    return (finding.get("suggestion") or "").strip()


def _message(finding: Mapping[str, Any]) -> str:
    """The finding's reason, plus the likely replacement when one was found."""
    likely = _likely(finding)
    reason = str(finding["reason"])
    return f"{reason} Likely now: {likely}." if likely else reason


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def render_markdown(
    findings: Sequence[Mapping[str, Any]],
    *,
    gate: GateResult | None,
    documents_scanned: int | None = None,
    suppressed: int = 0,
) -> str:
    """Step-summary / PR-comment markdown: verdict first, then a capped table.

    With a gate, only the failing findings are tabulated and a passing run
    stays short. Without one, every finding is.
    """
    lines: list[str] = []
    if gate is not None:
        threshold = f"{gate.fail_on:.2f}"
        if gate.passed:
            lines.append(f"**No documentation drift at or above {threshold}.**")
        else:
            n = len(gate.failing)
            verb = "fails" if n == 1 else "fail"
            lines.append(
                f"**Documentation drift: {plural(n, 'finding')} {verb} the gate** "
                f"(confidence at or above {threshold}). Edit the document named "
                "in each row."
            )
        rows = gate.failing
    elif findings:
        lines.append(
            f"**Documentation drift: {plural(len(findings), 'finding')}.** "
            "Edit the document named in each row."
        )
        rows = list(findings)
    else:
        lines.append("**No documentation drift found.**")
        rows = []

    notes: list[str] = []
    if documents_scanned is not None:
        notes.append(f"{plural(documents_scanned, 'document')} scanned")
    if gate is not None and gate.baselined:
        notes.append(f"{len(gate.baselined)} accepted by the baseline")
    if gate is not None and gate.below_threshold:
        notes.append(f"{gate.below_threshold} below the threshold")
    if suppressed:
        notes.append(f"{plural(suppressed, 'reference')} suppressed inline")
    if notes:
        lines.append("")
        summary = "; ".join(notes)
        lines.append(summary[:1].upper() + summary[1:] + ".")

    if rows:
        lines += [
            "",
            "| Document:line | Claims this exists | Kind | Confidence | Likely now |",
            "| --- | --- | --- | --- | --- |",
        ]
        ordered = _order(rows)
        for f in ordered[:ROW_LIMIT]:
            lines.append(
                f"| {cell(f['file_path'])}:{int(f['line_number'])} "
                f"| {cell(f['target'])} | {cell(f['kind'])} "
                f"| {float(f['confidence']):.2f} | {cell(_likely(f)) or '-'} |"
            )
        if len(ordered) > ROW_LIMIT:
            lines += ["", more_line(len(ordered) - ROW_LIMIT, "findings")]

    lines += ["", f"_{DETECTION_BASIS}_"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# GitHub workflow annotations
# ---------------------------------------------------------------------------


def _annotation(level: str, finding: Mapping[str, Any]) -> str:
    return github.annotation(
        level,
        _message(finding),
        file=str(finding["file_path"]),
        line=int(finding["line_number"]),
        title=f"Doc drift ({finding['kind']})",
    )


def render_github_annotations(
    findings: Sequence[Mapping[str, Any]],
    *,
    gate: GateResult | None,
) -> list[str]:
    """``::error`` for failing findings, ``::warning`` for the rest, errors first.

    Baselined findings are accepted, so they are not annotated. Without a gate,
    the high tier is an error. Capped by :func:`~repowise.core.ci.github.cap_annotations`,
    which ends with a notice counting what it cut.
    """
    if gate is not None:
        failing = {fingerprint_of(f) for f in gate.failing}
        accepted = {fingerprint_of(f) for f in gate.baselined}
    else:
        failing = {
            fingerprint_of(f)
            for f in findings
            if float(f["confidence"]) >= HIGH_CONFIDENCE_THRESHOLD
        }
        accepted = set()
    errors: list[str] = []
    warnings: list[str] = []
    for f in _order(findings):
        fp = fingerprint_of(f)
        if fp in accepted:
            continue
        if fp in failing:
            errors.append(_annotation("error", f))
        else:
            warnings.append(_annotation("warning", f))
    return github.cap_annotations(errors + warnings, noun="documentation drift findings")


# ---------------------------------------------------------------------------
# SARIF 2.1.0
# ---------------------------------------------------------------------------


def _sarif_rules() -> list[dict]:
    return [
        sarif.rule(
            kind.value,
            f"DocDrift{kind.value.capitalize()}",
            _RULE_TEXT[kind][0],
            f"{_RULE_TEXT[kind][1]} {DETECTION_BASIS}",
            DETECTION_BASIS,
        )
        for kind in DriftKind
    ]


def render_sarif(
    findings: Sequence[Mapping[str, Any]],
    *,
    tool_version: str,
    fail_on: float = HIGH_CONFIDENCE_THRESHOLD,
) -> dict:
    """One SARIF 2.1.0 run; ``partialFingerprints`` survive line shifts.

    A finding at or above *fail_on* is an ``error``, so the level matches the gate.
    """
    results = []
    for f in _order(findings):
        confidence = float(f["confidence"])
        properties: dict[str, Any] = {
            "confidence": round(confidence, 2),
            "origin": f["origin"],
            "target": f["target"],
        }
        if likely := _likely(f):
            properties["suggestion"] = likely
            if f.get("suggestion_basis"):
                properties["suggestion_basis"] = f["suggestion_basis"]
        results.append(
            sarif.result(
                str(f["kind"]),
                "error" if confidence >= fail_on else "warning",
                _message(f),
                str(f["file_path"]),
                int(f["line_number"]),
                SARIF_FINGERPRINT_KEY,
                fingerprint_of(f),
                properties,
            )
        )
    return sarif.run(SARIF_TOOL_NAME, tool_version, _sarif_rules(), results)
