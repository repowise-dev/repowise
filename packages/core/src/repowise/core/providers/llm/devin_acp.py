"""Devin ACP provider for repowise.

This provider delegates generation to the authenticated local Devin CLI via the
Agent Client Protocol (``devin acp``). It requires the ``agent-client-protocol``
package and a Devin CLI that has already been authenticated with ``devin auth
login``.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog
from acp import PROTOCOL_VERSION, spawn_agent_process, text_block
from acp.client import ClientSideConnection
from acp.interfaces import Client
from acp.schema import (
    ClientCapabilities,
    DeniedOutcome,
    Implementation,
    RequestPermissionResponse,
)

from repowise.core.providers.llm.base import (
    BaseProvider,
    CacheHint,
    GeneratedResponse,
    ProviderError,
    ProviderModelOption,
    normalize_stop_reason,
)
from repowise.core.rate_limiter import RateLimiter
from repowise.core.reasoning import ReasoningMode

log = structlog.get_logger(__name__)

_DEFAULT_MODEL_LABEL = "devin_acp/default"
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
            "devin_acp",
            f"Invalid model name {model!r}. Model names may only contain "
            "alphanumeric characters, dots, hyphens, underscores, and forward slashes.",
        )


def _normalize_model(model: str | None) -> str | None:
    """Return the native Devin model slug, or None to use the ACP default."""
    if not model:
        return None
    if model == _DEFAULT_MODEL_LABEL:
        return None
    if model.startswith("devin_acp/"):
        suffix = model.removeprefix("devin_acp/")
        return suffix or None
    return model


def _model_label(model: str | None) -> str:
    """Return the persisted attribution label for a Devin ACP model."""
    native = _normalize_model(model)
    return f"devin_acp/{native}" if native else _DEFAULT_MODEL_LABEL


def _combine_prompt(system_prompt: str, user_prompt: str) -> str:
    return (
        "System instructions for this task:\n\n"
        f"{system_prompt.strip()}\n\n"
        "---\n\n"
        "User request and context:\n\n"
        f"{user_prompt.strip()}\n\n"
        "Do not read, edit, create, or run any files. "
        "Do not use any tools. Answer only in Markdown."
    )


def _tail(text: str, max_chars: int = _MAX_STDERR_CHARS) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _text_from_block(block: Any) -> str:
    """Best-effort extraction of text from an ACP content block."""
    if block is None:
        return ""
    if getattr(block, "type", None) == "text":
        return str(getattr(block, "text", "") or "")
    return ""


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
                label="Devin ACP default",
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
            label="Devin ACP default",
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


class DevinAcpClient(Client):
    """Minimal ACP client that collects agent message chunks and denies tools."""

    def __init__(self, provider_name: str) -> None:
        self.provider_name = provider_name
        self.message_text: list[str] = []
        self.thought_text: list[str] = []
        self.last_usage: dict[str, Any] = {}

    async def request_permission(
        self,
        session_id: str,
        tool_call: Any,
        options: Any,
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        log.debug("devin_acp.permission.denied", session_id=session_id, tool_call=tool_call)
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    def on_connect(self, conn: Any) -> None:
        pass

    async def session_update(
        self,
        session_id: str,
        update: Any,
        **kwargs: Any,
    ) -> None:
        kind = getattr(update, "session_update", None)
        if kind == "agent_message_chunk":
            text = _text_from_block(getattr(update, "content", None))
            if text:
                self.message_text.append(text)
        elif kind == "agent_thought_chunk":
            text = _text_from_block(getattr(update, "content", None))
            if text:
                self.thought_text.append(text)
        elif kind == "usage_update":
            self.last_usage = update.model_dump()

    # Tool/terminal/fs handlers: the provider advertises no capabilities, so
    # these should never be called. They are implemented defensively so the
    # agent cannot silently drive tools through the client.
    async def read_text_file(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("read_text_file is disabled")

    async def write_text_file(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("write_text_file is disabled")

    async def create_terminal(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("create_terminal is disabled")

    async def terminal_output(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("terminal_output is disabled")

    async def release_terminal(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("release_terminal is disabled")

    async def wait_for_terminal_exit(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("wait_for_terminal_exit is disabled")

    async def kill_terminal(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("kill_terminal is disabled")

    async def create_elicitation(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("create_elicitation is disabled")

    async def complete_elicitation(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("complete_elicitation is disabled")

    async def ext_method(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("ext_method is disabled")

    async def ext_notification(self, *args: Any, **kwargs: Any) -> None:
        pass


class DevinAcpProvider(BaseProvider):
    """LLM provider backed by ``devin acp``.

    Args:
        model: Optional native Devin model slug. If omitted, the ACP session
            uses the CLI's default model. Persisted labels like
            ``devin_acp/swe-1-7`` are accepted and normalized before calling
            the agent.
        repo_path: Working directory passed as the ACP session ``cwd``.
        rate_limiter: Accepted for interface consistency; the provider
            serializes ACP sessions by default.
    """

    # A full Devin ACP turn can take minutes for long documents, but we still
    # want a ceiling so the caller does not hang indefinitely.
    interactive_timeout_s: float = 300.0

    def __init__(
        self,
        model: str | None = None,
        repo_path: str | Path | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        devin_cmd = _resolve_devin_executable()
        if not devin_cmd:
            raise ProviderError(
                "devin_acp",
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
        self._session_semaphore: asyncio.Semaphore | None = None
        self._semaphore_loop: asyncio.AbstractEventLoop | None = None

    @property
    def provider_name(self) -> str:
        return "devin_acp"

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
            self._session_semaphore = asyncio.Semaphore(1)
            self._semaphore_loop = loop
        return self._session_semaphore  # type: ignore[return-value]

    async def _set_session_option(
        self,
        conn: ClientSideConnection,
        session_id: str,
        config_id: str,
        value: str,
    ) -> None:
        """Best-effort set of an ACP config option; ignored if unsupported."""
        try:
            await conn.set_config_option(config_id, session_id, value)
        except Exception as exc:
            log.debug(
                "devin_acp.set_config_option.failed",
                config_id=config_id,
                value=value,
                error=str(exc),
            )

    async def _generate_acp(
        self,
        system_prompt: str,
        user_prompt: str,
        request_id: str | None,
    ) -> GeneratedResponse:
        client = DevinAcpClient(self.provider_name)
        prompt = _combine_prompt(system_prompt, user_prompt)

        async with spawn_agent_process(
            client,
            self._devin_cmd,
            "acp",
            cwd=str(self._repo_path),
        ) as (conn, _proc):
            log.debug(
                "devin_acp.generate.start",
                model=self.model_name,
                repo_path=str(self._repo_path),
                request_id=request_id,
            )

            await conn.initialize(
                protocol_version=PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(),
                client_info=Implementation(name="repowise", version="dev"),
            )

            session = await conn.new_session(
                cwd=str(self._repo_path),
                mcp_servers=[],
            )

            # Put the session into no-edit Q&A mode so doc generation does not
            # accidentally write files.
            await self._set_session_option(conn, session.session_id, "mode", "ask")

            # If the user selected a specific model, tell the agent to use it.
            if self._model:
                await self._set_session_option(conn, session.session_id, "model", self._model)

            response = await conn.prompt(
                session_id=session.session_id,
                prompt=[text_block(prompt)],
            )

        content = "".join(client.message_text).strip()
        if not content:
            raise ProviderError(
                "devin_acp",
                "devin acp completed but produced no assistant message.",
            )

        normalized, provider_stop = normalize_stop_reason(response.stop_reason)
        usage = response.usage

        usage_payload: dict[str, Any] = {
            "source": "devin_acp",
            "model": self.model_name,
            "estimated": False,
        }

        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0
        if usage is not None:
            input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
            cached_tokens = int(
                getattr(usage, "cached_read_tokens", 0)
                or getattr(usage, "cached_write_tokens", 0)
                or 0
            )
            usage_payload["total_tokens"] = getattr(usage, "total_tokens", None)
            usage_payload["thought_tokens"] = getattr(usage, "thought_tokens", None)

        log.debug(
            "devin_acp.generate.done",
            request_id=request_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        return GeneratedResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            usage=usage_payload,
            stop_reason=normalized,
            provider_stop_reason=provider_stop,
        )

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

        async with self._get_semaphore():
            try:
                return await asyncio.wait_for(
                    self._generate_acp(
                        system_prompt,
                        user_prompt,
                        request_id,
                    ),
                    timeout=_EXEC_TIMEOUT_SECONDS,
                )
            except TimeoutError as exc:
                raise ProviderError(
                    "devin_acp",
                    f"devin acp timed out after {_EXEC_TIMEOUT_SECONDS} seconds.",
                ) from exc
