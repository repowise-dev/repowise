"""``security_gate``: the inline ignore marker, custom patterns and the staged scan.

Scans run against real temp git repositories, as in ``test_security_gate``,
because scoping to changed lines and commits is part of what is under test.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from repowise.core.analysis.changed_lines import changed_lines
from repowise.core.analysis.security_gate import (
    MAX_CUSTOM_LINE_LENGTH,
    CustomPattern,
    SarifExtras,
    custom_patterns,
    evaluate,
    fingerprint_of,
    render_gitlab,
    render_markdown,
    render_sarif,
    scan_change,
)

KEY = "AKIA" + "QZXNRTVYWMPKLBHG"
TOKEN = "itk_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"
OTHER_TOKEN = "itk_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4"


def _git(cwd, *args: str, stdin: str | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, input=stdin
    ).stdout


def _write(repo, name: str, text: str) -> None:
    (repo / name).write_text(text, encoding="utf-8", newline="\n")


def _commit(repo, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)


def init_repo(path):
    """A repository with one commit on ``main`` and a ``feature`` branch checked out."""
    _git(path, "init", "-q", "-b", "main")
    for key, value in (("user.email", "t@t.co"), ("user.name", "t"), ("core.autocrlf", "false")):
        _git(path, "config", key, value)
    _write(path, "app.py", "x = 1\n")
    _commit(path, "base")
    _git(path, "switch", "-qc", "feature")
    return path


@pytest.fixture
def repo(tmp_path):
    return init_repo(tmp_path)


def _patterns(*entries: dict) -> tuple[CustomPattern, ...]:
    patterns, errors = custom_patterns({"security": {"patterns": list(entries)}})
    assert errors == ()
    return patterns


def _pattern(regex: str = r"itk_[A-Za-z0-9]{32}", severity: str = "high") -> CustomPattern:
    (pattern,) = _patterns({"name": "internal_token", "regex": regex, "severity": severity})
    return pattern


def _scan(repo, revspec: str = "main...HEAD", patterns=()):
    changed, label = changed_lines(str(repo), revspec)
    return scan_change(str(repo), changed, label, patterns=patterns)


def _staged_scan(repo, patterns=()):
    changed, label = changed_lines(str(repo), staged=True)
    return scan_change(str(repo), changed, label, patterns=patterns, staged=True)


# ---------------------------------------------------------------------------
# Inline marker
# ---------------------------------------------------------------------------


def test_a_bare_marker_silences_every_kind_on_its_line(repo):
    _write(repo, "cfg.py", f"AWS = '{KEY}'  # repowise-security-ignore\nimport os\nos.system(c)\n")
    _commit(repo, "add")
    scan = _scan(repo)
    assert [f["kind"] for f in scan.findings] == ["os_system"]
    (suppressed,) = scan.suppressed
    assert (suppressed["kind"], suppressed["line_number"]) == ("aws_access_key", 1)


def test_a_kind_scoped_marker_silences_only_the_kinds_it_names(repo):
    _write(
        repo,
        "cfg.py",
        f"AWS = '{KEY}'; eval(x)  # repowise-security-ignore: aws_access_key, custom:other\n",
    )
    _commit(repo, "add")
    scan = _scan(repo)
    assert [f["kind"] for f in scan.findings] == ["eval_call"]
    assert [f["kind"] for f in scan.suppressed] == ["aws_access_key"]


@pytest.mark.parametrize(
    "text",
    [
        f"AWS = '{KEY}'  # repowise-security-ignore-file\n",
        f"AWS = '{KEY}'  # repowise-security-ignore-next-line\n",
        f"AWS = '{KEY}'  # Repowise-Security-Ignore\n",
        f"AWS = '{KEY}'  # repowise-security-ignore: see ticket 12\n",
        f"AWS = '{KEY}'  # repowise-security-ignore:\n",
        f"AWS = '{KEY}'  # repowise-security-ignore: #12\n",
        f"# repowise-security-ignore\nAWS = '{KEY}'\n",  # the marker on another line
    ],
)
def test_near_misses_free_text_kinds_and_other_lines_silence_nothing(repo, text):
    _write(repo, "cfg.py", text)
    _commit(repo, "add")
    scan = _scan(repo)
    assert [f["kind"] for f in scan.findings] == ["aws_access_key"]
    assert scan.suppressed == []


def test_the_marker_must_be_in_the_commit_that_added_the_line(repo):
    _write(repo, "cfg.py", f"AWS = '{KEY}'\n")
    _commit(repo, "leak")
    _write(repo, "cfg.py", f"AWS = '{KEY}'  # repowise-security-ignore\n")
    _commit(repo, "silence")
    scan = _scan(repo)
    (finding,) = scan.findings
    assert finding["commit"] and finding["kind"] == "aws_access_key"
    assert [f["commit"] for f in scan.suppressed] == [None]

    _git(repo, "reset", "-q", "--soft", "main")
    _commit(repo, "squashed")
    squashed = _scan(repo)
    assert squashed.findings == [] and len(squashed.suppressed) == 1


def test_a_fingerprint_is_never_both_failing_and_suppressed(repo):
    # The marker sits past the 120-character snippet, so both commits share a fingerprint.
    line = "password = 'hunter2hunter2'  # " + "x" * 130
    _write(repo, "cfg.py", line + "\n")
    _commit(repo, "leak")
    _write(repo, "cfg.py", line + " repowise-security-ignore\n")
    _commit(repo, "silence")
    scan = _scan(repo)
    (finding,) = scan.findings
    assert finding["commit"] and scan.suppressed == []


def test_suppressed_findings_never_fail_and_are_counted_in_json(repo):
    _write(repo, "cfg.py", f"AWS = '{KEY}'  # repowise-security-ignore\n")
    _commit(repo, "add")
    scan = _scan(repo)
    gate = evaluate(scan.findings, fail_on="low", suppressed=scan.suppressed)
    assert gate.passed
    doc = gate.to_dict()
    assert doc["suppressed_count"] == 1
    (row,) = doc["suppressed"]
    assert set(row) == {"file_path", "line_number", "kind", "severity", "fingerprint", "commit"}
    assert KEY not in json.dumps(doc)


def test_sarif_carries_suppressed_findings_in_source(repo):
    _write(repo, "cfg.py", f"AWS = '{KEY}'  # repowise-security-ignore\n")
    _commit(repo, "add")
    scan = _scan(repo)
    gate = evaluate(scan.findings, suppressed=scan.suppressed)
    log = render_sarif(
        scan.findings, tool_version="0", extras=SarifExtras(suppressed=gate.suppressed)
    )
    (result,) = log["runs"][0]["results"]
    assert result["ruleId"] == "aws_access_key"
    assert result["suppressions"] == [{"kind": "inSource"}]


def test_markdown_lists_suppressed_findings_after_the_table(repo):
    _write(repo, "cfg.py", f"AWS = '{KEY}'  # repowise-security-ignore\neval(x)\n")
    _commit(repo, "add")
    scan = _scan(repo)
    gate = evaluate(scan.findings, suppressed=scan.suppressed)
    text = render_markdown(gate, label="main...HEAD", files_scanned=1, commits_scanned=1)
    assert "1 finding suppressed inline" in text
    assert text.index("| Where |") < text.index("<details>") < text.index("a floor, not")
    assert "cfg.py:1" in text


# ---------------------------------------------------------------------------
# Custom patterns: config
# ---------------------------------------------------------------------------


def _errors(patterns) -> tuple[str, ...]:
    return custom_patterns({"security": {"patterns": patterns}})[1]


@pytest.mark.parametrize(
    ("entry", "needle"),
    [
        ({"name": "a", "regex": "x", "extra": 1}, "unknown key extra"),
        ({"regex": "x"}, "name must be"),
        ({"name": "Bad Name", "regex": "x"}, "name must be"),
        ({"name": "a" * 41, "regex": "x"}, "name must be"),
        ({"name": "a"}, "regex must be a non-empty string"),
        ({"name": "a", "regex": "("}, "does not compile"),
        ({"name": "a", "regex": "x*"}, "zero characters"),
        ({"name": "a", "regex": r"\b"}, "zero characters"),
        ({"name": "a", "regex": "(?=a)"}, "zero characters"),
        ({"name": "a", "regex": "(?=z)"}, "zero characters"),
        ({"name": "a", "regex": "(a+)+b"}, "exponential"),
        ({"name": "a", "regex": r"k=(\w*)*;"}, "exponential"),
        ({"name": "a", "regex": "(a|aa)+b"}, "exponential"),
        ({"name": "a", "regex": "x" * 501}, "longer than 500"),
        ({"name": "a", "regex": "x", "severity": "critical"}, "severity must be one of"),
        ("not a mapping", "must be a mapping"),
    ],
)
def test_every_invalid_entry_is_an_error(entry, needle):
    (error,) = _errors([entry])
    assert error.startswith("security.patterns[0]") and needle in error


@pytest.mark.parametrize(
    "regex", [r"itk_[A-Za-z0-9]{32}", r"(itk|xtk)_[a-z]+", r"[(+*]+x", r"\(a+\)+", r"(?:ab)+c"]
)
def test_ordinary_patterns_pass_the_backtracking_check(regex):
    assert _errors([{"name": "a", "regex": regex}]) == ()


def test_every_problem_is_listed_not_just_the_first():
    errors = _errors([{"name": "a", "regex": "("}, {"name": "b", "regex": "x", "severity": "x"}])
    assert len(errors) == 2


def test_a_duplicate_name_is_an_error():
    (error,) = _errors([{"name": "a", "regex": "x"}, {"name": "a", "regex": "y"}])
    assert error.startswith("security.patterns[1]") and "duplicate" in error


def test_the_block_shape_and_the_pattern_cap_are_checked():
    assert custom_patterns({}) == ((), ())
    assert custom_patterns({"security": "x"})[1] == ("security must be a mapping.",)
    assert "unknown key rules" in custom_patterns({"security": {"rules": []}})[1][0]
    assert "must be a list" in _errors({"name": "a"})[0]
    many = [{"name": f"p{i}", "regex": "x"} for i in range(51)]
    assert any("the limit is 50" in e for e in _errors(many))


def test_severity_defaults_to_high():
    (pattern,) = _patterns({"name": "a", "regex": "x"})
    assert pattern.severity == "high" and pattern.kind == "custom:a"


# ---------------------------------------------------------------------------
# Custom patterns: scanning
# ---------------------------------------------------------------------------


def test_a_custom_pattern_finds_and_masks_on_a_changed_line_only(repo):
    _write(repo, "old.py", f"T = '{TOKEN}'\n")
    _commit(repo, "before the change")
    _git(repo, "branch", "-f", "main", "HEAD")
    _write(repo, "old.py", f"T = '{TOKEN}'\nU = '{OTHER_TOKEN}'\n")
    _commit(repo, "add one")
    scan = _scan(repo, patterns=(_pattern(),))
    (finding,) = scan.findings
    assert (finding["kind"], finding["line_number"], finding["severity"]) == (
        "custom:internal_token",
        2,
        "high",
    )
    assert finding["snippet"] == "U = 'itk_****'"
    assert OTHER_TOKEN not in json.dumps(scan.findings)


def test_the_whole_match_is_masked_even_with_a_capture_group(repo):
    _write(repo, "cfg.txt", f"id {TOKEN} end\n")
    _commit(repo, "add")
    scan = _scan(repo, patterns=(_pattern(r"(itk|xtk)_[A-Za-z0-9]{32}"),))
    (finding,) = scan.findings
    assert finding["snippet"] == "id itk_**** end"


def test_overlapping_matches_are_masked_as_one_span(repo):
    _write(repo, "cfg.txt", "k abcdefghijklmnop z\n")
    _commit(repo, "add")
    patterns = _patterns({"name": "a", "regex": "abcdefgh"}, {"name": "b", "regex": "efghijklmnop"})
    scan = _scan(repo, patterns=patterns)
    assert {f["snippet"] for f in scan.findings} == {"k abcd**** z"}


def test_custom_severity_is_kept_under_a_test_path(repo):
    (repo / "tests").mkdir()
    _write(repo, "tests/cfg.py", f"T = '{TOKEN}'\n")
    _commit(repo, "add")
    (finding,) = _scan(repo, patterns=(_pattern(),)).findings
    assert finding["severity"] == "high"


def test_a_custom_secret_is_masked_in_a_builtin_snippet_and_its_fingerprint_is_kept(repo):
    _write(repo, "run.py", f"import os\nos.system('{TOKEN}')\n")
    _commit(repo, "add")
    (plain,) = _scan(repo).findings
    scan = _scan(repo, patterns=(_pattern(),))
    builtin = next(f for f in scan.findings if f["kind"] == "os_system")
    assert builtin["fingerprint"] == plain["fingerprint"]
    assert TOKEN not in json.dumps(scan.findings)


def test_adding_a_pattern_leaves_builtin_fingerprints_unchanged(repo):
    long_line = f"os.system('{TOKEN}')  # " + "y" * MAX_CUSTOM_LINE_LENGTH
    _write(repo, "run.py", f"import os\n{long_line}\nAWS = '{KEY}'\n")
    _commit(repo, "add")
    before = {(f["kind"], f["fingerprint"], f["snippet"]) for f in _scan(repo).findings}
    after = _scan(repo, patterns=(_pattern(),)).findings
    assert {(f["kind"], f["fingerprint"], f["snippet"]) for f in after} == before


def test_a_custom_fingerprint_is_the_masked_line_not_the_raw_token(repo):
    _write(repo, "a.py", f"T = '{TOKEN}'\n")
    _commit(repo, "a")
    (finding,) = _scan(repo, patterns=(_pattern(),)).findings
    expected = fingerprint_of("a.py", "custom:internal_token", "T = 'itk_****'")
    assert finding["fingerprint"] == expected
    assert _scan(repo, patterns=(_pattern(),)).findings[0]["fingerprint"] == finding["fingerprint"]


def test_custom_patterns_are_scanned_in_intermediate_commits(repo):
    _write(repo, "a.py", f"T = '{TOKEN}'\n")
    _commit(repo, "leak")
    _write(repo, "a.py", "T = None\n")
    _commit(repo, "remove")
    (finding,) = _scan(repo, patterns=(_pattern(),)).findings
    assert finding["kind"] == "custom:internal_token" and finding["commit"]


def test_a_custom_finding_can_be_silenced_by_its_kind(repo):
    _write(repo, "a.py", f"T = '{TOKEN}'  # repowise-security-ignore: custom:internal_token\n")
    _commit(repo, "add")
    scan = _scan(repo, patterns=(_pattern(),))
    assert scan.findings == [] and [f["kind"] for f in scan.suppressed] == ["custom:internal_token"]


def test_an_over_long_line_is_skipped_for_custom_patterns_and_counted_once(repo):
    _write(repo, "a.py", f"T = '{TOKEN}'" + " " * MAX_CUSTOM_LINE_LENGTH + "\n")
    _commit(repo, "add")
    scan = _scan(repo, patterns=(_pattern(),))
    assert scan.findings == []
    assert scan.long_lines_skipped == 1  # at the head and in its commit: one line
    log = render_sarif(scan.findings, tool_version="0", extras=SarifExtras(long_lines_skipped=1))
    assert log["runs"][0]["properties"] == {"longLinesSkipped": 1}


def test_sarif_and_gitlab_name_a_custom_rule_custom_slash_name(repo):
    _write(repo, "a.py", f"T = '{TOKEN}'\n")
    _commit(repo, "add")
    pattern = _pattern()
    scan = _scan(repo, patterns=(pattern,))
    log = render_sarif(scan.findings, tool_version="0", extras=SarifExtras(patterns=(pattern,)))
    run = log["runs"][0]
    (result,) = run["results"]
    assert result["ruleId"] == "custom/internal_token"
    assert run["tool"]["driver"]["rules"][result["ruleIndex"]]["id"] == "custom/internal_token"
    (issue,) = render_gitlab(scan.findings)
    assert issue["check_name"] == "repowise-security/custom/internal_token"


# ---------------------------------------------------------------------------
# Staged
# ---------------------------------------------------------------------------


def test_a_staged_scan_reads_the_index_and_skips_unstaged_edits(repo):
    _write(repo, "staged.py", f"AWS = '{KEY}'\n")
    _git(repo, "add", "staged.py")
    _write(repo, "loose.py", f"AWS = '{KEY}'\n")  # untracked, never staged
    _write(repo, "app.py", "import os\nos.system(c)\n")  # tracked, edited, unstaged
    scan = _staged_scan(repo)
    assert [(f["file_path"], f["kind"]) for f in scan.findings] == [("staged.py", "aws_access_key")]
    assert scan.commits_scanned == 0


@pytest.mark.skipif(sys.platform == "win32", reason="git for Windows refuses a ':' in a path")
def test_a_staged_path_that_looks_like_a_stage_number_is_read(repo):
    sha = _git(repo, "hash-object", "-w", "--stdin", stdin=f"AWS = '{KEY}'\n").strip()
    _git(repo, "update-index", "--add", "--cacheinfo", f"100644,{sha},1:notes.txt")
    (finding,) = _staged_scan(repo).findings
    assert finding["file_path"] == "1:notes.txt"


def test_an_external_diff_or_forced_colour_cannot_hide_a_staged_secret(repo):
    _git(repo, "config", "diff.external", "true")
    _git(repo, "config", "color.diff", "always")
    _write(repo, "staged.py", f"AWS = '{KEY}'\n")
    _git(repo, "add", "staged.py")
    assert [f["kind"] for f in _staged_scan(repo).findings] == ["aws_access_key"]
