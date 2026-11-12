"""What get_answer skips on a repeat, and what it runs while the model writes.

A keyless reply is kept for the process, so asking again does not re-run
retrieval; the episode check's git queries start before synthesis, so the
attach after it does not wait on them again.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from types import SimpleNamespace

import pytest

from repowise.core.precedent.store import TIER_GIT, Episode, EpisodeStore, default_store_path
from repowise.server.mcp_server.tool_answer import cache
from repowise.server.mcp_server.tool_answer.episodes import (
    _prefetch,
    attach_episode,
    attach_episode_sync,
)

_HITS = [
    {"page_id": "file_page:src/auth/service.py", "score": 3.0},
    {"page_id": "file_page:src/auth/middleware.py", "score": 2.9},
]
QUESTION = "how does the auth service check a token"


def _patch_retrieval(monkeypatch, answer_mod) -> list[str]:
    asked: list[str] = []

    async def _fake_retrieve(question, ctx):
        asked.append(question)
        return [dict(h) for h in _HITS]

    async def _fake_hydrate(hits, ctx, *, scope=None):
        for h in hits:
            h["target_path"] = h["page_id"].removeprefix("file_page:")
            h["title"] = h["target_path"]
            h["summary"] = h["snippet"] = ""
            h["page_type"] = "file_page"
        return hits

    monkeypatch.setattr(answer_mod, "_hybrid_retrieve", _fake_retrieve)
    monkeypatch.setattr(answer_mod, "_hydrate_hits", _fake_hydrate)
    return asked


class _Provider:
    provider_name = "mock"
    model_name = "mock-1"

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    async def generate(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(content=self.content)


def _without(payload: dict, *keys: str) -> dict:
    return {k: v for k, v in payload.items() if k not in keys}


# -- the keyless repeat ------------------------------------------------------


@pytest.mark.asyncio
async def test_a_repeated_keyless_question_is_served_without_retrieval(setup_mcp, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    monkeypatch.delenv("REPOWISE_ANSWER_DISABLE_CACHE", raising=False)
    asked = _patch_retrieval(monkeypatch, answer_mod)
    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: None)

    first = await get_answer(QUESTION)
    again = await get_answer(QUESTION)

    assert first["degraded"] == "no-llm-provider"
    assert len(asked) == 1, "the repeat must not run retrieval again"
    assert again["_meta"]["cached"] is True
    assert _without(again, "_meta") == _without(first, "_meta")
    assert again["_meta"]["degraded"] == "no-llm-provider"


@pytest.mark.asyncio
async def test_a_model_set_up_since_answers_instead_of_the_kept_reply(setup_mcp, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    monkeypatch.delenv("REPOWISE_ANSWER_DISABLE_CACHE", raising=False)
    _patch_retrieval(monkeypatch, answer_mod)
    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: None)
    await get_answer(QUESTION)

    provider = _Provider("The auth service checks the token in service.py.")
    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: provider)
    fresh = await get_answer(QUESTION)

    assert provider.calls == 1
    assert "degraded" not in fresh


@pytest.mark.asyncio
async def test_no_reply_is_kept_while_the_cache_is_off(setup_mcp, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    monkeypatch.setenv("REPOWISE_ANSWER_DISABLE_CACHE", "1")
    asked = _patch_retrieval(monkeypatch, answer_mod)
    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: None)

    await get_answer(QUESTION)
    again = await get_answer(QUESTION)

    assert len(asked) == 2
    assert not again["_meta"].get("cached")


def _kept(commit: str = "abc123") -> SimpleNamespace:
    repository = SimpleNamespace(head_commit=commit, name="demo")
    cache.remember_keyless_reply(
        {"answer": "x", "citations": ["src/a.py"], "_meta": {"degraded": "no-llm-provider"}},
        repository=repository,
        repo_id="r1",
        qhash="q1",
    )
    return repository


def test_a_kept_reply_is_dropped_once_the_index_moves(monkeypatch):
    monkeypatch.setattr(cache, "_KEYLESS_REPLIES", cache.OrderedDict())
    _kept("abc123")
    moved = SimpleNamespace(head_commit="def456", name="demo")

    served = cache.serve_keyless_reply(
        repository=moved, repo_id="r1", qhash="q1", exclude_spec=None, t0=time.perf_counter()
    )

    assert served is None
    assert not cache._KEYLESS_REPLIES


def test_a_kept_reply_naming_a_newly_excluded_file_is_dropped(monkeypatch):
    import pathspec

    monkeypatch.setattr(cache, "_KEYLESS_REPLIES", cache.OrderedDict())
    repository = _kept()
    spec = pathspec.PathSpec.from_lines("gitwildmatch", ["src/a.py"])

    served = cache.serve_keyless_reply(
        repository=repository, repo_id="r1", qhash="q1", exclude_spec=spec, t0=time.perf_counter()
    )

    assert served is None


def test_kept_replies_are_bounded(monkeypatch):
    monkeypatch.setattr(cache, "_KEYLESS_REPLIES", cache.OrderedDict())
    repository = SimpleNamespace(head_commit="abc", name="demo")
    for i in range(cache._KEYLESS_REPLIES_MAX + 5):
        cache.remember_keyless_reply(
            {"answer": str(i)}, repository=repository, repo_id="r1", qhash=f"q{i}"
        )

    assert len(cache._KEYLESS_REPLIES) == cache._KEYLESS_REPLIES_MAX
    assert ("r1", "q0") not in cache._KEYLESS_REPLIES


# -- the episode check runs beside synthesis ---------------------------------


def _git(root, *args):
    subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "checkout"
    (root / ".repowise").mkdir(parents=True)
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "first")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(root), capture_output=True, text=True, check=True
    ).stdout.strip()
    with EpisodeStore(default_store_path(root)) as store:
        store.append_tier(
            tier=TIER_GIT,
            episodes=[
                Episode(
                    tier=TIER_GIT,
                    kind="code_fix",
                    subject="token check",
                    body="The token check was moved into app.py after a timing bug.",
                    evidence=f"commit {head[:9]}",
                    nodes=("app.py",),
                    birth_commit=head,
                )
            ],
        )
    return root


@pytest.fixture
def git_calls(monkeypatch):
    from repowise.core.precedent import currency as mod

    calls: list[tuple] = []
    real = mod.commits_since

    def counted(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(mod, "commits_since", counted)
    return calls


def _answer() -> dict:
    return {
        "answer": "The token is checked in app.py.",
        "citations": ["app.py"],
        "confidence": "medium",
        "fallback_targets": ["app.py"],
        "retrieval": [],
    }


def _attach(payload: dict, repo) -> dict:
    attach_episode_sync(payload, question=QUESTION, repo_path=repo, repo_name="demo")
    return payload


def test_the_attach_reuses_the_verdicts_checked_before_synthesis(repo, git_calls):
    _prefetch(question=QUESTION, paths=["app.py"], repo_path=repo, repo_name="demo")
    assert len(git_calls) == 1

    payload = _attach(_answer(), repo)

    assert len(git_calls) == 1, "the attach must not ask git again"
    assert payload["episodes"][0]["scope"] == ["app.py"]


def test_the_same_episode_attaches_with_or_without_the_prefetch(repo, monkeypatch):
    from collections import OrderedDict

    from repowise.server.mcp_server.tool_answer import episodes

    _prefetch(question=QUESTION, paths=["app.py"], repo_path=repo, repo_name="demo")
    with_prefetch = _attach(_answer(), repo)
    monkeypatch.setattr(episodes, "_VERDICTS", OrderedDict())
    without = _attach(_answer(), repo)

    assert with_prefetch == without


def test_a_file_cited_outside_the_prefetch_is_still_checked(repo, git_calls):
    _prefetch(question=QUESTION, paths=["other.py"], repo_path=repo, repo_name="demo")
    assert git_calls == []

    payload = _attach(_answer(), repo)

    assert len(git_calls) == 1
    assert payload["episodes"][0]["scope"] == ["app.py"]


def test_a_repeated_answer_asks_git_once_until_head_moves(repo, git_calls):
    first = _attach(_answer(), repo)
    again = _attach(_answer(), repo)
    assert len(git_calls) == 1
    assert first == again

    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "touch app")
    moved = _attach(_answer(), repo)

    assert len(git_calls) == 2
    assert "episodes" not in moved, "the record's scope changed since, so it is not served"


@pytest.mark.asyncio
async def test_a_high_confidence_answer_does_not_wait_for_the_prefetch(repo):
    never: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    payload = {**_answer(), "confidence": "high"}

    await asyncio.wait_for(
        attach_episode(
            payload, question=QUESTION, repo_path=repo, repo_name="demo", prefetch=never
        ),
        timeout=5,
    )

    assert "episodes" not in payload


# -- per-stage timings -------------------------------------------------------


@pytest.mark.asyncio
async def test_a_synthesized_answer_logs_where_its_time_went(setup_mcp, monkeypatch, caplog):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    monkeypatch.setenv("REPOWISE_ANSWER_DISABLE_CACHE", "1")
    _patch_retrieval(monkeypatch, answer_mod)
    provider = _Provider("The auth service checks the token in service.py.")
    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: provider)

    with caplog.at_level(logging.INFO, logger="repowise.mcp.answer"):
        await get_answer(QUESTION)

    lines = [r.getMessage() for r in caplog.records if "get_answer stages" in r.getMessage()]
    assert len(lines) == 1
    for stage in ("lookup=", "retrieve=", "prepare=", "synthesize=", "grade=", "total="):
        assert stage in lines[0]
    assert "(synthesized)" in lines[0]
