"""Devin CLI provider for repowise.

This provider delegates generation to the local Devin CLI via ``devin -p``
(single-turn, non-interactive mode). It uses the user's existing Devin CLI
installation and authentication (``devin auth login``) without requiring a
separate API key managed by repowise.

Security: uses ``asyncio.create_subprocess_exec`` (no shell), validates model
names against a safe character set, resolves all paths before passing them to
the subprocess, and runs the CLI against a read-only permission profile
(``normal`` is the Devin default, which auto-approves read-only tools within
the working directory and prompts for write/execute operations). The ``cwd``
is pinned to the target repo so the CLI reasons about the right directory even
when repowise is launched from elsewhere.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
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
from repowise.core.reasoning import ReasoningMode

log = structlog.get_logger(__name__)

_DEFAULT_MODEL_LABEL = "devin/adaptive"
_EXEC_TIMEOUT_SECONDS = 600
_MAX_STDERR_CHARS = 1_000

# Devin CLI supports a short list of model-family aliases plus "adaptive", its
# intelligent model router. These are stable, cross-version short names that
# always resolve to the latest version in their family (docs.devin.ai/cli/models).
_KNOWN_MODELS: tuple[str, ...] = (
    "adaptive",
    "opus",
    "sonnet",
    "gpt",
    "swe",
    "codex",
    "gemini",
)

# Anything other than an alpha-numeric, dot, hyphen, underscore or forward
# slash is rejected before it can reach the subprocess argv.
_MODEL_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/\-]*$")


def _resolve_devin_executable() -> str | None:
    return shutil.which("devin")


def _validate_model_name(model: str) -> None:
    if not _MODEL_NAME_RE.match(model):
        raise ProviderError(
            "devin",
            f"Invalid model name {model!r}. Model names may only contain "
            "alphanumeric characters, dots, hyphens, underscores, and forward slashes.",
        )


def _normalize_model(model: str | None) -> str | None:
    if not model:
        return None
    if model == _DEFAULT_MODEL_LABEL:
        return None
    if model.startswith("devin/"):
        suffix = model.removeprefix("devin/")
        return suffix or None
    return model


def _model_label(model: str | None) -> str:
    native = _normalize_model(model)
    return f"devin/{native}" if native else _DEFAULT_MODEL_LABEL


def _combine_prompt(system_prompt: str, user_prompt: str) -> str:
    return (
        "System instructions for this task:\n\n"
        f"{system_prompt.strip()}\n\n"
        "---\n\n"
        "User request and context:\n\n"
        f"{user_prompt.strip()}"
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


def _devin_model_options() -> tuple[ProviderModelOption, ...]:
    """Return the built-in Devin model options.

    Devin CLI does not expose a stable ``models list`` machine-readable
    subcommand for the models a logged-in user may actually select (the docs
    tell users to run ``/model`` interactively), so repowise ships the
    documented model-family aliases. The user can always type a custom model
    id in the interactive picker.
    """
    options: list[ProviderModelOption] = [
        ProviderModelOption(
            model=_DEFAULT_MODEL_LABEL,
            label="Devin Adaptive",
            reasoning_modes=("auto",),
            recommended=True,
            source="builtin",
            notes="intelligent model router (recommended)",
        )
    ]
    for native in _KNOWN_MODELS:
        if native == "adaptive":
            continue
        options.append(
            ProviderModelOption(
                model=_model_label(native),
                label=native,
                reasoning_modes=("auto",),
                recommended=False,
                source="local",
                notes="model family alias",
            )
        )
    return tuple(options)


class DevinProvider(BaseProvider):
    """LLM provider backed by ``devin -p``.

    Uses the local Devin CLI for generation. Does not require an API key —
    Devin manages its own authentication via ``devin auth login``.

    Args:
        model:     Optional Devin model-family alias (e.g. ``adaptive``,
                   ``opus``, ``sonnet``). If omitted or ``devin/adaptive``,
                   Devin uses its default (Adaptive router).
        repo_path: Working directory the CLI runs in (passed as ``cwd``).
        rate_limiter: Serializes subprocess calls by default.
    """

    # ``devin -p`` spawns a process and drives a whole agent turn. Minutes,
    # not seconds. Stays under _EXEC_TIMEOUT_SECONDS so the caller gives up
    # before the subprocess does and the error names the real cause.
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
                "devin",
                "Devin CLI is not installed.\n\n"
                "Installation:\n"
                "  curl -fsSL https://cli.devin.ai/install.sh | bash\n\n"
                "After installing, run 'devin' once to log in (or 'devin auth "
                "login'). No API keys are managed by repowise — Devin handles "
                "all authentication.",
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
        return "devin"

    @property
    def model_name(self) -> str:
        return _model_label(self._model)

    def supported_reasoning_modes(self) -> tuple[ReasoningMode, ...]:
        return ("auto",)

    def available_model_options(self) -> tuple[ProviderModelOption, ...]:
        return _devin_model_options()

    def _get_semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._semaphore_loop is not loop:
            self._subprocess_semaphore = asyncio.Semaphore(1)
            self._semaphore_loop = loop
        return self._subprocess_semaphore  # type: ignore[return-value]

    def _build_command(self) -> list[str]:
        # --respect-workspace-trust false: non-interactive mode cannot show the
        # workspace trust prompt, so this skips the check for scripts/CI.
        cmd = [
            self._devin_cmd,
            "-p",
            "--permission-mode",
            "normal",
            "--respect-workspace-trust",
            "false",
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
        # The prompt is passed as the trailing positional argument to `devin -p`.
        cmd = [*self._build_command(), prompt]
        log.debug(
            "devin.generate.start",
            model=self.model_name,
            repo_path=str(self._repo_path),
            request_id=request_id,
        )

        async with self._get_semaphore():
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    cwd=str(self._repo_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=os.environ.copy(),
                )
            except FileNotFoundError as exc:
                raise ProviderError(
                    "devin",
                    "Devin CLI not found. Install it with: "
                    "curl -fsSL https://cli.devin.ai/install.sh | bash",
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
                    "devin",
                    f"devin -p timed out after {_EXEC_TIMEOUT_SECONDS} seconds.",
                ) from exc
            finally:
                await _close_subprocess_transport(proc)

        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

        if proc.returncode != 0:
            raise ProviderError(
                "devin",
                _error_message(stderr, stdout, proc.returncode),
                status_code=proc.returncode,
            )

        content = stdout.strip()
        if not content:
            raise ProviderError(
                "devin",
                "devin -p completed but produced no output on stdout.",
            )

        log.debug(
            "devin.generate.done",
            request_id=request_id,
        )

        # Devin's plain-text single-turn output carries no token accounting, so
        # usage is marked estimated (repowise prices the model at $0 locally).
        usage_payload: dict[str, Any] = {
            "source": "devin_p",
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


async def _close_subprocess_transport(proc: asyncio.subprocess.Process) -> None:
    transport = getattr(proc, "_transport", None)
    close = getattr(transport, "close", None)
    if not callable(close):
        return
    with contextlib.suppress(Exception):
        close()
    await asyncio.sleep(0)
