"""``security_gate``: what a change adds, scoped to its lines and its commits.

Scans run against real temp git repositories, because the scoping (changed
lines at the head, added lines per commit) is the thing under test.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from repowise.core.analysis import security_gate
from repowise.core.analysis.changed_lines import changed_lines
from repowise.core.analysis.doc_drift.render import render_sarif as doc_drift_sarif
from repowise.core.analysis.security_gate import (
    evaluate,
    fingerprint_of,
    github_annotations,
    render_markdown,
    render_sarif,
    scan_change,
    write_baseline,
)
from repowise.core.analysis.security_scan import _PATTERNS, _SPANNING_PATTERNS
from repowise.core.ci.baseline import read_baseline, read_entries

KEY = "AKIA" + "QZXNRTVYWMPKLBHG"
PEM_BODY = "MIIEpAIBAAKCAQEAu1SU1LfVLPHCozMxH2Mo4lgOEePzNm0tRgeLezV6ffAt0gunVTLw"


def _git(cwd, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def _write(repo, name: str, text: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _commit(repo, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t.co")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "core.autocrlf", "false")
    _write(tmp_path, "app.py", "import os\n\nos.system('old')\n\ndef run():\n    return 1\n")
    _commit(tmp_path, "base")
    _git(tmp_path, "switch", "-qc", "feature")
    return tmp_path


def _scan(repo, revspec: str = "main...HEAD"):
    changed, label = changed_lines(str(repo), revspec)
    return scan_change(str(repo), changed, label)


def _kinds(scan) -> list[tuple[str, int, bool]]:
    return sorted((f["kind"], f["line_number"], f["commit"] is not None) for f in scan.findings)


# ---------------------------------------------------------------------------
# Line scoping
# ---------------------------------------------------------------------------


def test_only_findings_on_changed_lines_count(repo):
    _write(
        repo,
        "app.py",
        "import os\n\nos.system('old')\n\ndef run():\n    return eval('1')\n",
    )
    _commit(repo, "eval")
    scan = _scan(repo)
    # The pre-existing os.system on line 3 is untouched and stays out.
    assert _kinds(scan) == [("eval_call", 6, False)]
    assert scan.files_scanned == 1 and scan.commits_scanned == 1


def test_a_multi_line_call_counts_when_only_its_last_line_changed(repo):
    _write(repo, "run.py", "import subprocess\nsubprocess.run(\n    cmd,\n    shell=False,\n)\n")
    _commit(repo, "safe call")
    base = _git(repo, "rev-parse", "HEAD").strip()
    _write(repo, "run.py", "import subprocess\nsubprocess.run(\n    cmd,\n    shell=True,\n)\n")
    _commit(repo, "shell")
    scan = _scan(repo, f"{base}..HEAD")
    assert _kinds(scan) == [("subprocess_shell_true", 2, False)]


def test_a_pem_key_counts_when_only_its_body_changed(repo):
    pem = "-----BEGIN RSA PRIVATE KEY-----\n{}\n-----END RSA PRIVATE KEY-----\n"
    _write(repo, "key.pem", pem.format("x"))
    _commit(repo, "placeholder")
    base = _git(repo, "rev-parse", "HEAD").strip()
    _write(repo, "key.pem", pem.format(PEM_BODY))
    _commit(repo, "real key")
    scan = _scan(repo, f"{base}..HEAD")
    assert [(f["kind"], f["line_number"]) for f in scan.findings] == [("private_key_pem", 1)]


def test_a_document_reports_secrets_but_not_code_smells(repo):
    _write(repo, "README.md", f"Call `pickle.loads` and `os.system`.\n\nkey: {KEY}\n")
    _commit(repo, "docs")
    assert [f["kind"] for f in _scan(repo).findings] == ["aws_access_key"]


# ---------------------------------------------------------------------------
# Secrets inside the change
# ---------------------------------------------------------------------------


def test_a_secret_added_then_deleted_inside_the_change_is_caught(repo):
    _write(repo, "cfg.py", f"AWS = '{KEY}'\n")
    _commit(repo, "add key")
    introduced = _git(repo, "rev-parse", "HEAD").strip()
    _write(repo, "cfg.py", "AWS = None\n")
    _commit(repo, "remove key")
    scan = _scan(repo)
    (finding,) = scan.findings
    assert finding["kind"] == "aws_access_key"
    assert finding["commit"] == introduced
    assert scan.commits_scanned == 2


def test_a_secret_still_at_the_head_is_reported_once_at_the_head(repo):
    _write(repo, "cfg.py", f"AWS = '{KEY}'\n")
    _commit(repo, "add key")
    _write(repo, "other.py", "x = 1\n")
    _commit(repo, "unrelated")
    assert _kinds(_scan(repo)) == [("aws_access_key", 1, False)]


def test_a_secret_that_predates_the_change_is_not_blamed_on_it(repo):
    _git(repo, "switch", "-q", "main")
    _write(repo, "cfg.py", f"AWS = '{KEY}'\nX = 1\n")
    _commit(repo, "old key on main")
    _git(repo, "switch", "-qc", "later")
    _write(repo, "cfg.py", f"AWS = '{KEY}'\nX = 2\n")
    _commit(repo, "touch another line")
    assert _scan(repo).findings == []


def test_code_smells_in_intermediate_commits_do_not_count(repo):
    _write(repo, "tmp.py", "eval('1')\n")
    _commit(repo, "smell")
    _write(repo, "tmp.py", "x = 1\n")
    _commit(repo, "gone")
    assert _scan(repo).findings == []


def test_a_merge_commit_covers_the_branch_it_brought_in(repo):
    _write(repo, "cfg.py", f"AWS = '{KEY}'\n")
    _commit(repo, "add key")
    _write(repo, "cfg.py", "AWS = None\n")
    _commit(repo, "remove key")
    _git(repo, "switch", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge", "feature")
    (finding,) = _scan(repo, "HEAD").findings
    assert finding["kind"] == "aws_access_key" and finding["commit"]


def test_a_shallow_cut_inside_the_change_refuses_rather_than_blaming_old_lines(repo, tmp_path):
    _git(repo, "switch", "-q", "main")
    _write(repo, "cfg.py", f"AWS = '{KEY}'\n")
    _commit(repo, "old key on main")
    _git(repo, "switch", "-q", "feature")
    _git(repo, "merge", "-q", "main")
    _write(repo, "a.py", "x = 1\n")
    _commit(repo, "one")
    _write(repo, "a.py", "x = 2\n")
    _commit(repo, "two")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", "--depth", "2", "--branch", "feature", repo.as_uri(), str(clone))
    _git(clone, "fetch", "-q", "--depth", "1", "origin", "main:refs/remotes/origin/main")
    changed, label = changed_lines(str(clone), "origin/main..HEAD")
    with pytest.raises(security_gate.ShallowHistoryError, match="fetch-depth: 0"):
        scan_change(str(clone), changed, label)


def test_a_git_failure_raises_instead_of_reading_as_clean(repo):
    with pytest.raises(subprocess.CalledProcessError):
        scan_change(str(repo), {}, "no-such-ref..HEAD")


# ---------------------------------------------------------------------------
# Verdict and baseline
# ---------------------------------------------------------------------------


def _row(kind="eval_call", severity="high", path="a.py", line=1, snippet="eval(x)", commit=None):
    return {
        "file_path": path,
        "line_number": line,
        "kind": kind,
        "severity": severity,
        "snippet": snippet,
        "fingerprint": fingerprint_of(path, kind, snippet),
        "commit": commit,
    }


@pytest.mark.parametrize(
    ("fail_on", "failing", "below"),
    [("high", 1, 2), ("med", 2, 1), ("low", 3, 0)],
)
def test_fail_on_names_the_lowest_failing_severity(fail_on, failing, below):
    rows = [
        _row(),
        _row("tls_verify_false", "med", snippet="verify=False"),
        _row("weak_hash", "low", snippet="md5"),
    ]
    gate = evaluate(rows, fail_on=fail_on)
    assert len(gate.failing) == failing and gate.below_threshold == below
    assert gate.passed is False


def test_baseline_survives_a_line_shift(repo, tmp_path):
    _write(repo, "cfg.py", f"AWS = '{KEY}'\n")
    _commit(repo, "add key")
    baseline = tmp_path / "baseline.json"
    write_baseline(baseline, _scan(repo).findings)

    _write(repo, "cfg.py", f"import os\n\n\nAWS = '{KEY}'\n")
    _commit(repo, "shift")
    shifted = _scan(repo)
    assert shifted.findings[0]["line_number"] == 4
    gate = evaluate(shifted.findings, baseline=read_baseline(baseline))
    assert gate.passed and len(gate.baselined) == 1


def test_write_baseline_keeps_earlier_entries_and_is_deterministic(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    write_baseline(a, [_row(path="z.py")])
    assert write_baseline(a, [_row(path="a.py")], keep=read_entries(a)) == 2
    write_baseline(b, [_row(path="a.py"), _row(path="z.py"), _row(path="a.py")])
    assert a.read_bytes() == b.read_bytes()
    entries = json.loads(a.read_text(encoding="utf-8"))["entries"]
    assert [e["file_path"] for e in entries] == ["a.py", "z.py"]


# ---------------------------------------------------------------------------
# Renderings
# ---------------------------------------------------------------------------


def test_no_raw_secret_in_any_format(repo, tmp_path):
    _write(repo, "cfg.py", f"AWS = '{KEY}'\npassword = 'hunter2hunter2'\n")
    _write(repo, "key.pem", f"-----BEGIN RSA PRIVATE KEY-----\n{PEM_BODY}\n-----END RSA PRIVATE KEY-----\n")
    _commit(repo, "secrets")
    _write(repo, "old.py", f"TOKEN = '{KEY}'\n")
    _commit(repo, "more")
    _write(repo, "old.py", "TOKEN = None\n")
    _commit(repo, "remove")
    scan = _scan(repo)
    assert {f["kind"] for f in scan.findings} >= {"aws_access_key", "hardcoded_password"}
    assert any(f["commit"] for f in scan.findings)
    gate = evaluate(scan.findings)
    baseline = tmp_path / "b.json"
    write_baseline(baseline, scan.findings)
    outputs = [
        json.dumps(scan.findings),
        json.dumps(gate.to_dict()),
        render_markdown(gate, label="main...HEAD", files_scanned=2, commits_scanned=3),
        "\n".join(github_annotations(scan.findings, gate)),
        json.dumps(render_sarif(scan.findings, tool_version="0")),
        baseline.read_text(encoding="utf-8"),
    ]
    for raw in (KEY, "hunter2hunter2", PEM_BODY[:20]):
        for text in outputs:
            assert raw not in text


def test_annotations_are_capped_escaped_and_skip_the_baseline():
    rows = [_row(path=f"src/a,b:{i}.py", snippet=f"eval({i})") for i in range(12)]
    rows.append(_row("weak_hash", "low", snippet="md5"))
    rows.append(_row(snippet="eval(accepted)"))
    gate = evaluate(rows, baseline=frozenset({rows[-1]["fingerprint"]}))
    lines = github_annotations(rows, gate)
    assert len(lines) == 11 and lines[-1].startswith("::notice::3 more security findings")
    assert all(line.startswith("::error ") for line in lines[:10])
    assert "file=src/a%2Cb%3A0.py,line=1," in lines[0]
    assert not any("accepted" in line for line in lines)


def test_an_earlier_commit_secret_is_annotated_without_a_line():
    row = _row("aws_access_key", snippet="AWS = 'AKIA****'", commit="a" * 40)
    (line,) = github_annotations([row], evaluate([row]))
    assert "line=" not in line and "aaaaaaa" in line


def test_markdown_passes_short_and_always_states_its_basis():
    text = render_markdown(evaluate([]), label="main...HEAD", files_scanned=0, commits_scanned=0)
    assert text.startswith("**No security findings at or above high")
    assert "a floor, not a scanner" in text


def test_sarif_shares_doc_drifts_envelope():
    rows = [_row(), _row("weak_hash", "low", snippet="md5")]
    log = render_sarif(rows, tool_version="1.2.3")
    drift = doc_drift_sarif([], tool_version="1.2.3")
    assert log.keys() == drift.keys()
    assert log["runs"][0].keys() == drift["runs"][0].keys()
    driver = log["runs"][0]["tool"]["driver"]
    assert driver["name"] == "repowise-security" and driver["version"] == "1.2.3"
    first, second = log["runs"][0]["results"]
    assert (first["level"], second["level"]) == ("error", "warning")
    assert first["partialFingerprints"] == {"repowiseSecurity/v1": rows[0]["fingerprint"]}
    assert driver["rules"][first["ruleIndex"]]["id"] == "eval_call"


def test_every_gating_kind_has_a_sarif_rule():
    kinds = {kind for _, kind, _ in [*_PATTERNS, *_SPANNING_PATTERNS]}
    assert kinds == set(security_gate._RULE_TEXT)
