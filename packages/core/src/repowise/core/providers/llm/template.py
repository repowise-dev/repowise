"""Null provider for fully deterministic (no-LLM) generation.

``repowise init --index-only`` renders every wiki page from a Jinja template,
so there is no model to call and often no API key to call it with. The
generation engine still expects a :class:`BaseProvider` for its identity
fields (``provider_name`` / ``model_name`` end up as page provenance), so this
stands in for one.

:meth:`generate` raises rather than returning a stub: a provider call on the
deterministic path means a page type forgot to branch, and a silent empty page
would be far harder to notice than a traceback.
"""

from __future__ import annotations

from repowise.core.providers.llm.base import BaseProvider, GeneratedResponse
from repowise.core.reasoning import ReasoningMode

# Matches the ``provider_name`` the tier-2 file-page renderer has always
# stamped, so "was this page written by a model?" stays a single check
# (``provider_name == "template"``) across both paths.
TEMPLATE_PROVIDER_NAME = "template"


class TemplateProvider(BaseProvider):
    """Stands in for an LLM when every page is rendered from a template."""

    @property
    def provider_name(self) -> str:
        return TEMPLATE_PROVIDER_NAME

    @property
    def model_name(self) -> str:
        return TEMPLATE_PROVIDER_NAME

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        request_id: str | None = None,
        reasoning: ReasoningMode = "auto",
        cache_hints: tuple = (),
    ) -> GeneratedResponse:
        raise RuntimeError(
            "TemplateProvider.generate() was called: a page type reached the "
            "LLM path during deterministic generation. Every generate_* method "
            "must branch on GenerationConfig.deterministic."
        )
