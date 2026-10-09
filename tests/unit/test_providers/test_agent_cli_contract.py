"""Contract every ``AgentCliProvider`` subclass must meet.

A new agent backend joins by adding one ``Backend`` entry to ``BACKENDS``.
The subprocess is faked; no real CLI runs.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from repowise.core.providers.llm.agent_cli import AgentCliProvider
from repowise.core.providers.llm.base import GeneratedResponse, ProviderError
from repowise.core.providers.llm.claude_cli import ClaudeCliProvider
from repowise.core.providers.llm.codex_cli import CodexCliProvider
from repowise.core.providers.llm.opencode import OpenCodeProvider
from repowise.core.providers.llm.registry import _BUILTIN_PROVIDERS


@dataclass(frozen=True)
class Backend:
    cls: type[AgentCliProvider]
    model: str  # a native slug the CLI receives after ``--model``
    success_stdout: Callable[[str], str]  # CLI output whose answer is the argument


def _claude_stdout(text: str) -> str:
    return json.dumps({"subtype": "success", "is_error": False, "result": text}) + "\n"


def _codex_stdout(text: str) -> str:
    return (
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}})
        + "\n"
    )


def _opencode_stdout(text: str) -> str:
    return json.dumps({"type": "text", "part": {"text": text}}) + "\n"


BACKENDS = [
    Backend(ClaudeCliProvider, "claude-sonnet-4-6", _claude_stdout),
    Backend(CodexCliProvider, "gpt-5.5", _codex_stdout),
    Backend(OpenCodeProvider, "deepseek/deepseek-v4-pro", _opencode_stdout),
]


class FakeProcess:
    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        on_communicate: Any | None = None,
    ) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._on_communicate = on_communicate
        self._transport = FakeTransport()
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


@pytest.fixture(params=BACKENDS, ids=lambda b: b.cls.provider_name)
def backend(request, monkeypatch, tmp_path) -> Backend:
    exe = str(tmp_path / "bin" / request.param.cls.executable_name)
    monkeypatch.setattr(
        "shutil.which", lambda cmd: exe if cmd == request.param.cls.executable_name else None
    )
    return request.param


def _make(backend: Backend, tmp_path) -> AgentCliProvider:
    # Every agent CLI provider is built with these kwargs by the registry.
    return backend.cls(model=backend.model, repo_path=tmp_path)


def _fake_exec(monkeypatch, proc_factory: Callable[[], FakeProcess]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def fake_exec(*args: str, **kwargs: Any) -> FakeProcess:
        proc = proc_factory()
        calls.append({"args": list(args), "kwargs": kwargs, "proc": proc})
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    return calls


def test_names_are_consistent(backend, tmp_path):
    provider = _make(backend, tmp_path)
    name = provider.provider_name
    assert _BUILTIN_PROVIDERS[name][1] == backend.cls.__name__
    assert provider.model_name == f"{name}/{backend.model}"
    # A persisted label round-trips instead of gaining a second prefix.
    assert backend.cls(model=provider.model_name, repo_path=tmp_path).model_name == (
        provider.model_name
    )


def test_missing_executable_raises(backend, monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _cmd: None)
    with pytest.raises(ProviderError) as caught:
        _make(backend, tmp_path)
    assert caught.value.provider == backend.cls.provider_name


async def test_command_stdin_and_output(backend, monkeypatch, tmp_path):
    calls = _fake_exec(monkeypatch, lambda: FakeProcess(stdout=backend.success_stdout("answer")))

    result = await _make(backend, tmp_path).generate("system rules", "user context")

    assert isinstance(result, GeneratedResponse)
    assert result.content == "answer"
    args = calls[0]["args"]
    assert args[0].endswith(backend.cls.executable_name)
    assert args[args.index("--model") + 1] == backend.model
    # Prompts are large, so they travel on stdin, never argv.
    assert calls[0]["kwargs"]["stdin"] == asyncio.subprocess.PIPE
    assert b"user context" in calls[0]["proc"].stdin_input
    assert not any("user context" in a for a in args)
    assert calls[0]["proc"]._transport.closed


async def test_nonzero_exit_raises_with_stderr_tail(backend, monkeypatch, tmp_path):
    stderr = "x" * 5_000 + " not logged in"
    _fake_exec(monkeypatch, lambda: FakeProcess(returncode=1, stderr=stderr))

    with pytest.raises(ProviderError) as caught:
        await _make(backend, tmp_path).generate("sys", "user")

    message = str(caught.value)
    assert caught.value.provider == backend.cls.provider_name
    assert message.endswith("not logged in")
    assert len(message) < len(stderr)


async def test_timeout_kills_the_process(backend, monkeypatch, tmp_path):
    monkeypatch.setattr(backend.cls, "exec_timeout_seconds", lambda _self: 0.01)

    async def hang() -> None:
        await asyncio.sleep(1)

    calls = _fake_exec(monkeypatch, lambda: FakeProcess(on_communicate=hang))

    with pytest.raises(ProviderError, match="timed out"):
        await _make(backend, tmp_path).generate("sys", "user")

    assert calls[0]["proc"].killed
    assert calls[0]["proc"]._transport.closed


async def test_cancellation_kills_the_process(backend, monkeypatch, tmp_path):
    started = asyncio.Event()

    async def block() -> None:
        started.set()
        await asyncio.Event().wait()

    calls = _fake_exec(monkeypatch, lambda: FakeProcess(on_communicate=block))

    task = asyncio.create_task(_make(backend, tmp_path).generate("sys", "user"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls[0]["proc"].killed


async def test_semaphore_bounds_concurrency(backend, monkeypatch, tmp_path):
    monkeypatch.setenv(backend.cls.concurrency_env, "2")
    active = 0
    max_active = 0

    async def track() -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1

    _fake_exec(
        monkeypatch,
        lambda: FakeProcess(stdout=backend.success_stdout("ok"), on_communicate=track),
    )
    provider = _make(backend, tmp_path)

    await asyncio.gather(*[provider.generate("sys", f"user {i}") for i in range(5)])

    assert max_active == 2
