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


def _rev_parse(cwd, *args):
    import subprocess

    return subprocess.check_output(["git", "rev-parse", *args], cwd=cwd, text=True).strip()


def _remove_worktree(base_repo, worktree_dir):
    """Release git's worktree bookkeeping, then sweep leftovers.

    Order matters: rmtree-first leaves git metadata pointing at a missing
    directory and ``worktree remove`` then exits 128. Both steps are
    best-effort so cleanup never masks the real test failure.
    """
    import shutil
    import subprocess

    subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree_dir)],
        cwd=base_repo,
        capture_output=True,
    )
    shutil.rmtree(worktree_dir, ignore_errors=True)
    subprocess.run(["git", "worktree", "prune"], cwd=base_repo, capture_output=True)


def _db_scalar(db_path, sql):
    """One-value query with an explicitly closed connection. ``with
    sqlite3.connect(...)`` only manages the transaction, not the handle, and
    a lingering handle breaks worktree cleanup on Windows."""
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute(sql).fetchone()[0]


def _db_column(db_path, sql):
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(db_path)) as conn:
        return [row[0] for row in conn.execute(sql).fetchall()]


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
        assert cfg["commit_limit"] == 10000  # 99999 clamped to the 10000 max

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
        assert "index-only update complete" in r1.output

        state1 = json.loads(
            (git_work_repo / ".repowise" / "state.json").read_text(encoding="utf-8")
        )
        assert state1["last_sync_commit"] != base_commit


class TestUpdateConfigChangeDetection:
    def _state(self, repo):
        import json

        return json.loads((repo / ".repowise" / "state.json").read_text(encoding="utf-8"))

    def test_init_stores_fingerprint_and_update_detects_config_change(self, runner, git_work_repo):
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

        runner.invoke(cli, ["init", str(git_work_repo), "--index-only"], catch_exceptions=False)
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
        runner.invoke(cli, ["init", str(git_work_repo), "--index-only"], catch_exceptions=False)

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

        runner.invoke(cli, ["init", str(git_work_repo), "--index-only"], catch_exceptions=False)

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


class TestInitSeedFrom:
    """Explicit --seed-from: copy a base checkout's index, then update.

    Every git call goes through the module-level ``_git`` helper so commits
    work on identity-less CI runners, and each test drops REPOWISE_DB_URL so
    the base repo and the worktree each use their own repo-local wiki.db
    (the fixture pins the env var to the base repo's DB, which would silently
    route the worktree's delegated update into the wrong database).
    """

    def test_seeds_from_base_branch(self, git_work_repo, monkeypatch):
        """Slightly stale but valid base: base indexed at commit A, worktree
        branch at commit B, A is an ancestor of B, so seeding works."""
        import json

        from click.testing import CliRunner

        monkeypatch.delenv("REPOWISE_DB_URL", raising=False)

        r0 = CliRunner().invoke(
            cli, ["init", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        assert r0.exit_code == 0, r0.output

        _git(["checkout", "-b", "feature"], git_work_repo)
        (git_work_repo / "new_file.py").write_text("print('hello')\n", encoding="utf-8")
        _git(["add", "new_file.py"], git_work_repo)
        _git(["commit", "-m", "feature commit"], git_work_repo)

        worktree_dir = git_work_repo.parent / "feature-worktree"
        _git(["worktree", "add", "-b", "feature2", str(worktree_dir), "feature"], git_work_repo)

        try:
            r1 = CliRunner().invoke(
                cli,
                ["init", str(worktree_dir), "--seed-from", str(git_work_repo), "--index-only"],
                catch_exceptions=False,
            )
            assert r1.exit_code == 0, r1.output
            assert "Worktree index seeded successfully" in r1.output
            assert "Delegating to update..." in r1.output

            assert (worktree_dir / ".repowise" / "state.json").exists()
            assert (worktree_dir / ".repowise" / "wiki.db").exists()

            state = json.loads(
                (worktree_dir / ".repowise" / "state.json").read_text(encoding="utf-8")
            )
            # The delegated update advanced last_sync_commit to the feature commit.
            assert state["last_sync_commit"] == _rev_parse(worktree_dir, "HEAD")
            # Index-only runs persist the dependency graph, not wiki pages:
            # surviving graph nodes prove the base's index rode along.
            count = _db_scalar(
                worktree_dir / ".repowise" / "wiki.db",
                "SELECT COUNT(*) FROM graph_nodes",
            )
            assert count > 0, "Base index content should have survived the seed"
        finally:
            _remove_worktree(git_work_repo, worktree_dir)

    def test_unrelated_repo_fallback(self, git_work_repo, tmp_path, monkeypatch):
        from click.testing import CliRunner

        monkeypatch.delenv("REPOWISE_DB_URL", raising=False)

        r0 = CliRunner().invoke(
            cli, ["init", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        assert r0.exit_code == 0, r0.output

        unrelated_repo = tmp_path / "unrelated"
        unrelated_repo.mkdir()
        _git(["init"], unrelated_repo)
        (unrelated_repo / "main.py").write_text("x = 1\n", encoding="utf-8")
        _git(["add", "."], unrelated_repo)
        _git(["commit", "-m", "init"], unrelated_repo)

        r1 = CliRunner().invoke(
            cli,
            ["init", str(unrelated_repo), "--seed-from", str(git_work_repo), "--index-only"],
            catch_exceptions=False,
        )
        assert r1.exit_code == 0, r1.output
        flat = " ".join(r1.output.split())
        assert "does not share the same initial commit" in flat
        assert "Falling back to full init" in flat

    def test_unreachable_commit_fallback(self, git_work_repo, monkeypatch):
        from click.testing import CliRunner

        monkeypatch.delenv("REPOWISE_DB_URL", raising=False)

        # The fixture's default branch name depends on the host git config
        # (main vs master), so capture it instead of assuming.
        default_branch = _rev_parse(git_work_repo, "--abbrev-ref", "HEAD")

        _git(["checkout", "-b", "feature1"], git_work_repo)
        (git_work_repo / "f1.py").write_text("x = 1\n", encoding="utf-8")
        _git(["add", "f1.py"], git_work_repo)
        _git(["commit", "-m", "f1"], git_work_repo)

        _git(["checkout", default_branch], git_work_repo)
        _git(["checkout", "-b", "feature2"], git_work_repo)
        (git_work_repo / "f2.py").write_text("y = 2\n", encoding="utf-8")
        _git(["add", "f2.py"], git_work_repo)
        _git(["commit", "-m", "f2"], git_work_repo)

        # Index feature1 (the seed source); feature2 has diverged from it.
        _git(["checkout", "feature1"], git_work_repo)
        r0 = CliRunner().invoke(
            cli, ["init", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        assert r0.exit_code == 0, r0.output

        worktree_dir = git_work_repo.parent / "f2-worktree"
        _git(
            ["worktree", "add", "-b", "feature2-wt", str(worktree_dir), "feature2"],
            git_work_repo,
        )

        try:
            r1 = CliRunner().invoke(
                cli,
                ["init", str(worktree_dir), "--seed-from", str(git_work_repo), "--index-only"],
                catch_exceptions=False,
            )
            assert r1.exit_code == 0, r1.output
            # Rich wraps long lines (Windows temp paths are wide), so collapse
            # whitespace before matching phrases.
            flat = " ".join(r1.output.split())
            assert "is not an ancestor of worktree HEAD" in flat
            assert "Falling back to full init" in flat
        finally:
            _remove_worktree(git_work_repo, worktree_dir)

    def test_seed_from_self_fails(self, git_work_repo):
        from click.testing import CliRunner

        r = CliRunner().invoke(cli, ["init", str(git_work_repo), "--seed-from", str(git_work_repo)])
        assert r.exit_code != 0
        assert "--seed-from cannot be the same as the target directory" in r.output

    def test_seeds_from_base_branch_with_provider(self, git_work_repo, monkeypatch):
        import json

        from click.testing import CliRunner

        monkeypatch.delenv("REPOWISE_DB_URL", raising=False)

        # Full doc coverage so the new file is not tier-gated out of a page;
        # the copied config carries the setting into the delegated update.
        r0 = CliRunner().invoke(
            cli,
            ["init", str(git_work_repo), "--provider", "mock", "--coverage", "1.0", "--yes"],
            catch_exceptions=False,
        )
        assert r0.exit_code == 0, r0.output

        _git(["checkout", "-b", "feature"], git_work_repo)
        (git_work_repo / "new_file.py").write_text("print('hello')\n", encoding="utf-8")
        _git(["add", "new_file.py"], git_work_repo)
        _git(["commit", "-m", "feature commit"], git_work_repo)

        worktree_dir = git_work_repo.parent / "feature-worktree-provider"
        _git(["worktree", "add", "-b", "feature2", str(worktree_dir), "feature"], git_work_repo)

        try:
            r1 = CliRunner().invoke(
                cli,
                ["init", str(worktree_dir), "--seed-from", str(git_work_repo)],
                catch_exceptions=False,
            )
            assert r1.exit_code == 0, r1.output
            assert "Delegating to update" in r1.output

            # Copied config.yaml and the vector db directory (lancedb).
            assert (worktree_dir / ".repowise" / "config.yaml").exists()
            assert (worktree_dir / ".repowise" / "lancedb").exists()

            # The seeded repository row must be adopted by the worktree: one
            # row, pointing at the worktree, with the seeded pages under it.
            # (Pre-fix, the delegated update minted a second repository named
            # after the worktree dir and split the index in two.)
            repos = _db_column(
                worktree_dir / ".repowise" / "wiki.db",
                "SELECT local_path FROM repositories",
            )
            assert repos == [str(worktree_dir)], repos

            paths = _db_column(
                worktree_dir / ".repowise" / "wiki.db",
                "SELECT target_path FROM wiki_pages",
            )
            assert len(paths) > 0, "Seeded pages should have survived"
            # Note: no assertion that new_file.py gets its own file page. The
            # update-time selection budget is computed over the affected-file
            # subset (not the whole repo), so a small update batch rarely
            # selects a brand-new file for a page. Tracked as #746;
            # independent of seeding.
            state = json.loads(
                (worktree_dir / ".repowise" / "state.json").read_text(encoding="utf-8")
            )
            assert state["last_sync_commit"] == _rev_parse(worktree_dir, "HEAD")
        finally:
            _remove_worktree(git_work_repo, worktree_dir)


class TestWorktreeAutoSeed:
    """Auto-detection: no --seed-from needed inside a linked worktree."""

    def test_init_auto_seeds_in_worktree(self, git_work_repo, monkeypatch):
        from click.testing import CliRunner

        monkeypatch.delenv("REPOWISE_DB_URL", raising=False)

        r0 = CliRunner().invoke(
            cli, ["init", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        assert r0.exit_code == 0, r0.output

        worktree_dir = git_work_repo.parent / "auto-seed-init"
        _git(["worktree", "add", "-b", "auto-seed-init", str(worktree_dir)], git_work_repo)
        try:
            r1 = CliRunner().invoke(
                cli, ["init", str(worktree_dir), "--index-only"], catch_exceptions=False
            )
            assert r1.exit_code == 0, r1.output
            assert "[worktree]" in r1.output
            assert "Worktree index seeded successfully" in r1.output
            assert (worktree_dir / ".repowise" / "state.json").exists()
            assert (worktree_dir / ".repowise" / "wiki.db").exists()
        finally:
            _remove_worktree(git_work_repo, worktree_dir)

    def test_init_no_seed_skips_auto_detection(self, git_work_repo, monkeypatch):
        from click.testing import CliRunner

        monkeypatch.delenv("REPOWISE_DB_URL", raising=False)

        r0 = CliRunner().invoke(
            cli, ["init", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        assert r0.exit_code == 0, r0.output

        worktree_dir = git_work_repo.parent / "no-seed"
        _git(["worktree", "add", "-b", "no-seed", str(worktree_dir)], git_work_repo)
        try:
            r1 = CliRunner().invoke(
                cli,
                ["init", str(worktree_dir), "--index-only", "--no-seed"],
                catch_exceptions=False,
            )
            assert r1.exit_code == 0, r1.output
            assert "[worktree]" not in r1.output
            # Cold init still produces a working index.
            assert (worktree_dir / ".repowise" / "state.json").exists()
        finally:
            _remove_worktree(git_work_repo, worktree_dir)

    def test_update_auto_seeds_unindexed_worktree(self, git_work_repo, monkeypatch):
        import json

        from click.testing import CliRunner

        monkeypatch.delenv("REPOWISE_DB_URL", raising=False)

        r0 = CliRunner().invoke(
            cli, ["init", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        assert r0.exit_code == 0, r0.output

        worktree_dir = git_work_repo.parent / "auto-seed-update"
        _git(["worktree", "add", "-b", "auto-seed-update", str(worktree_dir)], git_work_repo)
        try:
            # Advance the worktree so update has real work to catch up on.
            (worktree_dir / "wt_new.py").write_text("y = 2\n", encoding="utf-8")
            _git(["add", "wt_new.py"], worktree_dir)
            _git(["commit", "-m", "wt commit"], worktree_dir)

            r1 = CliRunner().invoke(
                cli, ["update", str(worktree_dir), "--index-only"], catch_exceptions=False
            )
            assert r1.exit_code == 0, r1.output
            assert "[worktree]" in r1.output

            state = json.loads(
                (worktree_dir / ".repowise" / "state.json").read_text(encoding="utf-8")
            )
            assert state["last_sync_commit"] == _rev_parse(worktree_dir, "HEAD")
        finally:
            _remove_worktree(git_work_repo, worktree_dir)

    def test_init_falls_back_when_base_unindexed(self, git_work_repo, monkeypatch):
        """Base has no .repowise: auto-seed stays silent, cold init proceeds."""
        from click.testing import CliRunner

        monkeypatch.delenv("REPOWISE_DB_URL", raising=False)

        worktree_dir = git_work_repo.parent / "unindexed-base"
        _git(["worktree", "add", "-b", "unindexed-base", str(worktree_dir)], git_work_repo)
        try:
            r1 = CliRunner().invoke(
                cli, ["init", str(worktree_dir), "--index-only"], catch_exceptions=False
            )
            assert r1.exit_code == 0, r1.output
            assert "[worktree]" not in r1.output
            assert (worktree_dir / ".repowise" / "state.json").exists()
        finally:
            _remove_worktree(git_work_repo, worktree_dir)
