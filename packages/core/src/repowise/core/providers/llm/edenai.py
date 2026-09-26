"""Eden AI provider for repowise.

Routes requests to models from many vendors (Mistral, GPT, Claude, Gemini, Cohere,
DeepSeek, Llama) through a single API key via Eden AI's OpenAI-compatible gateway at
https://api.edenai.run/v3. Eden AI is EU-headquartered and offers an EU endpoint
(https://api.eu.edenai.run/v3) for data residency / GDPR-sensitive workloads.

No additional pip install required, it uses the ``openai`` package with a custom
base_url, following the same pattern as OpenRouterProvider / DeepSeekProvider.

Models use ``vendor/model`` format:
    - mistral/mistral-small-latest  fast, economical EU model [default]
    - openai/gpt-4o-mini            OpenAI small model
    - anthropic/claude-haiku-4-5    Anthropic Claude Haiku
    - google/gemini-2.5-flash       Google Gemini Flash

The live catalogue is public and needs no authentication at
https://api.edenai.run/v3/models.

Set the EU endpoint for data residency:
    export EDENAI_BASE_URL="https://api.eu.edenai.run/v3"
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

_DEFAULT_BASE_URL = "https://api.edenai.run/v3"


def _model_leaf(model: str) -> str:
    return model.rsplit("/", 1)[-1].lower()


def _supports_reasoning_effort(model: str) -> bool:
    """True for OpenAI reasoning models routed through Eden (vendor/model form)."""
    return _model_leaf(model).startswith(("gpt-5", "o1", "o3", "o4"))


def _edenai_supported_reasoning_modes(model: str) -> tuple[ReasoningMode, ...]:
    """Reasoning efforts Eden forwards to the underlying OpenAI reasoning model.

    Eden accepts the OpenAI ``reasoning_effort`` parameter, so support is keyed on
    the OpenAI reasoning model families. Non-OpenAI or non-reasoning models expose
    only ``auto`` (provider default).
    """
    if not _supports_reasoning_effort(model):
        return ()
    leaf = _model_leaf(model)
    if leaf.startswith("gpt-5.1"):
        return ("none", "low", "medium", "high")
    if leaf.startswith("gpt-5-pro"):
        return ("high",)
    if leaf.startswith("gpt-5"):
        return ("minimal", "low", "medium", "high")
    return ("low", "medium", "high")


def _edenai_reasoning_kwargs(reasoning: ReasoningMode) -> dict[str, Any]:
    """Translate a validated repowise reasoning intent to Eden AI kwargs."""
    mode = normalize_reasoning(reasoning)
    if mode in ("auto", "off"):
        return {}
    if mode in ("none", "minimal", "low", "medium", "high", "xhigh"):
        return {"reasoning_effort": mode}
    return {}


def _edenai_notes(model_id: str, reasoning_modes: tuple[ReasoningMode, ...]) -> str:
    if len(reasoning_modes) > 1:
        return "reasoning_effort forwarded to the underlying OpenAI model"
    return ""


def _edenai_model_options(
    api_key: str,
    base_url: str,
    fallback_model: str,
) -> tuple[ProviderModelOption, ...]:
    return listed_model_options(
        api_key,
        base_url,
        fallback_model,
        reasoning_modes_for=_edenai_supported_reasoning_modes,
        notes_for=_edenai_notes,
        sort=True,
    )


class EdenAIProvider(OpenAICompatibleProvider):
    """Eden AI provider, reaching many vendors via a single OpenAI-compatible key.

    Args:
        api_key:      Eden AI API key. Falls back to EDENAI_API_KEY env var.
        model:        Model identifier in ``vendor/model`` form. Defaults to
                      ``mistral/mistral-small-latest``.
        base_url:     Override the Eden AI API URL. Falls back to EDENAI_BASE_URL,
                      then the global endpoint. Set the EU endpoint
                      (``https://api.eu.edenai.run/v3``) for data residency.
        rate_limiter: Optional RateLimiter instance.
        cost_tracker: Optional CostTracker instance for usage recording.
    """

    provider_id = "edenai"
    api_key_env = "EDENAI_API_KEY"
    base_url_env = "EDENAI_BASE_URL"
    default_base_url = _DEFAULT_BASE_URL
    reasoning_detail = (
        "EdenAIProvider maps explicit efforts to the OpenAI reasoning_effort "
        "parameter for OpenAI reasoning model ids routed via Eden AI."
    )
    # Differences from the other gateways that predate the shared base class:
    # status-less SDK errors are not wrapped (so not retried), no stop reason
    # is reported, and spend is always recorded as doc generation.
    wrap_connection_errors = False
    reports_stop_reason = False
    cost_operation = "doc_generation"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "mistral/mistral-small-latest",
        base_url: str | None = None,
        rate_limiter: RateLimiter | None = None,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        super().__init__(api_key, model, base_url, rate_limiter, cost_tracker)

    def available_model_options(self) -> tuple[ProviderModelOption, ...]:
        return _edenai_model_options(self._api_key, self._base_url, self._model)

    def _clean_base_url(self, base_url: str) -> str:
        return base_url.rstrip("/")

    def _explicit_reasoning_modes(self, model: str) -> tuple[ReasoningMode, ...]:
        return _edenai_supported_reasoning_modes(model)

    def _reasoning_kwargs(self, reasoning: ReasoningMode) -> dict[str, Any]:
        return _edenai_reasoning_kwargs(reasoning)
