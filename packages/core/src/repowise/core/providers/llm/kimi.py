"""Kimi provider for repowise.

Access Kimi models via the Kimi Code API at
https://api.kimi.com/coding/v1. The API is fully OpenAI-compatible — this provider
uses the openai Python SDK with a custom base_url, following the same pattern
as OpenRouterProvider.

Models:
    - kimi-for-coding  — coding-focused model [default]
    - kimi-for-coding-highspeed — faster Kimi Code model
    - kimi-k2.5        — K2.5 with instant and thinking modes
    - kimi-k2.6        — K2.6 with instant and thinking modes
    - kimi-k3          — K3 with graded reasoning effort
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

_DEFAULT_BASE_URL = "https://api.kimi.com/coding/v1"
_KIMI_K2_REASONING_MODES: tuple[ReasoningMode, ...] = ("off",)
_KIMI_K3_REASONING_MODES: tuple[ReasoningMode, ...] = ("low", "high", "max")


def _kimi_supported_reasoning_modes(model: str) -> tuple[ReasoningMode, ...]:
    if model.startswith(("kimi-k2.5", "kimi-k2.6")):
        return _KIMI_K2_REASONING_MODES
    if model.startswith("kimi-k3"):
        return _KIMI_K3_REASONING_MODES
    return ()


def _kimi_reasoning_kwargs(model: str, reasoning: ReasoningMode) -> dict[str, Any]:
    mode = normalize_reasoning(reasoning)
    if mode == "auto":
        return {}
    if model.startswith("kimi-k3"):
        return {"reasoning_effort": mode}
    return {"extra_body": {"thinking": {"type": "disabled"}}}


def _kimi_temperature(
    model: str,
    reasoning: ReasoningMode,
    requested: float,
) -> float:
    if model.startswith("kimi-for-coding"):
        # The Kimi coding endpoint rejects any temperature other than 1 for
        # these models ("invalid temperature: only 1 is allowed for this
        # model"), so the requested value cannot be honoured here.
        return 1.0
    if model.startswith(("kimi-k2.5", "kimi-k2.6")):
        return 0.6 if normalize_reasoning(reasoning) in ("off", "none") else 1.0
    return requested


def _kimi_notes(model_id: str, reasoning_modes: tuple[ReasoningMode, ...]) -> str:
    if model_id.startswith("kimi-k3"):
        return "K3 supports low, high, and max reasoning effort"
    return "thinking can be disabled for Kimi K2" if len(reasoning_modes) > 1 else ""


def _kimi_model_options(
    api_key: str,
    base_url: str,
    fallback_model: str,
) -> tuple[ProviderModelOption, ...]:
    return listed_model_options(
        api_key,
        base_url,
        fallback_model,
        reasoning_modes_for=_kimi_supported_reasoning_modes,
        notes_for=_kimi_notes,
    )


class KimiProvider(OpenAICompatibleProvider):
    """Kimi provider — access Kimi models via OpenAI-compatible API.

    Args:
        api_key:      Kimi API key. Falls back to KIMI_API_KEY env var.
        model:        Model identifier. Defaults to kimi-for-coding.
        base_url:     Override the Kimi API URL (rarely needed).
        rate_limiter: Optional RateLimiter instance.
        cost_tracker: Optional CostTracker instance for usage recording.
    """

    provider_id = "kimi"
    api_key_env = "KIMI_API_KEY"
    base_url_env = "KIMI_BASE_URL"
    default_base_url = _DEFAULT_BASE_URL
    reasoning_detail = (
        "Kimi /models lists IDs only; reasoning controls are enabled "
        "from the documented K2 and K3 model-family contracts."
    )
    # Status-less SDK errors are not retried for Kimi.
    wrap_connection_errors = False

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "kimi-for-coding",
        base_url: str | None = None,
        rate_limiter: RateLimiter | None = None,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        super().__init__(api_key, model, base_url, rate_limiter, cost_tracker)

    def available_model_options(self) -> tuple[ProviderModelOption, ...]:
        return _kimi_model_options(self._api_key, self._base_url, self._model)

    def _explicit_reasoning_modes(self, model: str) -> tuple[ReasoningMode, ...]:
        return _kimi_supported_reasoning_modes(model)

    def _reasoning_kwargs(self, reasoning: ReasoningMode) -> dict[str, Any]:
        return _kimi_reasoning_kwargs(self._model, reasoning)

    def _completion_kwargs(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
        reasoning: ReasoningMode,
    ) -> dict[str, Any]:
        return {
            **super()._completion_kwargs(messages, max_tokens, temperature, reasoning),
            "temperature": _kimi_temperature(self._model, reasoning, temperature),
            "top_p": 0.95,
        }

    def _stream_kwargs(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        return {
            **super()._stream_kwargs(messages, max_tokens, temperature),
            "temperature": _kimi_temperature(self._model, "auto", temperature),
            "top_p": 0.95,
        }
