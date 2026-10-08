"""CLI coverage for change-risk exclusion rules."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from repowise.cli.commands.risk_cmd import risk_command


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _commit(repo: Path, files: dict[str, str], message: str) -> None:
    for relative_path, content in files.items():
        path = repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        _git(["add", relative_path], repo)
    _git(["-c", "user.name=Dev", "-c", "user.email=dev@example.com", "commit", "-m", message], repo)


def test_risk_uses_root_riskignore_and_repeatable_excludes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"README.md": "# seed\n"}, "chore: seed")
    _commit(
        repo,
        {
            "src/app.py": "value = 1\n",
            "tests/test_app.py": "def test_value():\n    assert True\n",
            "web/app.spec.ts": "it('works', () => {})\n",
        },
        "feat: app",
    )
    (repo / ".riskignore").write_text("tests/\n", encoding="utf-8")

    result = CliRunner().invoke(
        risk_command,
        [
            "HEAD",
            "--path",
            str(repo),
            "--baseline",
            "0",
            "-x",
            "*.spec.ts",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["features"]["nf"] == 1
    assert payload["features"]["la"] == 1
    assert payload["exclude_patterns"] == ["tests/", "*.spec.ts"]
    assert payload["risk_authority"]["primary_fields"] == ["risk_percentile", "classification"]
    assert payload["risk_authority"]["score_role"] == "supporting_diff_shape_signal"
    assert {scale["unit"] for scale in payload["risk_scales"]} >= {
        "normalized_points",
        "percentile_rank",
        "category",
        "lines",
        "items",
        "shannon_bits",
        "prior_commits",
        "logit_points",
    }


def test_risk_human_output_labels_absolute_fallback_when_baseline_is_disabled(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"README.md": "# seed\n"}, "chore: seed")
    _commit(repo, {"src/app.py": "value = 1\n"}, "feat: app")

    result = CliRunner().invoke(
        risk_command,
        ["HEAD", "--path", str(repo), "--baseline", "0"],
    )

    assert result.exit_code == 0, result.output
    assert "Absolute fallback band:" in result.output
    assert "Benchmarked review priority:" not in result.output
    assert "probability" not in result.output.lower()


def test_risk_human_output_leads_with_benchmarked_review_priority(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {"README.md": "# seed\n"}, "chore: seed")
    for index in range(9):
        _commit(repo, {f"src/f{index}.py": f"value = {index}\n"}, f"feat: add {index}")

    result = CliRunner().invoke(risk_command, ["HEAD", "--path", str(repo)])

    assert result.exit_code == 0, result.output
    assert "Benchmarked review priority:" in result.output
    assert "percentile of recent commits by size and spread" in result.output
    assert "Absolute fallback band:" not in result.output


def test_risk_help_describes_repeatable_exclude() -> None:
    result = CliRunner().invoke(risk_command, ["--help"])

    assert result.exit_code == 0
    assert "-x, --exclude PATTERN" in result.output
    assert "Repeatable" in result.output


# ---------------------------------------------------------------------------
# --fail-above-percentile: the CI gate on risk_percentile
# ---------------------------------------------------------------------------


def _ranked_repo(tmp_path: Path) -> Path:
    """Enough history to rank HEAD against: a seed and nine one-file commits."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["checkout", "-B", "main"], repo)
    _commit(repo, {"README.md": "# seed\n"}, "chore: seed")
    for index in range(9):
        _commit(repo, {f"src/f{index}.py": f"value = {index}\n"}, f"feat: add {index}")
    return repo


def _gate(repo: Path, *args: str, env: dict[str, str] | None = None):
    from repowise.core.ci.base import CI_BASE_VARS

    return CliRunner(env={**dict.fromkeys(CI_BASE_VARS, ""), **(env or {})}).invoke(
        risk_command, ["--path", str(repo), *args]
    )


def test_gate_passes_at_or_below_the_percentile_and_fails_above(tmp_path: Path) -> None:
    repo = _ranked_repo(tmp_path)

    passed = _gate(repo, "HEAD", "--fail-above-percentile", "100", "--format", "json")
    assert passed.exit_code == 0, passed.output
    payload = json.loads(passed.stdout)
    gate = payload["gate"]
    assert (gate["fail_above_percentile"], gate["status"]) == (100.0, "pass")
    # Unrounded, beside the rounded risk_percentile.
    assert round(gate["percentile"], 1) == payload["risk_percentile"]

    failed = _gate(repo, "HEAD", "--fail-above-percentile", "0", "--format", "json")
    assert failed.exit_code == 1, failed.output
    assert json.loads(failed.stdout)["gate"]["status"] == "fail"


def test_json_has_no_gate_block_without_the_flag(tmp_path: Path) -> None:
    result = _gate(_ranked_repo(tmp_path), "HEAD", "--format", "json")

    assert result.exit_code == 0, result.output
    assert "gate" not in json.loads(result.stdout)


def test_an_unranked_change_cannot_be_gated(tmp_path: Path) -> None:
    repo = _ranked_repo(tmp_path)

    disabled = _gate(repo, "HEAD", "--baseline", "0", "--fail-above-percentile", "90")
    assert disabled.exit_code == 2
    assert "--baseline 0" in disabled.output

    young = tmp_path / "young"
    young.mkdir()
    _git(["init", "-q"], young)
    _commit(young, {"a.py": "a = 1\n"}, "feat: a")
    _commit(young, {"b.py": "b = 1\n"}, "feat: b")
    few = _gate(young, "HEAD", "--fail-above-percentile", "90", "--format", "json")
    assert few.exit_code == 2
    assert json.loads(few.stdout)["error"] == "no_baseline"


def test_an_unreadable_change_is_exit_2_for_every_revspec_run(tmp_path: Path) -> None:
    repo = _ranked_repo(tmp_path)

    for args in (["no-such-ref"], ["no-such-ref", "--fail-above-percentile", "90"]):
        result = _gate(repo, *args, "--baseline", "0")
        assert result.exit_code == 2, result.output
        assert "Could not read change" in result.output


def test_the_gate_scores_the_ci_change_when_revspec_is_omitted(tmp_path: Path) -> None:
    repo = _ranked_repo(tmp_path)
    _git(["update-ref", "refs/remotes/origin/main", "HEAD~1"], repo)
    # A dirty tree would be the subject without the flag; the gate ignores it.
    (repo / "src" / "f0.py").write_text("value = 100\n", encoding="utf-8")

    gated = _gate(
        repo, "--fail-above-percentile", "100", "--format", "json",
        env={"GITHUB_BASE_REF": "main"},
    )
    assert gated.exit_code == 0, gated.output
    assert json.loads(gated.stdout)["ref"] == "origin/main...HEAD"

    ungated = _gate(repo, "--baseline", "0", "--format", "json")
    assert json.loads(ungated.stdout)["working_tree"] is True


def test_the_gate_without_a_target_branch_exits_2(tmp_path: Path) -> None:
    repo = _ranked_repo(tmp_path)
    _git(["checkout", "-q", "--detach"], repo)
    _git(["branch", "-D", "main"], repo)

    result = _gate(repo, "--fail-above-percentile", "90")
    assert result.exit_code == 2
    assert "Pass REVSPEC" in result.output


def test_markdown_and_github_lead_with_the_verdict(tmp_path: Path) -> None:
    repo = _ranked_repo(tmp_path)
    summary = tmp_path / "summary.md"

    md = _gate(repo, "HEAD", "--fail-above-percentile", "0", "--format", "markdown")
    assert md.exit_code == 1
    first, _, scope = md.stdout.splitlines()[:3]
    assert first.startswith("**Above the 0th percentile gate**: larger and more spread out than ")
    assert first.endswith("% of recent commits")
    assert "absolute" not in md.stdout
    assert "across 1 directory" in scope

    gh = _gate(
        repo, "HEAD", "--fail-above-percentile", "0", "--format", "github",
        env={"GITHUB_STEP_SUMMARY": str(summary)},
    )
    assert gh.exit_code == 1
    assert gh.stdout.startswith("::error::Above the 0th percentile gate: ")
    assert summary.read_text(encoding="utf-8").startswith("**Above the 0th percentile gate**")


def test_ci_formats_score_the_ci_change_without_the_gate(tmp_path: Path) -> None:
    repo = _ranked_repo(tmp_path)
    _git(["update-ref", "refs/remotes/origin/main", "HEAD~1"], repo)

    md = _gate(repo, "--format", "markdown", env={"GITHUB_BASE_REF": "main"})
    assert md.exit_code == 0, md.output
    assert md.stdout.startswith("**Change risk**: larger and more spread out than ")
    assert "`origin/main...HEAD`" in md.stdout


def _scored(percentile: float | None):
    from types import SimpleNamespace

    return SimpleNamespace(percentile=percentile)


def test_percentile_text_never_hides_which_side_of_the_gate() -> None:
    from repowise.core.analysis.change_risk.render import (
        _rank_text,
        headline,
        percentile_gate,
        percentile_text,
    )

    assert _rank_text(95.0) == "95th"
    assert _rank_text(95.4) == "95.4th"
    assert _rank_text(95.001) == "95.001th"
    assert "." not in _rank_text(95.0).removesuffix("th")
    # Rounds as usual when rounding agrees with the gate.
    assert percentile_text(_scored(96.7), 95) == "97th"
    assert percentile_text(_scored(21.0)) == "21st"
    # Just above an integer gate: rounding to 95 would read as "not above".
    assert percentile_text(_scored(95.4), 95) == "95.4th"
    assert percentile_text(_scored(95.0004), 95) == "95.0004th"
    assert percentile_gate(_scored(95.0004), 95) == "fail"
    assert percentile_text(_scored(None)) == "unranked"
    unranked = headline(_scored(None), 95, markdown=False)
    assert unranked == "Change risk: unranked · too few recent commits to rank it"
    assert headline(_scored(40.0), 95, markdown=False) == (
        "Within the 95th percentile gate: larger and more spread out than 40% of recent commits"
    )


def test_headline_never_claims_more_than_was_measured() -> None:
    from repowise.core.analysis.change_risk.render import headline

    # 99.5 and 100.0 read 99%: "larger than 100%" cannot be true.
    for pct in (99.5, 100.0):
        assert "than 99%" in headline(_scored(pct), None, markdown=False)
        assert "than 99%" in headline(_scored(pct), 95, markdown=False)
    assert "than 42%" in headline(_scored(42.9), None, markdown=False)
    # The gate-aware decimals are already below the next whole number.
    assert headline(_scored(95.4), 95, markdown=False).endswith("than 95.4% of recent commits")


def test_target_mode_refuses_ci_formats_and_the_gate(tmp_path: Path) -> None:
    repo = _ranked_repo(tmp_path)

    fmt = _gate(repo, "--target", "src/f0.py", "--format", "markdown")
    assert fmt.exit_code == 2
    assert "--target supports --format table or json" in fmt.output
    gate = _gate(repo, "--target", "src/f0.py", "--fail-above-percentile", "90")
    assert gate.exit_code == 2
    assert "gates a change" in gate.output
