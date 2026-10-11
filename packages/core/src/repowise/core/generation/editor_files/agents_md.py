"""AgentsMdGenerator - generates and maintains AGENTS.md for Codex."""

from __future__ import annotations

from .base import BaseEditorFileGenerator


class AgentsMdGenerator(BaseEditorFileGenerator):
    """Generates and maintains a repo-level AGENTS.md file.

    The file has two sections:
      - User section outside the REPOWISE_AGENTS markers: never touched.
      - Repowise section between markers: regenerated from indexed data.
    """

    filename = "AGENTS.md"
    marker_tag = "REPOWISE_AGENTS"
    template_name = "agents_md.j2"
    user_placeholder = (
        "# AGENTS.md\n\n"
        "<!-- Add your project instructions above or below the Repowise section. "
        "Repowise only updates the managed section between markers. -->\n"
    )
