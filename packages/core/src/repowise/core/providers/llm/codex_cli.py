"""Codex CLI provider for repowise.

This provider delegates generation to the authenticated local Codex CLI via
``codex exec``. It is intended for users with Codex subscription/auth already
configured by ``codex login`` and does not require an OpenAI API key.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from repowise.core.providers.llm._concurrency import resolve_concurrency
from repowise.core.providers.llm.agent_cli import (
    EXEC_TIMEOUT_SECONDS,
    AgentCliProvider,
    iter_jsonl,
    model_label,
    normalize_model,
    tail,
)
from repowise.core.providers.llm.base import (
    GeneratedResponse,
    ProviderError,
    ProviderModelOption,
)
from repowise.core.reasoning import REASONING_MODES, ReasoningMode, normalize_reasoning

_DEFAULT_MODEL_LABEL = "codex_cli/default"
_EXEC_TIMEOUT_SECONDS = EXEC_TIMEOUT_SECONDS
_CATALOG_TIMEOUT_SECONDS = 5
_CONCURRENCY_ENV = "REPOWISE_CODEX_CLI_CONCURRENCY"


@dataclass(frozen=True)
class CodexModelReasoning:
    """Small reasoning-capability slice extracted from the Codex model catalog."""

    slug: str
    default_effort: str | None
    supported_efforts: tuple[str, ...]


def _normalize_model(model: str | None) -> str | None:
    """Return the native Codex model slug, or None to use CLI config."""
    return normalize_model(model, "codex_cli", None)


def _model_label(model: str | None) -> str:
    return model_label(_normalize_model(model), "codex_cli")


def _resolve_concurrency() -> int:
    return resolve_concurrency(_CONCURRENCY_ENV, "codex_cli")


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
            # Inside a stdio MCP server stdin is the JSON-RPC pipe; never share it.
            stdin=subprocess.DEVNULL,
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

    for event in iter_jsonl(stdout):
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


class CodexCliProvider(AgentCliProvider):
    """LLM provider backed by ``codex exec``.

    Args:
        model: Optional native Codex model slug. If omitted, Codex CLI config
            chooses the model. Persisted labels like ``codex_cli/gpt-5.5`` are
            accepted and normalized before calling the CLI.
        repo_path: Working directory passed to ``codex exec --cd``.
        rate_limiter: Accepted for interface consistency; the provider also
            bounds its own subprocess fan-out.
    """

    provider_name = "codex_cli"
    agent_slug = "codex"
    command_label = "codex exec"
    concurrency_env = _CONCURRENCY_ENV
    # No validation: the codex catalog decides, and an unlisted slug is passed through.
    validates_model_name = False

    def exec_timeout_seconds(self) -> float:
        return _EXEC_TIMEOUT_SECONDS  # a module global, so tests can patch it

    def supported_reasoning_modes(self) -> tuple[ReasoningMode, ...]:
        return _codex_supported_reasoning_modes(self._executable, self.model_name)

    def available_model_options(self) -> tuple[ProviderModelOption, ...]:
        return _codex_model_options(self._executable)

    def build_command(self, system_prompt: str, reasoning: ReasoningMode) -> list[str]:
        cmd = [
            self._executable,
            "exec",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--json",
            "--cd",
            str(self._repo_path),
            "--config",
            "project_doc_max_bytes=0",
        ]
        reasoning_config = _codex_reasoning_config(self._executable, self.model_name, reasoning)
        if reasoning_config:
            cmd.extend(["--config", reasoning_config])
        if self._model:
            cmd.extend(["--model", self._model])
        cmd.append("-")
        return cmd

    def build_stdin(self, system_prompt: str, user_prompt: str) -> str:
        return _combine_prompt(system_prompt, user_prompt)

    def parse_output(self, stdout: str, stderr: str) -> GeneratedResponse:
        content, usage = _parse_jsonl(stdout)
        if not content:
            raise ProviderError(
                "codex_cli",
                "codex exec completed but no agent_message was found in JSONL output.",
            )

        usage_payload = {
            **usage,
            "source": "codex_exec",
            "model": self.model_name,
            "stderr": tail(stderr, 1_000),
        }
        if not usage:
            usage_payload["estimated"] = True

        return GeneratedResponse(
            content=content,
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            cached_tokens=int(usage.get("cached_input_tokens", 0) or 0),
            usage=usage_payload,
        )
