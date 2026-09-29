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
    render_gitlab,
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
        json.dumps(render_gitlab(scan.findings)),
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


# ---------------------------------------------------------------------------
# Review regressions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "raw"),
    [
        ("PASSWORD = 'Tr0ub4dor\"TAILSECRET9876'  # md5\n", "TAILSECRET9876"),
        ('secret = "token = \'RAWVALUE12345678\'"  # md5\n', "RAWVALUE12345678"),
        ('ENV["NEXT_PUBLIC_API_KEY"] = "RAWVALUE12345678"  # md5\n', "RAWVALUE12345678"),
        ("ENV NEXT_PUBLIC_API_KEY RAWVALUE12345678 md5\n", "RAWVALUE12345678"),
        ('k = process.env.NEXT_PUBLIC_API_KEY ?? "RAWVALUE12345678" // md5\n', "RAWVALUE12345678"),
    ],
)
def test_no_raw_value_escapes_the_mask(line, raw):
    from repowise.core.analysis.security_scan import scan_source

    findings = scan_source("cfg.py", line)
    assert findings and all(raw not in f["snippet"] for f in findings)


@pytest.mark.parametrize(
    "source",
    [
        f"key: |\n  -----BEGIN RSA PRIVATE KEY-----\n  {PEM_BODY}md5\n  -----END RSA PRIVATE KEY-----\n",
        f"-----BEGIN RSA PRIVATE KEY-----\n{PEM_BODY}\n{PEM_BODY[::-1]}md5\n",
        f"KEY=-----BEGIN RSA PRIVATE KEY----- {PEM_BODY} md5 -----END RSA PRIVATE KEY-----\n",
    ],
)
def test_pem_body_lines_never_reach_a_snippet(source):
    from repowise.core.analysis.security_scan import scan_source

    for f in scan_source("cfg.yaml", source):
        assert PEM_BODY[:12] not in f["snippet"] and PEM_BODY[::-1][:12] not in f["snippet"]


def test_line_numbers_follow_git_past_a_form_feed(repo):
    _write(repo, "ff.py", "a = 1\n\x0c\nb = 2\n")
    _commit(repo, "ff")
    base = _git(repo, "rev-parse", "HEAD").strip()
    _write(repo, "ff.py", "a = 1\n\x0c\nb = 2\npassword = 'Zq8vLm2pXr7w'\n")
    _commit(repo, "pw")
    assert _kinds(_scan(repo, f"{base}..HEAD")) == [("hardcoded_password", 4, False)]


def test_a_non_ascii_path_is_scanned(repo):
    _write(repo, "café.py", "password = 'Zq8vLm2pXr7w'\n")
    _commit(repo, "accent")
    scan = _scan(repo)
    assert [f["file_path"] for f in scan.findings] == ["café.py"]


def test_two_vendor_keys_on_identical_lines_stay_distinct(repo, tmp_path):
    _write(repo, "cfg.py", f"AWS = '{KEY}'\n")
    _commit(repo, "key a")
    baseline = tmp_path / "b.json"
    write_baseline(baseline, _scan(repo).findings)
    other = "AKIA" + "ZXCVBNMLKJHGFDSA"
    _write(repo, "cfg.py", f"AWS = '{other}'\n")
    _commit(repo, "key b")
    scan = _scan(repo)
    # Both keys are reported: B at the head, A from the commit that added it.
    assert sorted(f["commit"] is None for f in scan.findings) == [False, True]
    gate = evaluate(scan.findings, baseline=read_baseline(baseline))
    assert [f["commit"] for f in gate.failing] == [None]


def test_a_shallow_cut_on_the_base_side_refuses(repo, tmp_path):
    _git(repo, "switch", "-q", "main")
    _write(repo, "old.py", "password = 'Zq8vLm2pXr7w'\n")
    _commit(repo, "old")
    _git(repo, "switch", "-qc", "feat")
    _write(repo, "a.py", "x = 1\n")
    _commit(repo, "f1")
    _git(repo, "switch", "-q", "main")
    _write(repo, "m.py", "y = 1\n")
    _commit(repo, "m4")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", "--depth", "1", "--branch", "main", repo.as_uri(), str(clone))
    _git(clone, "fetch", "-q", "--depth", "10", "origin", "feat:refs/remotes/origin/feat")
    changed, label = changed_lines(str(clone), "origin/main..origin/feat")
    with pytest.raises(security_gate.ShallowHistoryError, match="share no commit"):
        scan_change(str(clone), changed, label)


def test_a_path_git_does_not_have_raises():
    with pytest.raises(security_gate.MissingObjectError):
        security_gate._read_blobs(".", ["HEAD:no/such/file-anywhere.py"])


def test_sarif_suppresses_the_baseline_and_drops_history_lines():
    head = _row()
    old = _row("aws_access_key", snippet="AWS = 'AKIA****'", commit="a" * 40)
    log = render_sarif([head, old], tool_version="0", accepted=frozenset({head["fingerprint"]}))
    by_rule = {r["ruleId"]: r for r in log["runs"][0]["results"]}
    assert by_rule["eval_call"]["suppressions"] == [{"kind": "external"}]
    assert "suppressions" not in by_rule["aws_access_key"]
    assert "region" not in by_rule["aws_access_key"]["locations"][0]["physicalLocation"]


@pytest.mark.parametrize(
    ("line", "raw"),
    [
        ("ENV NEXT_PUBLIC_API_KEY p@ssw0rd!Xy#Zq9 md5\n", "p@ssw0rd!Xy#Zq9"),
        ('ENV["NEXT_PUBLIC_TOKEN"] = "Ab$9!kLm@2#Qr%zz"  # md5\n', "Ab$9!kLm@2#Qr%zz"),
        ('x = NEXT_PUBLIC_SECRET ?? "p@ss:w0rd!2024x" // md5\n', "p@ss:w0rd!2024x"),
        ("password = \"ab'SuperSecretPassword123\"; eval(x)\n", "SuperSecretPassword123"),
    ],
)
def test_values_with_symbols_or_an_early_quote_are_masked(line, raw):
    from repowise.core.analysis.security_scan import scan_source

    findings = scan_source("cfg.js", line)
    assert findings and all(raw[4:] not in f["snippet"] for f in findings)


def test_ordinary_text_after_a_public_env_name_is_left_readable():
    from repowise.core.analysis.security_scan import scan_source

    (hit,) = scan_source("a.ts", "const k = process.env.NEXT_PUBLIC_API_KEY;\n")
    assert hit["snippet"] == "const k = process.env.NEXT_PUBLIC_API_KEY;"


def test_a_pem_header_constant_does_not_mask_the_code_after_it():
    from repowise.core.analysis.security_scan import scan_source

    source = 'PEM = "-----BEGIN RSA PRIVATE KEY-----"\n\ndef f(s):\n    return eval(s)\n'
    (hit,) = [f for f in scan_source("a.py", source) if f["kind"] == "eval_call"]
    assert hit["snippet"] == "return eval(s)"


def test_a_lone_carriage_return_does_not_crash_the_scan():
    from repowise.core.analysis.security_scan import scan_source

    assert [f["kind"] for f in scan_source("a.py", "import os\rx = 1\reval(y)\r")] == ["eval_call"]


def test_a_submodule_in_the_change_is_skipped_not_fatal(repo, tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    _git(sub, "init", "-q")
    _git(sub, "-c", "user.email=t@t.co", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "s")
    _git(repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", sub.as_uri(), "sub")
    _commit(repo, "submodule")
    assert _scan(repo).findings == []


def test_diff_noprefix_config_does_not_misread_paths(repo):
    _git(repo, "config", "diff.noprefix", "true")
    _write(repo, "b/cfg.py", "password = 'Zq8vLm2pXr7w'\n")
    _commit(repo, "nested")
    assert [f["file_path"] for f in _scan(repo).findings] == ["b/cfg.py"]


def test_gitlab_maps_severities_drops_the_baseline_and_puts_history_on_line_one():
    accepted = _row(snippet="eval(accepted)")
    rows = [
        _row(line=4),
        _row("subprocess_shell_true", "med", line=2, snippet="run(x, shell=True)"),
        _row("weak_hash", "low", line=3, snippet="md5"),
        _row("aws_access_key", snippet="AWS = 'AKIA****'", line=9, commit="a" * 40),
        accepted,
    ]
    issues = render_gitlab(rows, accepted=frozenset({accepted["fingerprint"]}))
    by_check = {i["check_name"].removeprefix("repowise-security/"): i for i in issues}
    assert set(by_check) == {"eval_call", "subprocess_shell_true", "weak_hash", "aws_access_key"}
    assert by_check["eval_call"]["check_name"] == "repowise-security/eval_call"
    assert by_check["eval_call"]["severity"] == "critical"
    assert by_check["subprocess_shell_true"]["severity"] == "minor"
    assert by_check["weak_hash"]["severity"] == "minor"
    assert by_check["eval_call"]["location"] == {"path": "a.py", "lines": {"begin": 4}}
    assert by_check["aws_access_key"]["location"]["lines"] == {"begin": 1}
    assert "aaaaaaa" in by_check["aws_access_key"]["description"]
    assert by_check["eval_call"]["fingerprint"] == rows[0]["fingerprint"]


@pytest.mark.parametrize(
    ("fail_on", "expected"),
    [
        ("high", ["critical", "minor", "minor"]),
        ("med", ["critical", "major", "minor"]),
        ("low", ["critical", "major", "major"]),
    ],
)
def test_gitlab_severity_follows_the_gate(fail_on, expected):
    rows = [
        _row(),
        _row("subprocess_shell_true", "med", snippet="run(x, shell=True)"),
        _row("weak_hash", "low", snippet="md5"),
    ]
    assert [i["severity"] for i in render_gitlab(rows, fail_on=fail_on)] == expected


def test_gitlab_keeps_two_identical_lines_apart():
    # Two identical matched lines in one file share a fingerprint by design.
    rows = [_row(line=7), _row(line=2)]
    first, second = render_gitlab(rows)
    assert first["location"]["lines"]["begin"] == 2
    assert (first["fingerprint"], second["fingerprint"]) == (
        rows[0]["fingerprint"],
        rows[0]["fingerprint"] + ":2",
    )
