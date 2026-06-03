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
import subprocess
from dataclasses import dataclass
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
from repowise.core.reasoning import REASONING_MODES, ReasoningMode, normalize_reasoning

log = structlog.get_logger(__name__)

_DEFAULT_MODEL_LABEL = "codex_cli/default"
_EXEC_TIMEOUT_SECONDS = 600
_CATALOG_TIMEOUT_SECONDS = 5


async def _close_subprocess_transport(proc: asyncio.subprocess.Process) -> None:
    """Close asyncio's subprocess transport before the event loop shuts down."""

    transport = getattr(proc, "_transport", None)
    close = getattr(transport, "close", None)
    if not callable(close):
        return
    with contextlib.suppress(Exception):
        close()
    await asyncio.sleep(0)


@dataclass(frozen=True)
class CodexModelReasoning:
    """Small reasoning-capability slice extracted from the Codex model catalog."""

    slug: str
    default_effort: str | None
    supported_efforts: tuple[str, ...]


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


def _extract_codex_model_catalog(raw: object) -> dict[str, CodexModelReasoning]:
    if not isinstance(raw, dict):
        return {}

    raw_models = raw.get("models")
    if not isinstance(raw_models, list):
        return {}

    catalog: dict[str, CodexModelReasoning] = {}
    for raw_model in raw_models:
        if not isinstance(raw_model, dict):
            continue
        slug = raw_model.get("slug")
        if not isinstance(slug, str) or not slug.strip():
            continue

        supported: list[str] = []
        raw_levels = raw_model.get("supported_reasoning_levels")
        if isinstance(raw_levels, list):
            for raw_level in raw_levels:
                if not isinstance(raw_level, dict):
                    continue
                effort = raw_level.get("effort")
                if isinstance(effort, str) and effort.strip():
                    supported.append(effort.strip().lower())

        if not supported:
            continue

        default_effort = raw_model.get("default_reasoning_level")
        catalog[slug.lower()] = CodexModelReasoning(
            slug=slug,
            default_effort=(
                default_effort.strip().lower()
                if isinstance(default_effort, str) and default_effort.strip()
                else None
            ),
            supported_efforts=tuple(dict.fromkeys(supported)),
        )

    return catalog


@lru_cache(maxsize=8)
def _load_codex_model_catalog(codex_cmd: str) -> dict[str, CodexModelReasoning] | None:
    """Ask the installed Codex CLI for its bundled model catalog."""

    try:
        completed = subprocess.run(
            [codex_cmd, "debug", "models", "--bundled"],
            capture_output=True,
            check=False,
            text=True,
            timeout=_CATALOG_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if completed.returncode != 0:
        return None

    try:
        raw = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None

    catalog = _extract_codex_model_catalog(raw)
    return catalog or None


def _codex_effort_for_reasoning(
    reasoning: ReasoningMode,
    supported_efforts: tuple[str, ...] | None,
) -> str | None:
    mode = normalize_reasoning(reasoning)
    if mode == "auto":
        return None
    if mode in ("off", "none"):
        return "none"
    if mode == "minimal":
        if supported_efforts and "minimal" in supported_efforts:
            return "minimal"
        if supported_efforts and "low" in supported_efforts:
            return "low"
        return "low"
    return mode


def _catalog_supported_efforts(
    catalog: dict[str, CodexModelReasoning],
    native_model: str | None,
) -> tuple[str, ...] | None:
    if native_model:
        model = catalog.get(native_model.lower())
        return model.supported_efforts if model else None

    efforts: list[str] = []
    for model in catalog.values():
        efforts.extend(model.supported_efforts)
    return tuple(dict.fromkeys(efforts))


def _codex_modes_from_efforts(
    supported_efforts: tuple[str, ...],
) -> tuple[ReasoningMode, ...]:
    modes: list[ReasoningMode] = ["auto"]
    if "none" in supported_efforts:
        modes.extend(("off", "none"))
    if "minimal" in supported_efforts or "low" in supported_efforts:
        modes.append("minimal")
    for mode in ("low", "medium", "high", "xhigh", "max"):
        if mode in supported_efforts:
            modes.append(mode)
    return tuple(dict.fromkeys(modes))


def _codex_supported_reasoning_modes(
    codex_cmd: str,
    model: str,
) -> tuple[ReasoningMode, ...]:
    catalog = _load_codex_model_catalog(codex_cmd)
    if catalog is None:
        return REASONING_MODES

    supported_efforts = _catalog_supported_efforts(catalog, _normalize_model(model))
    if supported_efforts is None:
        return REASONING_MODES

    return _codex_modes_from_efforts(supported_efforts)


def _codex_model_options(codex_cmd: str) -> tuple[ProviderModelOption, ...]:
    catalog = _load_codex_model_catalog(codex_cmd)
    if catalog is None:
        return (
            ProviderModelOption(
                model=_DEFAULT_MODEL_LABEL,
                label="Codex CLI default",
                reasoning_modes=REASONING_MODES,
                recommended=True,
                source="fallback",
                notes="uses Codex CLI config",
            ),
        )

    default_efforts = _catalog_supported_efforts(catalog, None) or ()
    options: list[ProviderModelOption] = [
        ProviderModelOption(
            model=_DEFAULT_MODEL_LABEL,
            label="Codex CLI default",
            reasoning_modes=_codex_modes_from_efforts(default_efforts),
            recommended=True,
            source="local",
            notes="uses Codex CLI config",
        )
    ]
    for model in sorted(catalog.values(), key=lambda item: item.slug.lower()):
        notes = f"default {model.default_effort}" if model.default_effort else ""
        options.append(
            ProviderModelOption(
                model=_model_label(model.slug),
                label=model.slug,
                reasoning_modes=_codex_modes_from_efforts(model.supported_efforts),
                recommended=False,
                source="local",
                notes=notes,
            )
        )
    return tuple(options)


def _codex_reasoning_config(
    codex_cmd: str,
    model: str,
    reasoning: ReasoningMode,
) -> str | None:
    mode = normalize_reasoning(reasoning)
    if mode == "auto":
        return None

    native_model = _normalize_model(model)
    catalog = _load_codex_model_catalog(codex_cmd)
    supported_efforts = (
        _catalog_supported_efforts(catalog, native_model) if catalog is not None else None
    )
    effort = _codex_effort_for_reasoning(mode, supported_efforts)
    if effort is None:
        return None

    if supported_efforts is not None and effort not in supported_efforts:
        supported = ", ".join(supported_efforts)
        mapped = f" maps to model_reasoning_effort={effort!r}" if effort != mode else ""
        raise ProviderError(
            "codex_cli",
            (
                f"reasoning={mode!r}{mapped} is not supported by the Codex CLI "
                f"model catalog for model {model!r}. Supported reasoning efforts: "
                f"{supported}."
            ),
        )

    return f'model_reasoning_effort="{effort}"'


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

    def supported_reasoning_modes(self) -> tuple[ReasoningMode, ...]:
        return _codex_supported_reasoning_modes(self._codex_cmd, self.model_name)

    def available_model_options(self) -> tuple[ProviderModelOption, ...]:
        return _codex_model_options(self._codex_cmd)

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
        reasoning_config = _codex_reasoning_config(self._codex_cmd, self.model_name, reasoning)
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
        cache_hints: tuple[CacheHint, ...] = (),
    ) -> GeneratedResponse:
        if self._rate_limiter:
            await self._rate_limiter.acquire(estimated_tokens=max_tokens)

        # _build_command may shell out to `codex debug models` (cached) to validate
        # the reasoning effort; run it off the event loop so a cold catalog load
        # can't stall every concurrent generation coroutine.
        cmd = await asyncio.to_thread(self._build_command, reasoning=reasoning)
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
            finally:
                await _close_subprocess_transport(proc)

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
