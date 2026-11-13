"""``get_answer``'s ``no-llm-provider`` degraded path names what a model would add,
and an index no model wrote prose for names the command that adds it (#3105).

The line names a signed-in agent CLI on a paid plan when one is found, and falls
back to a provider key or a local model otherwise.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.core.index_scope import FreeRoute, answers_upgrade_line
from repowise.core.providers.llm import subscription
from repowise.server.mcp_server import _meta
from repowise.server.mcp_server.tool_answer.answer import _degraded_payload

HITS = [{"target_path": "pkg/mod.py", "title": "mod", "summary": "s", "score": 1.0}]

CLAUDE_MAX = subscription.CliAuth("Claude max plan: uses your plan quota", plan_quota=True)


def _repo(tmp_path: Path, docs_mode: str | None) -> Path:
    (tmp_path / ".repowise").mkdir()
    state = {"docs_mode": docs_mode} if docs_mode else {}
    (tmp_path / ".repowise" / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return tmp_path


def _signed_in(monkeypatch: pytest.MonkeyPatch, auth: subscription.CliAuth | None) -> None:
    """Claude Code signed in with *auth*, and no key or configured provider."""
    monkeypatch.setattr(
        subscription, "agent_cli_auth", lambda name: auth if name == "claude_cli" else None
    )
    monkeypatch.setattr(
        "repowise.core.providers.llm.resolve.credentialed_providers", lambda _repo: ()
    )
    monkeypatch.delenv("REPOWISE_PROVIDER", raising=False)


async def _payload(reason: str, repo: Path | None = None) -> dict:
    repository = SimpleNamespace(id=None, local_path=str(repo)) if repo else None
    return await _degraded_payload(
        reason=reason,
        note="DEGRADED",
        question="how does mod work",
        hits=HITS,
        fallback_targets=["pkg/mod.py"],
        repository=repository,
        t0=0.0,
    )


async def _note(repo: Path) -> str:
    return (await _meta.keyless_note_meta(str(repo)))["keyless_note"]


async def test_a_no_prose_index_names_generate_and_the_restart(tmp_path: Path) -> None:
    note = await _note(_repo(tmp_path, "deterministic"))
    assert "`repowise generate`" in note
    assert "no re-index" in note
    assert "a provider key or a local model" in note
    assert "restart the MCP server" in note


async def test_an_index_only_repo_names_update_full(tmp_path: Path) -> None:
    note = await _note(_repo(tmp_path, "none"))
    assert "`repowise update --full`" in note
    assert "no re-index" not in note


async def test_model_written_prose_needs_only_the_provider(tmp_path: Path) -> None:
    note = await _note(_repo(tmp_path, "llm"))
    assert "repowise generate" not in note
    assert "repowise config set provider" in note
    assert "restart the MCP server" in note


async def test_a_signed_in_plan_is_named_with_its_quota(tmp_path, monkeypatch) -> None:
    _signed_in(monkeypatch, CLAUDE_MAX)
    note = await _note(_repo(tmp_path, "deterministic"))
    assert note == (
        "answers and docs: `repowise generate --provider claude_cli` uses your Claude Code "
        "sign-in and plan quota; then restart the MCP server"
    )


async def test_a_signed_in_plan_on_a_prose_index_names_the_setting(tmp_path, monkeypatch):
    _signed_in(monkeypatch, CLAUDE_MAX)
    note = await _note(_repo(tmp_path, "llm"))
    assert "`repowise config set provider claude_cli` uses your Claude Code sign-in" in note


@pytest.mark.parametrize(
    "auth",
    [
        subscription.CliAuth("API key: billed per token"),
        subscription.CliAuth("signed in without a paid Claude plan", offer=False),
        None,
    ],
)
async def test_a_login_without_a_plan_quota_is_never_named(tmp_path, monkeypatch, auth) -> None:
    _signed_in(monkeypatch, auth)
    note = await _note(_repo(tmp_path, "deterministic"))
    assert "claude_cli" not in note
    assert "a provider key or a local model" in note


def test_a_configured_key_is_not_upgraded_to_a_cli(tmp_path, monkeypatch) -> None:
    _signed_in(monkeypatch, CLAUDE_MAX)
    monkeypatch.setattr(
        "repowise.core.providers.llm.resolve.credentialed_providers",
        lambda _repo: ("anthropic",),
    )
    assert subscription.detect_free_route(_repo(tmp_path, "deterministic")) is None


def test_minutes_follow_the_unwritten_pages() -> None:
    route = FreeRoute("claude_cli", "Claude Code")
    assert subscription.route_minutes(route, 0) is route
    timed = subscription.route_minutes(route, 17)
    assert timed is not None
    assert timed.minutes == subscription.subscription_minutes("claude_cli", 17)
    assert f"plan quota, about {timed.minutes} minute" in timed.uses()
    assert "about" not in timed.uses(timed=False)


async def test_overview_names_the_upgrade_once_per_process(tmp_path: Path) -> None:
    repo = str(_repo(tmp_path, "deterministic"))
    first = await _meta.prose_upgrade_meta(repo)
    assert first == {"keyless_note": answers_upgrade_line(_meta.read_index_scope(repo))}
    assert await _meta.prose_upgrade_meta(repo) == {}


async def test_overview_line_is_absent_once_a_model_wrote_the_prose(tmp_path: Path) -> None:
    assert await _meta.prose_upgrade_meta(str(_repo(tmp_path, "llm"))) == {}
    assert await _meta.prose_upgrade_meta(None) == {}


async def test_overview_and_answer_share_one_line(tmp_path: Path) -> None:
    repo = str(_repo(tmp_path, "deterministic"))
    answer = await _meta.keyless_note_meta(repo)
    assert await _meta.prose_upgrade_meta(repo) == answer


async def test_every_weak_no_llm_provider_response_carries_the_note(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "deterministic")
    first = await _payload("no-llm-provider", repo)
    second = await _payload("no-llm-provider", repo)
    assert "repowise generate" in first["_meta"]["keyless_note"]
    assert second["_meta"]["keyless_note"] == first["_meta"]["keyless_note"]


async def test_other_degraded_reasons_never_carry_the_note(tmp_path: Path) -> None:
    payload = await _payload("synthesis-failed", _repo(tmp_path, "deterministic"))
    assert "keyless_note" not in payload["_meta"]


async def test_a_medium_reply_with_a_body_does_not_carry_the_note(tmp_path, monkeypatch):
    from repowise.server.mcp_server.tool_answer import degraded

    body = {"path": "pkg/mod.py", "name": "mod", "source": "def mod(): ...", "lines": [1, 1]}
    monkeypatch.setattr(degraded, "_build_symbol_bodies", lambda *_a, **_k: ([body], True))
    monkeypatch.setattr(degraded, "_degraded_confidence", lambda *_a, **_k: "medium")

    async def _next(*_a, **_k):
        return "read it"

    monkeypatch.setattr(degraded, "_degraded_next_action", _next)
    payload = await _payload("no-llm-provider", _repo(tmp_path, "deterministic"))
    assert payload["confidence"] == "medium"
    assert "keyless_note" not in payload["_meta"]


def test_only_the_first_response_waits_for_the_probe(monkeypatch) -> None:
    import threading

    release = threading.Event()
    monkeypatch.setattr(_meta, "_detect_route", lambda _p: release.wait(5) and None)
    assert _meta._probed_route("slow", 0.05) is None
    joined: list[float] = []
    real_join = threading.Thread.join
    monkeypatch.setattr(
        threading.Thread, "join", lambda self, t=None: joined.append(t) or real_join(self, 0)
    )
    assert _meta._probed_route("slow", 0.05) is None
    assert joined == []
    release.set()


async def test_a_slow_generate_count_never_holds_the_reply(tmp_path, monkeypatch) -> None:
    """The count plans decision lanes on a worker thread, which once never
    returned (a Windows DLL load). The line goes out without minutes past the
    wait, and the count that kept running times a later reply."""
    import asyncio

    from repowise.core.generation import scope

    release = asyncio.Event()
    started: list[str] = []

    async def _slow_count(_session, repo_id, _path):
        started.append(repo_id)
        await release.wait()
        return 17

    monkeypatch.setattr(_meta, "_CALLS_WAIT_S", 0.05)
    monkeypatch.setattr(_meta, "_detect_route", lambda _p: FreeRoute("claude_cli", "Claude Code"))
    monkeypatch.setattr(scope, "generate_model_calls", _slow_count)
    monkeypatch.setattr("repowise.core.persistence.get_session", lambda _f: contextlib.nullcontext())
    repo = str(tmp_path)

    first = await asyncio.wait_for(_meta._upgrade_route(repo, object(), "r1"), 2)
    assert first == FreeRoute("claude_cli", "Claude Code")
    # Only the reply that started the count waits for it.
    monkeypatch.setattr(_meta, "_CALLS_WAIT_S", 30)
    second = await asyncio.wait_for(_meta._upgrade_route(repo, object(), "r1"), 2)
    assert second == first and started == ["r1"]

    release.set()
    await _meta._generate_counts["r1"]
    timed = await _meta._upgrade_route(repo, object(), "r1")
    assert timed == subscription.route_minutes(first, 17) and started == ["r1"]
