"""Unit tests for ClaudeCliProvider.

All tests mock the Claude Code subprocess; no real ``claude`` calls are made.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from repowise.core.providers.llm.base import GeneratedResponse, ProviderError
from repowise.core.providers.llm.claude_cli import (
    ClaudeCliProvider,
    _model_label,
    _normalize_model,
    _parse_result,
)


class FakeProcess:
    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        on_communicate: Any | None = None,
        transport: Any | None = None,
    ) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._on_communicate = on_communicate
        self._transport = transport
        self.stdin_input: bytes | None = None
        self.killed = False

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        self.stdin_input = input
        if self._on_communicate is not None:
            await self._on_communicate()
        return self._stdout.encode("utf-8"), self._stderr.encode("utf-8")

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode


class FakeTransport:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _success_json(text: str = "OK", **overrides: Any) -> str:
    payload: dict[str, Any] = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "stop_reason": "end_turn",
        "num_turns": 1,
        "total_cost_usd": 0.0179915,
        "result": text,
        "usage": {
            "input_tokens": 120,
            "output_tokens": 40,
            "cache_read_input_tokens": 30,
            "cache_creation_input_tokens": 7955,
        },
    }
    payload.update(overrides)
    return json.dumps(payload) + "\n"


@pytest.fixture
def claude_on_path(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/claude" if cmd == "claude" else None)
    return "/usr/bin/claude"


# ---------------------------------------------------------------------------
# Model naming
# ---------------------------------------------------------------------------


def test_provider_name_and_default_model(claude_on_path):
    provider = ClaudeCliProvider()
    assert provider.provider_name == "claude_cli"
    assert provider.model_name == "claude_cli/claude-haiku-4-5"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (None, "claude-haiku-4-5"),
        ("claude-sonnet-4-6", "claude-sonnet-4-6"),
        # A persisted label must round-trip rather than become "claude_cli/claude_cli/...".
        ("claude_cli/claude-sonnet-4-6", "claude-sonnet-4-6"),
        ("claude_cli/default", "claude-haiku-4-5"),
    ],
)
def test_model_normalization_round_trips(given, expected):
    assert _normalize_model(given) == expected


def test_model_label_is_prefixed(claude_on_path):
    assert _model_label("claude-opus-4-6") == "claude_cli/claude-opus-4-6"
    assert ClaudeCliProvider(model="claude_cli/claude-opus-4-6").model_name == (
        "claude_cli/claude-opus-4-6"
    )


def test_invalid_model_name_is_rejected(claude_on_path):
    """Model names reach argv, so they are validated against a safe charset."""
    with pytest.raises(ProviderError, match="Invalid model name"):
        ClaudeCliProvider(model="claude; rm -rf /")


def test_missing_cli_raises(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _cmd: None)
    with pytest.raises(ProviderError, match="Claude Code CLI not found"):
        ClaudeCliProvider()


def test_reasoning_modes_match_the_claude_cli_effort_flag(claude_on_path):
    assert ClaudeCliProvider().supported_reasoning_modes() == (
        "auto",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    )


def test_available_model_options_are_labelled(claude_on_path):
    options = ClaudeCliProvider().available_model_options()
    assert [o.model for o in options] == [
        "claude_cli/claude-haiku-4-5",
        "claude_cli/claude-sonnet-4-6",
        "claude_cli/claude-opus-4-6",
    ]
    assert sum(1 for o in options if o.recommended) == 1
    assert all(
        o.reasoning_modes == ClaudeCliProvider().supported_reasoning_modes() for o in options
    )


# ---------------------------------------------------------------------------
# generate()
# ---------------------------------------------------------------------------


async def test_generate_success(claude_on_path, monkeypatch):
    proc = FakeProcess(stdout=_success_json("Hello from Claude"), transport=FakeTransport())
    captured: dict[str, Any] = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    result = await ClaudeCliProvider(model="claude-sonnet-4-6").generate(
        "system rules", "user context"
    )

    assert isinstance(result, GeneratedResponse)
    assert result.content == "Hello from Claude"
    # The prompt total, not the uncached remainder: Claude Code splits a prompt
    # across ``input_tokens``, ``cache_read_input_tokens`` and
    # ``cache_creation_input_tokens``.
    assert result.input_tokens == 120 + 30 + 7955
    assert result.usage["uncached_input_tokens"] == 120
    assert result.output_tokens == 40
    # cached_tokens is the *read* half; creations are recorded separately.
    assert result.cached_tokens == 30
    assert result.usage["cache_creation_input_tokens"] == 7955
    assert result.stop_reason == "end_turn"
    assert result.usage["source"] == "claude_cli"
    assert result.usage["model"] == "claude_cli/claude-sonnet-4-6"

    # The user prompt goes on stdin, not argv (prompts are large).
    assert proc.stdin_input == b"user context"

    args = list(captured["args"])
    assert args[0] == claude_on_path
    assert "-p" in args
    assert args[args.index("--model") + 1] == "claude-sonnet-4-6"
    system_prompt = args[args.index("--system-prompt") + 1]
    assert system_prompt.startswith("system rules\n\n")
    assert "You have no tools available" in system_prompt
    assert "single response" in system_prompt
    assert args[args.index("--max-turns") + 1] == "1"
    assert "--strict-mcp-config" in args
    assert args[args.index("--tools") + 1] == ""
    assert "--disallowed-tools" not in args
    # --bare would force ANTHROPIC_API_KEY and never read the OAuth login,
    # defeating the point of this provider.
    assert "--bare" not in args


async def test_generate_runs_outside_the_repo(claude_on_path, monkeypatch, tmp_path):
    """cwd must not be a repo, or CLAUDE.md is auto-discovered into every prompt."""
    captured: dict[str, Any] = {}

    async def fake_exec(*args, **kwargs):
        captured["kwargs"] = kwargs
        return FakeProcess(stdout=_success_json())

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    await ClaudeCliProvider().generate("sys", "user")

    cwd = captured["kwargs"]["cwd"]
    assert cwd is not None
    assert "repowise-claude-cli-" in str(cwd)
    assert not Path(cwd).exists(), "the per-call scratch directory leaked"


@pytest.mark.parametrize("reasoning", ["low", "medium", "high", "xhigh", "max"])
async def test_supported_reasoning_is_passed_as_effort(claude_on_path, monkeypatch, reasoning):
    captured: dict[str, Any] = {}

    async def fake_exec(*args, **_kwargs):
        captured["args"] = args
        return FakeProcess(stdout=_success_json())

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    result = await ClaudeCliProvider().generate("sys", "user", reasoning=reasoning)
    args = list(captured["args"])
    assert result.content == "OK"
    assert args[args.index("--effort") + 1] == reasoning


async def test_unsupported_reasoning_warns_but_does_not_raise(claude_on_path, monkeypatch):
    """Raising here would kill a whole docs run over a flag the CLI cannot express."""

    captured: dict[str, Any] = {}

    async def fake_exec(*args, **_kwargs):
        captured["args"] = args
        return FakeProcess(stdout=_success_json())

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    result = await ClaudeCliProvider().generate("sys", "user", reasoning="none")
    assert result.content == "OK"
    assert "--effort" not in captured["args"]


async def test_nonzero_exit_raises_with_stderr(claude_on_path, monkeypatch):
    async def fake_exec(*_args, **_kwargs):
        return FakeProcess(returncode=1, stderr="credit balance too low")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(ProviderError, match="credit balance too low"):
        await ClaudeCliProvider().generate("sys", "user")


async def test_nonzero_exit_surfaces_json_error_and_http_status(claude_on_path, monkeypatch):
    """Claude writes API failures to stdout as JSON and often leaves stderr empty."""

    async def fake_exec(*_args, **_kwargs):
        return FakeProcess(
            returncode=1,
            stdout=_success_json(
                "There's an issue with the selected model.",
                subtype="error_during_execution",
                is_error=True,
                api_error_status=404,
            ),
        )

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(ProviderError, match="selected model") as caught:
        await ClaudeCliProvider().generate("sys", "user")

    assert caught.value.status_code == 404


async def test_error_subtype_raises(claude_on_path, monkeypatch):
    async def fake_exec(*_args, **_kwargs):
        return FakeProcess(
            stdout=_success_json(
                "", subtype="error_max_turns", is_error=True, api_error_status=None
            )
        )

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(ProviderError, match="error_max_turns"):
        await ClaudeCliProvider().generate("sys", "user")


async def test_empty_result_raises(claude_on_path, monkeypatch):
    async def fake_exec(*_args, **_kwargs):
        return FakeProcess(stdout=_success_json("   "))

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(ProviderError, match="no result text"):
        await ClaudeCliProvider().generate("sys", "user")


async def test_timeout_kills_process_and_raises(claude_on_path, monkeypatch):
    monkeypatch.setattr(
        "repowise.core.providers.llm.claude_cli._EXEC_TIMEOUT_SECONDS",
        0.01,
    )

    async def on_communicate() -> None:
        await asyncio.sleep(1)

    proc = FakeProcess(stdout="", on_communicate=on_communicate)

    async def fake_exec(*_args, **_kwargs):
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    with pytest.raises(ProviderError, match="timed out"):
        await ClaudeCliProvider().generate("sys", "user")

    assert proc.killed


async def test_cancellation_kills_process(claude_on_path, monkeypatch):
    """The interactive caller cancels at 180s; the 600s CLI must not keep running."""
    started = asyncio.Event()

    async def on_communicate() -> None:
        started.set()
        await asyncio.Event().wait()

    proc = FakeProcess(on_communicate=on_communicate)

    async def fake_exec(*_args, **_kwargs):
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    task = asyncio.create_task(ClaudeCliProvider().generate("sys", "user"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert proc.killed


async def test_transport_is_closed(claude_on_path, monkeypatch):
    transport = FakeTransport()

    async def fake_exec(*_args, **_kwargs):
        return FakeProcess(stdout=_success_json(), transport=transport)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    await ClaudeCliProvider().generate("sys", "user")
    assert transport.closed


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------


def test_parse_result_tolerates_leading_noise():
    noisy = 'warning: something\n{"subtype": "success", "result": "hi"}\n'
    assert _parse_result(noisy)["result"] == "hi"


def test_parse_result_rejects_unparseable_output():
    with pytest.raises(ProviderError, match="could not parse"):
        _parse_result("not json at all")


def test_parse_result_rejects_empty_output():
    with pytest.raises(ProviderError, match="no output"):
        _parse_result("   ")


async def test_missing_usage_is_flagged_estimated(claude_on_path, monkeypatch):
    async def fake_exec(*_args, **_kwargs):
        return FakeProcess(stdout=json.dumps({"subtype": "success", "result": "hi"}) + "\n")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    result = await ClaudeCliProvider().generate("sys", "user")
    assert result.usage["estimated"] is True
    assert result.input_tokens == 0
    assert result.output_tokens == 0


# ---------------------------------------------------------------------------
# Token accounting and the cost ledger (#2267)
# ---------------------------------------------------------------------------


def _cached_page_json(*, uncached: int, read: int, created: int) -> str:
    """A ``claude -p`` payload shaped like a real page generation.

    Claude Code splits the prompt across the three usage fields: a page prompt
    carries a large stable prefix, so the bulk of it arrives as a cache write on
    the first page of a type and a cache read on every later one, leaving a
    couple of uncached tokens. Only reading ``input_tokens`` recorded 2 against
    a ~20k-token page, and ``repowise status`` summed a whole wiki to 0.
    """
    return _success_json(
        "page prose",
        usage={
            "input_tokens": uncached,
            "output_tokens": 2465,
            "cache_read_input_tokens": read,
            "cache_creation_input_tokens": created,
        },
    )


@pytest.mark.parametrize(
    ("uncached", "read", "created"),
    [
        # First page of a page type: the prompt is written to the cache.
        (2, 0, 19311),
        # Later pages of the same type: that prefix is read back.
        (2, 19311, 0),
        # A short prompt that fits entirely in the uncached remainder.
        (120, 0, 0),
    ],
)
async def test_input_tokens_is_the_prompt_total(
    claude_on_path, monkeypatch, uncached, read, created
):
    async def fake_exec(*_args, **_kwargs):
        return FakeProcess(stdout=_cached_page_json(uncached=uncached, read=read, created=created))

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    result = await ClaudeCliProvider().generate("sys", "user")

    assert result.input_tokens == uncached + read + created
    assert result.usage["uncached_input_tokens"] == uncached
    # The read half stays its own number: cached_tokens means "served from
    # cache", and the report and the UI both read it that way.
    assert result.cached_tokens == read
    assert result.usage["cache_creation_input_tokens"] == created
    assert result.output_tokens == 2465


class _RecordingTracker:
    """Minimal stand-in that captures what the provider books."""

    def __init__(self) -> None:
        self.operation = "doc_generation"
        self.calls: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> float:
        self.calls.append(kwargs)
        return 0.0


async def test_cost_ledger_row_carries_the_prompt_total(claude_on_path, monkeypatch):
    """No row was written at all, so a claude_cli wiki produced an empty ledger.

    ``repowise costs`` printed "No cost records found" and the run report's
    dollar figure stayed $0.000 because the tokens it multiplies by a rate were
    never recorded.
    """

    async def fake_exec(*_args, **_kwargs):
        return FakeProcess(stdout=_cached_page_json(uncached=2, read=0, created=19311))

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    provider = ClaudeCliProvider(model="claude-opus-5")
    tracker = _RecordingTracker()
    provider._cost_tracker = tracker

    await provider.generate("sys", "user")

    assert len(tracker.calls) == 1
    booked = tracker.calls[0]
    # The prefixed label is what prices the call at zero; the bare slug would
    # bill a subscription seat at API rates.
    assert booked["model"] == "claude_cli/claude-opus-5"
    assert booked["input_tokens"] == 19313
    assert booked["output_tokens"] == 2465
    assert booked["operation"] == "doc_generation"


async def test_cost_ledger_row_is_priced_at_zero_for_a_seat(claude_on_path, monkeypatch):
    """The ledger shows the run's token volume and $0.00 of API spend."""

    async def fake_exec(*_args, **_kwargs):
        return FakeProcess(stdout=_cached_page_json(uncached=2, read=0, created=19311))

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    from repowise.core.generation.cost_tracker import CostTracker, get_model_pricing

    tracker = CostTracker()
    provider = ClaudeCliProvider(model="claude-opus-5")
    provider._cost_tracker = tracker

    await provider.generate("sys", "user")

    assert get_model_pricing(provider.model_name) == {"input": 0.0, "output": 0.0}
    assert tracker.session_tokens == 19313 + 2465
    assert tracker.session_cost == 0.0


async def test_a_failing_cost_tracker_does_not_break_generation(claude_on_path, monkeypatch):
    """Cost telemetry is non-critical: a tracker that raises must not lose the page."""

    async def fake_exec(*_args, **_kwargs):
        return FakeProcess(stdout=_success_json("still produced"))

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    class _ExplodingTracker:
        operation = "doc_generation"

        async def record(self, **_kwargs: Any) -> float:
            raise RuntimeError("ledger unavailable")

    provider = ClaudeCliProvider()
    provider._cost_tracker = _ExplodingTracker()

    result = await provider.generate("sys", "user")

    assert result.content == "still produced"


async def test_no_cost_tracker_is_a_no_op(claude_on_path, monkeypatch):
    """The MCP and server paths construct the provider without a tracker."""

    async def fake_exec(*_args, **_kwargs):
        return FakeProcess(stdout=_success_json("no tracker"))

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    provider = ClaudeCliProvider()
    assert not hasattr(provider, "_cost_tracker")

    result = await provider.generate("sys", "user")

    assert result.content == "no tracker"
    assert result.input_tokens == 120 + 30 + 7955


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_registered_as_keyless_provider():
    from repowise.core.providers.llm.registry import KEYLESS_PROVIDERS, provider_is_usable

    assert "claude_cli" in KEYLESS_PROVIDERS
    # Keyless: never rejected for a "missing" key.
    assert provider_is_usable("claude_cli", lambda _name: None) is True


def test_subscription_usage_is_priced_at_zero():
    """A seat is not API spend; the bare `claude` prefixes would price it wrongly."""
    from repowise.core.cost_estimator.pricing import _lookup_cost

    assert _lookup_cost("claude_cli/claude-haiku-4-5") == (0.0, 0.0)
    assert _lookup_cost("claude_cli/claude-opus-4-6") == (0.0, 0.0)
    # The keyed API path is unaffected.
    assert _lookup_cost("claude-haiku-4-5") != (0.0, 0.0)


def test_resolves_through_the_registry(claude_on_path):
    from repowise.core.providers.llm.registry import get_provider

    provider = get_provider("claude_cli")
    assert provider.provider_name == "claude_cli"
    assert provider.model_name == "claude_cli/claude-haiku-4-5"
