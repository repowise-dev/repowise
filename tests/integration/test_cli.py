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

    def test_no_prose_dry_run_writes_no_wiki(self, runner, work_repo):
        """The branch above prices a model and returns; this one never did.

        ``--no-prose`` and a keyless run both reach the deterministic
        generation phase, which takes no ``dry_run`` argument at all.
        """
        result = runner.invoke(
            cli,
            ["init", str(work_repo), "--no-prose", "--dry-run", "--yes"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "Dry run" in result.output
        assert not (work_repo / ".repowise" / "wiki.db").exists()
        assert not (work_repo / ".repowise" / "state.json").exists()

    def test_no_provider_dry_run_writes_no_wiki(self, runner, work_repo, monkeypatch):
        """The other half of the predicate, and the worse one.

        A keyless run sets ``no_provider`` rather than ``index_only``, so it
        walked past the downgrade guard at the top of the command even
        interactively.
        """
        for key in (
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "OPENROUTER_API_KEY",
            "DEEPSEEK_API_KEY",
            "REPOWISE_PROVIDER",
        ):
            monkeypatch.delenv(key, raising=False)

        result = runner.invoke(
            cli,
            ["init", str(work_repo), "--prose", "--dry-run", "--yes"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "Dry run" in result.output
        assert not (work_repo / ".repowise" / "wiki.db").exists()
        assert not (work_repo / ".repowise" / "state.json").exists()

    def test_fast_dry_run_promises_no_wiki(self, runner, work_repo):
        """Fast skips generation, so the preview must not promise a wiki."""
        result = runner.invoke(
            cli,
            ["init", str(work_repo), "--mode", "fast", "--dry-run", "--yes"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "No wiki written" in result.output
        assert "Generation Plan" not in result.output
        assert not (work_repo / ".repowise" / "wiki.db").exists()

    def test_dry_run_does_not_replace_a_model_written_wiki(self, runner, work_repo):
        """Rendering templates over an existing wiki rewrote every page and
        downgraded ``docs_mode``, on the one command that promises not to act.
        Recoverable through page history, but every reader served templates
        afterwards and the spend behind the originals was wasted.
        """
        import json
        import sqlite3
        from contextlib import closing

        seed = runner.invoke(
            cli,
            ["init", str(work_repo), "--provider", "mock", "--yes"],
            catch_exceptions=False,
        )
        assert seed.exit_code == 0, seed.output

        db_path = work_repo / ".repowise" / "wiki.db"
        state_path = work_repo / ".repowise" / "state.json"
        with closing(sqlite3.connect(db_path)) as db:
            db.execute("UPDATE wiki_pages SET provider_name='gemini', content='WRITTEN'")
            db.commit()
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["docs_mode"] = "llm"
        state_path.write_text(json.dumps(state), encoding="utf-8")

        result = runner.invoke(
            cli,
            ["init", str(work_repo), "--no-prose", "--dry-run", "--yes"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output

        written = _db_scalar(db_path, "SELECT COUNT(*) FROM wiki_pages WHERE content='WRITTEN'")
        templated = _db_scalar(
            db_path, "SELECT COUNT(*) FROM wiki_pages WHERE provider_name='template'"
        )
        assert templated == 0
        assert written > 0
        assert json.loads(state_path.read_text(encoding="utf-8"))["docs_mode"] == "llm"


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
    def test_index_only_creates_db_and_template_pages(self, runner, work_repo):
        result = runner.invoke(
            cli,
            ["init", str(work_repo), "--index-only"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        db_path = work_repo / ".repowise" / "wiki.db"
        assert db_path.exists()
        assert (work_repo / ".repowise" / "state.json").exists()
        assert "index complete" in result.output

        import json

        state = json.loads((work_repo / ".repowise" / "state.json").read_text(encoding="utf-8"))
        assert state.get("docs_mode") == "deterministic"
        # Still False on purpose: an older reader treats it as "do not
        # LLM-regenerate", which is right for a repo with no provider.
        assert state.get("docs_enabled") is False

        assert _db_scalar(db_path, "SELECT COUNT(*) FROM wiki_pages") > 0
        providers = set(_db_column(db_path, "SELECT DISTINCT provider_name FROM wiki_pages"))
        assert providers == {"template"}

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


class TestStatusDoctorWithEnvDb:
    """Regression guard for #1274: with REPOWISE_DB_URL set, status and doctor
    must query the configured DB even when no repo-local wiki.db exists.

    The env DB lives outside the repo (like a Postgres container), so the
    pre-fix file-existence guards bailed out before ever resolving the URL.
    """

    @pytest.fixture
    def env_db_repo(self, tmp_path, sample_repo_path, monkeypatch):
        """sample_repo copy with REPOWISE_DB_URL pointing at an external DB."""
        dest = tmp_path / "repo"
        shutil.copytree(sample_repo_path, dest)
        db_path = tmp_path / "external.db"
        monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{db_path}")
        return dest

    def test_status_queries_env_db_without_local_wiki_db(self, runner, env_db_repo):
        result = runner.invoke(
            cli,
            ["init", str(env_db_repo), "--provider", "mock", "--yes"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        # The scenario that used to fail: data in the env DB, no local wiki.db.
        assert not (env_db_repo / ".repowise" / "wiki.db").exists()

        result = runner.invoke(
            cli,
            ["status", str(env_db_repo)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "Sync State" in result.output
        assert "Database not found" not in result.output
        assert "Pages by Type" in result.output

    def test_doctor_reports_db_ok_with_env_db(self, runner, env_db_repo):
        runner.invoke(
            cli,
            ["init", str(env_db_repo), "--provider", "mock", "--yes"],
            catch_exceptions=False,
        )
        assert not (env_db_repo / ".repowise" / "wiki.db").exists()

        result = runner.invoke(
            cli,
            ["doctor", str(env_db_repo)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "wiki.db not found" not in result.output

    def test_status_still_reports_not_found_with_no_db_configured(
        self, runner, tmp_path, sample_repo_path, monkeypatch
    ):
        monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
        monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
        dest = tmp_path / "repo"
        shutil.copytree(sample_repo_path, dest)
        # A .repowise/ dir with state but no DB — the "Database not found"
        # branch requires an initialized repo to get past the earlier guard.
        (dest / ".repowise").mkdir()
        (dest / ".repowise" / "state.json").write_text("{}", encoding="utf-8")

        result = runner.invoke(cli, ["status", str(dest)], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert "Database not found" in result.output

    def test_doctor_still_reports_fail_with_no_db_configured(
        self, runner, tmp_path, sample_repo_path, monkeypatch
    ):
        monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
        monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
        dest = tmp_path / "repo"
        shutil.copytree(sample_repo_path, dest)

        result = runner.invoke(cli, ["doctor", str(dest)], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert "wiki.db not found" in result.output

    def test_doctor_corrupt_local_db_shows_one_fail_row(
        self, runner, tmp_path, sample_repo_path, monkeypatch
    ):
        """A local wiki.db that exists but cannot be opened must report the real
        connection error — not an extra, contradictory "wiki.db not found" row
        (regression guard for the reviewer finding on #1274)."""
        monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
        monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
        dest = tmp_path / "repo"
        shutil.copytree(sample_repo_path, dest)
        (dest / ".repowise").mkdir()
        (dest / ".repowise" / "state.json").write_text("{}", encoding="utf-8")
        # Corrupt: not a valid SQLite file.
        (dest / ".repowise" / "wiki.db").write_text("this is not a database", encoding="utf-8")

        result = runner.invoke(cli, ["doctor", str(dest)], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        # Exactly one Database row — the real connection error, not a second
        # contradictory "wiki.db not found" row.
        assert "file is not a" in result.output
        assert "wiki.db not found" not in result.output


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


class TestUpdateWorkingTree:
    """``repowise watch``'s change source: work that is on disk, not in git.

    Without this the watcher was decorative — it fired an update on every save
    and the update diffed commit-to-commit, which on a repo with no new commits
    is empty by definition, so it printed "Already up to date" and returned.
    """

    def _symbol_names(self, repo):
        import sqlite3

        con = sqlite3.connect(repo / ".repowise" / "wiki.db")
        try:
            return {row[0] for row in con.execute("select name from wiki_symbols")}
        finally:
            con.close()

    def _dirty(self, repo):
        (repo / "new_module.py").write_text(
            "def uncommitted_addition():\n    return 1\n", encoding="utf-8"
        )

    def test_uncommitted_work_is_indexed(self, runner, git_work_repo):
        from repowise.cli.commands.update_cmd.command import UpdateOutcome, run_update

        r0 = runner.invoke(
            cli, ["init", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        assert r0.exit_code == 0, r0.output
        assert "uncommitted_addition" not in self._symbol_names(git_work_repo)

        self._dirty(git_work_repo)

        outcome = run_update(
            path=str(git_work_repo),
            provider_name=None,
            model=None,
            since=None,
            reasoning=None,
            cascade_budget=None,
            dry_run=False,
            workspace=False,
            no_workspace=True,
            repo_alias=None,
            index_only=True,
            include_working_tree=True,
        )

        assert outcome is UpdateOutcome.REGENERATED
        assert "uncommitted_addition" in self._symbol_names(git_work_repo)

    def _wt_update(self, repo, *, release_lock=True):
        """One watcher-style update. Mirrors ``_watch_single_repo``'s trigger,
        lock release included — in-process repeat runs need it."""
        from repowise.cli.commands.update_cmd.command import run_update
        from repowise.cli.commands.watch_cmd import _release_own_update_lock

        try:
            return run_update(
                path=str(repo),
                provider_name=None,
                model=None,
                since=None,
                reasoning=None,
                cascade_budget=None,
                dry_run=False,
                workspace=False,
                no_workspace=True,
                repo_alias=None,
                index_only=True,
                include_working_tree=True,
            )
        finally:
            if release_lock:
                _release_own_update_lock(repo)

    def _page_status(self, repo, path):
        import sqlite3

        con = sqlite3.connect(repo / ".repowise" / "wiki.db")
        try:
            row = con.execute(
                "select freshness_status from wiki_pages where id = ?", (f"file_page:{path}",)
            ).fetchone()
            return row[0] if row else None
        finally:
            con.close()

    def test_deleted_uncommitted_work_is_tombstoned(self, runner, git_work_repo):
        """Working-tree state has no ``last_sync_commit`` to diff against: a
        path stops being reported the moment it stops diverging from HEAD, so
        without explicit tracking nothing would ever mention a deleted
        untracked file again and its page would be served forever."""
        r0 = runner.invoke(
            cli, ["init", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        assert r0.exit_code == 0, r0.output

        self._dirty(git_work_repo)
        self._wt_update(git_work_repo)
        assert "uncommitted_addition" in self._symbol_names(git_work_repo)
        assert self._page_status(git_work_repo, "new_module.py") != "tombstone"

        (git_work_repo / "new_module.py").unlink()
        self._wt_update(git_work_repo)

        assert self._page_status(git_work_repo, "new_module.py") == "tombstone"

    def test_the_lock_is_not_left_held_between_runs(self, runner, git_work_repo):
        """The watcher is one long-lived process. ``run_update`` only drops
        the single-flight lock at process exit, so without the watcher's own
        release every save after the first would defer."""
        from repowise.cli.commands.update_cmd.command import UpdateOutcome

        r0 = runner.invoke(
            cli, ["init", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        assert r0.exit_code == 0, r0.output

        self._dirty(git_work_repo)
        assert self._wt_update(git_work_repo) is UpdateOutcome.REGENERATED

        (git_work_repo / "second_module.py").write_text(
            "def second_addition():\n    return 2\n", encoding="utf-8"
        )
        # Without the release this is DEFERRED — the lock is held by this very
        # process — and nothing after the first save is ever indexed.
        assert self._wt_update(git_work_repo) is UpdateOutcome.REGENERATED
        assert "second_addition" in self._symbol_names(git_work_repo)

    def test_without_the_release_a_repeat_run_defers(self, runner, git_work_repo):
        """Pins the failure mode the release exists for."""
        from repowise.cli.commands.update_cmd.command import UpdateOutcome

        runner.invoke(cli, ["init", str(git_work_repo), "--index-only"], catch_exceptions=False)
        self._dirty(git_work_repo)
        self._wt_update(git_work_repo, release_lock=False)

        assert self._wt_update(git_work_repo) is UpdateOutcome.DEFERRED

    def test_commit_anchored_update_is_unchanged(self, runner, git_work_repo):
        """The default stays commit-to-commit: hooks and webhooks must not
        start indexing whatever half-finished edit happens to be on disk."""
        r0 = runner.invoke(
            cli, ["init", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        assert r0.exit_code == 0, r0.output

        self._dirty(git_work_repo)

        r1 = runner.invoke(
            cli, ["update", str(git_work_repo), "--index-only"], catch_exceptions=False
        )

        assert r1.exit_code == 0, r1.output
        assert "Already up to date" in r1.output
        assert "uncommitted_addition" not in self._symbol_names(git_work_repo)


class TestModuleAttributionRepair:
    """A wrong ``module`` corrects itself on a plain update, quiet repo included.

    ``module`` is persisted, so changing how it is derived only reaches stored
    rows when something rewrites them. The alternative trigger — bumping
    ``HEALTH_ANALYZER_VERSION`` — buys that at the price of a full health
    re-score, and its gate is only consulted once an update reaches the
    incremental path, so a repo with no new commits never picks it up at all.
    """

    def _modules(self, repo):
        import sqlite3
        from contextlib import closing

        db = repo / ".repowise" / "wiki.db"
        with closing(sqlite3.connect(db)) as conn:
            return dict(conn.execute("SELECT file_path, module FROM health_file_metrics"))

    def _corrupt(self, repo):
        """Write the labels the old community-map path produced."""
        import sqlite3
        from contextlib import closing

        db = repo / ".repowise" / "wiki.db"
        with closing(sqlite3.connect(db)) as conn:
            conn.execute("UPDATE health_file_metrics SET module = 'tests/unit (3)'")
            conn.commit()

    def test_a_quiet_update_repairs_stale_module_labels(self, runner, git_work_repo):
        r0 = runner.invoke(
            cli, ["init", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        assert r0.exit_code == 0, r0.output
        indexed = self._modules(git_work_repo)
        assert indexed, "no health rows to test against"

        self._corrupt(git_work_repo)
        assert set(self._modules(git_work_repo).values()) == {"tests/unit (3)"}

        # No new commits and no config change: update takes the early return.
        r1 = runner.invoke(
            cli, ["update", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        assert r1.exit_code == 0, r1.output
        assert "Already up to date" in r1.output

        # Repaired anyway, and back to exactly what the indexer wrote.
        assert self._modules(git_work_repo) == indexed

    def test_dry_run_writes_nothing(self, runner, git_work_repo):
        """``--dry-run`` means nothing written, and the repair is a write.

        It runs before the early return so a quiet repo is still corrected,
        which puts it upstream of every other write in the command — and
        therefore upstream of the guard they all sit behind.
        """
        r0 = runner.invoke(
            cli, ["init", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        assert r0.exit_code == 0, r0.output

        self._corrupt(git_work_repo)
        corrupted = self._modules(git_work_repo)

        r1 = runner.invoke(
            cli,
            ["update", str(git_work_repo), "--index-only", "--dry-run"],
            catch_exceptions=False,
        )
        assert r1.exit_code == 0, r1.output
        assert "Module attribution" not in r1.output
        assert self._modules(git_work_repo) == corrupted

    def test_a_second_update_changes_nothing(self, runner, git_work_repo):
        """Idempotent, and silent when there is nothing to do.

        If the repair and the indexer disagreed about the repo layout, the two
        would alternate and every update would report work.
        """
        r0 = runner.invoke(
            cli, ["init", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        assert r0.exit_code == 0, r0.output

        r1 = runner.invoke(
            cli, ["update", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        assert r1.exit_code == 0, r1.output
        assert "Module attribution" not in r1.output


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

    def test_init_stamps_the_rescore_cadence_so_the_next_update_skips_it(
        self, runner, git_work_repo
    ):
        """A fresh index must not be re-scored by the update right after it.

        ``init`` scores every file. Until it stamped ``last_full_rescore_at``,
        the first update read the missing stamp as "never re-scored" and scored
        every file again — about 30s on a 2k-file repo, on every fresh install.
        Asserted against HEAD's committer timestamp rather than "is present",
        because the gate compares it to exactly that and a wall-clock value
        would drift under ``REPOWISE_GIT_WINDOW_ANCHOR``.
        """
        r0 = runner.invoke(
            cli, ["init", str(git_work_repo), "--index-only"], catch_exceptions=False
        )
        assert r0.exit_code == 0, r0.output

        import subprocess

        head_ts = float(
            subprocess.check_output(
                ["git", "log", "-1", "--format=%ct"], cwd=git_work_repo, text=True
            ).strip()
        )
        assert self._state(git_work_repo)["last_full_rescore_at"] == head_ts

        # And the gate agrees, which is the behaviour the stamp exists for.
        from repowise.cli.commands.update_cmd.persistence import full_rescore_due

        assert full_rescore_due(self._state(git_work_repo), head_ts) is False

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

    # The no-diffs half of the branch — which keeps its early return — is
    # already covered by test_init_stores_fingerprint_and_update_detects_config_change
    # above, which asserts exactly the same three things.

    @pytest.mark.parametrize("mode", ["index-only", "docs"])
    def test_config_change_with_source_diffs_still_indexes_the_commits(
        self, runner, git_work_repo, mode
    ):
        """A config change must never advance the sync pointer past commits it
        did not index.

        The config branch used to re-score health and return early, skipping the
        graph rebuild, git re-index, dead-code and page regeneration — and then
        ``_run_full_health_rescore`` saved ``last_sync_commit=head`` anyway. In
        index-only mode ``base_ref`` *is* ``last_sync_commit``, so the very next
        update saw ``base_ref == head``, said "Already up to date", and the
        commit's files stayed out of the index until a manual ``--full``.

        Parameterized because the two modes lose different amounts: index-only
        loses the commits permanently, while docs mode self-heals on the next
        update via its separate ``last_docs_commit`` pointer — but skips the git
        re-index on the run itself either way, which is what this asserts.
        """
        import json

        # Docs mode regenerates pages, so it needs a provider; ``mock`` is the
        # registered test one and keeps this a CLI test, not a provider test.
        args = ["--index-only"] if mode == "index-only" else ["--docs", "--provider", "mock"]
        runner.invoke(cli, ["init", str(git_work_repo), "--index-only"], catch_exceptions=False)

        # Switch the *periodic* re-score gate off, so a passing run can only be
        # the config-forced re-score. ``init`` now stamps ``last_full_rescore_at``
        # itself, which already suppresses the #728 time gate here; this seeding
        # stays because "far future" is the stronger statement of the two and it
        # is what makes the assertion below about the *config* trigger alone.
        state_file = git_work_repo / ".repowise" / "state.json"
        seeded = json.loads(state_file.read_text(encoding="utf-8"))
        seeded["last_full_rescore_at"] = 9e18  # far future: never "due"
        state_file.write_text(json.dumps(seeded), encoding="utf-8")

        # New source commit AND a config change in the same update window.
        (git_work_repo / "new_module.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        _git(["add", "-A"], git_work_repo)
        _git(["commit", "-m", "add module"], git_work_repo)
        (git_work_repo / ".repowise" / "health-rules.json").write_text(
            '{"disabled_biomarkers": ["ungoverned_hotspot"]}', encoding="utf-8"
        )

        result = runner.invoke(cli, ["update", str(git_work_repo), *args], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert "Config files changed" in result.output
        # The re-score still happens — it just runs off the graph the
        # incremental path already built instead of a second traverse. With the
        # time gate seeded off above, this line can only come from the
        # config-forced re-score, so it pins that the force is wired to the
        # path this mode actually takes (they are different code).
        assert "Health re-score:" in result.output

        # git_metadata is the decisive table: the standalone re-score rebuilds
        # and persists the graph itself, so graph_nodes / health_file_metrics
        # carry the new file either way and prove nothing. Only the git
        # re-index — churn, ownership, co-change — is genuinely skipped by the
        # early return, and it is 0 rows there.
        db = git_work_repo / ".repowise" / "wiki.db"
        assert (
            _db_scalar(db, "SELECT COUNT(*) FROM git_metadata WHERE file_path = 'new_module.py'")
            == 1
        ), "the config branch returned before the git re-index reached the commit's files"

        # And nothing is left pending: a second update has no work to find.
        r2 = runner.invoke(cli, ["update", str(git_work_repo), *args], catch_exceptions=False)
        assert r2.exit_code == 0, r2.output
        assert "Already up to date" in r2.output


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

        # Leave REPOWISE_DB_URL pinned (as git_work_repo sets it) to verify
        # that the delegated update does not leak worktree pages into the base DB.

        # Every file gets a structural page now, so no coverage knob is needed
        # to keep the new file from being tier-gated out.
        r0 = CliRunner().invoke(
            cli,
            ["init", str(git_work_repo), "--provider", "mock", "--yes"],
            catch_exceptions=False,
        )
        assert r0.exit_code == 0, r0.output

        _git(["checkout", "-b", "feature"], git_work_repo)
        (git_work_repo / "new_file.py").write_text("def my_func(): pass\n", encoding="utf-8")
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

            # Since the file has a symbol and we have 1.0 coverage, it must be selected
            assert "new_file.py" in paths, f"Expected 'new_file.py' in {paths}"
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
