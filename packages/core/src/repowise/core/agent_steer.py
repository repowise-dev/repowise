"""The one line every generated agent file leads with.

Text only, no imports: the CLI's instruction block imports it on the path that
runs on every invocation.

A block that listed the tools without saying to call one first moved almost
no agent, and MCP server instructions carrying the same text were not
followed, so the line lives in the files agents read. A plain "call a tool
first" was still skipped by models that open with Grep, run the CLI instead of
the MCP tool, or hand exploration to a subagent that never sees the file; each
clause here answers one of those. The MUST is scoped to questions about the
code, and a known file to edit goes straight to the edit, since a tool call
there only adds a turn. "Use what it returns" keeps an adopting model from
re-reading and re-grepping what the tool already showed, which is where the
extra tokens of a bare MUST went.
"""

AGENT_STEER = (
    "For any question about how, where or why this codebase works, or which files "
    "a task involves, you MUST call a Repowise MCP tool first (get_answer for "
    "how/where/why questions, search_codebase for an identifier, get_context for "
    "callers or a file) before Grep, find or reading files, and pass this rule on in "
    "any subagent brief. Use what it returns: open a cited file only for lines it did "
    "not show, and skip Grep when it marks a list complete. To edit a file you already "
    "know, go straight to it."
)
