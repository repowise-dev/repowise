"""The one line every generated agent file leads with.

Text only, no imports: the CLI's instruction block imports it on the path that
runs on every invocation.

A block that listed the tools without saying to call one first moved almost
no agent, and MCP server instructions carrying the same text were not
followed, so the line lives in the files agents read.

There are two lines because hosts differ in what the model sees before its
first call. A host that loads every tool schema up front (Codex and the other
AGENTS.md readers) already starts with a Repowise call on ``AGENT_STEER``;
stronger wording there only traded shell calls for more tool calls and more
tokens. Claude Code defers MCP tool schemas, so the generated text is all the
model has to go on, and a plain "call a tool first" was skipped by models that
open with Grep, run the CLI instead of the MCP tool, or hand exploration to a
subagent that never sees the file. ``AGENT_STEER_DEFERRED`` answers each of
those, scopes the MUST to questions about the code, lets a known file go
straight to the edit, and says to use what the tool returned rather than
re-read and re-grep it, which is where the extra tokens of a bare MUST went.
"""

#: For hosts that load tool schemas up front: AGENTS.md and the shared block.
AGENT_STEER = (
    "For any question about this codebase, call a Repowise tool first "
    "(get_answer for how/where/why questions, search_codebase for an identifier, "
    "get_context for callers or a file)."
)

#: For Claude Code, which defers MCP tool schemas: CLAUDE.md and its hook.
AGENT_STEER_DEFERRED = (
    "For any question about how, where or why this codebase works, or which files "
    "a task involves, you MUST call a Repowise MCP tool first (get_answer for "
    "how/where/why questions, search_codebase for an identifier, get_context for "
    "callers or a file) before Grep, find or reading files, and pass this rule on in "
    "any subagent brief. Use what it returns: open a cited file only for lines it did "
    "not show, and skip Grep when it marks a list complete. To edit a file you already "
    "know, go straight to it."
)
