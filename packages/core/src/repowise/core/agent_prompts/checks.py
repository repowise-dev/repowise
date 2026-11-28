"""Asks for the check tools, offered where an agent has not used one yet.

Each names Repowise, so the agent reaches for its tools. ``packages/ui`` mirrors
:data:`CHECK_PROMPTS` in ``savings/check-prompts.ts``, held equal by a parity
test.
"""

from __future__ import annotations

#: One ask per check tool, and ``switch_profile`` to give an agent the standard
#: tool profile. ``{agent}`` is the agent's display name.
CHECK_PROMPTS: dict[str, str] = {
    "get_change_risk": (
        "Before you commit, use Repowise to check how risky this change is and what it could break."
    ),
    "get_why": (
        "Before you change a file, use Repowise to look up why it is the way it is "
        "and which decisions govern it."
    ),
    "get_risk": (
        "Before you edit a file, use Repowise to check how risky it is: "
        "whether it is a hotspot and what usually changes with it."
    ),
    "get_health": (
        "Use Repowise to check the health of the code you are about to change, "
        "and tell me what it finds."
    ),
    "get_dead_code": "Before you clean up, use Repowise to find which code in this area is unused.",
    "switch_profile": (
        "Give {agent} Repowise's standard MCP tool profile for this repository by running "
        "`repowise config set mcp.tools standard`, then tell me to restart {agent} "
        "so it loads the check tools."
    ),
}


__all__ = ["CHECK_PROMPTS"]
