"""get_answer on "which change introduced X": the commit and PR, never X's definition."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime

import pytest

from repowise.server.mcp_server.tool_answer.origin import _is_object, asks_origin


@pytest.mark.parametrize(
    ("question", "origin"),
    [
        ("Which pull request introduced _MAX_COOKIES limit on cookies?", True),
        ("Which commit added parse_headers?", True),
        ("Who added CookieJar?", True),
        ("When was cookies.py added?", True),
        ("When did AuthService start?", True),
        ("Where was AuthService first introduced?", True),
        ("What is _MAX_COOKIES?", False),
        ("How does CookieJar pick cookies?", False),
        ("When was the session closed in close()?", False),
        ("Which function adds the header?", False),
    ],
)
def test_origin_questions(question, origin):
    assert asks_origin(question) is origin


@pytest.mark.parametrize(
    ("question", "name", "expected"),
    [
        ("Which PR introduced _MAX_COOKIES limit on cookies?", "_MAX_COOKIES", True),
        ("Which commit introduced the CookieJar class?", "CookieJar", True),
        ("When was `parse` first added?", "parse", True),
        ("When did AuthService start?", "AuthService", True),
        # A behaviour inside the symbol: its own first commit is not the answer.
        ("Which PR added retries to fetch_url?", "fetch_url", False),
        ("When did AuthService start failing?", "AuthService", False),
        ("When did AuthService start to fail?", "AuthService", False),
        ("Who added CookieJar.", "CookieJar", True),
        ("Who added cookies.py.", "cookies.py", True),
        ("Who added cookies.pyc", "cookies.py", False),
        # Present tense asks about behaviour; a lowercase word is not the class.
        ("Which commit changed how Session is created?", "Session", False),
        ("Which PR added session handling?", "Session", False),
        ("Which PR introduced AuthService's retry logic?", "AuthService", False),
    ],
)
def test_the_verb_must_take_the_name(question, name, expected):
    assert _is_object(question, name) is expected


def _git(cwd, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=Dev", "-c", "user.email=dev@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def history(tmp_path):
    """service.py exists first; a later commit adds the AuthService class."""
    _git(tmp_path, "init", "-q")
    path = tmp_path / "src/auth/service.py"
    path.parent.mkdir(parents=True)
    # A longer name holding the class name is not the class.
    path.write_text("x = 1\nAuthServiceError = None\n", encoding="utf-8")
    _git(tmp_path, "add", "src")
    _git(tmp_path, "commit", "-qm", "seed")
    path.write_text(
        "x = 1\nAuthServiceError = None\nclass AuthService:\n    pass\n", encoding="utf-8"
    )
    _git(tmp_path, "commit", "-qam", "Add the auth service (#7)")
    path.write_text(
        "x = 2\nAuthServiceError = None\nclass AuthService:\n    retries = 3\n", encoding="utf-8"
    )
    _git(tmp_path, "commit", "-qam", "Retry logins")
    return _git(tmp_path, "rev-parse", "HEAD~1")


async def _seed_change(
    session, sha: str, *, forge: str = "github", number: int = 7, forge_ref: str = "#7"
) -> None:
    from repowise.core.persistence.crud import upsert_changes

    await upsert_changes(
        session,
        "repo1",
        [
            {
                "change_id": f"pr-{number}",
                "identity_kind": "pr",
                "identity_value": str(number),
                "shape": "squash",
                "forge": forge,
                "forge_ref": forge_ref,
                "title": "Add the auth service",
                "merged_at": datetime(2026, 3, 1, tzinfo=UTC),
                "head_sha": sha,
                "commit_shas_json": json.dumps([sha]),
                "files_json": json.dumps(
                    [{"path": "src/auth/service.py", "added": 3, "deleted": 0}]
                ),
                "files_changed": 1,
                "lines_added": 3,
                "lines_deleted": 0,
                "category": "feature",
                "metadata_source": "git",
            }
        ],
    )
    await session.commit()


@pytest.mark.asyncio
async def test_names_the_change_that_added_the_symbol(setup_mcp, session, history):
    from repowise.server.mcp_server import get_answer

    await _seed_change(session, history)
    result = await get_answer("Which PR introduced AuthService?")
    assert result["confidence"] == "high"
    assert result["grounding"] == "git_history"
    assert result["answer"].startswith('`AuthService` was introduced by #7 "Add the auth service"')
    assert history[:12] in result["answer"]
    assert result["origin"]["commit"] == history
    assert result["origin"]["change"] == "pr-7"
    assert result["citations"] == ["src/auth/service.py"]
    assert "change:pr-7" in result["next_action_hint"]


@pytest.mark.asyncio
async def test_a_commit_outside_the_change_records_still_answers(setup_mcp, history):
    from repowise.server.mcp_server import get_answer

    result = await get_answer("Who added AuthService?")
    assert result["confidence"] == "high"
    assert "change" not in result["origin"]
    assert f'commit {history[:12]} "Add the auth service (#7)" (by Dev,' in result["answer"]


@pytest.mark.asyncio
async def test_a_behaviour_inside_the_symbol_is_said_unknown(setup_mcp, history):
    from repowise.server.mcp_server import get_answer

    result = await get_answer("Which PR added retries to AuthService?")
    assert result["confidence"] == "low"
    assert result["answer"].startswith("Not known from the index which change introduced")
    assert "git log -L:AuthService:src/auth/service.py" in result["next_action_hint"]


@pytest.mark.asyncio
async def test_without_git_history_it_says_so(setup_mcp):
    from repowise.server.mcp_server import get_answer

    result = await get_answer("Which commit introduced AuthService?")
    assert result["confidence"] == "low"
    assert "this checkout has no git history" in result["answer"]
    assert 'git log -S"AuthService" --reverse -- src/auth/service.py' in result["next_action_hint"]


@pytest.mark.asyncio
async def test_a_file_is_dated_by_the_commit_that_added_it(setup_mcp, history, tmp_path):
    from repowise.server.mcp_server import get_answer

    seed = _git(tmp_path, "rev-list", "--max-parents=0", "HEAD")
    result = await get_answer("When was src/auth/service.py added?")
    assert result["origin"]["commit"] == seed
    assert result["answer"].startswith(
        f'`src/auth/service.py` was introduced by commit {seed[:12]} "seed"'
    )


@pytest.mark.asyncio
async def test_a_slow_history_walk_is_said_to_time_out(setup_mcp, history, monkeypatch):
    from repowise.server.mcp_server import get_answer
    from repowise.server.mcp_server.tool_answer import origin

    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 10)

    monkeypatch.setattr(origin.subprocess, "run", slow)
    result = await get_answer("Who added AuthService?")
    assert result["confidence"] == "low"
    assert "took longer than" in result["answer"]


@pytest.mark.asyncio
async def test_a_file_without_history_gets_a_file_command(setup_mcp):
    from repowise.server.mcp_server import get_answer

    result = await get_answer("When was src/auth/service.py added?")
    assert result["confidence"] == "low"
    assert "git log --follow --diff-filter=A -- src/auth/service.py" in result["next_action_hint"]


@pytest.mark.asyncio
async def test_a_repeat_reuses_the_walk_until_the_head_moves(
    setup_mcp, history, tmp_path, monkeypatch
):
    from repowise.server.mcp_server import get_answer
    from repowise.server.mcp_server.tool_answer import origin

    walks = []
    real = origin._oldest_adding_commit

    def counted(*args):
        walks.append(args)
        return real(*args)

    monkeypatch.setattr(origin, "_oldest_adding_commit", counted)
    first = await get_answer("Who added AuthService?")
    again = await get_answer("Which commit introduced AuthService?")
    assert len(walks) == 1
    assert again["origin"] == first["origin"]
    (tmp_path / "notes.txt").write_text("n\n", encoding="utf-8")
    _git(tmp_path, "add", "notes.txt")
    _git(tmp_path, "commit", "-qm", "notes")
    await get_answer("Who added AuthService?")
    assert len(walks) == 2


async def _seed_file_history(session, sha: str, *, first_at: datetime, committed_at: datetime):
    from sqlalchemy import update

    from repowise.core.persistence.models import GitCommit, GitCommitFile, GitMetadata

    await session.execute(
        update(GitMetadata)
        .where(GitMetadata.file_path == "src/auth/service.py")
        .values(first_commit_at=first_at, commit_count_capped=False, original_path=None)
    )
    session.add(
        GitCommit(
            repository_id="repo1",
            sha=sha,
            author_name="Indexed Author",
            committed_at=committed_at,
            subject="Create the auth service",
        )
    )
    session.add(GitCommitFile(repository_id="repo1", sha=sha, file_path="src/auth/service.py"))
    await session.commit()


@pytest.mark.asyncio
async def test_a_file_is_dated_from_the_indexed_history_without_git(setup_mcp, session):
    from repowise.server.mcp_server import get_answer

    sha = "a" * 40
    when = datetime(2025, 6, 2, 8, 18, 57, tzinfo=UTC)
    await _seed_file_history(session, sha, first_at=when, committed_at=when)
    await _seed_change(session, sha)
    # No git checkout at all: the answer comes from the index.
    result = await get_answer("When was src/auth/service.py added?")
    assert result["confidence"] == "high"
    assert result["origin"]["commit"] == sha
    assert result["origin"]["change"] == "pr-7"
    assert result["note"].startswith("Read from the indexed history")


@pytest.mark.asyncio
async def test_indexed_history_short_of_the_first_commit_falls_back(setup_mcp, session):
    from repowise.server.mcp_server import get_answer

    # The stored window starts after the file's first commit: not its origin.
    await _seed_file_history(
        session,
        "b" * 40,
        first_at=datetime(2016, 9, 13, tzinfo=UTC),
        committed_at=datetime(2024, 1, 21, tzinfo=UTC),
    )
    result = await get_answer("When was src/auth/service.py added?")
    assert result["confidence"] == "low"
    assert "this checkout has no git history" in result["answer"]


@pytest.mark.asyncio
async def test_a_name_many_files_define_is_said_so(setup_mcp, session):
    from repowise.core.persistence.models import WikiSymbol
    from repowise.server.mcp_server import get_answer

    for i in range(6):
        session.add(
            WikiSymbol(
                id=f"close{i}",
                repository_id="repo1",
                file_path=f"src/m{i}.py",
                symbol_id=f"src/m{i}.py::close_all",
                name="close_all",
                qualified_name=f"m{i}.close_all",
                kind="function",
            )
        )
    await session.commit()
    result = await get_answer("Who added close_all?")
    assert result["confidence"] == "low"
    assert "6 indexed files define `close_all`, too many to name one origin" in result["answer"]


@pytest.mark.asyncio
async def test_a_file_name_two_files_carry_asks_for_the_path(setup_mcp, session):
    from repowise.core.persistence.models import GitMetadata
    from repowise.server.mcp_server import get_answer

    for path in ("src/a/util.py", "src/b/util.py"):
        session.add(GitMetadata(repository_id="repo1", file_path=path))
    await session.commit()
    result = await get_answer("When was util.py added?")
    assert result["confidence"] == "low"
    assert "2 indexed files are named `util.py` (src/a/util.py, src/b/util.py)" in result["answer"]


@pytest.mark.asyncio
async def test_a_scope_outside_the_defining_file_finds_nothing(setup_mcp, history):
    from repowise.server.mcp_server import get_answer

    result = await get_answer("Who added AuthService?", scope="src/db/")
    assert result["confidence"] == "low"
    assert "no indexed file under `src/db/` defines `AuthService`" in result["answer"]
    inside = await get_answer("Who added AuthService?", scope="src/auth/")
    assert inside["confidence"] == "high"


@pytest.mark.asyncio
async def test_a_shallow_clone_walk_is_not_kept(setup_mcp, history, monkeypatch):
    from repowise.server.mcp_server import get_answer
    from repowise.server.mcp_server.tool_answer import origin

    monkeypatch.setattr(origin, "shallow_roots", lambda _path: frozenset({"0" * 40}))
    await get_answer("Who added AuthService?")
    assert not origin._WALKS


def _dated(cwd, when: str, *args: str, authored: str | None = None) -> str:
    """``git *args`` committed at *when*, authored at *authored* (default *when*)."""
    env = {
        **os.environ,
        "GIT_COMMITTER_DATE": f"{when}T12:00:00Z",
        "GIT_AUTHOR_DATE": f"{authored or when}T12:00:00Z",
    }
    return subprocess.run(
        ["git", "-c", "user.name=Dev", "-c", "user.email=dev@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()


@pytest.fixture(params=["merge", "squash", "rebase"])
def late_landing(request, tmp_path):
    """AuthService written on a branch on 2026-01-18 that lands on main on 2026-08-12."""
    _dated(tmp_path, "2026-01-01", "init", "-q", "-b", "main")
    path = tmp_path / "src/auth/service.py"
    path.parent.mkdir(parents=True)
    path.write_text("x = 1\n", encoding="utf-8")
    _dated(tmp_path, "2026-01-01", "add", "src")
    _dated(tmp_path, "2026-01-01", "commit", "-qm", "seed")
    _dated(tmp_path, "2026-01-18", "checkout", "-qb", "feat")
    path.write_text("x = 1\nclass AuthService:\n    pass\n", encoding="utf-8")
    _dated(tmp_path, "2026-01-18", "commit", "-qam", "Add the auth service")
    _dated(tmp_path, "2026-03-01", "checkout", "-q", "main")
    (tmp_path / "notes.txt").write_text("n\n", encoding="utf-8")
    _dated(tmp_path, "2026-03-01", "add", "notes.txt")
    _dated(tmp_path, "2026-03-01", "commit", "-qm", "notes")
    land = "2026-08-12"
    if request.param == "merge":
        _dated(tmp_path, land, "merge", "-q", "--no-ff", "feat", "-m", "Merge pull request #7")
    elif request.param == "squash":
        _dated(tmp_path, land, "merge", "-q", "--squash", "feat")
        # The forge keeps the branch's author date on the squash commit.
        _dated(tmp_path, land, "commit", "-qm", "Add the auth service (#7)", authored="2026-01-18")
    else:
        _dated(tmp_path, land, "rebase", "-q", "main", "feat")
        _dated(tmp_path, land, "checkout", "-q", "main")
        _dated(tmp_path, land, "merge", "-q", "--ff-only", "feat")
    (tmp_path / "notes.txt").write_text("n\nm\n", encoding="utf-8")
    _dated(tmp_path, "2026-09-01", "commit", "-qam", "more notes")
    return request.param


@pytest.mark.asyncio
async def test_the_date_is_when_the_change_landed_not_when_it_was_written(setup_mcp, late_landing):
    from repowise.server.mcp_server import get_answer

    result = await get_answer("When did AuthService first appear?")
    assert result["confidence"] == "high"
    assert result["origin"]["date"] == "2026-08-12"
    assert "authored" not in result["origin"]
    assert "(by Dev, landed 2026-08-12, authored 2026-01-18)" in result["answer"]


@pytest.mark.asyncio
async def test_a_commit_written_and_landed_together_gives_one_date(setup_mcp, history):
    from repowise.server.mcp_server import get_answer

    result = await get_answer("Who added AuthService?")
    assert "authored" not in result["answer"]
    assert f"(by Dev, {result['origin']['date']})" in result["answer"]


@pytest.mark.asyncio
async def test_a_stored_change_dates_the_origin_by_its_merge(setup_mcp, session, history):
    from repowise.server.mcp_server import get_answer

    await _seed_change(session, history)  # merged_at 2026-03-01
    result = await get_answer("Which PR introduced AuthService?")
    assert result["origin"]["date"] == "2026-03-01"


@pytest.mark.asyncio
async def test_a_use_before_the_definition_is_not_the_origin(setup_mcp, tmp_path):
    from repowise.server.mcp_server import get_answer

    _git(tmp_path, "init", "-q")
    path = tmp_path / "src/auth/service.py"
    path.parent.mkdir(parents=True)
    # A call on another module that happens to use the same word.
    path.write_text("x = other.AuthService()\n", encoding="utf-8")
    _git(tmp_path, "add", "src")
    _git(tmp_path, "commit", "-qm", "use it")
    path.write_text("x = other.AuthService()\nclass AuthService:\n    pass\n", encoding="utf-8")
    _git(tmp_path, "commit", "-qam", "Define it")
    defined = _git(tmp_path, "rev-parse", "HEAD")
    result = await get_answer("Who added AuthService?")
    assert result["origin"]["commit"] == defined


@pytest.mark.parametrize(
    ("forge", "subject", "question"),
    [
        ("gitlab", "Add the auth service (!12)", "Which MR introduced AuthService?"),
        (
            "azure",
            "Merged PR 12: Add the auth service",
            "Which pull request introduced AuthService?",
        ),
    ],
)
@pytest.mark.asyncio
async def test_other_forges_name_their_own_change_ref(
    setup_mcp, session, history, forge, subject, question
):
    from repowise.core.changes.reconstruct import change_identity
    from repowise.server.mcp_server import get_answer

    # The ref exactly as change reconstruction stores it for that forge.
    kind, number, forge_ref = change_identity(subject, "", history, False, forge)
    assert (kind, number) == ("pr", "12")
    await _seed_change(session, history, forge=forge, number=12, forge_ref=forge_ref)
    result = await get_answer(question)
    assert result["confidence"] == "high"
    assert result["answer"].startswith(
        f'`AuthService` was introduced by {forge_ref} "Add the auth service"'
    )
    assert result["origin"]["change"] == "pr-12"


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("When did we start limiting the number of content codings?", True),
        ("When and why did aiohttp start refusing chained codings? Which pull request?", True),
        ("Which PR started bounding the request queue?", True),
        # A regression began, or no history is asked: not a behaviour's origin.
        ("When did the login start failing?", False),
        ("When did the tests start timing out?", False),
        # A whole failure verb only: "timestamping" is a behaviour.
        ("When did we start timestamping commits?", True),
        ("How do we limit login attempts?", False),
    ],
)
def test_behaviour_origin_questions(question, expected):
    from repowise.server.mcp_server.tool_answer.origin import asks_behaviour_origin

    assert asks_behaviour_origin(question) is expected


@pytest.mark.parametrize(
    ("name", "limit", "cover"),
    [
        ("MAX_LOGIN_ATTEMPTS", True, 3),
        ("maxLoginAttempts", True, 3),
        # The MAX part counts only when the question describes a limit.
        ("MAX_LOGIN_ATTEMPTS", False, 0),
        # A part the question does not spell rules the name out.
        ("SUPPORTED_LOGIN_ATTEMPTS", True, 0),
        # One spelled word names too much.
        ("LoginForm", True, 0),
    ],
)
def test_a_name_resolves_only_when_the_question_spells_all_of_it(name, limit, cover):
    from repowise.server.mcp_server.tool_answer.origin import _behaviour_words, _name_cover

    words = _behaviour_words("When did we start limiting login attempts?")
    assert _name_cover(name, words, limit) == cover


@pytest.fixture
def limit_history(tmp_path):
    """service.py exists first; a later commit adds the login-attempt limit."""
    _git(tmp_path, "init", "-q")
    path = tmp_path / "src/auth/service.py"
    path.parent.mkdir(parents=True)
    path.write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "src")
    _git(tmp_path, "commit", "-qm", "seed")
    path.write_text("x = 1\nMAX_LOGIN_ATTEMPTS = 5\n", encoding="utf-8")
    _git(tmp_path, "commit", "-qam", "Lock accounts after five failed logins (#9)")
    return _git(tmp_path, "rev-parse", "HEAD")


async def _seed_symbol(session, name: str, path: str = "src/auth/service.py") -> None:
    from repowise.core.persistence.models import WikiSymbol

    session.add(
        WikiSymbol(
            id=f"{name}-{path}"[:32],
            repository_id="repo1",
            file_path=path,
            symbol_id=f"{path}::{name}",
            name=name,
            qualified_name=name,
            kind="variable",
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_a_described_behaviour_resolves_to_the_symbol_it_spells(
    setup_mcp, session, limit_history
):
    from repowise.server.mcp_server import get_answer

    await _seed_symbol(session, "MAX_LOGIN_ATTEMPTS")
    result = await get_answer("When did we start limiting login attempts?")
    assert result["confidence"] == "high"
    assert result["answer"].startswith("The question describes `MAX_LOGIN_ATTEMPTS`")
    assert (
        f'commit {limit_history[:12]} "Lock accounts after five failed logins (#9)"'
        in (result["answer"])
    )
    assert result["origin"]["subject"] == "MAX_LOGIN_ATTEMPTS"
    assert result["origin"]["resolved_from"] == "question"


@pytest.mark.asyncio
async def test_two_names_matching_as_well_resolve_to_neither(setup_mcp, session, limit_history):
    from repowise.server.mcp_server import get_answer

    await _seed_symbol(session, "MAX_LOGIN_ATTEMPTS")
    await _seed_symbol(session, "LOGIN_ATTEMPTS_LIMIT", "src/auth/policy.py")
    result = await get_answer("When did we start limiting login attempts?")
    assert "origin" not in result
    assert result["next_action_hint"].startswith("Run `git log -S'<identifier>' --reverse")


@pytest.mark.asyncio
async def test_a_name_only_test_code_spells_is_not_the_behaviour(setup_mcp, session, limit_history):
    from repowise.server.mcp_server import get_answer

    await _seed_symbol(session, "MAX_LOGIN_ATTEMPTS", "tests/test_login.py")
    result = await get_answer("When did we start limiting login attempts?")
    assert "origin" not in result
    assert "git log -S'<identifier>'" in result["next_action_hint"]


@pytest.mark.parametrize(
    ("part", "word", "same"),
    [
        ("decompress", "decompresses", True),
        ("coding", "codings", True),
        ("validate", "validating", True),
        ("time", "timeout", False),
        ("read", "ready", False),
    ],
)
def test_a_name_part_matches_a_word_only_through_an_inflection(part, word, same):
    from repowise.server.mcp_server.tool_answer.origin import _same_word

    assert _same_word(part, word) is same


def test_the_name_must_name_the_behaviour_that_started():
    from repowise.server.mcp_server.tool_answer.origin import _behaviour_words, _name_cover

    question = "When did the config parser start returning None?"
    assert _name_cover("ConfigParser", _behaviour_words(question), False, "returning") == 0


@pytest.mark.asyncio
async def test_a_typed_symbol_is_where_the_behaviour_happens_not_the_behaviour(
    setup_mcp, session, limit_history
):
    from repowise.server.mcp_server import get_answer

    await _seed_symbol(session, "MAX_LOGIN_ATTEMPTS")
    result = await get_answer("When did MAX_LOGIN_ATTEMPTS start limiting logins?")
    assert "origin" not in result


@pytest.mark.asyncio
async def test_a_change_inside_a_named_symbol_still_says_unknown(setup_mcp, history):
    from repowise.server.mcp_server import get_answer

    result = await get_answer("When did we start adding retries to AuthService?")
    assert result["answer"].startswith("Not known from the index which change introduced")
    assert "git log -L:AuthService:src/auth/service.py" in result["next_action_hint"]


@pytest.mark.asyncio
async def test_a_scope_without_the_symbol_resolves_nothing(setup_mcp, session, limit_history):
    from repowise.server.mcp_server import get_answer

    await _seed_symbol(session, "MAX_LOGIN_ATTEMPTS")
    result = await get_answer("When did we start limiting login attempts?", scope="src/db/")
    assert "origin" not in result


def test_a_digit_run_stays_with_its_letters():
    from repowise.server.mcp_server.tool_answer.origin import _behaviour_words, _name_cover

    words = _behaviour_words("When did we start validating UTF8 input?")
    assert _name_cover("validateUTF8", words, False, "validating") == 2


def test_a_code_noun_in_the_question_still_spells_the_name():
    from repowise.server.mcp_server.tool_answer.origin import _behaviour_words, _name_cover

    words = _behaviour_words("Which pull request started limiting request body size?")
    assert "pull" not in words
    assert _name_cover("MAX_REQUEST_BODY_SIZE", words, True, "limiting") == 4


@pytest.mark.asyncio
async def test_a_scan_past_its_cap_resolves_nothing(setup_mcp, session, limit_history, monkeypatch):
    from repowise.server.mcp_server import get_answer
    from repowise.server.mcp_server.tool_answer import origin

    await _seed_symbol(session, "MAX_LOGIN_ATTEMPTS")
    await _seed_symbol(session, "LOGIN_ATTEMPTS_MAX_COUNT", "src/auth/policy.py")
    monkeypatch.setattr(origin, "_MAX_SCANNED_NAMES", 1)
    result = await get_answer("When did we start limiting login attempts?")
    assert "origin" not in result


@pytest.mark.asyncio
async def test_a_landing_git_cannot_tell_is_answered_but_not_kept(
    setup_mcp, late_landing, monkeypatch
):
    from repowise.server.mcp_server import get_answer
    from repowise.server.mcp_server.tool_answer import origin

    monkeypatch.setattr(origin, "landing_date", lambda *args: None)
    result = await get_answer("When did AuthService first appear?")
    assert result["confidence"] == "high"
    assert not origin._WALKS
    # The commit's own date, never labelled a landing nobody read.
    assert "landed" not in result["answer"]
    assert "authored" not in result["answer"]
