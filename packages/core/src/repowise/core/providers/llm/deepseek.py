"""DeepSeek provider for repowise.

Access DeepSeek models (V4 Flash, V4 Pro) via the DeepSeek API at
https://api.deepseek.com. The API is fully OpenAI-compatible — this provider
uses the openai Python SDK with a custom base_url, following the same pattern
as OpenRouterProvider.

Models:
    - deepseek-v4-flash  — fast, economical (284B total / 13B active params) [default]
    - deepseek-v4-pro    — highest quality, full reasoning
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from repowise.core.providers.llm.base import ProviderModelOption
from repowise.core.providers.llm.openai_compat import (
    OpenAICompatibleProvider,
    listed_model_options,
)
from repowise.core.rate_limiter import RateLimiter
from repowise.core.reasoning import ReasoningMode, normalize_reasoning

if TYPE_CHECKING:
    from repowise.core.generation.cost_tracker import CostTracker

_DEFAULT_BASE_URL = "https://api.deepseek.com"
_DEEPSEEK_REASONING_MODES: tuple[ReasoningMode, ...] = (
    "off",
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)
_DEEPSEEK_REASONING_MODELS = frozenset(
    {
        "deepseek-flash",
        "deepseek-v4-pro",
        # Retired aliases remain accepted and are routed to V4.1 Flash.
        "deepseek-v4-flash",
        "deepseek-v4-flash-vision-exp",
    }
)


def _deepseek_supported_reasoning_modes(model: str) -> tuple[ReasoningMode, ...]:
    if model in _DEEPSEEK_REASONING_MODELS:
        return _DEEPSEEK_REASONING_MODES
    return ()


def _deepseek_reasoning_kwargs(reasoning: ReasoningMode) -> dict[str, Any]:
    mode = normalize_reasoning(reasoning)
    if mode == "auto":
        return {}
    if mode in ("off", "none"):
        return {"extra_body": {"thinking": {"type": "disabled"}}}
    effort = "low" if mode in ("minimal", "low") else "max" if mode == "max" else "high"
    return {
        "reasoning_effort": effort,
        "extra_body": {"thinking": {"type": "enabled"}},
    }


def _deepseek_notes(model_id: str, reasoning_modes: tuple[ReasoningMode, ...]) -> str:
    if len(reasoning_modes) > 1:
        return "thinking toggle and graded effort documented by DeepSeek"
    return ""


def _deepseek_model_options(
    api_key: str,
    base_url: str,
    fallback_model: str,
) -> tuple[ProviderModelOption, ...]:
    return listed_model_options(
        api_key,
        base_url,
        fallback_model,
        reasoning_modes_for=_deepseek_supported_reasoning_modes,
        notes_for=_deepseek_notes,
    )


class DeepSeekProvider(OpenAICompatibleProvider):
    """DeepSeek provider — access DeepSeek V4 models via OpenAI-compatible API.

    Args:
        api_key:      DeepSeek API key. Falls back to DEEPSEEK_API_KEY env var.
        model:        Model identifier. Defaults to deepseek-v4-flash.
        base_url:     Override the DeepSeek API URL (rarely needed).
        rate_limiter: Optional RateLimiter instance.
        cost_tracker: Optional CostTracker instance for usage recording.
    """

    provider_id = "deepseek"
    api_key_env = "DEEPSEEK_API_KEY"
    base_url_env = "DEEPSEEK_BASE_URL"
    default_base_url = _DEFAULT_BASE_URL
    reasoning_detail = (
        "DeepSeek /models lists IDs only; reasoning controls are enabled "
        "for the documented DeepSeek Flash and V4 model families."
    )

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "deepseek-v4-flash",
        base_url: str | None = None,
        rate_limiter: RateLimiter | None = None,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        super().__init__(api_key, model, base_url, rate_limiter, cost_tracker)

    def available_model_options(self) -> tuple[ProviderModelOption, ...]:
        return _deepseek_model_options(self._api_key, self._base_url, self._model)

    def _explicit_reasoning_modes(self, model: str) -> tuple[ReasoningMode, ...]:
        return _deepseek_supported_reasoning_modes(model)

    def _reasoning_kwargs(self, reasoning: ReasoningMode) -> dict[str, Any]:
        return _deepseek_reasoning_kwargs(reasoning)

    def _stream_kwargs(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        return {
            **super()._stream_kwargs(messages, max_tokens, temperature),
            # Thinking tool calls require reasoning_content from every prior
            # assistant turn. Repowise's shared chat history intentionally
            # does not retain hidden reasoning, so use DeepSeek's documented
            # non-thinking mode for this protocol instead of producing an
            # invalid follow-up request after the first tool call.
            "extra_body": {"thinking": {"type": "disabled"}},
        }
