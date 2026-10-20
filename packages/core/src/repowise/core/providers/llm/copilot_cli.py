"""GitHub Copilot CLI provider for repowise.

Runs the authenticated Copilot CLI as a tool-free JSONL text generator. The
shared agent-CLI base handles subprocess lifecycle, prompt transport, timeout,
cancellation, concurrency, and errors.
"""

from __future__ import annotations

from typing import Any

from repowise.core.providers.llm.agent_cli import (
    AgentCliProvider,
    iter_jsonl,
    model_label,
    normalize_model,
    tail,
)
from repowise.core.providers.llm.base import GeneratedResponse, ProviderError
from repowise.core.reasoning import ReasoningMode

# Copilot treats an unknown allowlist entry as no available tools. Omitting this
# flag exposes the CLI's default tools, so keep the explicit empty-list sentinel.
_NO_TOOLS_ALLOWLIST = "repowise_no_tools"


def _combine_prompt(system_prompt: str, user_prompt: str) -> str:
    return (
        "System instructions for this task:\n\n"
        f"{system_prompt.strip()}\n\n"
        "---\n\n"
        "User request and context:\n\n"
        f"{user_prompt.strip()}\n\n"
        "You have no tools available. Do not attempt to call tools; answer directly."
    )


def _token_count(value: object) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0, int(value))
    return 0


def _parse_jsonl(stdout: str) -> tuple[str, dict[str, Any]]:
    content_parts: list[str] = []
    accounting: dict[str, Any] = {}

    for event in iter_jsonl(stdout):
        if not isinstance(event, dict):
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue

        if event.get("type") == "assistant.message":
            content = data.get("content")
            if isinstance(content, str) and content.strip():
                content_parts.append(content)
        elif event.get("type") == "session.usage_checkpoint":
            snapshot = data.get("accountingSnapshot")
            if isinstance(snapshot, dict):
                accounting = snapshot

    return "\n".join(content_parts), accounting


def _model_label(model: str | None) -> str:
    return model_label(normalize_model(model, "copilot_cli", None), "copilot_cli")


class CopilotCliProvider(AgentCliProvider):
    """LLM provider backed by the user's authenticated GitHub Copilot CLI."""

    provider_name = "copilot_cli"
    agent_slug = "copilot"
    command_label = "copilot"
    concurrency_env = "REPOWISE_COPILOT_CLI_CONCURRENCY"

    def build_command(self, system_prompt: str, reasoning: ReasoningMode) -> list[str]:
        cmd = [
            self._executable,
            "--output-format",
            "json",
            "--no-ask-user",
            f"--available-tools={_NO_TOOLS_ALLOWLIST}",
            "--disable-builtin-mcps",
            "--no-custom-instructions",
            "--silent",
            "-C",
            str(self._repo_path),
        ]
        if self._model:
            cmd.extend(["--model", self._model])
        return cmd

    def build_stdin(self, system_prompt: str, user_prompt: str) -> str:
        return _combine_prompt(system_prompt, user_prompt)

    def parse_output(self, stdout: str, stderr: str) -> GeneratedResponse:
        content, accounting = _parse_jsonl(stdout)
        if not content:
            raise ProviderError(
                "copilot_cli",
                "copilot completed but no text was found in JSONL output.",
            )

        token_details = accounting.get("tokenDetails")
        token_details = token_details if isinstance(token_details, dict) else {}
        cache_read = token_details.get("cache_read")
        cache_read = cache_read if isinstance(cache_read, dict) else {}
        input_tokens = _token_count(accounting.get("lastCallInputTokens"))
        output_tokens = _token_count(accounting.get("lastCallOutputTokens"))
        usage: dict[str, Any] = {
            "source": "copilot_cli_jsonl",
            "model": self.model_name,
            "stderr": tail(stderr, 1_000),
        }
        if accounting:
            if "totalPremiumRequestCost" in accounting:
                usage["totalPremiumRequestCost"] = accounting["totalPremiumRequestCost"]
        else:
            usage["estimated"] = True

        return GeneratedResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=_token_count(cache_read.get("tokenCount")),
            usage=usage,
        )