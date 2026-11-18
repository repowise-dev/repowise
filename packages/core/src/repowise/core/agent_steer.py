"""The one line every generated agent file leads with.

Text only, no imports: the CLI's instruction block imports it on the path that
runs on every invocation.

An agent given this line called a Repowise tool first in every measured run,
whether it sat in ``CLAUDE.md`` or arrived from a session-start hook; a block
that listed the tools without saying to call one first moved almost none. MCP
server instructions carrying the same text were not followed, so the line lives
in the files agents read, not there.
"""

AGENT_STEER = (
    "For any question about this codebase, call a Repowise tool first "
    "(get_answer for how/where/why questions, search_codebase for an identifier, "
    "get_context for callers or a file)."
)
