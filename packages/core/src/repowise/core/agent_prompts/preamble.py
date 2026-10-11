"""The opening an agent prompt starts with: who the agent is, then what it holds.

The first half names the agent and is the same for every producer; the second
says what Repowise handed over and how to treat it, so it is the producer's own.
A new producer adds a row to ``OPENINGS``.
"""

from __future__ import annotations

from typing import Literal, get_args

Flavor = Literal["generic", "claude-code", "claude-code-mcp", "cursor"]
FLAVORS: tuple[str, ...] = get_args(Flavor)

_INDEX = "Repowise, a code-intelligence index, "

_WHO: dict[str, str] = {
    "generic": "You are a senior engineer in this repository. " + _INDEX,
    "claude-code": "You are Claude Code in this repository. " + _INDEX,
    "claude-code-mcp": "You are Claude Code in this repository, which Repowise indexes and serves over MCP. ",
    "cursor": "Work in this repository. " + _INDEX,
}

OPENINGS: dict[str, dict[str, str]] = {
    "action": {
        "generic": "produced the action below from its stored analysis of the repository (git history, code health, the dependency graph). Its evidence is listed in full or in part; treat each item as a lead to verify against the code, not as ground truth.",
        "claude-code": "produced the action below from its stored analysis. Treat each evidence item as a lead: read the code with Read and Grep, confirm it, and use TodoWrite to track the files you work through.",
        "claude-code-mcp": 'The action below comes from Repowise\'s stored analysis. Use the MCP calls listed under "Look closer" before reading files by hand: they return the full evidence with line numbers, reasons and history. Treat each item as a lead to confirm against the code.',
        "cursor": "produced the action below from its stored analysis. Treat each evidence item as a lead: open the file with @file, confirm it, then act.",
    },
    "fix_first": {
        "generic": "ranked the change below first among the work it found. Its facts come from stored analysis; treat them as leads to confirm against the code, not as ground truth.",
        "claude-code": "ranked the change below first among the work it found. Confirm each fact with Read and Grep before editing, and track the steps with TodoWrite.",
        "claude-code-mcp": 'Repowise ranked the change below first among the work it found. Call the MCP lookup under "Look closer" for the full record before reading files by hand, then confirm each fact against the code.',
        "cursor": "ranked the change below first among the work it found. Open the target with @file, confirm each fact, then make the change.",
    },
    "refactoring_plan": {
        "generic": "planned the refactoring below from its static analysis. Repowise does not edit code: the steps are a spec for you to apply. Read the code each step names and confirm it before you change it.",
        "claude-code": "planned the refactoring below from its static analysis. Repowise does not edit code: the steps are a spec for you to apply. Read each span with Read and Grep before editing, and track the steps with TodoWrite.",
        "claude-code-mcp": 'Repowise planned the refactoring below from its static analysis. It does not edit code: the steps are a spec for you to apply. Use the MCP lookup under "Look closer" and `get_symbol` on each span before reading files by hand.',
        "cursor": "planned the refactoring below from its static analysis. Repowise does not edit code: the steps are a spec for you to apply. Open each span with @file and confirm it before editing.",
    },
    "refactoring_opportunity": {
        "generic": "composed the ordered refactoring steps below for one file from its static analysis. Repowise does not edit code: the steps are a spec for you to apply. Read the code each step names and confirm it before you change it.",
        "claude-code": "composed the ordered refactoring steps below for one file from its static analysis. Repowise does not edit code: the steps are a spec for you to apply. Read each span with Read and Grep before editing, and track the steps with TodoWrite.",
        "claude-code-mcp": "Repowise composed the ordered refactoring steps below for one file from its static analysis. It does not edit code: the steps are a spec for you to apply. Pull the record with the call at the end and `get_symbol` each span before reading files by hand.",
        "cursor": "composed the ordered refactoring steps below for one file from its static analysis. Repowise does not edit code: the steps are a spec for you to apply. Open each span with @file and confirm it before editing.",
    },
}


def preamble(flavor: str, producer: str) -> str:
    return _WHO[flavor] + OPENINGS[producer][flavor]
