"""The rendered agent prompt one item's ``/prompt`` route returns."""

from __future__ import annotations

from pydantic import BaseModel

from repowise.core.agent_prompts import Flavor


class AgentPromptResponse(BaseModel):
    flavor: Flavor
    text: str
