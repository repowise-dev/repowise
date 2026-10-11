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

import json
import os
import re
import subprocess
import uuid
from functools import lru_cache, partial
from typing import Any

from repowise.core.providers.llm.agent_cli import (
    EXEC_TIMEOUT_SECONDS,
    AgentCliProvider,
    iter_jsonl,
    model_label,
    normalize_model,
    tail,
    validate_model_name,
)
from repowise.core.providers.llm.base import (
    GeneratedResponse,
    ProviderError,
    ProviderModelOption,
)
from repowise.core.reasoning import ReasoningMode

_DEFAULT_MODEL_LABEL = "opencode/default"
_EXEC_TIMEOUT_SECONDS = EXEC_TIMEOUT_SECONDS
_CATALOG_TIMEOUT_SECONDS = 10

_OPENCODE_READONLY_CONFIG = json.dumps(
    {
        "permission": {
            k: "deny"
            for k in (
                "edit",
                "bash",
                "webfetch",
                "websearch",
                "external_directory",
                "doom_loop",
                "task",
            )
        }
    }
)


_validate_model_name = partial(validate_model_name, "opencode")


def _model_label(model: str) -> str:
    return model_label(normalize_model(model, "opencode", None), "opencode")


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

    for event in iter_jsonl(stdout):
        event_type = event.get("type")
        part = event.get("part") or {}
        if event_type == "text":
            text = part.get("text")
            if isinstance(text, str) and text:
                content_parts.append(text)
        elif event_type == "step_finish":
            tokens = part.get("tokens")
            if isinstance(tokens, dict):
                _add_token_counts(usage, tokens)

    return "\n".join(content_parts), usage


def _add_token_counts(usage: dict[str, Any], tokens: dict[str, Any]) -> None:
    """Sum one step's token counts into *usage*, one level of nesting deep.

    ``step_finish`` reports flat counts (``input``, ``output``) alongside
    nested ones (``cache: {read, write}``); both accumulate across steps.
    """
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


def _error_event_message(stdout: str) -> str | None:
    """Message of the first ``error`` event in *stdout*.

    Stops at the first line that is not JSON: past that point the output is
    not an event stream, so nothing later is trusted.
    """
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
    return None


_MODEL_LINE_RE = re.compile(r"^\s*([a-zA-Z0-9][a-zA-Z0-9._\-]*)/([a-zA-Z0-9][a-zA-Z0-9._/\-]*)\s*$")


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
            # Inside a stdio MCP server stdin is the JSON-RPC pipe; never share it.
            stdin=subprocess.DEVNULL,
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


class OpenCodeProvider(AgentCliProvider):
    """LLM provider backed by ``opencode run``.

    Uses the local opencode CLI for generation. Does not require an API key —
    opencode manages its own authentication via ``opencode providers``.

    Args:
        model:     Optional model identifier in ``provider/model`` format
                   (e.g. ``deepseek/deepseek-v4-pro``). If omitted or
                   ``opencode/default``, opencode uses its configured default.
        repo_path: Working directory passed to opencode via ``--dir``.
        rate_limiter: Accepted for interface consistency; the provider also
            bounds its own subprocess fan-out.
    """

    provider_name = "opencode"
    agent_slug = "opencode"
    command_label = "opencode run"
    concurrency_env = "REPOWISE_OPENCODE_CONCURRENCY"
    error_tail_chars = 1_000

    def exec_timeout_seconds(self) -> float:
        return _EXEC_TIMEOUT_SECONDS  # a module global, so tests can patch it

    def available_model_options(self) -> tuple[ProviderModelOption, ...]:
        return _opencode_model_options(self._executable)

    def build_command(self, system_prompt: str, reasoning: ReasoningMode) -> list[str]:
        cmd = [
            self._executable,
            "run",
            "--format",
            "json",
            "--dir",
            str(self._repo_path),
            "--title",
            f"repowise_auto_{uuid.uuid4().hex}",
        ]
        if self._model:
            cmd.extend(["--model", self._model])
        return cmd

    def build_stdin(self, system_prompt: str, user_prompt: str) -> str:
        return _combine_prompt(system_prompt, user_prompt)

    def subprocess_env(self) -> dict[str, str]:
        return {
            **os.environ,
            "OPENCODE_CONFIG_CONTENT": _OPENCODE_READONLY_CONFIG,
            "OPENCODE_DISABLE_PROJECT_CONFIG": "true",
        }

    def stdout_error(self, stdout: str) -> str | None:
        return _error_event_message(stdout)

    def parse_output(self, stdout: str, stderr: str) -> GeneratedResponse:
        content, usage = _parse_jsonl(stdout)
        if not content:
            raise ProviderError(
                "opencode",
                "opencode run completed but no text was found in JSONL output.",
            )

        usage_payload = {
            **usage,
            "source": "opencode_run",
            "model": self.model_name,
            "stderr": tail(stderr, 1_000),
        }
        if not usage:
            usage_payload["estimated"] = True

        return GeneratedResponse(
            content=content,
            input_tokens=int(usage.get("input", 0) or 0),
            output_tokens=int(usage.get("output", 0) or 0),
            cached_tokens=int((usage.get("cache") or {}).get("read", 0) or 0),
            usage=usage_payload,
        )
