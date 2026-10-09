"""Shared base for LLM providers that drive a coding agent's CLI.

``claude_cli``, ``codex_cli`` and ``opencode`` generate pages by running the
user's already-authenticated agent CLI in a one-shot, non-interactive mode, so
no API key is involved. What they share lives here: finding the executable,
spawning it without a shell, sending the prompt on stdin (prompts are too large
for argv), the timeout and kill, killing the child when the caller cancels,
closing the subprocess transport before the loop shuts down, a per-event-loop
semaphore sized by ``resolve_concurrency``, the error tail, model label
handling and cost booking.

A subclass supplies its flags (``build_command``), how to read the CLI's output
(``parse_output``) and, where the CLI has them, model options and reasoning
modes. Anything that differs between the CLIs stays in the subclass.

Security: ``asyncio.create_subprocess_exec`` (no shell), and model names reach
argv, so they are validated against a safe character set unless a subclass
opts out.

Subscription seats are rate limited per account and each call is a full CLI
process: serializing turns a 100-page wiki into an hour or more, and too much
fan-out trips the account limit. The default of 4 comes from
``_concurrency.DEFAULT_CONCURRENCY``; the per-provider env var is a true
override, not a clamp, because only the operator knows their plan's ceiling.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import shutil
from abc import abstractmethod
from collections.abc import Iterator
from contextlib import AbstractContextManager
from typing import Any, ClassVar

import structlog

from repowise.core.providers.llm._concurrency import resolve_concurrency
from repowise.core.providers.llm.base import (
    BaseProvider,
    CacheHint,
    GeneratedResponse,
    ProviderError,
    record_generation_cost,
)
from repowise.core.rate_limiter import RateLimiter
from repowise.core.reasoning import ReasoningMode

log = structlog.get_logger(__name__)

# A process spawn plus a full agent turn on a prompt that can carry a lot of
# file context. Generous, because too low is not a slow page but no page.
EXEC_TIMEOUT_SECONDS = 600

_MODEL_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/\-]*$")


def tail(text: str, max_chars: int = 2_000) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def stderr_tail(stderr: str) -> str:
    """The stderr excerpt kept in a response's usage payload."""
    return tail(stderr, 1_000)


def iter_jsonl(stdout: str) -> Iterator[Any]:
    """Decoded JSON lines, skipping blank and undecodable ones."""
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def validate_model_name(provider: str, model: str) -> None:
    if not _MODEL_NAME_RE.match(model):
        raise ProviderError(
            provider,
            f"Invalid model name {model!r}. Model names may only contain "
            "alphanumeric characters, dots, hyphens, underscores, and forward slashes.",
        )


def normalize_model(model: str | None, provider: str, default: str | None) -> str | None:
    """Return the native model slug, or *default* when *model* names none.

    Accepts the persisted ``<provider>/<slug>`` label as well as a bare slug,
    so a value read back out of config.yaml round-trips.
    """
    prefix = f"{provider}/"
    if not model or model in (provider, f"{prefix}default"):
        return default
    return model.removeprefix(prefix) or default


def model_label(native: str | None, provider: str) -> str:
    """Persisted attribution label: prefixed so cost estimation prices it at zero."""
    return f"{provider}/{native}" if native else f"{provider}/default"


async def close_subprocess_transport(proc: asyncio.subprocess.Process) -> None:
    """Close asyncio's subprocess transport before the event loop shuts down."""
    transport = getattr(proc, "_transport", None)
    close = getattr(transport, "close", None)
    if not callable(close):
        return
    with contextlib.suppress(Exception):
        close()
    await asyncio.sleep(0)


async def _kill(proc: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
    with contextlib.suppress(ProcessLookupError):
        await proc.wait()


class AgentCliProvider(BaseProvider):
    """Base for providers backed by a local agent CLI. See the module docstring."""

    provider_name: ClassVar[str]  # type: ignore[misc]
    executable_name: ClassVar[str]
    # How errors name the invocation, e.g. ``codex exec``.
    command_label: ClassVar[str]
    concurrency_env: ClassVar[str]
    not_found_message: ClassVar[str]
    # Raised when the executable vanishes between lookup and spawn.
    spawn_not_found_message: ClassVar[str | None] = None
    # None: the CLI's own config picks the model.
    default_model: ClassVar[str | None] = None
    validates_model_name: ClassVar[bool] = True
    # Only claude_cli books llm_costs rows today; the others never have.
    records_cost: ClassVar[bool] = False
    error_tail_chars: ClassVar[int] = 2_000

    # A process spawn plus a full agent turn: the floor is tens of seconds, so
    # an interactive caller must budget in minutes or it cancels every call
    # (#1119). Stays under the exec timeout so the caller gives up first and
    # the error names the real cause.
    interactive_timeout_s: float = 180.0

    def __init__(self, model: str | None = None, rate_limiter: RateLimiter | None = None) -> None:
        executable = self.resolve_executable()
        if not executable:
            raise ProviderError(self.provider_name, self.not_found_message)
        self._executable = executable
        self._model = normalize_model(model, self.provider_name, self.default_model)
        if self._model is not None and self.validates_model_name:
            validate_model_name(self.provider_name, self._model)
        self._rate_limiter = rate_limiter
        self._semaphore: asyncio.Semaphore | None = None
        self._semaphore_loop: asyncio.AbstractEventLoop | None = None

    @property
    def model_name(self) -> str:
        return model_label(self._model, self.provider_name)

    def resolve_executable(self) -> str | None:
        return shutil.which(self.executable_name)

    # -- subclass hooks -------------------------------------------------------

    @abstractmethod
    def build_command(self, system_prompt: str, reasoning: ReasoningMode) -> list[str]:
        """Return argv for one generation; the prompt goes on stdin."""

    @abstractmethod
    def parse_output(self, stdout: str, stderr: str) -> GeneratedResponse:
        """Build the response from a successful run's stdout."""

    def build_stdin(self, system_prompt: str, user_prompt: str) -> str:
        return user_prompt

    def working_dir(self) -> AbstractContextManager[str | None]:
        return contextlib.nullcontext()

    def subprocess_env(self) -> dict[str, str] | None:
        return None

    def exec_timeout_seconds(self) -> float:
        return EXEC_TIMEOUT_SECONDS

    def exit_error(self, returncode: int | None, stdout: str, stderr: str) -> ProviderError:
        return ProviderError(
            self.provider_name,
            self.error_message(stderr, stdout, returncode),
            status_code=returncode,
        )

    def stdout_error(self, stdout: str) -> str | None:
        """An error message the CLI reports in structured stdout, if any."""
        return None

    # -- shared machinery -----------------------------------------------------

    def error_message(self, stderr: str, stdout: str, returncode: int | None) -> str:
        for candidate in (tail(stderr, self.error_tail_chars), tail(stdout, self.error_tail_chars)):
            # Structured stdout can carry request details; never echo it raw.
            if candidate and not candidate.lstrip().startswith(("{", "[")):
                return candidate
        return self.stdout_error(stdout) or f"{self.command_label} exited with {returncode}"

    def _get_semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._semaphore_loop is not loop:
            self._semaphore = asyncio.Semaphore(
                resolve_concurrency(self.concurrency_env, self.provider_name)
            )
            self._semaphore_loop = loop
        return self._semaphore  # type: ignore[return-value]

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        request_id: str | None = None,
        reasoning: ReasoningMode = "auto",
        cache_hints: tuple[CacheHint, ...] = (),
    ) -> GeneratedResponse:
        # temperature and max_tokens have no CLI equivalent; the base contract
        # says clip rather than raise, so they are dropped.
        if self._rate_limiter:
            await self._rate_limiter.acquire(estimated_tokens=max_tokens)

        # Off the loop: building can load a model catalog by subprocess.
        cmd = await asyncio.to_thread(self.build_command, system_prompt, reasoning)
        prompt = self.build_stdin(system_prompt, user_prompt)
        log.debug(
            f"{self.provider_name}.generate.start", model=self.model_name, request_id=request_id
        )

        async with self._get_semaphore():
            with self.working_dir() as cwd:
                returncode, stdout, stderr = await self._run(cmd, prompt, cwd)

        if returncode != 0:
            raise self.exit_error(returncode, stdout, stderr)

        response = self.parse_output(stdout, stderr)
        log.debug(
            f"{self.provider_name}.generate.done",
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cached_tokens=response.cached_tokens,
            request_id=request_id,
        )
        if self.records_cost:
            await record_generation_cost(
                getattr(self, "_cost_tracker", None), model=self.model_name, result=response
            )
        return response

    async def _run(
        self, cmd: list[str], prompt: str, cwd: str | None
    ) -> tuple[int | None, str, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=self.subprocess_env(),
            )
        except FileNotFoundError as exc:
            message = self.spawn_not_found_message or self.not_found_message
            raise ProviderError(self.provider_name, message) from exc

        timeout = self.exec_timeout_seconds()
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(prompt.encode("utf-8")), timeout=timeout
            )
        except asyncio.CancelledError:
            # The interactive caller gives up first; the CLI must not outlive it.
            await _kill(proc)
            raise
        except TimeoutError as exc:
            await _kill(proc)
            raise ProviderError(
                self.provider_name, f"{self.command_label} timed out after {timeout} seconds."
            ) from exc
        finally:
            await close_subprocess_transport(proc)

        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
        return proc.returncode, stdout, stderr
