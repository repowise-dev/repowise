"""get_why for a question naming no file, when no decision record matches.

Before, such a question got a redirect and nothing else, even when a comment in
the code stated the reason. Now the question is searched across the repository:
strong-marker rationale comments first, then the commits carrying its terms,
each held to the decision store's own relevance floor and served as evidence,
never as a decision. With neither, the answer says there is no recorded
rationale and invents none.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from repowise.server.mcp_server._code_rationale import repo_rationale
from repowise.server.mcp_server._query_shape import _is_path
from repowise.server.mcp_server.tool_why.archaeology import question_commits

_RETRY_SOURCE = (
    "def send(request):\n"
    "    # Retry on 429 here rather than in the shared client, because the\n"
    "    # client is shared across tenants and a global backoff would starve them.\n"
    "    return request\n"
    "\n"
    "# Helper for formatting tenant names.\n"
    "def fmt(name):\n"
    "    return name\n"
)


def _commit(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t.dev", "-c", "user.name=t", "commit", "-qm", message],
        cwd=repo,
        check=True,
    )


def _repo(tmp_path: Path, files: dict[str, str], messages: list[str]) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for i, message in enumerate(messages):
        for name, content in files.items():
            p = tmp_path / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content + f"\n# rev {i}\n", encoding="utf-8")
        _commit(tmp_path, message)
    return tmp_path


@pytest.mark.parametrize(
    ("query", "is_path"),
    [
        ("upload/download split", False),
        ("why is upload/download split into two calls?", False),
        ("client/server boundary", False),
        ("how does the plan/apply flow work", False),
        ("why is retrieval hybrid", False),
        ("src/app/main.py", True),
        ("packages/core/", True),
        ("app/services/upload.py", True),
        ("main.go", True),
    ],
)
def test_natural_language_takes_precedence_over_a_slash(query, is_path):
    assert _is_path(query) is is_path


def test_repo_rationale_serves_the_comment_that_states_the_reason(tmp_path):
    repo = _repo(tmp_path, {"svc/client.py": _RETRY_SOURCE}, ["init"])

    out = repo_rationale(repo, "why do we retry on 429 in send rather than the shared client")

    assert [r["path"] for r in out] == ["svc/client.py"]
    assert out[0]["lines"][0] == 2
    assert "shared across tenants" in out[0]["comment"]


def test_repo_rationale_serves_nothing_off_topic_or_markerless(tmp_path):
    repo = _repo(tmp_path, {"svc/client.py": _RETRY_SOURCE}, ["init"])

    # Off topic: no comment carries the question's weight.
    assert repo_rationale(repo, "why is the cache keyed on the snapshot id") == []
    # The marker-less "Helper for formatting tenant names" comment is never
    # harvested, however well the question matches it.
    assert repo_rationale(repo, "why a helper for formatting tenant names") == []


def test_repo_rationale_outside_a_git_repo_is_empty(tmp_path):
    (tmp_path / "a.py").write_text(_RETRY_SOURCE, encoding="utf-8")
    assert repo_rationale(tmp_path, "why retry on 429 rather than the shared client") == []


def test_question_commits_keeps_only_subjects_clearing_the_floor(tmp_path):
    repo = _repo(
        tmp_path,
        {"a.py": "X = 1"},
        [
            "Initial import",
            "Use node buffer instead of safe-buffer",
            "Tidy formatting",
        ],
    )

    out = question_commits(repo, "why use node buffer instead of safe-buffer")

    assert [r["message"] for r in out] == ["Use node buffer instead of safe-buffer"]
    assert question_commits(repo, "why is the scheduler capped at forty jobs") == []


# --- Through the tool ---------------------------------------------------------


async def _seed_decision(session, rid: str, *, title: str, decision: str) -> None:
    from repowise.core.persistence.models import DecisionRecord

    session.add(
        DecisionRecord(
            id="d1",
            repository_id=rid,
            title=title,
            status="proposed",
            context="ctx",
            decision=decision,
            rationale="why",
            affected_files_json=json.dumps([]),
            affected_modules_json=json.dumps([]),
            evidence_commits_json=json.dumps(["d1"]),
            evidence_file=None,
            source="pr",
            confidence=0.8,
            staleness_score=0.0,
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_rationale_is_served_labelled_when_no_decision_matches(setup_mcp, tmp_path):
    from repowise.server.mcp_server import get_why

    _repo(tmp_path, {"svc/client.py": _RETRY_SOURCE}, ["init"])

    result = await get_why("why do we retry on 429 in send rather than the shared client")

    assert result["decisions"] == []
    rows = result["code_rationale"]
    assert rows[0]["path"] == "svc/client.py"
    assert rows[0]["provenance"] == "extracted_rationale"
    assert result["answer_basis"] == "rationale"
    assert "try_instead" not in result
    assert "not as the recorded reason" in result["reason"]


@pytest.mark.asyncio
async def test_rationale_is_not_served_when_a_decision_matches(session, setup_mcp, tmp_path):
    from repowise.server.mcp_server import get_why

    _repo(tmp_path, {"svc/client.py": _RETRY_SOURCE}, ["init"])
    await _seed_decision(
        session,
        setup_mcp,
        title="Retry 429 in send, not in the shared client",
        decision="retry on 429 in send rather than the shared client",
    )

    result = await get_why("why do we retry on 429 in send rather than the shared client")

    assert [d["id"] for d in result["decisions"]] == ["d1"]
    assert "code_rationale" not in result


@pytest.mark.asyncio
async def test_no_recorded_rationale_invents_no_reason(setup_mcp, tmp_path):
    from repowise.server.mcp_server import get_why

    _repo(tmp_path, {"svc/client.py": _RETRY_SOURCE}, ["init"])

    result = await get_why("why is the scheduler capped at forty jobs")

    assert result["decisions"] == []
    assert "code_rationale" not in result
    assert "git_archaeology" not in result
    assert "answer_basis" not in result
    assert result["reason"].startswith("No recorded rationale")


@pytest.mark.asyncio
async def test_commits_are_the_fallback_when_no_comment_answers(setup_mcp, tmp_path):
    from repowise.server.mcp_server import get_why

    _repo(
        tmp_path,
        {"a.py": "X = 1"},
        ["Initial import", "Use node buffer instead of safe-buffer"],
    )

    result = await get_why("why use node buffer instead of safe-buffer")

    assert "code_rationale" not in result
    commits = result["git_archaeology"]["git_log"]
    assert [c["message"] for c in commits] == ["Use node buffer instead of safe-buffer"]
    assert result["git_archaeology"]["summary"].startswith("No recorded rationale")
    assert result["answer_basis"] == "archaeology"
