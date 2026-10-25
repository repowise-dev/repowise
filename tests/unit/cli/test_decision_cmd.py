"""CLI coverage for ``repowise decision`` subcommands."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from repowise.cli.main import cli
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import DecisionRecord, Repository

_REPO_ID = "decision-cli-repo"


def _seed_wiki_db(repo_root: Path, decisions: list[dict]) -> None:
    """Create ``.repowise/wiki.db`` with a repository row and decision records."""

    async def _build() -> None:
        db_path = repo_root / ".repowise" / "wiki.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
        await init_db(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            session.add(Repository(id=_REPO_ID, name=repo_root.name, local_path=str(repo_root)))
            for spec in decisions:
                session.add(
                    DecisionRecord(
                        id=spec["id"],
                        repository_id=_REPO_ID,
                        title=spec["title"],
                        decision=spec.get("decision", "use X"),
                        rationale=spec.get("rationale", "because Y"),
                        context=spec.get("context", "forced by Z"),
                        status=spec.get("status", "active"),
                        source=spec.get("source", "cli"),
                        confidence=spec.get("confidence", 0.9),
                        staleness_score=spec.get("staleness", 0.0),
                        affected_files_json=json.dumps(
                            spec.get("affected_files", ["src/app.py"])
                        ),
                        evidence_file=spec["id"],
                    )
                )
            await session.commit()
        await engine.dispose()

    asyncio.run(_build())


def _create_empty_index(repo_root: Path) -> None:
    """Create ``.repowise/wiki.db`` with the schema and no rows: an indexed repo.

    ``decision`` commands refuse a checkout with no index, so a repo that is
    only an empty ``.repowise/`` directory no longer stands in for one.
    """

    async def _build() -> None:
        db_path = repo_root / ".repowise" / "wiki.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
        await init_db(engine)
        await engine.dispose()

    asyncio.run(_build())


@pytest.fixture
def indexed_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _create_empty_index(repo)
    return repo


def test_decision_help_lists_subcommands() -> None:
    result = CliRunner().invoke(cli, ["decision", "--help"])

    assert result.exit_code == 0, result.output
    for name in ("add", "list", "show", "confirm", "dismiss", "deprecate", "health"):
        assert name in result.output



def _stored_column(repo_root: Path, decision_id: str, column: str) -> str:
    """One column of a record as written, for the ones no command prints."""
    import sqlite3

    conn = sqlite3.connect(repo_root / ".repowise" / "wiki.db")
    try:
        row = conn.execute(
            f"SELECT {column} FROM decision_records WHERE id LIKE ?",
            (decision_id + "%",),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, decision_id
    return row[0]


def _stored_kind(repo_root: Path, decision_id: str) -> str:
    return _stored_column(repo_root, decision_id, "kind")


def _stored_files(repo_root: Path, decision_id: str) -> str:
    return _stored_column(repo_root, decision_id, "affected_files_json")


def test_decision_add_records_without_prompting(indexed_repo: Path) -> None:
    """With --title and --decision, nothing is asked and the id comes back.

    ``add`` was eight blocking prompts and no flags, so with no stdin it died
    on the first one — the whole command was unreachable to anything scripted.
    """
    result = CliRunner().invoke(
        cli,
        [
            "decision", "add",
            "--title", "Escape LIKE patterns",
            "--decision", "Escape % and _ before interpolating",
            "--rationale", "an unescaped pattern scans the table",
            "--alternative", "match in Python",
            "--consequence", "one more helper on the query path",
            "--affects", "src/db/models.py",
            "--tag", "database",
            "--format", "json",
            str(indexed_repo),
        ],
        input="",
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["decision"]["title"] == "Escape LIKE patterns"
    assert len(payload["decision"]["id"]) > 8, "the full id, not the table's prefix"

    shown = CliRunner().invoke(
        cli, ["decision", "show", payload["decision"]["id"], str(indexed_repo), "--format", "json"]
    )
    assert shown.exit_code == 0, shown.output
    record = json.loads(shown.output)["decision"]
    assert record["rationale"] == "an unescaped pattern scans the table"
    assert record["alternatives"] == ["match in Python"]
    assert record["consequences"] == ["one more helper on the query path"]
    assert record["affected_files"] == ["src/db/models.py"]
    assert record["tags"] == ["database"]


def test_decision_commands_leave_the_indexed_commit_alone(tmp_path: Path) -> None:
    """Recording or confirming a decision is not a sync, so the row's commit stays.

    Moving it to HEAD left the database and ``state.json`` naming two different
    commits, which made test selection distrust the index and run every test.
    """
    import sqlite3

    repo = tmp_path / "repo"
    git = repo / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git / "refs" / "heads" / "main").write_text("b" * 40 + "\n", encoding="utf-8")
    indexed = "a" * 40
    _seed_wiki_db(repo, [{"id": "cccccccc", "title": "Old", "status": "proposed"}])
    conn = sqlite3.connect(repo / ".repowise" / "wiki.db")
    with conn:
        conn.execute("UPDATE repositories SET head_commit = ?", (indexed,))
    conn.close()

    added = CliRunner().invoke(
        cli,
        ["decision", "add", "--title", "T", "--decision", "D", str(repo)],
        input="",
    )
    assert added.exit_code == 0, added.output
    confirmed = CliRunner().invoke(cli, ["decision", "confirm", "cccccccc", str(repo)])
    assert confirmed.exit_code == 0, confirmed.output
    listed = CliRunner().invoke(cli, ["decision", "list", str(repo)])
    assert listed.exit_code == 0, listed.output

    conn = sqlite3.connect(repo / ".repowise" / "wiki.db")
    try:
        rows = conn.execute("SELECT head_commit FROM repositories").fetchall()
    finally:
        conn.close()
    assert rows == [(indexed,)]


def test_a_flag_driven_decision_lands_proposed(indexed_repo: Path) -> None:
    """A caller that inferred a decision has not reviewed it, and the store says so.

    The prompts still record ``active``: a person answering eight questions is
    the reviewed case. Both paths writing one status is what makes the
    difference invisible.
    """
    result = CliRunner().invoke(
        cli,
        [
            "decision", "add",
            "--title", "Prefer ruff check",
            "--decision", "Run ruff check, never ruff format",
            "--format", "json",
            str(indexed_repo),
        ],
        input="",
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["decision"]["status"] == "proposed"


def test_decision_add_prompts_record_active(indexed_repo: Path) -> None:
    """Answering the prompts is an acceptance, and a scope is part of it."""
    answers = "\nInteractive title\ncontext\nthe decision\nwhy\n\n\nsrc/app.py\n\n"
    result = CliRunner().invoke(cli, ["decision", "add", str(indexed_repo)], input=answers)

    assert result.exit_code == 0, result.output
    listed = CliRunner().invoke(cli, ["decision", "list", str(indexed_repo), "--format", "json"])
    records = json.loads(listed.output)["decisions"]
    assert [d["status"] for d in records if d["title"] == "Interactive title"] == ["active"]


def test_decision_add_asks_again_for_a_blank_decision(indexed_repo: Path) -> None:
    """Acceptance needs the text, so a blank answer is asked again, not stored."""
    answers = "\nBlank first\ncontext\n   \nthe decision\nwhy\n\n\nsrc/app.py\n\n"
    result = CliRunner().invoke(cli, ["decision", "add", str(indexed_repo)], input=answers)

    assert result.exit_code == 0, result.output
    assert "state what was chosen" in result.output
    listed = CliRunner().invoke(cli, ["decision", "list", str(indexed_repo), "--format", "json"])
    records = json.loads(listed.output)["decisions"]
    assert [d["status"] for d in records if d["title"] == "Blank first"] == ["active"]


def test_decision_add_without_a_scope_stays_a_candidate(indexed_repo: Path) -> None:
    """A decision that names no files cannot be checked, so it cannot govern.

    It is kept rather than refused: eight answered questions are worth more
    than the round trip, and ``confirm --scope`` finishes the job.
    """
    answers = "\nUnscoped title\ncontext\nthe decision\nwhy\n\n\n\n\n"
    result = CliRunner().invoke(cli, ["decision", "add", str(indexed_repo)], input=answers)

    assert result.exit_code == 0, result.output
    assert "Stored as a candidate" in result.output
    listed = CliRunner().invoke(cli, ["decision", "list", str(indexed_repo), "--format", "json"])
    records = json.loads(listed.output)["decisions"]
    assert [d["status"] for d in records if d["title"] == "Unscoped title"] == ["proposed"]


def test_an_agreement_is_accepted_although_it_names_no_files(indexed_repo: Path) -> None:
    """The asymmetry the split exists for. Its other half is the test above.

    An agreement names no file because it is not about one, and its acceptance
    row records the repository as its scope, so the contract is satisfied
    honestly rather than exempted. Refusing it for the files it is defined not
    to have is what made a working agreement structurally unacceptable before
    the split. An architectural record with no files is still refused:
    ``test_decision_add_without_a_scope_stays_a_candidate`` is that half, and
    the two only mean anything read together.
    """
    answers = "agreement\nNever use em dashes\ncontext\nno em dashes anywhere\nwhy\n\n\n\n"
    result = CliRunner().invoke(cli, ["decision", "add", str(indexed_repo)], input=answers)

    assert result.exit_code == 0, result.output
    assert "Stored as a candidate" not in result.output
    listed = CliRunner().invoke(cli, ["decision", "list", str(indexed_repo), "--format", "json"])
    records = json.loads(listed.output)["decisions"]
    stored = [d for d in records if d["title"] == "Never use em dashes"]
    assert [d["status"] for d in stored] == ["active"]
    # Accepted on the noun, not because it quietly acquired a scope.
    assert _stored_files(indexed_repo, stored[0]["id"]) == "[]"


def test_the_kind_flag_records_the_noun(indexed_repo: Path) -> None:
    """--kind reaches the column, so a person can type an agreement at all.

    Read off the record rather than the command's own output: what matters is
    what the store holds, because the column is what the SessionStart injector
    reads.
    """
    result = CliRunner().invoke(
        cli,
        [
            "decision", "add",
            "--title", "Commit as the user",
            "--decision", "every commit is authored by the user",
            "--kind", "agreement",
            "--format", "json",
            str(indexed_repo),
        ],
        input="",
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)["decision"]
    assert payload["kind"] == "agreement"
    assert _stored_kind(indexed_repo, payload["id"]) == "agreement"


def test_a_decision_added_without_the_flag_is_architectural(indexed_repo: Path) -> None:
    """The default is the checkable noun, so nothing silently stops being checked."""
    result = CliRunner().invoke(
        cli,
        [
            "decision", "add",
            "--title", "Prefer ruff check here",
            "--decision", "Run ruff check, never ruff format",
            "--format", "json",
            str(indexed_repo),
        ],
        input="",
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)["decision"]
    assert _stored_kind(indexed_repo, payload["id"]) == "architectural"


def test_half_a_command_line_is_an_error_not_a_prompt(indexed_repo: Path) -> None:
    """--title alone must not fall through to the prompts.

    A caller with no stdin would hang there, or abort on a message naming
    neither flag. The exit code carries it too: printing the reason and
    returning 0 is what a script reads as success.
    """
    result = CliRunner().invoke(
        cli,
        ["decision", "add", "--title", "Half a decision", str(indexed_repo), "--format", "json"],
        input="",
    )

    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["error"].startswith("--title and --decision")


def test_decision_add_help_lists_a_flag_per_field() -> None:
    result = CliRunner().invoke(cli, ["decision", "add", "--help"])

    assert result.exit_code == 0, result.output
    for flag in (
        "--title",
        "--context",
        "--decision",
        "--rationale",
        "--alternative",
        "--consequence",
        "--affects",
        "--tag",
        "--kind",
        "--format",
    ):
        assert flag in result.output


def test_decision_list_help_lists_filters() -> None:
    result = CliRunner().invoke(cli, ["decision", "list", "--help"])

    assert result.exit_code == 0, result.output
    assert "--status" in result.output
    assert "--source" in result.output
    assert "--proposed" in result.output
    assert "--stale-only" in result.output
    assert "--limit" in result.output
    assert "--all" in result.output


def test_decision_list_empty_repo_prints_none_found(indexed_repo: Path) -> None:
    result = CliRunner().invoke(cli, ["decision", "list", str(indexed_repo)])

    assert result.exit_code == 0, result.output
    assert "No decisions found." in result.output


def test_decision_list_and_show_seeded_records(indexed_repo: Path) -> None:
    _seed_wiki_db(
        indexed_repo,
        [
            {
                "id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "title": "Prefer SQLite locally",
                "status": "active",
                "source": "cli",
            },
            {
                "id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "title": "Propose Redis sessions",
                "status": "proposed",
                "source": "git_archaeology",
            },
        ],
    )

    listed = CliRunner().invoke(cli, ["decision", "list", str(indexed_repo)])
    assert listed.exit_code == 0, listed.output
    assert "aaaaaaaa" in listed.output
    assert "bbbbbbbb" in listed.output
    assert "Prefer" in listed.output and "SQLite" in listed.output
    assert "Propose" in listed.output and "Redis" in listed.output
    assert "active" in listed.output
    assert "proposed" in listed.output

    proposed = CliRunner().invoke(cli, ["decision", "list", "--proposed", str(indexed_repo)])
    assert proposed.exit_code == 0, proposed.output
    assert "bbbbbbbb" in proposed.output
    assert "Propose" in proposed.output
    assert "aaaaaaaa" not in proposed.output
    assert "Prefer" not in proposed.output

    shown = CliRunner().invoke(cli, ["decision", "show", "aaaaaaaa", str(indexed_repo)])
    assert shown.exit_code == 0, shown.output
    assert "Prefer SQLite locally" in shown.output
    assert "Status: active" in shown.output
    assert "use X" in shown.output


def _many_decisions(n: int, **extra) -> list[dict]:
    return [{"id": f"{i:032x}", "title": f"Decision {i}", **extra} for i in range(n)]


def _list_json(repo: Path, *args: str) -> dict:
    result = CliRunner().invoke(cli, ["decision", "list", str(repo), *args, "--format", "json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_decision_list_says_how_many_rows_it_cut(indexed_repo: Path) -> None:
    _seed_wiki_db(indexed_repo, _many_decisions(105))

    table = CliRunner().invoke(cli, ["decision", "list", str(indexed_repo)])
    assert table.exit_code == 0, table.output
    assert "Showing 100 of 105 decisions" in table.output

    data = _list_json(indexed_repo)
    assert (data["total"], data["truncated"], len(data["decisions"])) == (105, True, 100)

    limited = _list_json(indexed_repo, "--limit", "3")
    assert (limited["total"], limited["truncated"], len(limited["decisions"])) == (105, True, 3)

    everything = CliRunner().invoke(cli, ["decision", "list", str(indexed_repo), "--all"])
    assert everything.exit_code == 0, everything.output
    assert "105 decisions." in everything.output
    assert "Showing" not in everything.output


def test_stale_only_counts_stale_rows_past_the_first_page(indexed_repo: Path) -> None:
    # Fresh rows outrank on confidence, so the stale ones sort past row 100.
    rows = _many_decisions(100, confidence=0.9) + [
        {"id": f"{i:032x}", "title": f"Stale {i}", "confidence": 0.1, "staleness": 0.9}
        for i in range(100, 104)
    ]
    _seed_wiki_db(indexed_repo, rows)
    # Stale is a property of accepted decisions only.
    for i in range(100, 104):
        confirmed = CliRunner().invoke(cli, ["decision", "confirm", f"{i:032x}", str(indexed_repo)])
        assert confirmed.exit_code == 0, confirmed.output

    data = _list_json(indexed_repo, "--stale-only")
    assert (data["total"], data["truncated"], len(data["decisions"])) == (4, False, 4)


def test_decision_list_shows_a_long_title_whole(indexed_repo: Path) -> None:
    title = "Backfill path mapping needs repo-tied evidence for deleted directories"
    _seed_wiki_db(indexed_repo, [{"id": "c" * 32, "title": title}])

    result = CliRunner().invoke(cli, ["decision", "list", str(indexed_repo)])
    assert result.exit_code == 0, result.output
    # The column wraps, so every word survives in order, with other cells
    # between wrapped lines; independent of width and box style.
    assert re.search(r"\b" + r"\b.*?\b".join(map(re.escape, title.split())), result.output, re.S)

def test_decision_confirm_promotes_proposed(indexed_repo: Path) -> None:
    _seed_wiki_db(
        indexed_repo,
        [
            {
                "id": "cccccccccccccccccccccccccccccccc",
                "title": "Needs confirmation",
                "status": "proposed",
            }
        ],
    )

    result = CliRunner().invoke(cli, ["decision", "confirm", "cccccccc", str(indexed_repo)])
    assert result.exit_code == 0, result.output
    assert "accepted (governing)" in result.output

    shown = CliRunner().invoke(cli, ["decision", "show", "cccccccc", str(indexed_repo)])
    assert shown.exit_code == 0, shown.output
    assert "Status: active" in shown.output


def test_decision_dismiss_and_deprecate(indexed_repo: Path) -> None:
    _seed_wiki_db(
        indexed_repo,
        [
            {
                "id": "dddddddddddddddddddddddddddddddd",
                "title": "Dismiss me",
                "status": "proposed",
            },
            {
                "id": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
                "title": "Deprecate me",
                "status": "active",
            },
        ],
    )

    dismissed = CliRunner().invoke(
        cli, ["decision", "dismiss", "dddddddd", str(indexed_repo)], input="y\n"
    )
    assert dismissed.exit_code == 0, dismissed.output
    assert "dismissed" in dismissed.output

    deprecated = CliRunner().invoke(cli, ["decision", "deprecate", "eeeeeeee", str(indexed_repo)])
    assert deprecated.exit_code == 0, deprecated.output
    assert "deprecated" in deprecated.output


def test_decision_ambiguous_prefix_errors(indexed_repo: Path) -> None:
    _seed_wiki_db(
        indexed_repo,
        [
            {"id": "ffff1111111111111111111111111111", "title": "One"},
            {"id": "ffff2222222222222222222222222222", "title": "Two"},
        ],
    )

    result = CliRunner().invoke(cli, ["decision", "show", "ffff", str(indexed_repo)])
    assert result.exit_code != 0
    assert "ambiguous" in result.output.lower()


def test_decision_health_prints_summary(indexed_repo: Path) -> None:
    _seed_wiki_db(
        indexed_repo,
        [
            {
                "id": "gggggggggggggggggggggggggggggggg",
                "title": "Active one",
                "status": "active",
            },
            {
                "id": "hhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhh",
                "title": "Proposed one",
                "status": "proposed",
            },
            {
                "id": "iiiiiiiiiiiiiiiiiiiiiiiiiiiiiiii",
                "title": "Stale one",
                "status": "active",
                "staleness": 0.8,
            },
        ],
    )

    result = CliRunner().invoke(cli, ["decision", "health", str(indexed_repo)])
    assert result.exit_code == 0, result.output
    assert "Decision Health" in result.output
    assert "Active decisions" in result.output
    assert "Proposed (needs review)" in result.output


def test_decision_health_lists_print_the_whole_title(indexed_repo: Path) -> None:
    title = "Backfill path mapping needs repo-tied evidence for deleted directories"
    assert len(title) > 50
    _seed_wiki_db(
        indexed_repo,
        [
            {
                "id": "jjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjj",
                "title": title,
                "status": "proposed",
            },
            {
                "id": "kkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkk",
                "title": title,
                "status": "active",
                "staleness": 0.8,
            },
        ],
    )

    result = CliRunner().invoke(cli, ["decision", "health", str(indexed_repo)])
    assert result.exit_code == 0, result.output
    # Rich wraps long lines at the terminal width, so compare ignoring whitespace.
    flattened = " ".join(result.output.split())
    assert flattened.count(title) == 2, result.output


def test_decision_health_lists_treat_titles_as_plain_text(indexed_repo: Path) -> None:
    markup_title = "Use [bold]x[/bold] for the cache key"
    closing_title = "Drop the [/] shorthand"
    _seed_wiki_db(
        indexed_repo,
        [
            {
                "id": "llllllllllllllllllllllllllllllll",
                "title": markup_title,
                "status": "proposed",
            },
            {
                "id": "mmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmm",
                "title": closing_title,
                "status": "active",
                "staleness": 0.8,
            },
        ],
    )

    result = CliRunner().invoke(cli, ["decision", "health", str(indexed_repo)])
    assert result.exit_code == 0, result.output
    flattened = " ".join(result.output.split())
    assert markup_title in flattened, result.output
    assert closing_title in flattened, result.output


class TestTheLifecycleIsDrivableByAnAgent:
    """Confirm/dismiss/deprecate are how a decision gains and loses authority.

    They were human-only: no machine-readable output, exit 0 on a bad id, and a
    dismissal that blocked on a prompt no non-interactive caller can answer.
    """

    def test_confirm_emits_json_and_the_new_status(self, indexed_repo: Path):
        _seed_wiki_db(indexed_repo, [{"id": "c0ffee01", "title": "T", "status": "proposed"}])

        result = CliRunner().invoke(
            cli, ["decision", "confirm", "c0ffee01", str(indexed_repo), "--format", "json"]
        )

        assert result.exit_code == 0, result.output
        (only,) = json.loads(result.output)["results"]
        assert (only["id"], only["status"], only["action"]) == ("c0ffee01", "active", "accepted")

    def test_dismiss_does_not_prompt_under_json(self, indexed_repo: Path):
        _seed_wiki_db(indexed_repo, [{"id": "c0ffee02", "title": "T", "status": "proposed"}])

        result = CliRunner().invoke(
            cli, ["decision", "dismiss", "c0ffee02", str(indexed_repo), "--format", "json"]
        )

        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["results"][0]["status"] == "dismissed"

    def test_dismiss_yes_skips_the_prompt_for_a_person_too(self, indexed_repo: Path):
        _seed_wiki_db(indexed_repo, [{"id": "c0ffee03", "title": "T", "status": "proposed"}])

        result = CliRunner().invoke(
            cli, ["decision", "dismiss", "c0ffee03", str(indexed_repo), "--yes"]
        )

        assert result.exit_code == 0, result.output
        assert "dismissed" in result.output

    @pytest.mark.parametrize("action", ["confirm", "dismiss", "deprecate", "show"])
    def test_an_unknown_id_exits_non_zero(self, indexed_repo: Path, action: str):
        """Exit 0 on a typo'd id made a failed confirm indistinguishable from a
        successful one."""
        _seed_wiki_db(indexed_repo, [])

        result = CliRunner().invoke(
            cli, ["decision", action, "deadbeef", str(indexed_repo), "--format", "json"]
        )

        assert result.exit_code != 0, result.output

    @pytest.mark.parametrize("action", ["confirm", "dismiss", "deprecate"])
    def test_an_unknown_id_reports_a_parseable_reason(self, indexed_repo: Path, action: str):
        _seed_wiki_db(indexed_repo, [])

        result = CliRunner().invoke(
            cli, ["decision", action, "deadbeef", str(indexed_repo), "--format", "json"]
        )

        payload = json.loads(result.output)
        assert (payload["succeeded"], payload["failed"]) == (0, 1)
        (only,) = payload["results"]
        assert (only["given"], only["error"]) == ("deadbeef", "decision_not_found")

    def test_deprecate_records_the_successor(self, indexed_repo: Path):
        _seed_wiki_db(
            indexed_repo,
            [
                {"id": "c0ffee04", "title": "old"},
                {"id": "c0ffee05", "title": "new"},
            ],
        )

        result = CliRunner().invoke(
            cli,
            [
                "decision", "deprecate", "c0ffee04", str(indexed_repo),
                "--superseded-by", "c0ffee05", "--format", "json",
            ],
        )

        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["results"][0]["status"] == "deprecated"


def test_confirm_refuses_a_candidate_with_nothing_to_accept(indexed_repo: Path) -> None:
    """Acceptance needs a reason, a scope and an evidence reference.

    Storing a blank one would make the acceptance log say a person agreed to
    something it cannot state, which is the pre-split promotion under a new
    name. The refusal is machine-readable, because a scripted review has to be
    able to tell it apart from a crash.
    """
    _seed_wiki_db(
        indexed_repo,
        [
            {
                "id": "badbadbadbadbadbadbadbadbadbadba",
                "title": "Nothing to go on",
                "status": "proposed",
                "rationale": "",
                "decision": "",
                "affected_files": [],
            }
        ],
    )

    result = CliRunner().invoke(
        cli, ["decision", "confirm", "badbadba", str(indexed_repo), "--format", "json"]
    )

    assert result.exit_code == 1, result.output
    (refused,) = json.loads(result.output)["results"]
    assert refused["error"] == "acceptance_refused"
    assert any("scope" in blocker for blocker in refused["blockers"])

    # And it is acceptable once the missing parts are supplied.
    fixed = CliRunner().invoke(
        cli,
        [
            "decision",
            "confirm",
            "badbadba",
            str(indexed_repo),
            "--decision",
            "Keep the measured faster path",
            "--reason",
            "Because the alternative was measured slower",
            "--scope",
            "src/app.py",
            "--evidence",
            "docs/notes.md",
            "--format",
            "json",
        ],
    )
    assert fixed.exit_code == 0, fixed.output
    assert json.loads(fixed.output)["results"][0]["status"] == "active"


# --- a manual entry is embedded like a mined one ---------------------------


def test_embed_decision_writes_the_records_vector(tmp_path, monkeypatch):
    """Without this, a manual entry is invisible to semantic dedup until a
    reindex, in both directions, at the top of the source ladder."""
    import repowise.cli.providers.embedders as embedders
    import repowise.cli.providers.vector_store as vector_store
    from repowise.cli.commands import decision_cmd

    written: list[tuple[str, str]] = []

    class _Store:
        _embedder = object()

        async def embed_and_upsert(self, page_id, text, metadata):
            written.append((page_id, text))
            return True

    monkeypatch.setattr(embedders, "resolve_embedder_for_repo", lambda p: "openai")
    monkeypatch.setattr(embedders, "build_embedder", lambda name, p: object())
    monkeypatch.setattr(vector_store, "build_vector_store", lambda p, e: _Store())

    asyncio.run(
        decision_cmd._embed_decision(
            tmp_path,
            "abc123",
            "Use one cache reader",
            "Do not write a second cache reader",
            None,
        )
    )

    assert written == [
        ("decision:abc123", "Use one cache reader\nDo not write a second cache reader")
    ]


def test_decision_add_stays_silent_when_the_repo_has_no_real_embedder(tmp_path, monkeypatch):
    """A keyless user must still be able to record a decision.

    The sentinel is asserted outside the helper, because the helper catches
    every exception: an ``AssertionError`` raised inside it would be swallowed
    and the test would pass against an implementation with no guard at all.
    """
    import repowise.cli.providers.embedders as embedders
    import repowise.cli.providers.vector_store as vector_store
    from repowise.cli.commands import decision_cmd
    from repowise.core.providers.embedding.base import KeylessEmbedder

    built: list[object] = []

    monkeypatch.setattr(embedders, "resolve_embedder_for_repo", lambda p: "keyless")
    monkeypatch.setattr(embedders, "build_embedder", lambda name, p: KeylessEmbedder())
    monkeypatch.setattr(
        vector_store, "build_vector_store", lambda p, e: built.append(e) or None
    )

    asyncio.run(decision_cmd._embed_decision(tmp_path, "abc123", "t", "d", None))

    assert built == [], "no store should be built without a real embedder"
    assert not (tmp_path / ".repowise" / "lancedb").exists()


# -- index routing: worktrees and unindexed checkouts --------------------------


def _git(args: list[str], cwd: Path) -> None:
    import subprocess

    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def base_checkout(tmp_path: Path) -> Path:
    base = tmp_path / "base"
    base.mkdir()
    _git(["init", "-q", "-b", "main"], base)
    (base / "a.py").write_text("x = 1\n")
    _git(["add", "."], base)
    _git(["commit", "-q", "-m", "init"], base)
    return base


@pytest.fixture
def linked_worktree(base_checkout: Path, tmp_path: Path) -> Path:
    wt = tmp_path / "wt"
    _git(["worktree", "add", "-q", str(wt), "-b", "feature"], base_checkout)
    return wt


def _stored_titles(repo_root: Path) -> list[str]:
    import sqlite3

    conn = sqlite3.connect(repo_root / ".repowise" / "wiki.db")
    try:
        return [r[0] for r in conn.execute("SELECT title FROM decision_records")]
    finally:
        conn.close()


_ADD = ["decision", "add", "--title", "Use X", "--decision", "We use X"]


def test_add_in_an_unindexed_repo_refuses_and_creates_nothing(tmp_path: Path) -> None:
    repo = tmp_path / "plain"
    repo.mkdir()

    result = CliRunner().invoke(cli, [*_ADD, str(repo)])

    assert result.exit_code == 1, result.output
    assert "No index found" in result.output
    assert "repowise init" in result.output
    assert not (repo / ".repowise").exists()


def test_add_json_in_an_unindexed_repo_emits_a_refusal_document(tmp_path: Path) -> None:
    repo = tmp_path / "plain"
    repo.mkdir()

    result = CliRunner().invoke(cli, [*_ADD, str(repo), "--format", "json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["error"] == "no_index"
    assert payload["remedy"] == "Run `repowise init` first."
    assert not (repo / ".repowise").exists()


def test_add_in_an_unindexed_worktree_of_an_unindexed_base_refuses(
    base_checkout: Path, linked_worktree: Path
) -> None:
    result = CliRunner().invoke(cli, [*_ADD, str(linked_worktree)])

    assert result.exit_code == 1, result.output
    assert not (linked_worktree / ".repowise").exists()
    assert not (base_checkout / ".repowise").exists()


def test_add_in_an_unindexed_worktree_writes_to_the_base_index(
    base_checkout: Path, linked_worktree: Path
) -> None:
    _create_empty_index(base_checkout)

    result = CliRunner().invoke(cli, [*_ADD, str(linked_worktree), "--format", "json"])

    assert result.exit_code == 0, result.output
    assert not (linked_worktree / ".repowise").exists()
    assert _stored_titles(base_checkout) == ["Use X"]


def test_add_from_a_worktree_says_where_the_record_went(
    base_checkout: Path, linked_worktree: Path
) -> None:
    _create_empty_index(base_checkout)

    result = CliRunner().invoke(cli, [*_ADD, str(linked_worktree)])

    assert result.exit_code == 0, result.output
    assert "using the base checkout's" in result.output
    assert str(base_checkout) in result.output.replace("\n", "")


def test_add_json_from_a_worktree_reports_the_base_as_the_repo(
    base_checkout: Path, linked_worktree: Path
) -> None:
    _create_empty_index(base_checkout)

    result = CliRunner().invoke(cli, [*_ADD, str(linked_worktree), "--format", "json"])

    assert result.exit_code == 0, result.output
    # Click 8.1 mixes stderr into the captured stdout and 8.2+ keeps them apart,
    # so lift the document out of the output instead of assuming either. That
    # the notice itself goes to stderr under --format json is pinned by
    # test_notice_console_is_stderr_under_json.
    output = result.output
    document, _ = json.JSONDecoder().raw_decode(output[output.index("{") :])
    assert Path(document["repo"]).resolve() == base_checkout.resolve()
    assert "using the base checkout's" in output


def test_a_worktree_with_its_own_index_keeps_writing_to_it(
    base_checkout: Path, linked_worktree: Path
) -> None:
    _create_empty_index(base_checkout)
    _create_empty_index(linked_worktree)

    result = CliRunner().invoke(cli, [*_ADD, str(linked_worktree)])

    assert result.exit_code == 0, result.output
    assert _stored_titles(linked_worktree) == ["Use X"]
    assert _stored_titles(base_checkout) == []


def test_list_in_a_worktree_reads_the_base_index(
    base_checkout: Path, linked_worktree: Path
) -> None:
    _seed_wiki_db(base_checkout, [{"id": "aaaaaaaa11111111", "title": "Base decision"}])

    result = CliRunner().invoke(cli, ["decision", "list", str(linked_worktree)])

    assert result.exit_code == 0, result.output
    assert "Base decision" in result.output
    assert not (linked_worktree / ".repowise").exists()


def test_list_in_an_unindexed_repo_refuses_instead_of_creating_a_store(tmp_path: Path) -> None:
    repo = tmp_path / "plain"
    repo.mkdir()

    result = CliRunner().invoke(cli, ["decision", "list", str(repo)])

    assert result.exit_code == 1, result.output
    assert not (repo / ".repowise").exists()


def test_a_configured_database_counts_as_an_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "plain"
    repo.mkdir()
    db = tmp_path / "shared.db"
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{db.as_posix()}")

    result = CliRunner().invoke(cli, [*_ADD, str(repo)])

    assert result.exit_code == 0, result.output
    assert db.exists()


def test_config_commands_do_not_need_an_index(tmp_path: Path) -> None:
    repo = tmp_path / "plain"
    repo.mkdir()

    result = CliRunner().invoke(cli, ["decision", "config", "show", str(repo)])

    assert result.exit_code == 0, result.output
    assert "No index found" not in result.output
