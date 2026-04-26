"""Gemini provider for repowise using the native google-genai SDK.

Uses the same google-genai SDK as GeminiEmbedder for consistency.
Runs the synchronous SDK call in a thread pool to avoid blocking asyncio.

Recommended models:
    - gemini-3.1-flash-lite-preview  — fast + cheap (default)
    - gemini-3-flash-preview         — higher quality
"""

from __future__ import annotations

import asyncio
import logging
import os

import structlog

# Suppress "Both GOOGLE_API_KEY and GEMINI_API_KEY are set" from google-genai SDK.
# We resolve and pass the key explicitly, so the env-var conflict warning is noise.
logging.getLogger("google_genai._api_client").setLevel(logging.ERROR)
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from repowise.core.providers.llm.base import (
    BaseProvider,
    ChatStreamEvent,
    ChatToolCall,
    GeneratedResponse,
    ProviderError,
    RateLimitError,
)

from typing import TYPE_CHECKING, Any, AsyncIterator
from repowise.core.rate_limiter import RateLimiter

if TYPE_CHECKING:
    from repowise.core.generation.cost_tracker import CostTracker

log = structlog.get_logger(__name__)

_MAX_RETRIES = 3
_MIN_WAIT = 1.0
_MAX_WAIT = 4.0


class GeminiProvider(BaseProvider):
    """Native Gemini provider using the google-genai SDK.

    Args:
        model:        Gemini model name. Defaults to gemini-3.1-flash-lite-preview.
        api_key:      Google API key. Falls back to GEMINI_API_KEY or GOOGLE_API_KEY env var.
        base_url:     Optional custom base URL (e.g., for proxy/self-hosted endpoints).
        rate_limiter: Optional RateLimiter instance.
        cost_tracker: Optional CostTracker for recording token usage and cost.
    """

    def __init__(
        self,
        model: str = "gemini-3.1-flash-lite-preview",
        api_key: str | None = None,
        base_url: str | None = None,
        rate_limiter: RateLimiter | None = None,
        cost_tracker: "CostTracker | None" = None,
    ) -> None:
        self._model = model
        self._api_key = (
            api_key
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )
        if not self._api_key:
            raise ProviderError(
                "gemini",
                "No API key found. Pass api_key= or set GEMINI_API_KEY / GOOGLE_API_KEY env var.",
            )
        self._base_url = base_url or os.environ.get("GEMINI_BASE_URL")
        self._rate_limiter = rate_limiter
        self._cost_tracker = cost_tracker
        self._client: object | None = None  # cached; created once on first call

    @property
    def provider_name(self) -> str:
        return "gemini"

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
        if self._rate_limiter:
            await self._rate_limiter.acquire(estimated_tokens=max_tokens)

        log.debug(
            "gemini.generate.start",
            model=self._model,
            max_tokens=max_tokens,
            request_id=request_id,
        )

        try:
            return await self._generate_with_retry(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                request_id=request_id,
            )
        except RetryError as exc:
            raise ProviderError(
                "gemini",
                f"All {_MAX_RETRIES} retries exhausted: {exc}",
            ) from exc

    @retry(
        retry=retry_if_exception_type(ProviderError),
        stop=stop_after_attempt(_MAX_RETRIES),
        wait=wait_exponential_jitter(initial=_MIN_WAIT, max=_MAX_WAIT),
        reraise=True,
    )
    async def _generate_with_retry(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        temperature: float,
        request_id: str | None,
    ) -> GeneratedResponse:
        # Capture self attrs for thread safety (avoids closing over self)
        model = self._model
        api_key = self._api_key
        base_url = self._base_url

        def _call_sync() -> GeneratedResponse:
            from google import genai  # type: ignore[import-untyped]
            from google.genai import types as genai_types  # type: ignore[import-untyped]

            if self._client is None:
                client_kwargs: dict[str, Any] = {"api_key": api_key}
                http_opts = None

                if base_url:
                    try:
                        http_opts = genai_types.HttpOptions(base_url=base_url)
                    except TypeError:
                        log.warning(
                            "gemini.http_options.base_url_unsupported",
                            base_url=base_url,
                        )

                if http_opts is not None:
                    try:
                        self._client = genai.Client(**client_kwargs, http_options=http_opts)
                    except TypeError:
                        log.warning(
                            "gemini.client.http_options_unsupported",
                            base_url=base_url,
                        )
                        self._client = genai.Client(**client_kwargs)
                else:
                    self._client = genai.Client(**client_kwargs)
            client = self._client
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=user_prompt,
                    config=genai_types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        temperature=temperature,
                        # max_output_tokens intentionally omitted — Gemini flash
                        # models default to 65k tokens, which is far better for
                        # documentation generation than any low cap we'd impose.
                    ),
                )
            except Exception as exc:
                exc_str = str(exc)
                status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
                if status_code == 429 or "429" in exc_str or "quota" in exc_str.lower():
                    raise RateLimitError("gemini", exc_str, status_code=429) from exc
                raise ProviderError("gemini", f"{type(exc).__name__}: {exc_str}") from exc

            usage = response.usage_metadata
            return GeneratedResponse(
                content=response.text or "",
                input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
                output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
                cached_tokens=getattr(usage, "cached_content_token_count", 0) or 0,
                usage={
                    "prompt_token_count": getattr(usage, "prompt_token_count", 0) or 0,
                    "candidates_token_count": getattr(usage, "candidates_token_count", 0) or 0,
                    "total_token_count": getattr(usage, "total_token_count", 0) or 0,
                } if usage else {},
            )

        try:
            result = await asyncio.to_thread(_call_sync)
        except (RateLimitError, ProviderError):
            raise
        except Exception as exc:
            raise ProviderError("gemini", f"{type(exc).__name__}: {exc}") from exc

        log.debug(
            "gemini.generate.done",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            request_id=request_id,
        )

        if self._cost_tracker is not None:
            try:
                asyncio.get_event_loop().create_task(
                    self._cost_tracker.record(
                        model=self._model,
                        input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens,
                        operation="doc_generation",
                        file_path=None,
                    )
                )
            except RuntimeError:
                pass  # No running event loop — skip async record

        return result

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
        """Stream chat using Gemini's native generate_content.

        Gemini requires thought_signature to be preserved on function_call
        parts when sending them back. To handle this, we use non-streaming
        generate_content and run the full agentic loop internally using
        native Content objects (which preserve thought signatures). The
        tool_executor callback is required for Gemini — if not provided
        and the model requests tool calls, a stop event with tool_use is
        yielded and the caller must handle it (though this will fail on
        the next round-trip due to missing thought signatures).
        """
        import json as _json

        model_name = self._model
        api_key = self._api_key
        base_url = self._base_url

        def _call_sync(contents, config):
            """Single Gemini generate_content call in thread."""
            from google import genai  # type: ignore[import-untyped]
            from google.genai import types as genai_types  # type: ignore[import-untyped]

            if self._client is None:
                http_opts = genai_types.HttpOptions(base_url=base_url) if base_url else None
                self._client = genai.Client(api_key=api_key, http_options=http_opts)
            client = self._client
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config,
                )
            except Exception as exc:
                exc_str = str(exc)
                status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
                if status_code == 429 or "429" in exc_str or "quota" in exc_str.lower():
                    raise RateLimitError("gemini", exc_str, status_code=429) from exc
                raise ProviderError("gemini", f"{type(exc).__name__}: {exc_str}") from exc
            return response

        from google.genai import types as genai_types  # type: ignore[import-untyped]

        # Convert OpenAI tools to Gemini FunctionDeclarations
        gemini_tools = None
        if tools:
            declarations = []
            for t in tools:
                fn = t.get("function", t)
                params = fn.get("parameters", {})
                declarations.append(genai_types.FunctionDeclaration(
                    name=fn["name"],
                    description=fn.get("description", ""),
                    parameters=params if params else None,
                ))
            gemini_tools = [genai_types.Tool(function_declarations=declarations)]

        config = genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,
            tools=gemini_tools,
        )

        # Convert initial OpenAI messages to Gemini native Content objects
        contents = _to_gemini_contents(messages)

        max_loops = 10
        for _ in range(max_loops):
            # Call Gemini
            try:
                response = await asyncio.to_thread(_call_sync, contents, config)
            except (RateLimitError, ProviderError):
                raise
            except Exception as exc:
                raise ProviderError("gemini", f"{type(exc).__name__}: {exc}") from exc

            if not response.candidates:
                yield ChatStreamEvent(type="stop", stop_reason="end_turn")
                return

            # The model's response content — preserved as-is for the next turn
            model_content = response.candidates[0].content

            # Extract events from response parts
            function_calls_found: list[tuple[str, str, dict]] = []
            for part in model_content.parts:
                if hasattr(part, "text") and part.text:
                    yield ChatStreamEvent(type="text_delta", text=part.text)
                elif hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call
                    args = dict(fc.args) if fc.args else {}
                    tc_id = f"gemini-{fc.name}-{id(part)}"
                    function_calls_found.append((tc_id, fc.name, args))
                    yield ChatStreamEvent(
                        type="tool_start",
                        tool_call=ChatToolCall(
                            id=tc_id,
                            name=fc.name,
                            arguments=args,
                        ),
                    )

            if not function_calls_found:
                yield ChatStreamEvent(type="stop", stop_reason="end_turn")
                return

            # If no tool_executor, yield stop and let the caller handle
            # (will break on next round-trip due to thought_signature, but
            # this is a fallback)
            if tool_executor is None:
                yield ChatStreamEvent(type="stop", stop_reason="tool_use")
                return

            # Execute tools and build function_response parts
            # Append the model's response (with thought signatures) to contents
            contents.append(model_content)

            response_parts = []
            for tc_id, name, args in function_calls_found:
                result = await tool_executor(name, args)
                yield ChatStreamEvent(
                    type="tool_result",
                    tool_call=ChatToolCall(id=tc_id, name=name, arguments=args),
                    tool_result_data=result,
                )
                response_parts.append(
                    genai_types.Part.from_function_response(
                        name=name,
                        response=result,
                    )
                )

            # Append tool results as a user turn
            contents.append(genai_types.Content(role="user", parts=response_parts))
            # Loop back to get the model's text response

        # Max loops reached
        yield ChatStreamEvent(type="stop", stop_reason="end_turn")


def _to_gemini_contents(messages: list[dict[str, Any]]) -> list:
    """Convert OpenAI-format messages to Gemini Content objects.

    Only used for the initial history conversion. Subsequent tool-call
    round-trips use native Gemini Content objects (preserving thought
    signatures).
    """
    from google.genai import types as genai_types  # type: ignore[import-untyped]
    import json as _json

    contents = []
    for msg in messages:
        role = msg.get("role", "")
        if role == "system":
            continue  # Handled via system_instruction

        gemini_role = "model" if role == "assistant" else "user"
        parts = []

        if role == "tool":
            # Tool result → function_response part
            content_str = msg.get("content", "{}")
            try:
                response_data = _json.loads(content_str) if isinstance(content_str, str) else content_str
            except Exception:
                response_data = {"result": content_str}
            parts.append(genai_types.Part.from_function_response(
                name=msg.get("name", "unknown"),
                response=response_data,
            ))
            gemini_role = "user"
        elif role == "assistant":
            text = msg.get("content")
            if text:
                parts.append(genai_types.Part.from_text(text=text))
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", {})
                args_str = fn.get("arguments", "{}")
                if isinstance(args_str, str):
                    try:
                        args = _json.loads(args_str)
                    except Exception:
                        args = {}
                else:
                    args = args_str
                parts.append(genai_types.Part.from_function_call(
                    name=fn.get("name", ""),
                    args=args,
                ))
        else:
            parts.append(genai_types.Part.from_text(text=msg.get("content", "")))

        if parts:
            contents.append(genai_types.Content(role=gemini_role, parts=parts))

    return contents
