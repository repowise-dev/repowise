"""``repowise doc-drift --check``: the index-free CI gate and its exit codes."""

from __future__ import annotations

import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from repowise.cli.commands import doc_drift_cmd
from repowise.core.analysis.doc_drift.live import LiveTreeError
from tests.unit.cli.test_format_json_rollout import _split_runner


def _finding(**kw):
    out = {
        "file_path": kw.get("file_path", "docs/a.md"),
        "line_number": kw.get("line_number", 3),
        "kind": kw.get("kind", "path"),
        "target": kw.get("target", "src/gone.py"),
        "confidence": kw.get("confidence", 0.9),
        "origin": "path_no_candidate",
        "reason": "Document names src/gone.py, which no longer exists.",
        "raw": "src/gone.py",
        "context": "see src/gone.py",
        "evidence": ["docs/a.md:3 states `src/gone.py`"],
    }
    if "suggestion" in kw:
        out["suggestion"] = kw["suggestion"]
        out["suggestion_basis"] = "package_split"
    return out


def _report():
    return SimpleNamespace(documents_scanned=4, references_checked=20, suppressed=1)


def _invoke(monkeypatch, tmp_path, findings, args=(), env=None, runner=None):
    monkeypatch.setattr(doc_drift_cmd, "_repo_path", lambda *a, **k: tmp_path)
    monkeypatch.setattr(
        doc_drift_cmd, "_read_live", lambda root, **k: (_report(), list(findings))
    )
    return (runner or CliRunner()).invoke(
        doc_drift_cmd.doc_drift_command, ["--check", *args], env=env or {}
    )


def test_gate_fails_on_a_high_confidence_finding(monkeypatch, tmp_path):
    result = _invoke(monkeypatch, tmp_path, [_finding()])
    assert result.exit_code == 1
    assert "Gate failed" in result.output


def test_gate_passes_below_the_threshold(monkeypatch, tmp_path):
    result = _invoke(monkeypatch, tmp_path, [_finding(confidence=0.5)])
    assert result.exit_code == 0
    assert "Gate passed" in result.output


def test_fail_on_confidence_is_honoured(monkeypatch, tmp_path):
    findings = [_finding(confidence=0.5)]
    result = _invoke(monkeypatch, tmp_path, findings, ["--fail-on-confidence", "0.4"])
    assert result.exit_code == 1


def test_not_a_git_repository_exits_two_with_a_json_document(monkeypatch, tmp_path):
    monkeypatch.setattr(doc_drift_cmd, "_repo_path", lambda *a, **k: tmp_path)

    def _raise(root, **k):
        raise LiveTreeError("not a git repository")

    monkeypatch.setattr(doc_drift_cmd, "_read_live", _raise)
    result = CliRunner().invoke(
        doc_drift_cmd.doc_drift_command, ["--check", "--format", "json"]
    )
    assert result.exit_code == 2
    assert json.loads(result.output)["error"] == "not_a_git_repository"


def test_json_carries_the_gate_and_the_denominators(monkeypatch, tmp_path):
    result = _invoke(monkeypatch, tmp_path, [_finding()], ["--format", "json"])
    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload["gate"]["passed"] is False
    assert payload["documents_scanned"] == 4
    assert payload["suppressed"] == 1


def test_baseline_round_trip_accepts_known_findings(monkeypatch, tmp_path):
    baseline = tmp_path / "baseline.json"
    findings = [_finding()]
    wrote = _invoke(monkeypatch, tmp_path, findings, ["--write-baseline", str(baseline)])
    assert wrote.exit_code == 0
    assert baseline.exists()

    # A line shift must not turn an accepted finding back into a new one.
    shifted = [_finding(line_number=40)]
    result = _invoke(monkeypatch, tmp_path, shifted, ["--baseline", str(baseline)])
    assert result.exit_code == 0

    fresh = [*shifted, _finding(target="src/other.py")]
    result = _invoke(monkeypatch, tmp_path, fresh, ["--baseline", str(baseline)])
    assert result.exit_code == 1


def test_unreadable_baseline_exits_two(monkeypatch, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    result = _invoke(monkeypatch, tmp_path, [_finding()], ["--baseline", str(bad)])
    assert result.exit_code == 2


def test_unwritable_baseline_exits_two(monkeypatch, tmp_path):
    target = tmp_path / "missing" / "baseline.json"
    result = _invoke(monkeypatch, tmp_path, [_finding()], ["--write-baseline", str(target)])
    assert result.exit_code == 2
    assert not target.exists()


def test_github_format_emits_an_error_command_when_it_cannot_evaluate(monkeypatch, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    result = _invoke(
        monkeypatch, tmp_path, [_finding()], ["--baseline", str(bad), "--format", "github"],
        runner=_split_runner(),
    )
    assert result.exit_code == 2
    assert result.stdout.startswith("::error::")


def test_min_confidence_above_fail_on_warns_on_stderr(monkeypatch, tmp_path):
    args = ["--min-confidence", "0.9", "--fail-on-confidence", "0.7", "--format", "json"]
    result = _invoke(
        monkeypatch, tmp_path, [_finding(confidence=0.95)], args, runner=_split_runner()
    )
    assert "hidden from the gate" in result.stderr
    assert json.loads(result.stdout)["gate"]["fail_on"] == 0.7


def test_sarif_level_follows_the_gate_threshold(monkeypatch, tmp_path):
    args = ["--format", "sarif", "--fail-on-confidence", "0.4"]
    result = _invoke(monkeypatch, tmp_path, [_finding(confidence=0.5)], args)
    assert json.loads(result.output)["runs"][0]["results"][0]["level"] == "error"


def test_baseline_flags_require_check(monkeypatch, tmp_path):
    monkeypatch.setattr(doc_drift_cmd, "_repo_path", lambda *a, **k: tmp_path)
    result = CliRunner().invoke(
        doc_drift_cmd.doc_drift_command, ["--baseline", str(tmp_path / "b.json")]
    )
    assert result.exit_code == 2
    assert "require --check" in result.output


def test_github_format_annotates_and_fills_the_step_summary(monkeypatch, tmp_path):
    summary = tmp_path / "summary.md"
    result = _invoke(
        monkeypatch,
        tmp_path,
        [_finding(suggestion="src/gone/")],
        ["--format", "github"],
        env={"GITHUB_STEP_SUMMARY": str(summary)},
    )
    assert result.exit_code == 1
    assert result.output.startswith("::error file=docs/a.md,line=3")
    assert "src/gone/" in result.output
    assert "docs/a.md" in summary.read_text(encoding="utf-8")


def test_sarif_is_one_json_document(monkeypatch, tmp_path):
    result = _invoke(monkeypatch, tmp_path, [_finding()], ["--format", "sarif"])
    sarif = json.loads(result.output)
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"][0]["locations"][0]["physicalLocation"][
        "artifactLocation"
    ]["uri"] == "docs/a.md"


def test_document_filter_narrows_the_gate(monkeypatch, tmp_path):
    findings = [_finding(), _finding(file_path=".github/b.md")]
    result = _invoke(
        monkeypatch, tmp_path, findings, ["--document", "./.github/b.md", "--format", "json"]
    )
    payload = json.loads(result.output)
    assert [f["file_path"] for f in payload["findings"]] == [".github/b.md"]


def test_table_shows_the_likely_replacement(monkeypatch, tmp_path):
    result = _invoke(monkeypatch, tmp_path, [_finding(suggestion="src/gone/")])
    assert "likely now: src/gone/" in result.output


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_end_to_end_on_a_real_working_tree(tmp_path):
    def git(*args):
        subprocess.run(
            ["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t", *args],
            check=True,
            capture_output=True,
        )

    (tmp_path / "src" / "tools" / "cli").mkdir(parents=True)
    (tmp_path / "src" / "tools" / "cli" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "overview.md").write_text(
        "The entry point is `src/tools/cli.py`.\n", encoding="utf-8"
    )
    git("init", "-q")
    git("add", ".")
    git("commit", "-qm", "init")

    result = CliRunner().invoke(
        doc_drift_cmd.doc_drift_command,
        ["--check", "--no-workspace", "--format", "json", str(tmp_path)],
    )
    payload = json.loads(result.output)
    assert result.exit_code == 1, result.output
    (finding,) = payload["findings"]
    assert finding["target"] == "src/tools/cli.py"
    assert finding["suggestion"] == "src/tools/cli/"


def _scoped(monkeypatch, *changes):
    from repowise.core.analysis.doc_drift import scope as scope_mod

    scope = scope_mod.ChangeScope.from_changes(changes, label="main...HEAD")
    monkeypatch.setattr(scope_mod, "scope_since", lambda root, revspec: scope)


def test_since_gates_only_drift_the_change_is_answerable_for(monkeypatch, tmp_path):
    from repowise.core.analysis.change_health.sources import FileChange

    _scoped(monkeypatch, FileChange(head_path=None, base_path="src/gone.py", status="deleted"))
    findings = [_finding(), _finding(file_path="docs/b.md", target="src/old.py")]
    result = _invoke(
        monkeypatch, tmp_path, findings, ["--since", "main...HEAD", "--format", "json"]
    )
    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert [f["file_path"] for f in payload["findings"]] == ["docs/a.md"]
    assert payload["findings"][0]["scope_reason"] == "removed"
    assert payload["scope"] == {
        "revspec": "main...HEAD",
        "documents_changed": 0,
        "paths_removed": 1,
        "out_of_scope": 1,
    }


def test_since_passes_when_the_drift_predates_the_change(monkeypatch, tmp_path):
    _scoped(monkeypatch)
    result = _invoke(monkeypatch, tmp_path, [_finding()], ["--since", "main...HEAD"])
    assert result.exit_code == 0
    assert "Gate passed" in result.output


def test_since_markdown_names_the_scope(monkeypatch, tmp_path):
    _scoped(monkeypatch)
    result = _invoke(
        monkeypatch, tmp_path, [_finding()], ["--since", "main...HEAD", "--format", "markdown"]
    )
    assert "scoped to main...HEAD, 1 outside the change" in result.output


def test_since_auto_without_a_base_exits_two(monkeypatch, tmp_path):
    from repowise.core.ci import base

    def _none(root, env=None):
        raise base.BaseNotFoundError("no base")

    monkeypatch.setattr(base, "default_revspec", _none)
    result = _invoke(monkeypatch, tmp_path, [_finding()], ["--since", "auto", "--format", "json"])
    assert result.exit_code == 2
    assert json.loads(result.output)["error"] == "base_not_found"


def test_since_on_a_bad_revision_exits_two(monkeypatch, tmp_path):
    from repowise.core.analysis.doc_drift import scope as scope_mod

    def _bad(root, revspec):
        raise ValueError("unknown revision")

    monkeypatch.setattr(scope_mod, "scope_since", _bad)
    result = _invoke(
        monkeypatch, tmp_path, [_finding()], ["--since", "nope...HEAD", "--format", "json"]
    )
    assert result.exit_code == 2
    assert json.loads(result.output)["error"] == "diff_failed"


def test_since_requires_check_and_refuses_write_baseline(monkeypatch, tmp_path):
    monkeypatch.setattr(doc_drift_cmd, "_repo_path", lambda *a, **k: tmp_path)
    runner = CliRunner()
    alone = runner.invoke(doc_drift_cmd.doc_drift_command, ["--since", "main...HEAD"])
    assert alone.exit_code == 2
    both = runner.invoke(
        doc_drift_cmd.doc_drift_command,
        ["--check", "--since", "main...HEAD", "--write-baseline", str(tmp_path / "b.json")],
    )
    assert both.exit_code == 2
