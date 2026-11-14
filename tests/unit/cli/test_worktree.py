"""Unit tests for git-worktree detection and seed preconditions."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repowise.cli.worktree import base_is_seedable, detect_worktree_base


def _git(args: list[str], cwd: Path) -> None:
    subprocess.check_call(
        ["git", *args],
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@pytest.fixture()
def base_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "base"
    repo.mkdir()
    _git(["init"], repo)
    _git(["config", "user.email", "t@t.t"], repo)
    _git(["config", "user.name", "t"], repo)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "init"], repo)
    return repo


def test_plain_repo_is_not_a_worktree(base_repo: Path) -> None:
    assert detect_worktree_base(base_repo) is None


def test_non_git_dir_is_not_a_worktree(tmp_path: Path) -> None:
    d = tmp_path / "plain"
    d.mkdir()
    assert detect_worktree_base(d) is None


def test_linked_worktree_resolves_base(base_repo: Path, tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    _git(["worktree", "add", "-b", "feature", str(wt)], base_repo)
    detected = detect_worktree_base(wt)
    assert detected is not None
    assert detected.resolve() == base_repo.resolve()


def test_submodule_git_file_is_not_a_worktree(base_repo: Path, tmp_path: Path) -> None:
    # Fake a submodule layout: .git file whose gitdir points at the parent's
    # modules dir, which does not end in a bare ".git" component.
    sub = tmp_path / "sub"
    sub.mkdir()
    modules = base_repo / ".git" / "modules" / "sub"
    modules.mkdir(parents=True)
    (sub / ".git").write_text(f"gitdir: {modules}\n", encoding="utf-8")
    assert detect_worktree_base(sub) is None


def test_base_is_seedable_requires_state_and_db(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    (repo / ".repowise").mkdir(parents=True)
    assert not base_is_seedable(repo)
    (repo / ".repowise" / "state.json").write_text("{}", encoding="utf-8")
    assert not base_is_seedable(repo)
    (repo / ".repowise" / "wiki.db").write_text("", encoding="utf-8")
    assert base_is_seedable(repo)


def test_adopting_identity_rekeys_the_repo_level_pages(tmp_path: Path) -> None:
    """The overview id carries the repo name; the seeded copy must be findable and regenerable."""
    import sqlite3
    from contextlib import closing

    from repowise.cli.worktree import _adopt_repository_identity

    src, dest = tmp_path / "base", tmp_path / "feature-tree"
    db = tmp_path / "wiki.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.executescript(
            "CREATE TABLE repositories (id TEXT, name TEXT, local_path TEXT);"
            "CREATE TABLE wiki_pages (id TEXT, repository_id TEXT, page_type TEXT,"
            " target_path TEXT, parent_page_id TEXT);"
            "CREATE TABLE wiki_page_versions (id TEXT, page_id TEXT);"
            "CREATE VIRTUAL TABLE page_fts USING fts5(page_id UNINDEXED, title, target_path);"
        )
        conn.execute("INSERT INTO repositories VALUES ('r1', 'base', ?)", (str(src),))
        conn.execute(
            "INSERT INTO wiki_pages VALUES ('repo_overview:base', 'r1', 'repo_overview', 'base', NULL)"
        )
        conn.execute(
            "INSERT INTO wiki_pages VALUES ('module_page:src', 'r1', 'module_page', 'src',"
            " 'repo_overview:base')"
        )
        conn.execute("INSERT INTO wiki_page_versions VALUES ('v1', 'repo_overview:base')")
        conn.execute("INSERT INTO page_fts VALUES ('repo_overview:base', 'Overview', 'base')")
        conn.commit()

    _adopt_repository_identity(tmp_path, src_repo=src, dest_repo=dest)

    with closing(sqlite3.connect(db)) as conn:
        pages = conn.execute("SELECT id, target_path, parent_page_id FROM wiki_pages").fetchall()
        versions = conn.execute("SELECT page_id FROM wiki_page_versions").fetchall()
        fts = conn.execute("SELECT page_id, target_path FROM page_fts").fetchall()
    assert ("repo_overview:feature-tree", "feature-tree", None) in pages
    assert ("module_page:src", "src", "repo_overview:feature-tree") in pages
    assert versions == [("repo_overview:feature-tree",)]
    assert fts == [("repo_overview:feature-tree", "feature-tree")]


def test_as_commit_id_accepts_only_hex_commit_ids() -> None:
    from repowise.cli.helpers import as_commit_id

    assert as_commit_id("a" * 40) == "a" * 40
    assert as_commit_id("DeadBee") == "DeadBee"
    for bad in ("--output=x", "-abcdef0", "abc123", "a" * 41, "HEAD", "", None, 7):
        assert as_commit_id(bad) is None


def _base_moved_past_worktree(base_repo: Path, tmp_path: Path) -> tuple[Path, str]:
    """A worktree forked at the first commit, and the base synced one commit later."""
    import json
    import sqlite3

    wt = tmp_path / "wt"
    _git(["worktree", "add", "-b", "behind", str(wt)], base_repo)
    (base_repo / "b.py").write_text("y = 2\n", encoding="utf-8")
    _git(["add", "-A"], base_repo)
    _git(["commit", "-m", "base moves on"], base_repo)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=base_repo, text=True).strip()
    dot = base_repo / ".repowise"
    dot.mkdir()
    (dot / "state.json").write_text(json.dumps({"last_sync_commit": head}), encoding="utf-8")
    conn = sqlite3.connect(dot / "wiki.db")
    conn.execute("CREATE TABLE repositories (id TEXT, name TEXT, local_path TEXT)")
    conn.close()
    (dot / ".update.lock").write_text("{}", encoding="utf-8")
    (dot / ".update.queued").write_text("{}", encoding="utf-8")
    return wt, head


def test_seed_commit_refusal(base_repo: Path, tmp_path: Path) -> None:
    from repowise.cli.worktree import _seed_commit_refusal

    wt, head = _base_moved_past_worktree(base_repo, tmp_path)
    assert _seed_commit_refusal(wt, head, require_ancestor=True) == (
        "is not an ancestor of worktree HEAD"
    )
    assert _seed_commit_refusal(wt, head, require_ancestor=False) is None
    assert _seed_commit_refusal(wt, "f" * 40, require_ancestor=False) == (
        "is not in this repository"
    )
    assert _seed_commit_refusal(wt, "--bad", require_ancestor=False) == "is not a commit id"


def test_seed_accepts_a_base_past_the_fork_point(base_repo: Path, tmp_path: Path) -> None:
    from repowise.cli.worktree import seed_index_from_base

    wt, _ = _base_moved_past_worktree(base_repo, tmp_path)
    assert not seed_index_from_base(root=wt, repo_paths=[wt], seed_base=base_repo)
    assert seed_index_from_base(
        root=wt, repo_paths=[wt], seed_base=base_repo, require_ancestor=False
    )
    seeded = wt / ".repowise"
    assert (seeded / "state.json").exists()
    assert not (seeded / ".update.lock").exists()
    assert not (seeded / ".update.queued").exists()


def test_update_seeds_without_the_ancestor_check(tmp_path: Path, monkeypatch) -> None:
    import repowise.cli.worktree as worktree
    from repowise.cli.commands.update_cmd.command import _prepare_repo

    calls: list[dict] = []
    monkeypatch.setattr(worktree, "detect_worktree_base", lambda _p: tmp_path / "base")
    monkeypatch.setattr(worktree, "base_is_seedable", lambda _p: True)
    monkeypatch.setattr(worktree, "seed_index_from_base", lambda **kw: calls.append(kw))
    repo = tmp_path / "wt"
    repo.mkdir()

    _prepare_repo(repo)

    assert calls and calls[0]["require_ancestor"] is False


def test_refused_seed_does_not_promise_a_full_init(
    base_repo: Path, tmp_path: Path, monkeypatch
) -> None:
    """update has no full init to fall back to, so the refusal must not claim one (#4875)."""
    import io
    import json

    from rich.console import Console

    import repowise.cli.worktree as worktree

    wt, _ = _base_moved_past_worktree(base_repo, tmp_path)
    (base_repo / ".repowise" / "state.json").write_text(json.dumps({}), encoding="utf-8")
    out = io.StringIO()
    monkeypatch.setattr(worktree, "console", Console(file=out, width=400))

    assert not worktree.seed_index_from_base(
        root=wt, repo_paths=[wt], seed_base=base_repo, require_ancestor=False
    )
    assert "has no last_sync_commit" in out.getvalue()
    assert "Falling back to full init" not in out.getvalue()


def _seeded_init(base_repo: Path, tmp_path: Path, monkeypatch, **flags) -> dict:
    """Seed a worktree from a base whose pages a model wrote; return what update read."""
    import json
    import sqlite3
    from types import SimpleNamespace

    import repowise.cli.commands.update_cmd.command as update_command
    from repowise.cli.commands.init_cmd._options import InitOptions
    from repowise.cli.commands.init_cmd._seed import delegate_to_seeded_update
    from repowise.cli.helpers import load_config, load_state

    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=base_repo, text=True).strip()
    dot = base_repo / ".repowise"
    dot.mkdir()
    state = {"last_sync_commit": head, "docs_mode": "llm", "docs_enabled": True}
    (dot / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (dot / "config.yaml").write_text("provider: openai\n", encoding="utf-8")
    sqlite3.connect(dot / "wiki.db").execute(
        "CREATE TABLE repositories (id TEXT, name TEXT, local_path TEXT)"
    ).connection.close()
    wt = tmp_path / "wt"
    _git(["worktree", "add", "-b", "wt", str(wt)], base_repo)

    seen: dict = {}

    def fake_update(**kwargs) -> None:
        seen.update(state=load_state(wt), config=load_config(wt), kwargs=kwargs)

    monkeypatch.setattr(update_command, "run_update", fake_update)
    monkeypatch.delenv("REPOWISE_SKIP_EDITOR_SETUP", raising=False)
    fields = {name: None for name in InitOptions.__dataclass_fields__}
    fields.update(
        exclude=(), coverage_report=(), run_mode="standard", concurrency=10,
        onboarding=True, editor_setup=True, save_key=True, yes=True, index_only=False,
        follow_renames=False, skip_tests=False, skip_infra=False, no_seed=False,
        force=False, dry_run=False, no_workspace=False, verbose=False, progress="rich",
        no_cost_tracking=False,
    )
    fields.update(flags)
    scan = SimpleNamespace(repos=[SimpleNamespace(path=wt)], root=wt)
    assert delegate_to_seeded_update(InitOptions(**fields), wt, scan)
    return seen


def test_seeded_no_prose_init_leaves_a_no_prose_index(
    base_repo: Path, tmp_path: Path, monkeypatch
) -> None:
    """Otherwise the next update sends every file changed since the seed to the model."""
    from repowise.cli.commands.update_cmd.mode import _resolve_index_only_mode
    from repowise.core.docs_mode import resolve_docs_mode

    seen = _seeded_init(base_repo, tmp_path, monkeypatch, index_only=True)

    assert resolve_docs_mode(seen["state"]) == "deterministic"
    assert _resolve_index_only_mode(index_only=False, docs_flag=None, state=seen["state"])
    assert (base_repo / ".repowise" / "state.json").read_text("utf-8").count('"llm"') == 1


@pytest.mark.parametrize(
    "flags", [{}, {"index_only": True, "run_mode": "fast"}], ids=["prose", "fast"]
)
def test_seeded_init_keeps_the_base_docs_mode_otherwise(
    base_repo: Path, tmp_path: Path, monkeypatch, flags: dict
) -> None:
    from repowise.core.docs_mode import resolve_docs_mode

    seen = _seeded_init(base_repo, tmp_path, monkeypatch, **flags)

    assert resolve_docs_mode(seen["state"]) == "llm"


def test_seeded_init_records_no_editor_setup_before_update(
    base_repo: Path, tmp_path: Path, monkeypatch
) -> None:
    """update refreshes .claude/CLAUDE.md unless config records the opt-out."""
    seen = _seeded_init(base_repo, tmp_path, monkeypatch, editor_setup=False)

    assert seen["config"].get("editor_setup") is False
    assert not (tmp_path / "wt" / ".claude").exists()
