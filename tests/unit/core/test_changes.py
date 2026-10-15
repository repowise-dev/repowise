"""Change Records: reconstruction from git, the metadata seam, storage and export."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import git as gitpython
import pytest

from repowise.core import attribution
from repowise.core.changes import (
    PREDICATE_TYPE,
    change_metadata_sources,
    change_record,
    in_toto_statement,
    reconstruct_changes,
)
from repowise.core.changes.reconstruct import change_identity, change_title, without_own_ref
from repowise.core.changes.source import GitChangeMetadata
from repowise.core.changes.store import change_provenance, refresh_changes
from repowise.core.ingestion.git_indexer.commit_rows import build_commit_rows

GERRIT_ID = "I" + "a" * 40


class _Repo:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.clock = 1_700_000_000
        root.mkdir(parents=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Ann")
        self.git("config", "user.email", "ann@example.com")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args: str, author: str | None = None) -> str:
        self.clock += 60
        env = {
            **os.environ,
            "GIT_AUTHOR_DATE": f"{self.clock} +0000",
            "GIT_COMMITTER_DATE": f"{self.clock} +0000",
        }
        if author:
            env["GIT_AUTHOR_NAME"] = author
            env["GIT_AUTHOR_EMAIL"] = f"{author.lower()}@example.com"
        return subprocess.run(
            ["git", *args], cwd=self.root, env=env, check=True, capture_output=True, text=True
        ).stdout.strip()

    def commit(self, path: str, message: str, *, author: str | None = None) -> str:
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(f"{path} {self.clock}\n", encoding="utf-8")
        self.git("add", path)
        self.git("commit", "-q", "-m", message, author=author)
        return self.git("rev-parse", "HEAD")

    def merge(self, branch: str, message: str) -> str:
        self.git("merge", "-q", "--no-ff", branch, "-m", message)
        return self.git("rev-parse", "HEAD")

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")


@pytest.fixture
def history(tmp_path: Path) -> tuple[_Repo, dict[str, str]]:
    """One main line holding every merge style the reconstruction reads."""
    r = _Repo(tmp_path / "repo")
    shas: dict[str, str] = {}
    shas["root"] = r.commit("README.md", "Initial commit")
    shas["squash"] = r.commit("src/a.py", "feat: add a (#12)")

    r.git("checkout", "-q", "-b", "feature")
    shas["f1"] = r.commit("src/b.py", "start b", author="Bob")
    shas["f2"] = r.commit("src/b_test.py", "test b", author="Carol")
    r.git("checkout", "-q", "main")
    shas["gh_merge"] = r.merge("feature", "Merge pull request #13 from bob/feature\n\nAdd the b feature")

    shas["lone"] = r.commit("src/c.py", "fix: c crashes on empty input")
    shas["gerrit"] = r.commit("src/d.py", f"Add d\n\nLonger text.\n\nChange-Id: {GERRIT_ID}")

    r.git("checkout", "-q", "-b", "gl")
    r.commit("src/e.py", "e work")
    r.git("checkout", "-q", "main")
    shas["gl_merge"] = r.merge(
        "gl",
        "Merge branch 'gl' into 'main'\n\nGitLab title\n\n"
        "See merge request grp/proj!7\n\nApproved-by: Dee <dee@example.com>",
    )

    r.git("checkout", "-q", "-b", "az")
    r.commit("src/f.py", "f work")
    r.git("checkout", "-q", "main")
    shas["az_merge"] = r.merge("az", "Merged PR 21: Azure title")

    # Work done elsewhere and brought back by an unnamed merge: the squash
    # inside is its own change, the stray commit stays with the merge.
    r.git("checkout", "-q", "-b", "upstream")
    shas["up_squash"] = r.commit("src/g.py", "docs: g (#30)")
    shas["up_stray"] = r.commit("src/h.py", "h tweak")
    r.git("checkout", "-q", "main")
    shas["anon_merge"] = r.merge("upstream", "Merge branch 'main' of github.com:x/y")
    return r, shas


def _changes(r: _Repo, forge: str = "github", **kw):
    repo = gitpython.Repo(r.root)
    try:
        return reconstruct_changes(repo, forge, limit=500, **kw)
    finally:
        repo.close()


def _files(change) -> set[str]:
    return {p for p, _, _ in change.files}


def test_every_merge_style_reads_back_as_one_change(history):
    r, shas = history
    by_id = {c.change_id: c for c in _changes(r)}

    squash = by_id["pr-12"]
    assert (squash.shape, squash.forge_ref, squash.commit_shas) == ("squash", "#12", [shas["squash"]])
    assert _files(squash) == {"src/a.py"} and squash.category == "feature"
    assert not squash.title.endswith("(#12)")  # surfaces print the ref beside it

    merge = by_id["pr-13"]
    assert merge.shape == "merge" and merge.head_sha == shas["gh_merge"]
    assert merge.title == "Add the b feature"
    assert merge.commit_shas == [shas["f2"], shas["f1"]]
    assert _files(merge) == {"src/b.py", "src/b_test.py"}
    assert merge.author_name == "Bob"  # who opened the branch, not who merged it

    lone = by_id[f"commit-{shas['lone']}"]
    assert (lone.shape, lone.category, lone.forge_ref) == ("commit", "fix", None)

    gerrit = by_id[f"change-{GERRIT_ID}"]
    assert gerrit.identity_kind == "change_id" and _files(gerrit) == {"src/d.py"}

    gitlab = by_id["pr-7"]
    assert gitlab.title == "GitLab title" and _files(gitlab) == {"src/e.py"}

    azure = by_id["pr-21"]
    assert azure.title == "Azure title" and _files(azure) == {"src/f.py"}

    nested = by_id["pr-30"]
    assert nested.head_sha == shas["up_squash"] and _files(nested) == {"src/g.py"}
    anon = by_id[f"merge-{shas['anon_merge']}"]
    assert anon.commit_shas == [shas["up_stray"]] and _files(anon) == {"src/h.py"}

    assert f"commit-{shas['root']}" in by_id
    assert len(by_id) == 9


def test_forge_ref_follows_the_repo_forge(history):
    r, _ = history
    by_id = {c.change_id: c for c in _changes(r, forge="gitlab")}
    assert by_id["pr-7"].forge_ref == "!7"
    assert by_id["pr-7"].forge == "gitlab"


def test_merge_with_only_named_changes_is_dropped(tmp_path):
    r = _Repo(tmp_path / "repo")
    r.commit("a.py", "a")
    r.git("checkout", "-q", "-b", "up")
    up = r.commit("b.py", "b (#4)")
    r.git("checkout", "-q", "main")
    r.commit("c.py", "c")
    r.merge("up", "Merge branch 'main' of github.com:x/y")
    changes = _changes(r)
    assert [c.change_id for c in changes if c.shape == "merge"] == []
    assert {c.change_id: c.head_sha for c in changes}["pr-4"] == up


def test_identity_order_pr_then_change_id_then_merge_then_commit():
    body = f"Text\n\nChange-Id: {GERRIT_ID}"
    assert change_identity("Add x (#5)", body, "s", False, "github")[:2] == ("pr", "5")
    assert change_identity("Add x", body, "s", False, "github")[:2] == ("change_id", GERRIT_ID)
    assert change_identity("Merge branch 'x'", "", "m", True, "github")[:2] == ("merge", "m")
    assert change_identity("Add x", "", "c", False, "github")[:2] == ("commit", "c")


def test_title_skips_boilerplate_and_trailers():
    assert change_title("Merge pull request #1 from a/b", "Real title\n\nbody", True) == "Real title"
    assert change_title("Merge branch 'x' into 'main'", "See merge request g/p!3", True) == (
        "Merge branch 'x' into 'main'"
    )
    assert change_title("Merged PR 9: Fix it", "", True) == "Fix it"
    assert change_title("feat: x (#2)", "body", False) == "feat: x (#2)"
    # A merge committed without editing keeps git's conflict list in its body.
    subject = "Merge remote-tracking branch 'origin/x' into main"
    assert change_title(subject, "# Conflicts:\n#\ta.py\n", True) == subject
    assert change_title(subject, "Conflicts:\n\ta.py\n", True) == subject
    # A body line that only starts with "#" is still a title.
    assert change_title(subject, "#123 Fix foo\n", True) == "#123 Fix foo"


def test_title_drops_only_its_own_ref_suffix():
    assert without_own_ref("feat(x): add y (#3792)", "3792") == "feat(x): add y"
    assert without_own_ref("Fix the parser (!41)", "41") == "Fix the parser"
    # Another change's number, or a title that is only the ref, stays.
    assert without_own_ref("revert: z (#12)", "13") == "revert: z (#12)"
    assert without_own_ref("(#5)", "5") == "(#5)"
    assert without_own_ref("feat: (#5) in the middle", "5") == "feat: (#5) in the middle"


def test_repeated_identity_gets_a_distinct_id_and_keeps_it(tmp_path):
    r = _Repo(tmp_path / "repo")
    first = r.commit("a.py", "fix: a (#8)")
    second = r.commit("b.py", "fix: a again (#8)")
    ids = {c.head_sha: c.change_id for c in _changes(r)}
    assert ids[first] == "pr-8"
    assert ids[second] == f"pr-8-{second[:12]}"
    # An update that re-reads the second keeps both ids where they were.
    again = _changes(r, since_sha=first, taken={"pr-8": first})
    assert [c.change_id for c in again] == [f"pr-8-{second[:12]}"]


def test_since_reads_only_newer_and_rebuilds_off_history(history):
    r, shas = history
    newer = r.commit("z.py", "chore: z")
    assert [c.head_sha for c in _changes(r, since_sha=shas["anon_merge"])] == [newer]
    assert _changes(r, since_sha=newer) == []
    r.git("checkout", "-q", "-b", "side", shas["root"])
    assert _changes(r, since_sha=newer) is None


def test_commit_rows_store_the_category():
    rows = build_commit_rows(
        [
            {"sha": "a", "author_name": "A", "author_email": "a@x", "ts": 1, "subject": "fix: x", "changes": []},
            {"sha": "b", "author_name": "A", "author_email": "a@x", "ts": 2, "subject": "docs: y", "changes": []},
        ]
    )
    assert {r["sha"]: r["category"] for r in rows} == {"a": "fix", "b": "docs"}


def test_provenance_is_three_state_with_counts():
    labels = {
        "a": ("agent", "claude"),
        "h": ("human_attested", None),
        "u": ("unknown", None),
        "old": (None, "claude"),  # written before the label: derived from agent_name
    }
    assert change_provenance(["a", "u"], labels) == ("agent", 1, 1)
    assert change_provenance(["h"], labels) == ("human_attested", 0, 1)
    assert change_provenance(["h", "u"], labels) == ("unknown", 0, 1)
    assert change_provenance(["h", "missing"], labels) == ("unknown", 0, 1)
    assert change_provenance(["old"], labels) == ("agent", 1, 1)
    assert change_provenance([], labels) == ("unknown", 0, 0)


def test_metadata_seam_defaults_to_git_and_reads_trailers(history):
    r, _ = history
    assert isinstance(change_metadata_sources.get(), GitChangeMetadata)
    assert change_metadata_sources.default == "git"
    changes = _changes(r)
    meta = GitChangeMetadata().metadata(r.root, changes)
    assert meta["pr-7"].approvals == 1
    assert meta["pr-7"].reviewers is None and meta["pr-7"].labels is None


def test_metadata_seam_takes_a_plugin(monkeypatch):
    class Forge:
        def metadata(self, repo_path, changes):
            return {}

    change_metadata_sources.register("test-forge", Forge())
    monkeypatch.setenv("REPOWISE_SEAM_CHANGE_METADATA", "test-forge")
    assert isinstance(change_metadata_sources.get(), Forge)
    monkeypatch.delenv("REPOWISE_SEAM_CHANGE_METADATA")
    assert isinstance(change_metadata_sources.get(), GitChangeMetadata)


class _Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _row(**over) -> _Row:
    base = dict(
        change_id="pr-13",
        identity_kind="pr",
        identity_value="13",
        shape="merge",
        forge="github",
        forge_ref="#13",
        title="Add b",
        merged_at=datetime(2026, 1, 2, tzinfo=UTC),
        head_sha="m" * 40,
        author_name="Bob",
        author_email="bob@example.com",
        commit_shas_json=json.dumps(["b" * 40, "a" * 40]),
        files_json=json.dumps([{"path": "src/b.py", "added": 3, "deleted": 1}]),
        files_changed=1,
        lines_added=3,
        lines_deleted=1,
        category="feature",
        provenance="agent",
        provenance_agent=1,
        provenance_known=1,
        metadata_source="git",
        reviewers_json=None,
        approvals=None,
        labels_json=None,
        linked_issues_json=json.dumps(["#4"]),
    )
    base.update(over)
    return _Row(**base)


def test_record_is_versioned_and_honours_attribution_off():
    record = change_record(_row())
    assert record["schema_version"] == 1
    assert record["identity"] == {"kind": "pr", "value": "13"}
    assert record["provenance"] == {
        "label": "agent",
        "agent_commits": 1,
        "human_attested_commits": 0,
        "unknown_commits": 1,
        "coverage": 0.5,
    }
    assert record["metadata"] == {
        "source": "git",
        "reviewers": None,
        "approvals": None,
        "labels": None,
        "linked_issues": ["#4"],
    }
    assert change_record(_row(), attribution=False)["provenance"] is None
    assert change_record(_row(provenance=None))["provenance"] is None


def test_in_toto_statement_shape():
    record = change_record(_row())
    statement = in_toto_statement(record)
    assert statement["_type"] == "https://in-toto.io/Statement/v1"
    assert statement["predicateType"] == PREDICATE_TYPE
    assert [s["digest"] for s in statement["subject"]] == [
        {"gitCommit": "m" * 40},
        {"gitCommit": "b" * 40},
        {"gitCommit": "a" * 40},
    ]
    assert statement["predicate"] == record


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


@pytest.fixture
def _private_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    user_file = tmp_path / "home" / ".repowise" / "config.yaml"
    monkeypatch.setattr(attribution, "user_config_path", lambda: user_file)


async def _db(tmp_path: Path, repo_root: Path):
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence.database import get_session

    engine = create_engine(f"sqlite+aiosqlite:///{(tmp_path / 'wiki.db').as_posix()}")
    await init_db(engine)
    factory = create_session_factory(engine)
    async with get_session(factory) as session:
        repo = await upsert_repository(session, name="repo", local_path=str(repo_root))
        repo_id = repo.id
    return engine, factory, repo_id


@pytest.mark.asyncio
@pytest.mark.usefixtures("_private_user_config")
async def test_refresh_rebuilds_then_extends(tmp_path, history):
    from repowise.core.persistence.crud import (
        ChangeFilter,
        get_change,
        list_changes,
        upsert_git_commits_bulk,
    )
    from repowise.core.persistence.database import get_session

    r, shas = history
    engine, factory, repo_id = await _db(tmp_path, r.root)
    try:
        async with get_session(factory) as session:
            await upsert_git_commits_bulk(
                session,
                repo_id,
                [
                    {"sha": shas["f1"], "subject": "start b", "attribution": "agent", "agent_name": "claude"},
                    {"sha": shas["f2"], "subject": "test b", "attribution": "unknown"},
                ],
            )
            assert await refresh_changes(session, repo_id, r.root, rebuild=True) == 9
        async with get_session(factory) as session:
            row = await get_change(session, repo_id, "pr-13")
            assert (row.provenance, row.provenance_agent, row.provenance_known) == ("agent", 1, 1)
            assert (await get_change(session, repo_id, shas["f1"][:10])).change_id == "pr-13"
            assert (await get_change(session, repo_id, "pr-7")).approvals == 1

        newer = r.commit("z.py", "chore: z (#40)")
        async with get_session(factory) as session:
            assert await refresh_changes(session, repo_id, r.root, rebuild=False) == 1
            rows, total = await list_changes(session, repo_id, limit=3)
            assert total == 10 and rows[0].head_sha == newer
            _, fixes = await list_changes(session, repo_id, ChangeFilter(category="fix"))
            assert fixes == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.usefixtures("_private_user_config")
async def test_attribution_off_stores_no_provenance(tmp_path, history):
    from repowise.core.persistence.crud import get_change
    from repowise.core.persistence.database import get_session

    r, _ = history
    (r.root / ".repowise").mkdir()
    (r.root / ".repowise" / "config.yaml").write_text("attribution:\n  enabled: false\n")
    engine, factory, repo_id = await _db(tmp_path, r.root)
    try:
        async with get_session(factory) as session:
            await refresh_changes(session, repo_id, r.root, rebuild=True)
        async with get_session(factory) as session:
            row = await get_change(session, repo_id, "pr-13")
            assert (row.provenance, row.provenance_agent, row.provenance_known) == (None, None, None)
    finally:
        await engine.dispose()
