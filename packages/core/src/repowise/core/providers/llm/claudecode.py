"""Claude Code provider for repowise — uses `claude -p` subprocess.

Generates documentation by shelling out to the Claude Code CLI instead of
calling the Anthropic API directly. No API key required — uses the active
Claude Code session.

Usage:
    repowise init --provider claudecode --model haiku

Environment:
    No API key needed. Requires `claude` CLI to be installed and authenticated.

Supported models (via --model flag):
    haiku    → claude-haiku-4-5 (fast, cheap, good for docs)
    sonnet   → claude-sonnet-4-6 (better quality)
    opus     → claude-opus-4-6 (best quality)
"""

from __future__ import annotations

import asyncio
import json as _json
import time
from typing import Any, AsyncIterator

from repowise.core.providers.llm.base import (
    BaseProvider,
    ChatStreamEvent,
    ChatToolCall,
    GeneratedResponse,
    ProviderError,
)


class ClaudeCodeProvider(BaseProvider):
    """LLM provider that shells out to `claude -p` (Claude Code CLI).

    Implements both BaseProvider (for doc generation) and the ChatProvider
    protocol (for dashboard streaming chat), with no Anthropic API key required.

    Args:
        model:      Model alias passed to --model flag. Default: "haiku".
        timeout:    Subprocess timeout in seconds. Default: 120.
    """

    def __init__(
        self,
        model: str = "haiku",
        timeout: float = 120.0,
        **kwargs: Any,
    ) -> None:
        self._model = model
        self._timeout = timeout

    @property
    def provider_name(self) -> str:
        return "claudecode"

    @property
    def model_name(self) -> str:
        return self._model

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        request_id: str | None = None,
    ) -> GeneratedResponse:
        """Generate documentation via `claude -p` subprocess.

        Uses asyncio.create_subprocess_exec for true async concurrency —
        multiple pages can be generated in parallel without blocking.
        """
        start = time.monotonic()

        try:
            proc = await asyncio.create_subprocess_exec(
                "claude", "-p", system_prompt, "--model", self._model,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise ProviderError(
                "claudecode",
                "claude CLI not found. Is Claude Code installed and in PATH?",
            ) from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=user_prompt.encode()),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            raise ProviderError(
                "claudecode",
                f"claude -p timed out after {self._timeout}s",
            ) from exc

        if proc.returncode != 0:
            raise ProviderError(
                "claudecode",
                f"claude -p exited with code {proc.returncode}: {stderr.decode().strip()}",
            )

        content = stdout.decode().strip()
        elapsed = time.monotonic() - start

        # Estimate token counts (no exact count available from CLI)
        input_tokens = len(system_prompt + user_prompt) // 4
        output_tokens = len(content) // 4

        return GeneratedResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=0,
            usage={"elapsed_seconds": elapsed, "model": self._model},
        )

    # --- ChatProvider protocol implementation ---

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system_prompt: str,
        max_tokens: int = 8192,
        temperature: float = 0.7,
        request_id: str | None = None,
        tool_executor: Any | None = None,
    ) -> AsyncIterator[ChatStreamEvent]:
        """Stream a chat response via `claude -p --output-format stream-json --verbose`.

        Parses the NDJSON event stream emitted by Claude Code CLI and converts
        each event to a ChatStreamEvent. Tool use is not supported via the CLI
        streaming interface — tools parameter is accepted but ignored.

        The full conversation context is passed as the user prompt (formatted
        as a markdown conversation transcript), with the system_prompt as the
        CLI system argument.
        """
        # Build a flat conversation transcript from the messages list
        transcript_parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, list):
                # Extract text from content blocks
                content = " ".join(
                    block.get("text", "") for block in content if isinstance(block, dict)
                )
            if role == "system":
                continue
            transcript_parts.append(f"**{role.upper()}**: {content}")

        user_prompt = "\n\n".join(transcript_parts)

        try:
            proc = await asyncio.create_subprocess_exec(
                "claude",
                "-p", system_prompt,
                "--model", self._model,
                "--output-format", "stream-json",
                "--verbose",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise ProviderError(
                "claudecode",
                "claude CLI not found. Is Claude Code installed and in PATH?",
            ) from exc

        # Write user prompt and close stdin
        if proc.stdin:
            proc.stdin.write(user_prompt.encode())
            await proc.stdin.drain()
            proc.stdin.close()

        # Stream and parse NDJSON lines from stdout
        input_tokens = 0
        output_tokens = 0

        try:
            while True:
                try:
                    line = await asyncio.wait_for(
                        proc.stdout.readline(),
                        timeout=self._timeout,
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    raise ProviderError(
                        "claudecode",
                        f"claude streaming timed out after {self._timeout}s",
                    )

                if not line:
                    break

                line_str = line.decode().strip()
                if not line_str:
                    continue

                try:
                    event = _json.loads(line_str)
                except _json.JSONDecodeError:
                    continue

                event_type = event.get("type", "")

                # assistant message with text content
                if event_type == "assistant":
                    message = event.get("message", {})
                    for block in message.get("content", []):
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "")
                            if text:
                                yield ChatStreamEvent(type="text_delta", text=text)

                # result event carries final usage stats
                elif event_type == "result":
                    usage = event.get("usage", {})
                    input_tokens = usage.get("input_tokens", 0)
                    output_tokens = usage.get("output_tokens", 0)
                    if input_tokens or output_tokens:
                        yield ChatStreamEvent(
                            type="usage",
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                        )

        finally:
            # Ensure process is cleaned up
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()

        yield ChatStreamEvent(type="stop", stop_reason="end_turn")
