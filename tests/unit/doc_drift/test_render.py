"""CI renderings: markdown summary, GitHub annotations, SARIF, GitLab Code Quality."""

from __future__ import annotations

import json

from repowise.core.analysis.doc_drift.constants import DETECTION_BASIS
from repowise.core.analysis.doc_drift.gate import evaluate_gate
from repowise.core.analysis.doc_drift.render import (
    SARIF_FINGERPRINT_KEY,
    render_github_annotations,
    render_gitlab,
    render_markdown,
    render_sarif,
)
from repowise.core.analysis.doc_drift.serialize import derive_doc_drift_fingerprint
from repowise.core.ci.github import ANNOTATION_LIMIT
from repowise.core.ci.markdown import ROW_LIMIT


def _f(
    target: str,
    confidence: float = 0.9,
    *,
    doc: str = "docs/a.md",
    line: int = 3,
    kind: str = "path",
    reason: str | None = None,
    suggestion: str = "",
) -> dict:
    out = {
        "file_path": doc,
        "line_number": line,
        "kind": kind,
        "target": target,
        "confidence": confidence,
        "origin": "path_no_candidate",
        "reason": reason or f"Document names {target}, which no longer exists.",
        "raw": target,
        "context": "",
    }
    if suggestion:
        out["suggestion"] = suggestion
        out["suggestion_basis"] = "package_split"
    return out


# -- markdown ---------------------------------------------------------------


def test_passing_markdown_is_short_and_says_so():
    findings = [_f("a.py", 0.5)]
    md = render_markdown(findings, gate=evaluate_gate(findings), documents_scanned=4)
    assert md.startswith("**No documentation drift at or above 0.70.**")
    assert "|" not in md
    assert "4 documents scanned" in md and "1 below the threshold" in md
    assert DETECTION_BASIS in md


def test_failing_markdown_verdict_first_then_table():
    findings = [_f("b.py", doc="docs/z.md"), _f("a.py", line=9), _f("c.py", 0.4)]
    md = render_markdown(findings, gate=evaluate_gate(findings), suppressed=2)
    first = md.splitlines()[0]
    assert first.startswith("**Documentation drift: 2 findings fail the gate**")
    assert "document" in first.lower()
    assert "2 references suppressed inline" in md
    rows = [line for line in md.splitlines() if line.startswith("| docs/")]
    # Grouped by document, and the below-threshold finding stays out.
    assert rows[0].startswith("| docs/a.md:9 ") and rows[1].startswith("| docs/z.md:3 ")
    assert len(rows) == 2


def test_markdown_escapes_cells_and_shows_suggestion():
    findings = [_f("a|b`c.py", suggestion="pkg/a/")]
    md = render_markdown(findings, gate=evaluate_gate(findings))
    row = next(line for line in md.splitlines() if line.startswith("| docs/"))
    assert "a\\|b\\`c.py" in row
    assert "| pkg/a/ |" in row


def test_markdown_caps_rows():
    findings = [_f(f"f{i:02}.py", line=i + 1) for i in range(ROW_LIMIT + 5)]
    md = render_markdown(findings, gate=evaluate_gate(findings))
    rows = [line for line in md.splitlines() if line.startswith("| docs/")]
    assert len(rows) == ROW_LIMIT
    assert "and 5 more findings." in md


def test_markdown_without_gate_lists_everything():
    assert render_markdown([], gate=None).startswith("**No documentation drift found.**")
    md = render_markdown([_f("a.py", 0.4)], gate=None)
    assert md.startswith("**Documentation drift: 1 finding.**")
    assert "| docs/a.md:3 " in md


def test_markdown_reports_baselined_count():
    findings = [_f("a.py")]
    fp = derive_doc_drift_fingerprint("docs/a.md", "path", "a.py")
    md = render_markdown(findings, gate=evaluate_gate(findings, baseline=frozenset({fp})))
    assert md.startswith("**No documentation drift")
    assert "1 accepted by the baseline" in md


# -- annotations ------------------------------------------------------------


def test_annotations_errors_first_and_escaped():
    findings = [
        _f("low.py", 0.4, doc="docs/a.md"),
        _f("x.py", reason="50% broken\nnext", doc="docs/a,b:c.md", line=7, suggestion="y/"),
    ]
    lines = render_github_annotations(findings, gate=evaluate_gate(findings))
    assert lines[0] == (
        "::error file=docs/a%2Cb%3Ac.md,line=7,title=Doc drift (path)"
        "::50%25 broken%0Anext Likely now: y/."
    )
    assert lines[1].startswith("::warning file=docs/a.md,line=3,")
    assert len(lines) == 2


def test_baselined_findings_are_not_annotated():
    findings = [_f("a.py")]
    fp = derive_doc_drift_fingerprint("docs/a.md", "path", "a.py")
    assert render_github_annotations(
        findings, gate=evaluate_gate(findings, baseline=frozenset({fp}))
    ) == []


def test_annotations_are_capped_with_errors_kept_and_the_overflow_counted():
    findings = [_f(f"w{i}.py", 0.4, line=i + 1) for i in range(60)]
    findings.append(_f("err.py", doc="docs/z.md"))
    lines = render_github_annotations(findings, gate=evaluate_gate(findings))
    assert len(lines) == ANNOTATION_LIMIT + 1
    assert lines[0].startswith("::error ")
    assert lines[-1].startswith("::notice::51 more documentation drift findings")


# -- SARIF ------------------------------------------------------------------


def test_sarif_required_fields():
    findings = [_f("a.py", suggestion="pkg/a/"), _f("R.md#x", 0.5, kind="anchor")]
    sarif = render_sarif(findings, tool_version="1.2.3")
    assert sarif["$schema"] == "https://json.schemastore.org/sarif-2.1.0.json"
    assert sarif["version"] == "2.1.0"
    (run,) = sarif["runs"]
    driver = run["tool"]["driver"]
    assert driver["name"] == "repowise-doc-drift"
    assert driver["version"] == "1.2.3"
    assert driver["informationUri"] == "https://repowise.dev"
    assert [r["id"] for r in driver["rules"]] == ["path", "link", "anchor", "command"]
    assert all(DETECTION_BASIS in r["fullDescription"]["text"] for r in driver["rules"])

    by_rule = {r["ruleId"]: r for r in run["results"]}
    path = by_rule["path"]
    assert path["level"] == "error"
    assert path["message"]["text"].endswith("Likely now: pkg/a/.")
    loc = path["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"] == {"uri": "docs/a.md", "uriBaseId": "%SRCROOT%"}
    assert loc["region"] == {"startLine": 3}
    assert path["partialFingerprints"] == {
        SARIF_FINGERPRINT_KEY: derive_doc_drift_fingerprint("docs/a.md", "path", "a.py")
    }
    assert path["properties"]["suggestion"] == "pkg/a/"
    assert by_rule["anchor"]["level"] == "warning"
    assert "suggestion" not in by_rule["anchor"]["properties"]


def test_sarif_level_follows_fail_on_and_uri_is_percent_encoded():
    findings = [_f("a.py", 0.5, doc="docs/my notes#1.md")]
    (result,) = render_sarif(findings, tool_version="x", fail_on=0.4)["runs"][0]["results"]
    assert result["level"] == "error"
    uri = result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
    assert uri == "docs/my%20notes%231.md"
    (default,) = render_sarif(findings, tool_version="x")["runs"][0]["results"]
    assert default["level"] == "warning"


def test_sarif_is_deterministic():
    findings = [_f("b.py", doc="docs/z.md"), _f("a.py"), _f("c.py", line=1)]
    one = json.dumps(render_sarif(findings, tool_version="x"), sort_keys=True)
    two = json.dumps(render_sarif(list(reversed(findings)), tool_version="x"), sort_keys=True)
    assert one == two
    uris = [
        (r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"],
         r["locations"][0]["physicalLocation"]["region"]["startLine"])
        for r in render_sarif(findings, tool_version="x")["runs"][0]["results"]
    ]
    assert uris == [("docs/a.md", 1), ("docs/a.md", 3), ("docs/z.md", 3)]


# -- GitLab Code Quality ------------------------------------------------------


def test_gitlab_severity_follows_the_gate_and_drops_the_baseline():
    accepted = _f("gone.py")
    findings = [_f("a.py", suggestion="pkg/a/"), _f("R.md#x", 0.5, kind="anchor"), accepted]
    fp = derive_doc_drift_fingerprint("docs/a.md", "path", "gone.py")
    issues = render_gitlab(findings, accepted=frozenset({fp}))
    assert issues == [
        {
            "description": "Document names R.md#x, which no longer exists.",
            "check_name": "repowise-doc-drift/anchor",
            "fingerprint": derive_doc_drift_fingerprint("docs/a.md", "anchor", "R.md#x"),
            "severity": "minor",
            "location": {"path": "docs/a.md", "lines": {"begin": 3}},
        },
        {
            "description": "Document names a.py, which no longer exists. Likely now: pkg/a/.",
            "check_name": "repowise-doc-drift/path",
            "fingerprint": derive_doc_drift_fingerprint("docs/a.md", "path", "a.py"),
            "severity": "major",
            "location": {"path": "docs/a.md", "lines": {"begin": 3}},
        },
    ]
    (lowered,) = render_gitlab([_f("R.md#x", 0.5, kind="anchor")], fail_on=0.4)
    assert lowered["severity"] == "major"


def test_gitlab_is_deterministic_and_unique():
    findings = [_f("b.py", doc="docs/z.md"), _f("a.py"), _f("a.py", line=9)]
    one = render_gitlab(findings)
    assert one == render_gitlab(list(reversed(findings)))
    fps = [i["fingerprint"] for i in one]
    assert len(set(fps)) == 3 and fps[1] == fps[0] + ":2"
