"""OpenCode CLI provider for repowise.

This provider delegates generation to the local OpenCode CLI via ``opencode run``.
It uses the user's existing OpenCode installation and authentication (``opencode providers``)
without requiring a separate API key.

Security: uses ``asyncio.create_subprocess_exec`` (no shell), validates model names
against a safe character set, resolves all paths before passing them to the
subprocess, and enforces a read-only permission profile via ``OPENCODE_CONFIG_CONTENT``
(highest-precedence config that a project-local ``opencode.json`` cannot loosen).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import subprocess
from functools import lru_cache
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
from repowise.core.reasoning import ReasoningMode

log = structlog.get_logger(__name__)

_DEFAULT_MODEL_LABEL = "opencode/default"
_EXEC_TIMEOUT_SECONDS = 600
_CATALOG_TIMEOUT_SECONDS = 10
_MAX_STDERR_CHARS = 1_000

_MODEL_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/\-]*$")

_OPENCODE_READONLY_CONFIG = json.dumps(
    {"permission": {k: "deny" for k in
     ("edit", "bash", "webfetch", "websearch", "external_directory", "doom_loop", "task")}}
)


def _resolve_opencode_executable() -> str | None:
    return shutil.which("opencode")


def _validate_model_name(model: str) -> None:
    if not _MODEL_NAME_RE.match(model):
        raise ProviderError(
            "opencode",
            f"Invalid model name {model!r}. Model names may only contain "
            "alphanumeric characters, dots, hyphens, underscores, and forward slashes.",
        )


def _normalize_model(model: str | None) -> str | None:
    if not model:
        return None
    if model == _DEFAULT_MODEL_LABEL:
        return None
    if model.startswith("opencode/"):
        suffix = model.removeprefix("opencode/")
        return suffix or None
    return model


def _model_label(model: str | None) -> str:
    native = _normalize_model(model)
    return f"opencode/{native}" if native else _DEFAULT_MODEL_LABEL


def _combine_prompt(system_prompt: str, user_prompt: str) -> str:
    return (
        "System instructions for this task:\n\n"
        f"{system_prompt.strip()}\n\n"
        "---\n\n"
        "User request and context:\n\n"
        f"{user_prompt.strip()}"
    )


def _parse_jsonl(stdout: str) -> tuple[str, dict[str, Any]]:
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

        event_type = event.get("type")
        if event_type == "text":
            part = event.get("part") or {}
            text = part.get("text")
            if isinstance(text, str) and text:
                content_parts.append(text)
        elif event_type == "step_finish":
            part = event.get("part") or {}
            tokens = part.get("tokens")
            if isinstance(tokens, dict):
                for key, value in tokens.items():
                    if isinstance(value, dict):
                        existing = usage.get(key, {})
                        if not isinstance(existing, dict):
                            existing = {}
                        for sub_key, sub_value in value.items():
                            if isinstance(sub_value, (int, float)):
                                existing[sub_key] = existing.get(sub_key, 0) + int(sub_value)
                        usage[key] = existing
                    elif isinstance(value, (int, float)):
                        usage[key] = usage.get(key, 0) + int(value)

    return "\n".join(content_parts), usage


def _tail(text: str, max_chars: int = _MAX_STDERR_CHARS) -> str:
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

    try:
        for raw_line in stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            event = json.loads(line)
            if event.get("type") == "error":
                err = event.get("error") or {}
                msg = err.get("data", {}).get("message") or err.get("name", "")
                if msg:
                    return str(msg)
    except Exception:
        pass

    return f"opencode run exited with {returncode}"


_MODEL_LINE_RE = re.compile(
    r"^\s*([a-zA-Z0-9][a-zA-Z0-9._\-]*)/([a-zA-Z0-9][a-zA-Z0-9._/\-]*)\s*$"
)


def _parse_models_output(output: str) -> list[str]:
    models: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _MODEL_LINE_RE.match(line)
        if match:
            provider, model = match.group(1), match.group(2)
            models.append(f"{provider}/{model}")
    return models


@lru_cache(maxsize=4)
def _load_opencode_model_catalog(opencode_cmd: str) -> list[str] | None:
    try:
        completed = subprocess.run(
            [opencode_cmd, "models"],
            capture_output=True,
            check=False,
            text=True,
            timeout=_CATALOG_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if completed.returncode != 0:
        return None

    return _parse_models_output(completed.stdout) or None


def _opencode_model_options(
    opencode_cmd: str,
) -> tuple[ProviderModelOption, ...]:
    catalog = _load_opencode_model_catalog(opencode_cmd)
    if catalog is None:
        return (
            ProviderModelOption(
                model=_DEFAULT_MODEL_LABEL,
                label="OpenCode default",
                reasoning_modes=("auto",),
                recommended=True,
                source="fallback",
                notes="uses opencode config",
            ),
        )

    options: list[ProviderModelOption] = [
        ProviderModelOption(
            model=_DEFAULT_MODEL_LABEL,
            label="OpenCode default",
            reasoning_modes=("auto",),
            recommended=True,
            source="local",
            notes="uses opencode config",
        )
    ]
    for model in sorted(catalog):
        options.append(
            ProviderModelOption(
                model=_model_label(model),
                label=model,
                reasoning_modes=("auto",),
                recommended=False,
                source="local",
                notes="",
            )
        )
    return tuple(options)


class OpenCodeProvider(BaseProvider):
    """LLM provider backed by ``opencode run``.

    Uses the local opencode CLI for generation. Does not require an API key —
    opencode manages its own authentication via ``opencode providers``.

    Args:
        model:     Optional model identifier in ``provider/model`` format
                   (e.g. ``deepseek/deepseek-v4-pro``). If omitted or
                   ``opencode/default``, opencode uses its configured default.
        repo_path: Working directory passed to opencode via ``--dir``.
        rate_limiter: Serializes subprocess calls by default.
    """

    def __init__(
        self,
        model: str | None = None,
        repo_path: str | Path | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        opencode_cmd = _resolve_opencode_executable()
        if not opencode_cmd:
            raise ProviderError(
                "opencode",
                "OpenCode CLI is not installed.\n\n"
                "Installation:\n"
                "  curl -fsSL https://opencode.ai/install | bash\n\n"
                "After installing, run 'opencode' once to set up your model provider "
                "and authenticate. No API keys are managed by repowise — opencode "
                "handles all authentication.\n\n"
                "To choose a model:\n"
                "  opencode models                 # list available models\n"
                "  repowise init --provider opencode --model opencode/deepseek/deepseek-v4-pro\n\n"
                "More info: https://opencode.ai",
            )
        self._opencode_cmd = opencode_cmd
        native = _normalize_model(model)
        if native is not None:
            _validate_model_name(native)
        self._model = native
        self._repo_path = (
            Path(repo_path).resolve() if repo_path is not None else Path.cwd().resolve()
        )
        self._rate_limiter = rate_limiter
        self._subprocess_semaphore: asyncio.Semaphore | None = None
        self._semaphore_loop: asyncio.AbstractEventLoop | None = None

    @property
    def provider_name(self) -> str:
        return "opencode"

    @property
    def model_name(self) -> str:
        return _model_label(self._model)

    def supported_reasoning_modes(self) -> tuple[ReasoningMode, ...]:
        return ("auto",)

    def available_model_options(self) -> tuple[ProviderModelOption, ...]:
        return _opencode_model_options(self._opencode_cmd)

    def _get_semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._semaphore_loop is not loop:
            self._subprocess_semaphore = asyncio.Semaphore(1)
            self._semaphore_loop = loop
        return self._subprocess_semaphore  # type: ignore[return-value]

    def _build_command(self) -> list[str]:
        cmd = [
            self._opencode_cmd,
            "run",
            "--format",
            "json",
            "--dir",
            str(self._repo_path),
        ]
        if self._model:
            cmd.extend(["--model", self._model])
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

        cmd = self._build_command()
        prompt = _combine_prompt(system_prompt, user_prompt)
        log.debug(
            "opencode.generate.start",
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
                    env={
                        **os.environ,
                        "OPENCODE_CONFIG_CONTENT": _OPENCODE_READONLY_CONFIG,
                    },
                )
            except FileNotFoundError as exc:
                raise ProviderError(
                    "opencode",
                    "OpenCode CLI not found. Install it with: "
                    "curl -fsSL https://opencode.ai/install | bash",
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
                    "opencode",
                    f"opencode run timed out after {_EXEC_TIMEOUT_SECONDS} seconds.",
                ) from exc
            finally:
                await _close_subprocess_transport(proc)

        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

        if proc.returncode != 0:
            raise ProviderError(
                "opencode",
                _error_message(stderr, stdout, proc.returncode),
                status_code=proc.returncode,
            )

        content, usage = _parse_jsonl(stdout)
        if not content:
            raise ProviderError(
                "opencode",
                "opencode run completed but no text was found in JSONL output.",
            )

        usage_missing = not usage
        input_tokens = int(usage.get("input", 0) or 0)
        output_tokens = int(usage.get("output", 0) or 0)
        cached_tokens = int((usage.get("cache") or {}).get("read", 0) or 0)

        log.debug(
            "opencode.generate.done",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            request_id=request_id,
        )
        usage_payload = {
            **usage,
            "source": "opencode_run",
            "model": self.model_name,
            "stderr": _tail(stderr) if stderr.strip() else "",
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


async def _close_subprocess_transport(proc: asyncio.subprocess.Process) -> None:
    transport = getattr(proc, "_transport", None)
    close = getattr(transport, "close", None)
    if not callable(close):
        return
    with contextlib.suppress(Exception):
        close()
    await asyncio.sleep(0)
