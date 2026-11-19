"""The MCP server instructions: the text an agent reads before any tool call.

Hosts that defer tool schemas show the model only tool names and this string,
so it says when to call; tests pin the key lines and a length cap. Every line
or clause that names a tool is keyed by it and kept only when the server
serves that tool: a host calls a named tool even when it is unlisted.

Pure text with no server import, so any server that serves these tools can
send the same words.
"""

from __future__ import annotations

from collections.abc import Iterable

_HEAD = "repowise indexes this repository's code graph, git history and decisions."

TOOL_LINES = {
    "search_codebase": "- search_codebase(query) finds an identifier or literal: `lines` are read "
    "from live files; `complete: true` means every match.",
    "get_context": '- get_context(targets=[symbol], include=["references"]) before a rename: a '
    '`complete` list is the whole edit set. include=["files"] on a directory '
    "lists its files and tests.",
    "get_answer": "- get_answer(question): one how/where/why question per call.",
}
CHECK_CLAUSES = {
    "get_risk": "get_risk before editing a widely imported file",
    "get_change_risk": "get_change_risk before committing",
    "get_why": "get_why before changing an established pattern",
}
NO_INDEX_LINE = (
    "No index: tell the user to run 'repowise init --yes --no-prose' (no "
    "API key or model calls). Suggest it, do not run it yourself."
)

#: Opt-in workspace tools, named only in a workspace whose surface has them.
WORKSPACE_TOOL_LINES = {
    "get_architecture": "get_architecture for the workspace's coupling and architecture score",
    "get_blast_radius": "get_blast_radius for the services a change reaches in other repos",
}


def server_instructions(
    served: Iterable[str] | None,
    *,
    repo_line: str | None = None,
    workspace: bool = False,
    extra: Iterable[str] | None = None,
) -> str:
    """The server instructions, naming a tool only when it is served.

    *served* is the surface the server serves; ``None`` (before any selection)
    names every core tool but no workspace tool. *repo_line* says what get_why
    holds for the served repository. *workspace* says the server serves a
    workspace, the only place the workspace tools are named. *extra* lines
    follow everything else, verbatim.
    """
    names = set(served) if served is not None else set(TOOL_LINES) | set(CHECK_CLAUSES)
    lines = [_HEAD]
    lines += [line for name, line in TOOL_LINES.items() if name in names]
    clauses = [clause for name, clause in CHECK_CLAUSES.items() if name in names]
    if clauses:
        lines.append(f"- {', '.join(clauses)}.")
    lines.append(NO_INDEX_LINE)
    if repo_line and "get_why" in names:
        lines.append(repo_line)
    if workspace:
        tools = [line for name, line in WORKSPACE_TOOL_LINES.items() if name in names]
        if tools:
            lines.append(f"In this workspace, use {' and '.join(tools)}.")
    lines += extra or ()
    return "\n".join(lines)


__all__ = [
    "CHECK_CLAUSES",
    "NO_INDEX_LINE",
    "TOOL_LINES",
    "WORKSPACE_TOOL_LINES",
    "server_instructions",
]
