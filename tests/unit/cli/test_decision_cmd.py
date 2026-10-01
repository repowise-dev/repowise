"""CLI coverage for ``repowise decision`` subcommands."""

from __future__ import annotations

import asyncio
import json
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


@pytest.fixture
def indexed_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".repowise").mkdir()
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
        payload = json.loads(result.output)
        assert payload == {"id": "c0ffee01", "status": "active", "action": "accepted"}

    def test_dismiss_does_not_prompt_under_json(self, indexed_repo: Path):
        _seed_wiki_db(indexed_repo, [{"id": "c0ffee02", "title": "T", "status": "proposed"}])

        result = CliRunner().invoke(
            cli, ["decision", "dismiss", "c0ffee02", str(indexed_repo), "--format", "json"]
        )

        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["status"] == "dismissed"

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

        assert json.loads(result.output) == {
            "error": "decision_not_found",
            "decision_id": "deadbeef",
        }

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
        assert json.loads(result.output)["status"] == "deprecated"


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
    payload = json.loads(result.output)
    assert payload["error"] == "acceptance_refused"
    assert any("scope" in blocker for blocker in payload["blockers"])

    # And it is acceptable once the missing parts are supplied.
    fixed = CliRunner().invoke(
        cli,
        [
            "decision",
            "confirm",
            "badbadba",
            str(indexed_repo),
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
    assert json.loads(fixed.output)["status"] == "active"


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
