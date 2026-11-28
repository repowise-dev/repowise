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

#: Who the agent is, alone. ``packages/ui`` mirrors it as ``REPO_ROLE``.
ROLE: dict[str, str] = {
    "generic": "You are a senior engineer in this repository.",
    "claude-code": "You are Claude Code in this repository.",
    "claude-code-mcp": "You are Claude Code in this repository, which Repowise indexes and serves over MCP.",
    "cursor": "Work in this repository.",
}

#: Who the agent is across a workspace's repositories. ``packages/ui`` mirrors
#: it as ``WORKSPACE_ROLE``.
WORKSPACE_ROLE: dict[str, str] = {
    "generic": "You are a senior engineer working across the repositories of one workspace.",
    "claude-code": "You are Claude Code working across the repositories of one workspace.",
    "claude-code-mcp": "You are Claude Code working across the repositories of one workspace indexed by repowise, with its MCP tools available.",
    "cursor": "Work across the repositories referenced below.",
}

_WHO: dict[str, str] = {
    flavor: f"{role} " + ("" if flavor == "claude-code-mcp" else _INDEX) for flavor, role in ROLE.items()
}

OPENINGS: dict[str, dict[str, str]] = {
    "action": {
        "generic": "produced the action below from its stored analysis of the repository (git history, code health, the dependency graph). Its evidence is listed in full or in part; treat each item as a lead to verify against the code, not as ground truth.",
        "claude-code": "produced the action below from its stored analysis. Treat each evidence item as a lead: read and search the code, confirm it, and keep a step list to track the files you work through.",
        "claude-code-mcp": 'The action below comes from Repowise\'s stored analysis. Use the MCP calls listed under "Look closer" before reading files by hand: they return the full evidence with line numbers, reasons and history. Treat each item as a lead to confirm against the code.',
        "cursor": "produced the action below from its stored analysis. Treat each evidence item as a lead: open the file with @file, confirm it, then act.",
    },
    "fix_first": {
        "generic": "ranked the change below first among the work it found. Its facts come from stored analysis; treat them as leads to confirm against the code, not as ground truth.",
        "claude-code": "ranked the change below first among the work it found. Confirm each fact by reading and searching the code before editing, and keep a step list.",
        "claude-code-mcp": 'Repowise ranked the change below first among the work it found. Call the MCP lookup under "Look closer" for the full record before reading files by hand, then confirm each fact against the code.',
        "cursor": "ranked the change below first among the work it found. Open the target with @file, confirm each fact, then make the change.",
    },
    "refactoring_plan": {
        "generic": "planned the refactoring below from its static analysis. Repowise does not edit code: the steps are a spec for you to apply. Read the code each step names and confirm it before you change it.",
        "claude-code": "planned the refactoring below from its static analysis. Repowise does not edit code: the steps are a spec for you to apply. Read and search each span before editing, and keep a step list.",
        "claude-code-mcp": 'Repowise planned the refactoring below from its static analysis. It does not edit code: the steps are a spec for you to apply. Use the MCP lookup under "Look closer" and `get_symbol` on each span before reading files by hand.',
        "cursor": "planned the refactoring below from its static analysis. Repowise does not edit code: the steps are a spec for you to apply. Open each span with @file and confirm it before editing.",
    },
    "refactoring_opportunity": {
        "generic": "composed the ordered refactoring steps below for one file from its static analysis. Repowise does not edit code: the steps are a spec for you to apply. Read the code each step names and confirm it before you change it.",
        "claude-code": "composed the ordered refactoring steps below for one file from its static analysis. Repowise does not edit code: the steps are a spec for you to apply. Read and search each span before editing, and keep a step list.",
        "claude-code-mcp": "Repowise composed the ordered refactoring steps below for one file from its static analysis. It does not edit code: the steps are a spec for you to apply. Pull the record with the call at the end and `get_symbol` each span before reading files by hand.",
        "cursor": "composed the ordered refactoring steps below for one file from its static analysis. Repowise does not edit code: the steps are a spec for you to apply. Open each span with @file and confirm it before editing.",
    },
    "perf_opportunity": {
        "generic": "found the performance cause below in its stored analysis and planned the steps to remove it. Repowise does not edit code: treat the cause as a lead to confirm against the code, and the steps as a spec.",
        "claude-code": "found the performance cause below in its stored analysis and planned the steps to remove it. Read the loop and what it iterates before editing, and keep a step list.",
        "claude-code-mcp": 'Repowise found the performance cause below in its stored analysis and planned the steps to remove it. Use the MCP lookup under "Look closer" for the full record before reading files by hand, then confirm the cause against the code.',
        "cursor": "found the performance cause below in its stored analysis and planned the steps to remove it. Open the function with @file, confirm the cause, then make the change.",
    },
    "first_look_item": {
        "generic": "listed the item below in the repo report it wrote after indexing the repository. Its facts come from stored analysis; treat them as leads to confirm against the code, not as ground truth.",
        "claude-code": "listed the item below in its repo report. Confirm each fact by reading and searching the code before editing, and keep a step list.",
        "claude-code-mcp": "Repowise listed the item below in its repo report. Use its MCP tools to look closer before reading files by hand, then confirm each fact against the code.",
        "cursor": "listed the item below in its repo report. Open each file with @file, confirm the fact, then act.",
    },
    "first_look_report": {
        "generic": "wrote the repo report below after indexing the repository: what to fix, in order. Its facts come from stored analysis; treat each item as a lead to confirm against the code.",
        "claude-code": "wrote the repo report below after indexing the repository: what to fix, in order. Keep a step list with one step per item, and confirm each item against the code before editing.",
        "claude-code-mcp": "Repowise wrote the repo report below after indexing the repository: what to fix, in order. Look each item up with its MCP tools before reading files by hand, and confirm it against the code.",
        "cursor": "wrote the repo report below after indexing the repository: what to fix, in order. Open each file with @file and confirm the item before editing.",
    },
}


def preamble(flavor: str, producer: str) -> str:
    return _WHO[flavor] + OPENINGS[producer][flavor]
