"""One module per agent target.

Each exports a module-level ``TARGET`` implementing
:class:`~repowise.cli.agent_targets.types.AgentTarget`, plus the module-level
write functions that target owns. The registry resolves them lazily by
``module:attribute``, so nothing here is imported until a caller asks for that
specific agent.
"""
