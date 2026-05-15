"""Codex CLI provider for repowise.

This provider delegates generation to the authenticated local Codex CLI via
``codex exec``. It is intended for users with Codex subscription/auth already
configured by ``codex login`` and does not require an OpenAI API key.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
from pathlib import Path
from typing import Any

import structlog

from repowise.core.providers.llm.base import (
    BaseProvider,
    GeneratedResponse,
    ProviderError,
    ensure_reasoning_supported,
)
from repowise.core.rate_limiter import RateLimiter
from repowise.core.reasoning import ReasoningMode

log = structlog.get_logger(__name__)

_DEFAULT_MODEL_LABEL = "codex_cli/default"
_EXEC_TIMEOUT_SECONDS = 600


def _resolve_codex_executable() -> str | None:
    """Return the executable path used to launch Codex, or None if unavailable."""

    return shutil.which("codex")


def _normalize_model(model: str | None) -> str | None:
    """Return the native Codex model slug, or None to use CLI config."""
    if not model:
        return None
    if model == _DEFAULT_MODEL_LABEL:
        return None
    if model.startswith("codex_cli/"):
        suffix = model.removeprefix("codex_cli/")
        return suffix or None
    return model


def _model_label(model: str | None) -> str:
    """Return the persisted attribution label for a Codex CLI model."""
    native = _normalize_model(model)
    return f"codex_cli/{native}" if native else _DEFAULT_MODEL_LABEL


def _codex_reasoning_config(model: str, reasoning: ReasoningMode) -> str | None:
    mode = ensure_reasoning_supported(
        "codex_cli",
        model,
        reasoning,
        ("auto", "minimal"),
        detail=(
            "CodexCliProvider maps reasoning='minimal' to "
            "model_reasoning_effort='low'. reasoning='off' is not supported "
            "by the Codex CLI provider."
        ),
    )
    if mode == "minimal":
        return 'model_reasoning_effort="low"'
    return None


def _combine_prompt(system_prompt: str, user_prompt: str) -> str:
    return (
        "Follow these system instructions for this one-shot documentation task:\n\n"
        f"{system_prompt.strip()}\n\n"
        "User request and context:\n\n"
        f"{user_prompt.strip()}\n"
    )


def _parse_jsonl(stdout: str) -> tuple[str, dict[str, Any]]:
    """Parse Codex JSONL output, ignoring non-JSON noise."""
    content_parts: list[str] = []
    usage: dict[str, Any] = {}

    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        if event.get("type") == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message":
                text = item.get("text")
                if isinstance(text, str) and text:
                    content_parts.append(text)
        elif event.get("type") == "turn.completed":
            event_usage = event.get("usage")
            if isinstance(event_usage, dict):
                usage = event_usage

    return "\n".join(content_parts), usage


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
    return f"codex exec exited with {returncode}"


class CodexCliProvider(BaseProvider):
    """LLM provider backed by ``codex exec``.

    Args:
        model: Optional native Codex model slug. If omitted, Codex CLI config
            chooses the model. Persisted labels like ``codex_cli/gpt-5.5`` are
            accepted and normalized before calling the CLI.
        repo_path: Working directory passed to ``codex exec --cd``.
        rate_limiter: Accepted for interface consistency, but the provider
            serializes subprocess calls by default.
    """

    def __init__(
        self,
        model: str | None = None,
        repo_path: str | Path | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        codex_cmd = _resolve_codex_executable()
        if not codex_cmd:
            raise ProviderError(
                "codex_cli",
                "Codex CLI not found. Install it with: npm install -g @openai/codex",
            )
        self._codex_cmd = codex_cmd
        self._model = _normalize_model(model)
        self._repo_path = (
            Path(repo_path).resolve() if repo_path is not None else Path.cwd().resolve()
        )
        self._rate_limiter = rate_limiter
        self._subprocess_semaphore: asyncio.Semaphore | None = None
        self._semaphore_loop: asyncio.AbstractEventLoop | None = None

    @property
    def provider_name(self) -> str:
        return "codex_cli"

    @property
    def model_name(self) -> str:
        return _model_label(self._model)

    def _get_semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._semaphore_loop is not loop:
            self._subprocess_semaphore = asyncio.Semaphore(1)
            self._semaphore_loop = loop
        return self._subprocess_semaphore  # type: ignore[return-value]

    def _build_command(self, *, reasoning: ReasoningMode = "auto") -> list[str]:
        cmd = [
            self._codex_cmd,
            "exec",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--json",
            "--cd",
            str(self._repo_path),
        ]
        reasoning_config = _codex_reasoning_config(self.model_name, reasoning)
        if reasoning_config:
            cmd.extend(["--config", reasoning_config])
        if self._model:
            cmd.extend(["--model", self._model])
        cmd.append("-")
        return cmd

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        request_id: str | None = None,
        reasoning: ReasoningMode = "auto",
    ) -> GeneratedResponse:
        if self._rate_limiter:
            await self._rate_limiter.acquire(estimated_tokens=max_tokens)

        cmd = self._build_command(reasoning=reasoning)
        prompt = _combine_prompt(system_prompt, user_prompt)
        log.debug(
            "codex_cli.generate.start",
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
                )
            except FileNotFoundError as exc:
                raise ProviderError(
                    "codex_cli",
                    "Codex CLI not found. Install it with: npm install -g @openai/codex",
                ) from exc

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(prompt.encode("utf-8")),
                    timeout=_EXEC_TIMEOUT_SECONDS,
                )
            except TimeoutError as exc:
                proc.kill()
                with contextlib.suppress(ProcessLookupError):
                    await proc.wait()
                raise ProviderError(
                    "codex_cli",
                    f"codex exec timed out after {_EXEC_TIMEOUT_SECONDS} seconds.",
                ) from exc

        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

        if proc.returncode != 0:
            raise ProviderError(
                "codex_cli",
                _error_message(stderr, stdout, proc.returncode),
                status_code=proc.returncode,
            )

        content, usage = _parse_jsonl(stdout)
        if not content:
            raise ProviderError(
                "codex_cli",
                "codex exec completed but no agent_message was found in JSONL output.",
            )

        usage_missing = not usage
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        cached_tokens = int(usage.get("cached_input_tokens", 0) or 0)

        log.debug(
            "codex_cli.generate.done",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            request_id=request_id,
        )
        usage_payload = {
            **usage,
            "source": "codex_exec",
            "model": self.model_name,
            "stderr": _tail(stderr, max_chars=1_000) if stderr.strip() else "",
        }
        if usage_missing:
            usage_payload["estimated"] = True

        return GeneratedResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            usage=usage_payload,
        )
