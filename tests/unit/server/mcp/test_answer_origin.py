"""get_answer on "which change introduced X": the commit and PR, never X's definition."""

from __future__ import annotations

import json
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
        ("Who added CookieJar.", "CookieJar", True),
        ("Who added cookies.py.", "cookies.py", True),
        ("Who added cookies.pyc", "cookies.py", False),
        # Present tense asks about behaviour; a lowercase word is not the class.
        ("Which commit changed how Session is created?", "Session", False),
        ("Which PR added session handling?", "Session", False),
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


async def _seed_change(session, sha: str) -> None:
    from repowise.core.persistence.crud import upsert_changes

    await upsert_changes(
        session,
        "repo1",
        [
            {
                "change_id": "pr-7",
                "identity_kind": "pr",
                "identity_value": "7",
                "shape": "squash",
                "forge": "github",
                "forge_ref": "#7",
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
    assert "no commit in this checkout's history adds it" in result["answer"]
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
