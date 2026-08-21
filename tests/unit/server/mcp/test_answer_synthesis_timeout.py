"""get_answer's synthesis budget and its failure reporting.

Issue #1119: the budget was a hardcoded 30s. Providers that shell out to a
coding-agent CLI or drive a local model take longer than that on every call, so
synthesis was cancelled every single time and the user got an empty payload
whose only clue was the string "TimeoutError". Retrieval looked healthy, which
sent the diagnosis toward the index instead of the provider.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from repowise.core.providers.llm.registry import _BUILTIN_PROVIDERS, get_provider
from repowise.server.mcp_server.tool_answer import synthesis as synthesis_module
from repowise.server.mcp_server.tool_answer.answer import _degraded_payload
from repowise.server.mcp_server.tool_answer.config import (
    _SYNTHESIS_MAX_TOKENS,
    _SYNTHESIS_TEMPERATURE,
)
from repowise.server.mcp_server.tool_answer.synthesis import (
    _FALLBACK_TIMEOUT_S,
    _MAX_TIMEOUT_S,
    _TIMEOUT_ENV,
    _synthesis_failure_note,
    _synthesis_timeout,
    synthesize,
)


class _Provider:
    """Stand-in for a resolved provider; only the read attributes matter."""

    def __init__(self, budget=None, name="testprov", model="test-model"):
        if budget is not None:
            self.interactive_timeout_s = budget
        self.provider_name = name
        self.model_name = model


@pytest.fixture(autouse=True)
def _no_override(monkeypatch):
    monkeypatch.delenv(_TIMEOUT_ENV, raising=False)


# --- budget selection ------------------------------------------------------


def test_budget_comes_from_the_provider():
    assert _synthesis_timeout(_Provider(budget=180.0)) == 180.0


def test_provider_without_a_budget_gets_the_fallback():
    """A runtime-registered provider need not subclass BaseProvider."""
    assert _synthesis_timeout(_Provider(budget=None)) == _FALLBACK_TIMEOUT_S


def test_env_override_beats_the_provider_default(monkeypatch):
    monkeypatch.setenv(_TIMEOUT_ENV, "5")
    assert _synthesis_timeout(_Provider(budget=180.0)) == 5.0


def test_env_override_may_lower_the_budget_to_fail_fast(monkeypatch):
    """An agent harness would rather give up early than block on a slow box."""
    monkeypatch.setenv(_TIMEOUT_ENV, "2.5")
    assert _synthesis_timeout(_Provider(budget=120.0)) == 2.5


@pytest.mark.parametrize("bad", ["", "   ", "abc", "30s", "0", "-1", "nan_but_not", "nan", "-inf"])
def test_unusable_override_falls_back_to_the_provider_default(monkeypatch, bad):
    monkeypatch.setenv(_TIMEOUT_ENV, bad)
    assert _synthesis_timeout(_Provider(budget=180.0)) == 180.0


@pytest.mark.parametrize("huge", ["inf", "1e400", "999999"])
def test_an_effectively_unbounded_override_is_clamped(monkeypatch, huge):
    """asyncio.wait_for accepts inf and then never fires, disabling the guard.

    An unbounded budget cannot produce an answer either: the MCP client gives
    up first, and get_answer blocks until someone kills the server.
    """
    monkeypatch.setenv(_TIMEOUT_ENV, huge)
    assert _synthesis_timeout(_Provider(budget=180.0)) == _MAX_TIMEOUT_S


@pytest.mark.parametrize("junk", ["abc", True, False, object(), -5, 0, float("nan")], ids=repr)
def test_a_provider_declaring_junk_falls_back_instead_of_raising(junk):
    """The attribute belongs to a class that need not subclass BaseProvider.

    float("abc") would raise out of a call the caller does not guard, turning
    a degraded answer into a failed tool call.
    """
    assert _synthesis_timeout(_Provider(budget=junk)) == _FALLBACK_TIMEOUT_S


@pytest.mark.parametrize("excessive", [99_999.0, float("inf")])
def test_a_provider_budget_above_the_ceiling_is_clamped(excessive):
    assert _synthesis_timeout(_Provider(budget=excessive)) == _MAX_TIMEOUT_S


# --- the regression itself -------------------------------------------------


@pytest.mark.parametrize("name", ["codex_cli", "opencode"])
def test_agent_cli_providers_get_more_than_the_old_thirty_seconds(name):
    """#1119: 30s cancelled a codex/opencode turn before it could ever return."""
    cls = _load_provider_class(name)
    assert cls.interactive_timeout_s > 30.0


@pytest.mark.parametrize("name", ["ollama", "litellm"])
def test_local_and_proxied_providers_get_more_than_thirty_seconds(name):
    """Generation speed is the user's own hardware, or an unknown backend."""
    cls = _load_provider_class(name)
    assert cls.interactive_timeout_s > 30.0


def test_every_builtin_provider_has_a_deliberate_budget():
    """Adding a provider forces a choice about how slow it is allowed to be.

    Inheriting the remote-API default silently is what this table prevents:
    that is exactly what codex_cli did, and it is why #1119 existed.
    """
    expected = {
        "anthropic": 60.0,
        "openai": 60.0,
        "openrouter": 60.0,
        "gemini": 60.0,
        "deepseek": 60.0,
        "kimi": 60.0,
        "edenai": 60.0,
        "mock": 60.0,
        "ollama": 120.0,
        "litellm": 120.0,
        "codex_cli": 180.0,
        "opencode": 180.0,
    }
    assert set(expected) == set(_BUILTIN_PROVIDERS), (
        "a provider was added or removed; decide its interactive budget here"
    )
    actual = {name: _load_provider_class(name).interactive_timeout_s for name in expected}
    assert actual == expected


def test_agent_cli_budget_stays_under_its_own_subprocess_ceiling():
    """The caller must give up before the subprocess does, or the error is wrong."""
    from repowise.core.providers.llm import codex_cli, opencode

    assert codex_cli.CodexCliProvider.interactive_timeout_s < codex_cli._EXEC_TIMEOUT_SECONDS
    assert opencode.OpenCodeProvider.interactive_timeout_s < opencode._EXEC_TIMEOUT_SECONDS


def _load_provider_class(name: str):
    import importlib

    module_path, class_name = _BUILTIN_PROVIDERS[name]
    return getattr(importlib.import_module(module_path), class_name)


# --- failure reporting -----------------------------------------------------


def test_timeout_note_names_the_budget_the_provider_and_the_escape_hatch():
    note = _synthesis_failure_note(
        TimeoutError(),
        _Provider(budget=180.0, name="codex_cli", model="gpt-5.5"),
        180.0,
        timed_out=True,
    )
    assert "180s" in note
    assert "codex_cli" in note and "gpt-5.5" in note
    assert _TIMEOUT_ENV in note
    assert note.startswith("DEGRADED:")


def test_timeout_note_warns_that_the_client_has_its_own_timeout():
    """Raising ours past the client's just swaps a good error for a worse one."""
    note = _synthesis_failure_note(TimeoutError(), _Provider(), 180.0, timed_out=True)
    assert "client" in note.lower()


def test_timeout_note_stops_advertising_the_knob_once_it_is_maxed():
    """At the ceiling, "raise it" is advice that cannot be followed."""
    note = _synthesis_failure_note(TimeoutError(), _Provider(), _MAX_TIMEOUT_S, timed_out=True)
    assert _TIMEOUT_ENV not in note
    assert "faster provider" in note


def test_non_timeout_note_carries_the_real_error_not_just_its_class():
    """A 401 and a rate limit used to be indistinguishable from a slow model."""
    note = _synthesis_failure_note(
        ValueError("invalid x-api-key"), _Provider(name="anthropic"), 60.0, timed_out=False
    )
    assert "ValueError" in note
    assert "invalid x-api-key" in note
    assert _TIMEOUT_ENV not in note  # raising the budget would not help here


def test_note_collapses_and_truncates_a_sprawling_provider_error():
    note = _synthesis_failure_note(
        RuntimeError("line one\n\n  line two " * 200), _Provider(), 60.0, timed_out=False
    )
    assert "\n" not in note
    assert len(note) < 400


def test_note_survives_a_provider_missing_its_identity_attributes():
    class _Bare:
        pass

    note = _synthesis_failure_note(TimeoutError(), _Bare(), 60.0, timed_out=True)
    assert "provider=?" in note and "model=?" in note


def test_note_renders_a_whole_number_budget_without_a_trailing_zero():
    note = _synthesis_failure_note(TimeoutError(), _Provider(), 30.0, timed_out=True)
    assert "30s" in note and "30.0s" not in note


def test_real_provider_instance_reports_its_class_budget():
    """The attribute survives instantiation through the registry."""
    provider = get_provider("mock", with_rate_limiter=False)
    assert _synthesis_timeout(provider) == 60.0


# --- the wiring: the budget must reach the actual call ---------------------
#
# The tests above pin the ingredients. These pin the dish: revert synthesize()
# to a hardcoded timeout and they fail. Without them the class attributes are
# decoration and #1119 can come back while everything still looks fixed.


class _SlowProvider:
    """Takes `duration` seconds to answer, however long the caller allows."""

    provider_name = "slowprov"
    model_name = "slow-model"

    def __init__(self, duration: float, budget: float):
        self._duration = duration
        self.interactive_timeout_s = budget
        self.calls: list[dict] = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        await asyncio.sleep(self._duration)
        return SimpleNamespace(content="  the answer  ")


async def test_the_call_is_budgeted_with_the_providers_own_number(monkeypatch):
    """A codex-class provider must not be cancelled at the remote-API default."""
    seen: dict = {}
    real_wait_for = asyncio.wait_for

    async def _spy(coro, timeout):
        seen["timeout"] = timeout
        return await real_wait_for(coro, timeout)

    monkeypatch.setattr(synthesis_module.asyncio, "wait_for", _spy)

    provider = _SlowProvider(duration=0, budget=180.0)
    answer, note = await synthesize(provider, "sys", "user")

    assert seen["timeout"] == 180.0, "the flat 30s is back"
    assert answer == "the answer"
    assert note is None


async def test_a_provider_slower_than_its_budget_degrades_with_a_useful_note():
    """The #1119 shape end to end, at 1/1000th the wall clock."""
    provider = _SlowProvider(duration=5.0, budget=0.01)

    answer, note = await synthesize(provider, "sys", "user")

    assert answer == ""
    assert note is not None
    assert "slowprov" in note and "slow-model" in note
    assert _TIMEOUT_ENV in note
    assert "budget" in note


async def test_the_env_override_reaches_the_call(monkeypatch):
    seen: dict = {}
    real_wait_for = asyncio.wait_for

    async def _spy(coro, timeout):
        seen["timeout"] = timeout
        return await real_wait_for(coro, timeout)

    monkeypatch.setattr(synthesis_module.asyncio, "wait_for", _spy)
    monkeypatch.setenv(_TIMEOUT_ENV, "7.5")

    await synthesize(_SlowProvider(duration=0, budget=180.0), "sys", "user")
    assert seen["timeout"] == 7.5


async def test_prompts_and_sampling_are_passed_through_unchanged():
    provider = _SlowProvider(duration=0, budget=30.0)
    await synthesize(provider, "the system prompt", "the user prompt")

    assert provider.calls[0] == {
        "system_prompt": "the system prompt",
        "user_prompt": "the user prompt",
        "max_tokens": _SYNTHESIS_MAX_TOKENS,
        "temperature": _SYNTHESIS_TEMPERATURE,
    }


async def test_a_non_timeout_failure_is_not_reported_as_a_budget_overrun():
    """A socket timeout IS a builtin TimeoutError, so sniffing the type lies."""

    class _Broken(_SlowProvider):
        async def generate(self, **kwargs):
            raise TimeoutError("connection timed out after 3s")

    answer, note = await synthesize(_Broken(duration=0, budget=180.0), "sys", "user")

    assert answer == ""
    assert "connection timed out after 3s" in note
    assert "exceeded its" not in note
    assert _TIMEOUT_ENV not in note


async def test_an_empty_completion_is_reported_rather_than_shipped_blank():
    """A call can succeed and still produce nothing usable.

    Measured on ollama: a local reasoning model spent all 1024 tokens on hidden
    thinking and returned an empty content block. Nothing raised, so this used
    to reach the agent as an ordinary answer that happened to be blank.
    """

    class _Empty(_SlowProvider):
        async def generate(self, **kwargs):
            return SimpleNamespace(content=None, stop_reason=None)

    answer, note = await synthesize(_Empty(duration=0, budget=30.0), "sys", "user")
    assert answer == ""
    assert note is not None
    assert "empty completion" in note
    assert "slowprov" in note


async def test_a_reasoning_model_that_burned_its_budget_is_named_as_such():
    """ "Empty" and "spent 1024 tokens thinking" need different remedies."""

    class _AllThinking(_SlowProvider):
        async def generate(self, **kwargs):
            return SimpleNamespace(content="", stop_reason="max_tokens")

    answer, note = await synthesize(_AllThinking(duration=0, budget=30.0), "sys", "user")
    assert answer == ""
    assert str(_SYNTHESIS_MAX_TOKENS) in note
    assert "reasoning" in note.lower()


async def test_whitespace_only_completion_counts_as_empty():
    class _Blank(_SlowProvider):
        async def generate(self, **kwargs):
            return SimpleNamespace(content="   \n  ", stop_reason="end_turn")

    answer, note = await synthesize(_Blank(duration=0, budget=30.0), "sys", "user")
    assert answer == ""
    assert note is not None


# --- the degraded payload both failure modes share -------------------------

HITS = [{"target_path": "pkg/mod.py", "title": "mod", "summary": "s", "score": 1.0}]


async def _payload(reason="synthesis-failed", note="DEGRADED: boom"):
    return await _degraded_payload(
        reason=reason,
        note=note,
        question="how does mod work",
        hits=HITS,
        fallback_targets=["pkg/mod.py"],
        repository=None,
        t0=0.0,
    )


async def test_degraded_reason_is_mirrored_into_meta():
    """The failure path set only the top-level key, so _meta watchers missed it."""
    payload = await _payload()
    assert payload["degraded"] == "synthesis-failed"
    assert payload["_meta"]["degraded"] == "synthesis-failed"


@pytest.mark.parametrize("reason", ["no-llm-provider", "synthesis-failed"])
async def test_both_failure_modes_return_the_same_payload_shape(reason):
    """An agent should not have to diff key sets to tell why synthesis is missing."""
    assert set(await _payload(reason=reason)) == {
        "answer",
        "citations",
        "confidence",
        "retrieval_quality",
        "degraded",
        "fallback_targets",
        "retrieval",
        "candidates",
        "best_guesses",
        "next_action_hint",
        "note",
        "_meta",
    }


async def test_degraded_payload_still_hands_back_usable_retrieval():
    """Retrieval succeeded; losing it too would waste the work already done."""
    payload = await _payload()
    assert payload["confidence"] == "low"
    assert payload["fallback_targets"] == ["pkg/mod.py"]
    assert len(payload["retrieval"]) == 1
    assert payload["retrieval"][0]["path"] == "pkg/mod.py"


async def test_degraded_answer_describes_the_payload_instead_of_being_empty():
    """An empty ``answer`` beside working retrieval reads as a failed call.

    The field is the first thing a reader looks at, so leaving it blank while
    ``retrieval``/``candidates`` are populated invites throwing the whole
    result away. It must name what survived and where to find it.
    """
    payload = await _payload()
    answer = payload["answer"]
    assert answer, "degraded answer must not be empty"
    assert "synthesis-failed" in answer, "the reason belongs in the visible field"
    for field in ("retrieval", "fallback_targets", "candidates"):
        assert field in answer, f"{field} is populated but never mentioned"


async def test_degraded_answer_does_not_promise_hits_it_does_not_have():
    """The other direction: no hits must not produce a 'this is usable' claim.

    A sentence assembled from a template rather than from the payload would
    advertise ranked hits on an empty retrieval, which is the failure mode that
    would make the wording worse than the blank field it replaced.
    """
    payload = await _degraded_payload(
        reason="no-llm-provider",
        note="DEGRADED",
        question="how does mod work",
        hits=[],
        fallback_targets=[],
        repository=None,
        t0=0.0,
    )
    answer = payload["answer"]
    assert answer
    assert "matched nothing" in answer
    assert "ranked hit" not in answer
