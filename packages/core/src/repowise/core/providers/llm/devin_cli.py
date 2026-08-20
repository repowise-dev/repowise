"""Devin CLI provider for repowise.

This provider delegates generation to the authenticated local Devin CLI via
``devin -p``. It is intended for users with a Devin subscription/auth already
configured by ``devin auth login`` and does not require a separate API key.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from functools import lru_cache
from pathlib import Path

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

_DEFAULT_MODEL_LABEL = "devin_cli/default"
_EXEC_TIMEOUT_SECONDS = 600
_CATALOG_TIMEOUT_SECONDS = 10
_MAX_STDERR_CHARS = 1_000

# Devin model uids/aliases are alphanumeric with underscores, dots, hyphens
# and slashes (e.g. "claude-opus-5-medium", "MODEL_GPT_5_2_LOW", "opus").
_MODEL_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/\-]*$")


def _resolve_devin_executable() -> str | None:
    """Return the executable path used to launch Devin, or None if unavailable."""

    return shutil.which("devin")


def _validate_model_name(model: str) -> None:
    if not _MODEL_NAME_RE.match(model):
        raise ProviderError(
            "devin_cli",
            f"Invalid model name {model!r}. Model names may only contain "
            "alphanumeric characters, dots, hyphens, underscores, and forward slashes.",
        )


def _normalize_model(model: str | None) -> str | None:
    """Return the native Devin model slug, or None to use CLI config."""
    if not model:
        return None
    if model == _DEFAULT_MODEL_LABEL:
        return None
    if model.startswith("devin_cli/"):
        suffix = model.removeprefix("devin_cli/")
        return suffix or None
    return model


def _model_label(model: str | None) -> str:
    """Return the persisted attribution label for a Devin CLI model."""
    native = _normalize_model(model)
    return f"devin_cli/{native}" if native else _DEFAULT_MODEL_LABEL


def _combine_prompt(system_prompt: str, user_prompt: str) -> str:
    return (
        "System instructions for this task:\n\n"
        f"{system_prompt.strip()}\n\n"
        "---\n\n"
        "User request and context:\n\n"
        f"{user_prompt.strip()}\n\n"
        "Do not edit any files. Answer only in Markdown."
    )


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
    return f"devin -p exited with {returncode}"


def _parse_devin_models(raw: object) -> list[tuple[str, str, int, int]]:
    """Parse ``devin models list --format json`` into model tuples.

    Returns tuples of (model_uid, label, max_context_tokens, max_output_tokens).
    Falls back to conservative defaults when a field is missing or malformed.
    """
    if not isinstance(raw, dict):
        return []

    families = raw.get("families")
    if not isinstance(families, list):
        return []

    models: list[tuple[str, str, int, int]] = []
    for raw_family in families:
        if not isinstance(raw_family, dict):
            continue
        variants = raw_family.get("variants")
        if not isinstance(variants, list):
            continue
        for raw_variant in variants:
            if not isinstance(raw_variant, dict):
                continue
            model_uid = raw_variant.get("model_uid")
            if not isinstance(model_uid, str) or not model_uid.strip():
                continue
            label = raw_variant.get("label")
            if not isinstance(label, str) or not label.strip():
                label = model_uid
            context_tokens = raw_variant.get("max_context_tokens", 0)
            output_tokens = raw_variant.get("max_output_tokens", 0)
            try:
                context_tokens = int(context_tokens or 0)
            except (TypeError, ValueError):
                context_tokens = 0
            try:
                output_tokens = int(output_tokens or 0)
            except (TypeError, ValueError):
                output_tokens = 0
            models.append((model_uid, label.strip(), context_tokens, output_tokens))

    return models


@lru_cache(maxsize=4)
def _load_devin_model_catalog(devin_cmd: str) -> list[tuple[str, str, int, int]] | None:
    """Ask the installed Devin CLI for its model catalog."""

    try:
        completed = subprocess.run(
            [devin_cmd, "models", "list", "--format", "json"],
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

    catalog = _parse_devin_models(raw)
    return catalog or None


def _devin_model_options(
    devin_cmd: str,
) -> tuple[ProviderModelOption, ...]:
    catalog = _load_devin_model_catalog(devin_cmd)
    if catalog is None:
        return (
            ProviderModelOption(
                model=_DEFAULT_MODEL_LABEL,
                label="Devin CLI default",
                reasoning_modes=("auto",),
                recommended=True,
                source="fallback",
                notes="uses Devin CLI config",
            ),
        )

    default_context = 200_000
    options: list[ProviderModelOption] = [
        ProviderModelOption(
            model=_DEFAULT_MODEL_LABEL,
            label="Devin CLI default",
            reasoning_modes=("auto",),
            recommended=True,
            source="local",
            notes="uses Devin CLI config",
        )
    ]
    for model_uid, label, context_tokens, output_tokens in sorted(
        catalog, key=lambda item: item[1].lower()
    ):
        notes = f"{context_tokens or default_context} context"
        if output_tokens:
            notes += f", {output_tokens} output"
        options.append(
            ProviderModelOption(
                model=_model_label(model_uid),
                label=label,
                reasoning_modes=("auto",),
                recommended=False,
                source="local",
                notes=notes,
            )
        )
    return tuple(options)


async def _close_subprocess_transport(proc: asyncio.subprocess.Process) -> None:
    """Close asyncio's subprocess transport before the event loop shuts down."""

    transport = getattr(proc, "_transport", None)
    close = getattr(transport, "close", None)
    if not callable(close):
        return
    with contextlib.suppress(Exception):
        close()
    await asyncio.sleep(0)


class DevinCliProvider(BaseProvider):
    """LLM provider backed by ``devin -p``.

    Args:
        model: Optional native Devin model slug. If omitted, Devin CLI config
            chooses the model. Persisted labels like ``devin_cli/opus`` are
            accepted and normalized before calling the CLI.
        repo_path: Working directory passed to the Devin subprocess.
        rate_limiter: Accepted for interface consistency; the provider
            serializes subprocess calls by default.
    """

    # A process spawn plus a full Devin turn. The floor is tens of seconds even
    # for a short prompt, so an interactive caller has to budget in minutes.
    # Stays under _EXEC_TIMEOUT_SECONDS so the caller gives up before the
    # subprocess does and the error names the real cause.
    interactive_timeout_s: float = 180.0

    def __init__(
        self,
        model: str | None = None,
        repo_path: str | Path | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        devin_cmd = _resolve_devin_executable()
        if not devin_cmd:
            raise ProviderError(
                "devin_cli",
                "Devin CLI not found.\n\n"
                "Installation:\n"
                "  macOS/Linux/WSL: curl -fsSL https://cli.devin.ai/install.sh | bash\n"
                "  Windows: irm https://static.devin.ai/cli/setup.ps1 | iex\n\n"
                "After installing, run 'devin auth login' to authenticate.",
            )
        self._devin_cmd = devin_cmd
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
        return "devin_cli"

    @property
    def model_name(self) -> str:
        return _model_label(self._model)

    def supported_reasoning_modes(self) -> tuple[ReasoningMode, ...]:
        return ("auto",)

    def available_model_options(self) -> tuple[ProviderModelOption, ...]:
        return _devin_model_options(self._devin_cmd)

    def _get_semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._semaphore_loop is not loop:
            self._subprocess_semaphore = asyncio.Semaphore(1)
            self._semaphore_loop = loop
        return self._subprocess_semaphore  # type: ignore[return-value]

    def _build_command(self, prompt_file: Path) -> list[str]:
        cmd = [
            self._devin_cmd,
            "-p",
            "--prompt-file",
            str(prompt_file),
            "--respect-workspace-trust",
            "false",
            "--permission-mode",
            "auto",
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

        prompt = _combine_prompt(system_prompt, user_prompt)
        prompt_file = (
            Path(tempfile.gettempdir()) / f"devin_prompt_{os.getpid()}_{uuid.uuid4().hex}.txt"
        )
        try:
            try:
                prompt_file.write_text(prompt, encoding="utf-8")
            except OSError as exc:
                raise ProviderError(
                    "devin_cli",
                    f"Failed to write Devin prompt file {prompt_file}: {exc}",
                ) from exc

            cmd = self._build_command(prompt_file)
            log.debug(
                "devin_cli.generate.start",
                model=self.model_name,
                repo_path=str(self._repo_path),
                request_id=request_id,
            )

            async with self._get_semaphore():
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=str(self._repo_path),
                    )
                except FileNotFoundError as exc:
                    raise ProviderError(
                        "devin_cli",
                        "Devin CLI not found.\n\n"
                        "Installation:\n"
                        "  macOS/Linux/WSL: curl -fsSL https://cli.devin.ai/install.sh | bash\n"
                        "  Windows: irm https://static.devin.ai/cli/setup.ps1 | iex",
                    ) from exc

                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        proc.communicate(),
                        timeout=_EXEC_TIMEOUT_SECONDS,
                    )
                except TimeoutError as exc:
                    proc.kill()
                    with contextlib.suppress(ProcessLookupError):
                        await proc.wait()
                    raise ProviderError(
                        "devin_cli",
                        f"devin -p timed out after {_EXEC_TIMEOUT_SECONDS} seconds.",
                    ) from exc
                finally:
                    await _close_subprocess_transport(proc)

            stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

            if proc.returncode != 0:
                raise ProviderError(
                    "devin_cli",
                    _error_message(stderr, stdout, proc.returncode),
                    status_code=proc.returncode,
                )

            content = stdout.strip()
            if not content:
                raise ProviderError(
                    "devin_cli",
                    "devin -p completed but produced no output.",
                )

            log.debug(
                "devin_cli.generate.done",
                request_id=request_id,
            )
            usage_payload = {
                "source": "devin_cli",
                "model": self.model_name,
                "stderr": _tail(stderr) if stderr.strip() else "",
                "estimated": True,
            }

            return GeneratedResponse(
                content=content,
                input_tokens=0,
                output_tokens=0,
                cached_tokens=0,
                usage=usage_payload,
            )
        finally:
            with contextlib.suppress(OSError):
                prompt_file.unlink(missing_ok=True)
