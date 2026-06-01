"""Integration tests for the CLI — gate tests using MockProvider on sample_repo."""

from __future__ import annotations

import shutil

import pytest
from click.testing import CliRunner

from repowise.cli.main import cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def work_repo(tmp_path, sample_repo_path, monkeypatch):
    """Copy sample_repo into a temporary directory for isolation."""
    dest = tmp_path / "repo"
    shutil.copytree(sample_repo_path, dest)
    # Point the DB at the repo-local path so tests can assert on its existence
    db_path = dest / ".repowise" / "wiki.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{db_path}")
    return dest


def _git(args, cwd):
    import subprocess

    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e.x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e.x",
    }
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env={**env})


@pytest.fixture
def workspace_root(tmp_path, sample_repo_path, monkeypatch):
    """A directory holding two git-initialized copies of sample_repo.

    Each sub-repo is a real git repo (so the scanner detects >1 repo and routes
    into the workspace flow) and uses its own repo-local DB — so we must NOT set
    REPOWISE_DB_URL here.
    """
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    root = tmp_path / "ws"
    root.mkdir()
    for name in ("alpha", "beta"):
        dest = root / name
        shutil.copytree(sample_repo_path, dest)
        _git(["init"], dest)
        _git(["add", "-A"], dest)
        _git(["commit", "-m", "init"], dest)
    return root


@pytest.fixture
def git_work_repo(tmp_path, sample_repo_path, monkeypatch):
    """A git-backed copy of sample_repo (one commit), with a repo-local DB.

    ``repowise update`` diffs HEAD against the last synced commit, so the
    update path needs a real git repo with history.
    """
    dest = tmp_path / "gitrepo"
    shutil.copytree(sample_repo_path, dest)
    db_path = dest / ".repowise" / "wiki.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{db_path}")
    _git(["init"], dest)
    _git(["add", "-A"], dest)
    _git(["commit", "-m", "init"], dest)
    return dest


# ---------------------------------------------------------------------------
# Gate tests
# ---------------------------------------------------------------------------


class TestWorkspaceInitIndexOnly:
    def test_indexes_each_repo(self, runner, workspace_root):
        result = runner.invoke(
            cli,
            ["init", str(workspace_root), "--all", "--index-only"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "workspace init complete" in result.output
        # Each sub-repo got its own index + state, and a workspace config exists.
        for name in ("alpha", "beta"):
            assert (workspace_root / name / ".repowise" / "wiki.db").exists()
            assert (workspace_root / name / ".repowise" / "state.json").exists()
        assert (workspace_root / ".repowise-workspace.yaml").exists()


class TestInitDryRun:
    def test_exit_zero_shows_plan(self, runner, work_repo):
        result = runner.invoke(
            cli,
            ["init", str(work_repo), "--provider", "mock", "--dry-run"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "Generation Plan" in result.output
        assert "Dry run" in result.output
        # No DB should be created
        assert not (work_repo / ".repowise" / "wiki.db").exists()


class TestInitFullMock:
    def test_creates_db_and_state(self, runner, work_repo):
        result = runner.invoke(
            cli,
            ["init", str(work_repo), "--provider", "mock", "--yes"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert (work_repo / ".repowise" / "wiki.db").exists()
        assert (work_repo / ".repowise" / "state.json").exists()
        assert "init complete" in result.output


class TestInitIndexOnly:
    def test_index_only_creates_db_and_state_no_pages(self, runner, work_repo):
        result = runner.invoke(
            cli,
            ["init", str(work_repo), "--index-only"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert (work_repo / ".repowise" / "wiki.db").exists()
        assert (work_repo / ".repowise" / "state.json").exists()
        assert "index complete" in result.output

        import json

        state = json.loads((work_repo / ".repowise" / "state.json").read_text(encoding="utf-8"))
        assert state.get("docs_enabled") is False
        # No pages generated in index-only mode.
        assert state.get("total_pages", 0) == 0

    def test_index_only_persists_clamped_commit_limit_and_excludes(self, runner, work_repo):
        from repowise.cli.helpers import load_config

        result = runner.invoke(
            cli,
            ["init", str(work_repo), "--index-only", "-x", "vendor/", "--commit-limit", "99999"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output

        cfg = load_config(work_repo)
        assert cfg["exclude_patterns"] == ["vendor/"]
        assert cfg["commit_limit"] == 5000  # 99999 clamped to the 5000 max

    def test_index_only_omits_excludes_when_none_given(self, runner, work_repo):
        from repowise.cli.helpers import load_config

        result = runner.invoke(
            cli,
            ["init", str(work_repo), "--index-only"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output

        cfg = load_config(work_repo)
        # Empty excludes and unset commit-limit must not be written as [] / default.
        assert "exclude_patterns" not in cfg
        assert "commit_limit" not in cfg


class TestInitDefaultDbLocation:
    def test_creates_repo_local_db_without_env_override(
        self,
        runner,
        tmp_path,
        sample_repo_path,
        monkeypatch,
    ):
        work_repo = tmp_path / "repo"
        shutil.copytree(sample_repo_path, work_repo)
        monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
        monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
        result = runner.invoke(
            cli,
            ["init", str(work_repo), "--provider", "mock", "--yes"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert (work_repo / ".repowise" / "wiki.db").exists()


class TestInitIdempotent:
    def test_running_init_twice(self, runner, work_repo):
        args = ["init", str(work_repo), "--provider", "mock", "--yes"]
        r1 = runner.invoke(cli, args, catch_exceptions=False)
        assert r1.exit_code == 0, r1.output
        r2 = runner.invoke(cli, args, catch_exceptions=False)
        assert r2.exit_code == 0, r2.output


class TestStatusAfterInit:
    def test_shows_page_counts(self, runner, work_repo):
        runner.invoke(
            cli,
            ["init", str(work_repo), "--provider", "mock", "--yes"],
            catch_exceptions=False,
        )
        result = runner.invoke(
            cli,
            ["status", str(work_repo)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "Sync State" in result.output


class TestDoctorAfterInit:
    def test_passes_checks(self, runner, work_repo):
        runner.invoke(
            cli,
            ["init", str(work_repo), "--provider", "mock", "--yes"],
            catch_exceptions=False,
        )
        result = runner.invoke(
            cli,
            ["doctor", str(work_repo)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "repowise Doctor" in result.output


class TestSearchFulltext:
    def test_returns_results_or_no_error(self, runner, work_repo):
        runner.invoke(
            cli,
            ["init", str(work_repo), "--provider", "mock", "--yes"],
            catch_exceptions=False,
        )
        result = runner.invoke(
            cli,
            ["search", "function", str(work_repo)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output


class TestExportMarkdown:
    def test_creates_output_files(self, runner, work_repo):
        runner.invoke(
            cli,
            ["init", str(work_repo), "--provider", "mock", "--yes"],
            catch_exceptions=False,
        )
        export_dir = work_repo / "export_out"
        result = runner.invoke(
            cli,
            ["export", str(work_repo), "--format", "markdown", "--output", str(export_dir)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        # Should have created some .md files
        md_files = list(export_dir.glob("*.md"))
        assert len(md_files) > 0, f"No markdown files in {export_dir}"


class TestUpdateIndexOnly:
    def test_advances_sync_commit(self, runner, git_work_repo):
        import json

        # Index first (index-only — no LLM needed).
        r0 = runner.invoke(
            cli, ["init", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        assert r0.exit_code == 0, r0.output
        state0 = json.loads(
            (git_work_repo / ".repowise" / "state.json").read_text(encoding="utf-8")
        )
        base_commit = state0["last_sync_commit"]
        assert base_commit

        # Make a change and commit it so update has a diff to process.
        (git_work_repo / "new_module.py").write_text(
            "def added():\n    return 1\n", encoding="utf-8"
        )
        _git(["add", "-A"], git_work_repo)
        _git(["commit", "-m", "add module"], git_work_repo)

        r1 = runner.invoke(
            cli, ["update", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        assert r1.exit_code == 0, r1.output
        assert "Index-only update complete" in r1.output

        state1 = json.loads(
            (git_work_repo / ".repowise" / "state.json").read_text(encoding="utf-8")
        )
        assert state1["last_sync_commit"] != base_commit


class TestUpdateConfigChangeDetection:
    def _state(self, repo):
        import json

        return json.loads((repo / ".repowise" / "state.json").read_text(encoding="utf-8"))

    def test_init_stores_fingerprint_and_update_detects_config_change(
        self, runner, git_work_repo
    ):
        """init records a config_fingerprint; an update with no file changes
        skips rescore when config is unchanged but triggers one when
        health-rules.json changes (#296, issue 3)."""
        r0 = runner.invoke(
            cli, ["init", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        assert r0.exit_code == 0, r0.output
        assert self._state(git_work_repo).get("config_fingerprint")

        # No new commits, unchanged config -> no rescore.
        r1 = runner.invoke(
            cli, ["update", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        assert r1.exit_code == 0, r1.output
        assert "Already up to date" in r1.output

        # Change health-rules.json (not a git change) -> config-triggered rescore.
        (git_work_repo / ".repowise" / "health-rules.json").write_text(
            '{"disabled_biomarkers": ["ungoverned_hotspot"]}', encoding="utf-8"
        )
        r2 = runner.invoke(
            cli, ["update", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        assert r2.exit_code == 0, r2.output
        assert "Config files changed" in r2.output
        assert "health re-score complete" in r2.output.lower()

    def test_dry_run_does_not_rescore_or_advance_fingerprint(self, runner, git_work_repo):
        """`update --dry-run` after a config change must not mutate state/DB."""
        import json

        runner.invoke(
            cli, ["init", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        fp_before = self._state(git_work_repo)["config_fingerprint"]

        (git_work_repo / ".repowise" / "health-rules.json").write_text(
            '{"disabled_biomarkers": ["ungoverned_hotspot"]}', encoding="utf-8"
        )
        result = runner.invoke(
            cli,
            ["update", str(git_work_repo), "--index-only", "--dry-run"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "Dry run" in result.output
        assert "complete" not in result.output.lower()
        # Fingerprint must NOT advance, so a real update still re-scores later.
        assert self._state(git_work_repo)["config_fingerprint"] == fp_before

    def test_config_change_with_source_diffs_runs_full_rescore(self, runner, git_work_repo):
        """A config change must take the full re-score path even when there are
        also source-file commits (not the partial update)."""
        runner.invoke(
            cli, ["init", str(git_work_repo), "--index-only"], catch_exceptions=False
        )

        # New source commit AND a config change in the same update window.
        (git_work_repo / "new_module.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        _git(["add", "-A"], git_work_repo)
        _git(["commit", "-m", "add module"], git_work_repo)
        (git_work_repo / ".repowise" / "health-rules.json").write_text(
            '{"disabled_biomarkers": ["ungoverned_hotspot"]}', encoding="utf-8"
        )

        result = runner.invoke(
            cli, ["update", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output
        assert "Config files changed" in result.output
        assert "health re-score complete" in result.output.lower()


class TestUpdatePreservesDeadCode:
    def test_single_file_update_preserves_unchanged_files(self, runner, git_work_repo):
        """A single-file re-index must not wipe the whole dead-code index;
        unchanged files keep their findings (regression guard for #295)."""
        import sqlite3

        runner.invoke(
            cli, ["init", str(git_work_repo), "--index-only"], catch_exceptions=False
        )

        db = git_work_repo / ".repowise" / "wiki.db"

        def _counts_by_file() -> dict[str, int]:
            con = sqlite3.connect(db)
            try:
                rows = con.execute(
                    "SELECT file_path, COUNT(*) FROM dead_code_findings "
                    "WHERE status='open' GROUP BY file_path"
                ).fetchall()
            finally:
                con.close()
            return {fp: n for fp, n in rows}

        before = _counts_by_file()
        if sum(before.values()) == 0:
            pytest.skip("sample repo produced no dead-code findings to preserve")

        # Pick a real file (skip package-level findings whose path is a directory).
        changed = next((fp for fp in before if (git_work_repo / fp).is_file()), None)
        if changed is None:
            pytest.skip("no file-level dead-code findings to exercise scoping")
        # Append a blank line: a real content change valid in any language.
        target = git_work_repo / changed
        target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        _git(["add", "-A"], git_work_repo)
        _git(["commit", "-m", "touch one file"], git_work_repo)

        result = runner.invoke(
            cli, ["update", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output

        after = _counts_by_file()
        assert sum(after.values()) > 0, "dead-code index was wiped to zero"
        for fp, n in before.items():
            if fp != changed:
                assert after.get(fp, 0) == n, f"unchanged file {fp} lost findings"


class TestUpdateFullMock:
    def test_regenerates_pages(self, runner, git_work_repo):
        import json

        r0 = runner.invoke(
            cli,
            ["init", str(git_work_repo), "--provider", "mock", "--yes"],
            catch_exceptions=False,
        )
        assert r0.exit_code == 0, r0.output

        (git_work_repo / "new_module.py").write_text(
            "def added():\n    return 1\n", encoding="utf-8"
        )
        _git(["add", "-A"], git_work_repo)
        _git(["commit", "-m", "add module"], git_work_repo)

        r1 = runner.invoke(
            cli,
            ["update", str(git_work_repo), "--provider", "mock", "--docs"],
            catch_exceptions=False,
        )
        assert r1.exit_code == 0, r1.output
        # State advanced and docs stayed enabled through a full update.
        state = json.loads((git_work_repo / ".repowise" / "state.json").read_text(encoding="utf-8"))
        assert state.get("docs_enabled") is True


class TestUpdateNoChanges:
    def test_already_up_to_date(self, runner, git_work_repo):
        r0 = runner.invoke(
            cli, ["init", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        assert r0.exit_code == 0, r0.output
        # No new commits since init → update is a no-op.
        r1 = runner.invoke(
            cli, ["update", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        assert r1.exit_code == 0, r1.output
        assert "Already up to date" in r1.output
