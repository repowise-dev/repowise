"""Claude CLI provider for repowise.

This provider delegates generation to the authenticated local Claude Code CLI
via ``claude -p``. It is intended for users with a Claude subscription already
configured by ``claude`` OAuth login and does not require an Anthropic API key.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import structlog

from repowise.core.providers.llm.base import (
    BaseProvider,
    CacheHint,
    GeneratedResponse,
    ProviderError,
    ProviderModelOption,
)
from repowise.core.rate_limiter import RateLimiter
from repowise.core.reasoning import ReasoningMode, normalize_reasoning

log = structlog.get_logger(__name__)

_DEFAULT_MODEL_LABEL = "claude_cli/default"
_EXEC_TIMEOUT_SECONDS = 600

# Efforts the Claude CLI accepts for --effort. Reasoning modes outside this
# set are mapped in _claude_effort_for_reasoning.
_CLI_EFFORTS = ("low", "medium", "high", "xhigh", "max")

# Model aliases the CLI resolves itself. Offered as options; any other slug
# (e.g. a dated model id) is passed through to --model verbatim.
_KNOWN_MODEL_ALIASES = ("sonnet", "opus", "haiku")

# Never inherited by the subprocess. With ANTHROPIC_API_KEY in the
# environment the Claude CLI switches to per-token API billing and, on a key
# it has not seen before, blocks on an interactive trust prompt — a headless
# `claude -p` then hangs until the exec timeout. This provider exists for
# subscription OAuth; the `anthropic` provider is the API-key path.
_SCRUBBED_ENV_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def _subprocess_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in _SCRUBBED_ENV_VARS}


async def _close_subprocess_transport(proc: asyncio.subprocess.Process) -> None:
    """Close asyncio's subprocess transport before the event loop shuts down."""

    transport = getattr(proc, "_transport", None)
    close = getattr(transport, "close", None)
    if not callable(close):
        return
    with contextlib.suppress(Exception):
        close()
    await asyncio.sleep(0)


def _resolve_claude_executable() -> str | None:
    """Return the executable path used to launch Claude Code, or None."""

    return shutil.which("claude")


def _normalize_model(model: str | None) -> str | None:
    """Return the native Claude model slug, or None to use CLI config."""
    if not model:
        return None
    if model == _DEFAULT_MODEL_LABEL:
        return None
    if model.startswith("claude_cli/"):
        suffix = model.removeprefix("claude_cli/")
        return suffix or None
    return model


def _model_label(model: str | None) -> str:
    """Return the persisted attribution label for a Claude CLI model."""
    native = _normalize_model(model)
    return f"claude_cli/{native}" if native else _DEFAULT_MODEL_LABEL


def _claude_effort_for_reasoning(reasoning: ReasoningMode) -> str | None:
    """Map a repowise reasoning mode onto a --effort value, or None to omit."""
    mode = normalize_reasoning(reasoning)
    if mode == "auto":
        return None
    if mode in ("off", "none", "minimal"):
        return "low"
    return mode  # low | medium | high | xhigh | max — all CLI-native


_SUPPORTED_REASONING_MODES: tuple[ReasoningMode, ...] = (
    "auto",
    "off",
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)


def _tail(text: str, max_chars: int = 2_000) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _error_message(stderr: str, stdout: str, returncode: int) -> str:
    for candidate in (_tail(stderr), _tail(stdout)):
        if not candidate:
            continue
        if candidate.lstrip().startswith(("{", "[")):
            continue
        return candidate
    return f"claude -p exited with {returncode}"


def _parse_result(stdout: str) -> tuple[str, dict[str, Any], bool, str | None]:
    """Parse ``claude -p --output-format json`` output.

    Returns (content, usage, is_error, error_detail). The CLI prints one JSON
    object; anything else on stdout (warnings from wrappers) is skipped by
    scanning lines for the result object.
    """
    payload: dict[str, Any] | None = None
    try:
        candidate = json.loads(stdout)
        if isinstance(candidate, dict):
            payload = candidate
    except json.JSONDecodeError:
        for raw_line in stdout.splitlines():
            line = raw_line.strip()
            if not line.startswith("{"):
                continue
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict) and candidate.get("type") == "result":
                payload = candidate
                break

    if payload is None:
        return "", {}, True, "no JSON result object found in claude output"

    content = payload.get("result")
    usage = payload.get("usage")
    is_error = bool(payload.get("is_error"))
    subtype = payload.get("subtype")
    error_detail = None
    if is_error or (subtype and subtype != "success"):
        is_error = True
        error_detail = (
            content
            if isinstance(content, str) and content
            else f"claude reported subtype={subtype!r}"
        )
    return (
        content if isinstance(content, str) else "",
        usage if isinstance(usage, dict) else {},
        is_error,
        error_detail,
    )


class ClaudeCliProvider(BaseProvider):
    """LLM provider backed by ``claude -p``.

    Args:
        model: Optional native Claude model slug or alias (``opus``,
            ``sonnet``, ``claude-opus-5``). If omitted, the Claude CLI config
            chooses the model. Persisted labels like ``claude_cli/opus`` are
            accepted and normalized before calling the CLI.
        repo_path: Working directory for the subprocess, so relative paths in
            prompts resolve against the indexed repo.
        rate_limiter: Accepted for interface consistency, but the provider
            serializes subprocess calls by default.
    """

    # A process spawn plus a full model turn: budget in minutes, mirroring
    # codex_cli's rationale (#1119).
    interactive_timeout_s: float = 180.0

    def __init__(
        self,
        model: str | None = None,
        repo_path: str | Path | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        claude_cmd = _resolve_claude_executable()
        if not claude_cmd:
            raise ProviderError(
                "claude_cli",
                "Claude Code CLI not found. Install it with: "
                "npm install -g @anthropic-ai/claude-code",
            )
        self._claude_cmd = claude_cmd
        self._model = _normalize_model(model)
        self._repo_path = (
            Path(repo_path).resolve() if repo_path is not None else Path.cwd().resolve()
        )
        self._rate_limiter = rate_limiter
        self._subprocess_semaphore: asyncio.Semaphore | None = None
        self._semaphore_loop: asyncio.AbstractEventLoop | None = None

    @property
    def provider_name(self) -> str:
        return "claude_cli"

    @property
    def model_name(self) -> str:
        return _model_label(self._model)

    def supported_reasoning_modes(self) -> tuple[ReasoningMode, ...]:
        return _SUPPORTED_REASONING_MODES

    def available_model_options(self) -> tuple[ProviderModelOption, ...]:
        options = [
            ProviderModelOption(
                model=_DEFAULT_MODEL_LABEL,
                label="Claude CLI default",
                reasoning_modes=_SUPPORTED_REASONING_MODES,
                recommended=True,
                source="local",
                notes="uses Claude CLI config",
            )
        ]
        for alias in _KNOWN_MODEL_ALIASES:
            options.append(
                ProviderModelOption(
                    model=_model_label(alias),
                    label=alias,
                    reasoning_modes=_SUPPORTED_REASONING_MODES,
                    recommended=False,
                    source="local",
                    notes="CLI alias, resolved by claude",
                )
            )
        return tuple(options)

    def _get_semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._semaphore_loop is not loop:
            self._subprocess_semaphore = asyncio.Semaphore(1)
            self._semaphore_loop = loop
        return self._subprocess_semaphore  # type: ignore[return-value]

    def _build_command(
        self,
        system_prompt: str,
        *,
        reasoning: ReasoningMode = "auto",
    ) -> list[str]:
        cmd = [
            self._claude_cmd,
            "-p",
            "--output-format",
            "json",
            "--no-session-persistence",
            # One-shot prose generation: never load project instruction files
            # or user settings into the prompt, and never allow tool use to
            # mutate anything (the prompt carries all needed context).
            "--setting-sources",
            "",
            "--tools",
            "",
        ]
        # Unlike codex_cli's combined-prompt stdin, the Claude CLI assigns
        # the system role natively, so the model sees a true system turn
        # rather than instructions quoted inside the user message.
        if system_prompt.strip():
            cmd.extend(["--system-prompt", system_prompt.strip()])
        if self._model:
            cmd.extend(["--model", self._model])
        effort = _claude_effort_for_reasoning(reasoning)
        if effort:
            cmd.extend(["--effort", effort])
        return cmd

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
        if self._rate_limiter:
            await self._rate_limiter.acquire(estimated_tokens=max_tokens)

        cmd = self._build_command(system_prompt, reasoning=reasoning)
        log.debug(
            "claude_cli.generate.start",
            model=self.model_name,
            repo_path=str(self._repo_path),
            request_id=request_id,
        )

        async with self._get_semaphore():
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(self._repo_path),
                    env=_subprocess_env(),
                )
            except FileNotFoundError as exc:
                raise ProviderError(
                    "claude_cli",
                    "Claude Code CLI not found. Install it with: "
                    "npm install -g @anthropic-ai/claude-code",
                ) from exc

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(user_prompt.encode("utf-8")),
                    timeout=_EXEC_TIMEOUT_SECONDS,
                )
            except TimeoutError as exc:
                proc.kill()
                with contextlib.suppress(ProcessLookupError):
                    await proc.wait()
                raise ProviderError(
                    "claude_cli",
                    f"claude -p timed out after {_EXEC_TIMEOUT_SECONDS} seconds.",
                ) from exc
            finally:
                await _close_subprocess_transport(proc)

        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

        if proc.returncode != 0:
            raise ProviderError(
                "claude_cli",
                _error_message(stderr, stdout, proc.returncode),
                status_code=proc.returncode,
            )

        content, usage, is_error, error_detail = _parse_result(stdout)
        if is_error:
            raise ProviderError(
                "claude_cli",
                error_detail or "claude -p reported an error result.",
            )
        if not content:
            raise ProviderError(
                "claude_cli",
                "claude -p completed but returned an empty result.",
            )

        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        cached_tokens = int(usage.get("cache_read_input_tokens", 0) or 0)

        log.debug(
            "claude_cli.generate.done",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            request_id=request_id,
        )
        usage_payload: dict[str, Any] = {
            **usage,
            "source": "claude_p",
            "model": self.model_name,
            "stderr": _tail(stderr, max_chars=1_000) if stderr.strip() else "",
        }
        if not usage:
            usage_payload["estimated"] = True

        return GeneratedResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            usage=usage_payload,
        )
