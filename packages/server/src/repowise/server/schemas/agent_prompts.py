"""The rendered agent prompt one item's ``/prompt`` route returns."""

from __future__ import annotations

from pydantic import BaseModel

from repowise.core.agent_prompts import Flavor

from .preview import PreviewLabelled


class AgentPromptResponse(PreviewLabelled, BaseModel):
    flavor: Flavor
    text: str
    #: The measurement note when the item sits on a preview language cell
    #: (``finding_registry.preview_note``); the text then ends with it too.
    preview: str | None = None

    @classmethod
    def labelled(cls, flavor: Flavor, text: str, preview: str | None) -> AgentPromptResponse:
        """The prompt, with one closing line naming the preview when set, so
        the agent reading the text sees the measurement the page shows."""
        if preview:
            text = f"{text.rstrip()}\n\nThis language is served as a {preview}.\n"
        return cls(flavor=flavor, text=text, preview=preview)
