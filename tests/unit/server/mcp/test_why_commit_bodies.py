"""get_why serves the part of a commit's body (often its PR description) that answers the question."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from repowise.server.mcp_server.tool_why import commit_bodies
from repowise.server.mcp_server.tool_why.commit_bodies import (
    EXCERPT_CHARS,
    attach_body_excerpts,
    body_excerpt,
    keep_one_excerpt,
)
from repowise.server.mcp_server.tool_why.compact import compact_unanswered

_SQUASH_BODY = """* fix(router): make wildcard order deterministic

* feat(router): throw UnsupportedPathError when adding routes

Insert paths into per-method tries in add() so that unsupported path
combinations are detected at registration time instead of at the first match.

## Test plan

- [x] the registration check covers the conflicting wildcard routes listed above

Signed-off-by: A Dev <a@example.com>
Co-authored-by: B Dev <b@example.com>
"""


def test_the_paragraph_carrying_the_question_is_served() -> None:
    excerpt = body_excerpt(
        _SQUASH_BODY,
        "feat(router): throw UnsupportedPathError during route registration (#51)",
        "why does the router throw at registration instead of at the first match",
    )
    assert excerpt is not None
    assert excerpt.startswith("Insert paths into per-method tries")


def test_sub_commit_subjects_trailers_and_test_plans_are_never_served() -> None:
    body = "* feat: add the thing\n\n* fix: format\n\n## Test plan\n\nRan the whole suite on a large repo.\n\nSigned-off-by: A <a@b.c>\n"
    assert body_excerpt(body, "feat: add the thing (#3)", "why add the thing") is None


def test_a_body_sharing_under_two_question_words_is_not_served() -> None:
    body = (
        "A question word that is also a file name made that file a flow endpoint.\n\n"
        "The path search walked four hops through hub files."
    )
    query = "why does the infra predicate treat a shell script as infrastructure"
    assert body_excerpt(body, "fix: flow endpoints", query) is None


def test_an_excerpt_is_bounded() -> None:
    body = "Because " + " ".join(["the cache must be keyed by index state"] * 30)
    excerpt = body_excerpt(body, "perf: cache", "why is the cache keyed by index state")
    assert excerpt is not None
    assert len(excerpt) <= EXCERPT_CHARS


def _card_result(*rows: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": "search",
        "query": "why does the router throw at registration instead of at the first match",
        "decisions": [],
        "target_context": {"src/router.ts": {"git_archaeology": {"file_commits": list(rows)}}},
    }


async def test_a_stored_body_is_served_without_git(tmp_path: Path) -> None:
    meta = SimpleNamespace(
        significant_commits_json=json.dumps(
            [{"sha": "8a0b18fd", "message": "feat(router): x", "body": _SQUASH_BODY, "pr_number": 51}]
        )
    )
    result = _card_result({"sha": "8a0b18fd", "message": "feat(router): throw during route registration"})
    # tmp_path has no .git: only the indexed body can answer.
    await attach_body_excerpts(result, tmp_path, {"src/router.ts": meta}, result["query"])
    row = result["target_context"]["src/router.ts"]["git_archaeology"]["file_commits"][0]
    assert row["body_excerpt"].startswith("Insert paths")
    assert row["pr_number"] == 51


async def test_no_body_and_no_clone_leaves_the_rows_unchanged(tmp_path: Path) -> None:
    result = _card_result({"sha": "8a0b18fd", "message": "feat(router): x"})
    before = json.dumps(result)
    await attach_body_excerpts(result, tmp_path, {}, result["query"])
    assert json.dumps(result) == before


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


async def test_a_live_body_is_read_from_the_local_clone(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.txt").write_text("x")
    _git(tmp_path, "add", "a.txt")
    _git(
        tmp_path,
        "-c", "user.name=t", "-c", "user.email=t@t",
        "commit", "-q", "-m", "feat(router): throw during route registration (#51)", "-m", _SQUASH_BODY,
    )
    sha = _git(tmp_path, "rev-parse", "HEAD")[:12]
    result = {"mode": "search", "git_archaeology": {"git_log": [{"sha": sha, "message": "feat(router): x"}]}}
    await attach_body_excerpts(result, tmp_path, {}, _card_result()["query"])
    row = result["git_archaeology"]["git_log"][0]
    assert row["body_excerpt"].startswith("Insert paths")
    assert row["pr_number"] == 51


async def test_a_git_failure_serves_no_excerpt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".git").mkdir()

    def boom(*_a: Any, **_k: Any) -> Any:
        raise subprocess.TimeoutExpired("git", 5)

    monkeypatch.setattr(commit_bodies.subprocess, "run", boom)
    result = {"mode": "search", "git_archaeology": {"git_log": [{"sha": "8a0b18fd", "message": "x"}]}}
    await attach_body_excerpts(result, tmp_path, {}, "why")
    assert "body_excerpt" not in result["git_archaeology"]["git_log"][0]


def test_the_compact_reply_quotes_one_body() -> None:
    result = _card_result(
        {"sha": "11111111", "message": "router registration first match", "body_excerpt": "first", "pr_number": 7},
        {"sha": "22222222", "message": "router registration first match again", "body_excerpt": "second"},
    )
    compact_unanswered(result, {"query": result["query"], "targets": ["src/router.ts"]})
    excerpts = [c.get("body_excerpt") for c in result["key_commits"]]
    assert excerpts == ["first", None]
    assert result["key_commits"][0]["pr_number"] == 7


def test_keep_one_excerpt_walks_cards_and_top_level() -> None:
    result = _card_result({"sha": "1", "body_excerpt": "a"}, {"sha": "2", "body_excerpt": "b"})
    result["git_archaeology"] = {"git_log": [{"sha": "3", "body_excerpt": "c"}]}
    keep_one_excerpt(result)
    assert result["git_archaeology"]["git_log"][0]["body_excerpt"] == "c"
    rows = result["target_context"]["src/router.ts"]["git_archaeology"]["file_commits"]
    assert all("body_excerpt" not in r for r in rows)
