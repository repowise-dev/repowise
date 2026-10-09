"""Contract every ``AgentCliProvider`` subclass must meet.

A new agent backend joins by adding one ``Backend`` entry to ``BACKENDS``.
The subprocess is faked; no real CLI runs.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

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
    # CLI output whose answer is the argument, with usage that parses to ``tokens``.
    success_stdout: Callable[[str], str]
    tokens: tuple[int, int, int]  # parsed (input, output, cached)
    books_cost: bool
    # Flag that points the CLI at the repo; None means a per-call scratch cwd.
    repo_flag: str | None
    env: dict[str, str] | None  # entries the subprocess env must carry


def _jsonl(*events: dict[str, Any]) -> str:
    return "".join(json.dumps(event) + "\n" for event in events)


def _claude_stdout(text: str) -> str:
    usage = {
        "input_tokens": 100,
        "output_tokens": 40,
        "cache_read_input_tokens": 30,
        "cache_creation_input_tokens": 10,
    }
    return _jsonl({"subtype": "success", "is_error": False, "result": text, "usage": usage})


def _codex_stdout(text: str) -> str:
    return _jsonl(
        {"type": "item.completed", "item": {"type": "agent_message", "text": text}},
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 120, "cached_input_tokens": 30, "output_tokens": 40},
        },
    )


def _opencode_stdout(text: str) -> str:
    return _jsonl(
        {"type": "text", "part": {"text": text}},
        {
            "type": "step_finish",
            "part": {"tokens": {"input": 80, "output": 10, "cache": {"read": 20, "write": 0}}},
        },
    )


BACKENDS = [
    Backend(
        ClaudeCliProvider,
        "claude-sonnet-4-6",
        _claude_stdout,
        # Prompt total: uncached + cache read + cache write.
        tokens=(140, 40, 30),
        books_cost=True,
        repo_flag=None,
        env=None,
    ),
    Backend(
        CodexCliProvider,
        "gpt-5.5",
        _codex_stdout,
        tokens=(120, 40, 30),
        books_cost=False,
        repo_flag="--cd",
        env=None,
    ),
    Backend(
        OpenCodeProvider,
        "deepseek/deepseek-v4-pro",
        _opencode_stdout,
        tokens=(80, 10, 20),
        books_cost=False,
        repo_flag="--dir",
        env={"OPENCODE_DISABLE_PROJECT_CONFIG": "true"},
    ),
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
    assert (result.input_tokens, result.output_tokens, result.cached_tokens) == backend.tokens
    args = calls[0]["args"]
    assert args[0].endswith(backend.cls.executable_name)
    assert args[args.index("--model") + 1] == backend.model
    # Prompts are large, so they travel on stdin, never argv.
    assert calls[0]["kwargs"]["stdin"] == asyncio.subprocess.PIPE
    assert b"user context" in calls[0]["proc"].stdin_input
    assert not any("user context" in a for a in args)
    assert calls[0]["proc"]._transport.closed


async def test_runs_against_the_repo_or_a_scratch_dir(backend, monkeypatch, tmp_path):
    calls = _fake_exec(monkeypatch, lambda: FakeProcess(stdout=backend.success_stdout("ok")))

    await _make(backend, tmp_path).generate("sys", "user")

    args, cwd = calls[0]["args"], calls[0]["kwargs"]["cwd"]
    if backend.repo_flag is None:
        # A neutral per-call directory, removed afterwards.
        assert cwd is not None and Path(cwd) != tmp_path.resolve()
        assert not Path(cwd).exists()
    else:
        assert args[args.index(backend.repo_flag) + 1] == str(tmp_path.resolve())
        assert cwd is None


async def test_subprocess_env(backend, monkeypatch, tmp_path):
    calls = _fake_exec(monkeypatch, lambda: FakeProcess(stdout=backend.success_stdout("ok")))

    await _make(backend, tmp_path).generate("sys", "user")

    env = calls[0]["kwargs"]["env"]
    if backend.env is None:
        assert env is None  # inherits the parent environment unchanged
    else:
        assert backend.env.items() <= env.items()
    if backend.cls is OpenCodeProvider:
        permissions = json.loads(env["OPENCODE_CONFIG_CONTENT"])["permission"]
        assert permissions["edit"] == permissions["bash"] == "deny"


async def test_cost_booking(backend, monkeypatch, tmp_path):
    _fake_exec(monkeypatch, lambda: FakeProcess(stdout=backend.success_stdout("ok")))
    tracker = MagicMock(operation="doc_generation")
    tracker.record = AsyncMock(return_value=0.0)
    provider = _make(backend, tmp_path)
    provider._cost_tracker = tracker

    await provider.generate("sys", "user")

    if backend.books_cost:
        tracker.record.assert_awaited_once_with(
            model=provider.model_name,
            input_tokens=backend.tokens[0],
            output_tokens=backend.tokens[1],
            operation="doc_generation",
            file_path=None,
        )
    else:
        tracker.record.assert_not_awaited()


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
