"""Cheaper Inference provider for repowise.

Routes requests to models from several labs (GPT, Claude, Gemini, DeepSeek, GLM)
through a single API key via Cheaper Inference's OpenAI-compatible gateway at
https://api.cheaperinference.com/v1.

No additional pip install required, it uses the ``openai`` package with a custom
base_url, following the same pattern as EdenAIProvider / DeepSeekProvider.

Models use bare ids:
    - gpt-5.4-mini     fast, economical [default]
    - gpt-5.4
    - claude-sonnet-5
    - gemini-3.1-pro
    - deepseek-v4-flash

The live catalogue is at https://api.cheaperinference.com/v1/models. It also lists
image and video models; only text models are offered for selection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from repowise.core.providers.llm.base import ProviderModelOption, fallback_model_option
from repowise.core.providers.llm.openai_compat import (
    OpenAICompatibleProvider,
    fetch_model_list,
)
from repowise.core.rate_limiter import RateLimiter
from repowise.core.reasoning import ReasoningMode

if TYPE_CHECKING:
    from repowise.core.generation.cost_tracker import CostTracker

_DEFAULT_BASE_URL = "https://api.cheaperinference.com/v1"


def _cheaperinference_model_options(
    api_key: str,
    base_url: str,
    fallback_model: str,
) -> tuple[ProviderModelOption, ...]:
    """Text models from ``GET /models``; falls back to the configured model."""
    fallback = fallback_model_option(fallback_model, reasoning_modes=("auto",))
    data = fetch_model_list(api_key, base_url)
    if data is None:
        return (fallback,)

    options: list[ProviderModelOption] = []
    for raw in data:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            continue
        # The catalogue also carries image and video models.
        if raw.get("type", "text") != "text":
            continue
        model_id = raw["id"]
        options.append(
            ProviderModelOption(
                model=model_id,
                label=model_id,
                reasoning_modes=("auto",),
                recommended=model_id == fallback_model,
                source="api",
            )
        )

    if not options:
        return (fallback,)
    options.sort(key=lambda option: option.model)
    return tuple(options)


class CheaperInferenceProvider(OpenAICompatibleProvider):
    """Cheaper Inference provider, reaching several labs via one OpenAI-compatible key.

    Args:
        api_key:      Cheaper Inference API key. Falls back to
                      CHEAPER_INFERENCE_API_KEY env var.
        model:        Model identifier. Defaults to ``gpt-5.4-mini``.
        base_url:     Override the API URL. Falls back to
                      CHEAPER_INFERENCE_BASE_URL, then the default endpoint.
        rate_limiter: Optional RateLimiter instance.
        cost_tracker: Optional CostTracker instance for usage recording.
    """

    provider_id = "cheaperinference"
    api_key_env = "CHEAPER_INFERENCE_API_KEY"
    base_url_env = "CHEAPER_INFERENCE_BASE_URL"
    default_base_url = _DEFAULT_BASE_URL
    reasoning_detail = (
        "CheaperInferenceProvider sends no reasoning controls; models use their provider default."
    )

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-5.4-mini",
        base_url: str | None = None,
        rate_limiter: RateLimiter | None = None,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        super().__init__(api_key, model, base_url, rate_limiter, cost_tracker)

    def available_model_options(self) -> tuple[ProviderModelOption, ...]:
        return _cheaperinference_model_options(self._api_key, self._base_url, self._model)

    def _clean_base_url(self, base_url: str) -> str:
        return base_url.rstrip("/")

    def _explicit_reasoning_modes(self, model: str) -> tuple[ReasoningMode, ...]:
        return ()

    def _reasoning_kwargs(self, reasoning: ReasoningMode) -> dict[str, Any]:
        return {}
