# core/registry

Three small process-wide registries that let third-party packages extend
repowise without forking the source tree. Each registry is the seam
documented in [`docs/architecture/pluggable-storage.md`](../../../../../../docs/architecture/pluggable-storage.md).

## Purpose

The OSS CLI, MCP server, and pipeline orchestrator each hard-coded their
extension points before this subpackage existed. Plugins that wanted to
add a CLI command, MCP tool, or phase hook had to monkey-patch a
module-level singleton and hope their import landed at the right time.

These registries invert that dependency. Each command / tool / hook
registers itself on import; the host (CLI entry point, MCP server boot,
pipeline orchestrator) calls a single `apply` / `fire` method when it
needs the collected entries.

## Public API

| Module | Default instance | Purpose |
|---|---|---|
| `cli_registry` | `cli_registry` | Collect Click commands; `apply(root)` attaches them |
| `mcp_tool_registry` | `mcp_tool_registry` | Collect MCP tool functions; `apply(mcp)` registers them with FastMCP |
| `pipeline_hooks` | `pipeline_hooks` | Pre/post phase hooks; wrap a `ProgressCallback` with `HookProgressCallback` to fire them |

The convenience helpers `register_command(...)` and `register_hook(...)`
operate on the default singletons. Tests construct their own
`CLIRegistry()` / `MCPToolRegistry()` / `PipelineHookRegistry()` for
isolation.

## Internal layout

```
registry/
  __init__.py            # re-exports
  cli_registry.py        # CLIRegistry + cli_registry singleton
  mcp_tool_registry.py   # MCPToolRegistry + mcp_tool_registry singleton
  pipeline_hooks.py      # PipelineHookRegistry + HookProgressCallback + singleton
  README.md
```

Every file is under 200 lines; no module-level state beyond the three
singletons.

## Extension points

- **Adding a CLI command:** import the command object, then
  `register_command(my_command)`. Optional `parent=` attaches to a
  subgroup. Order of registration is preserved.
- **Adding an MCP tool:** decorate the function with
  `@mcp_tool_registry.register()` or
  `@mcp_tool_registry.register` (bare form). The host server calls
  `mcp_tool_registry.apply(mcp)` once at boot.
- **Adding a pipeline hook:** call `register_hook("parse", callback,
  when="post")`. The orchestrator already wraps its progress callback
  with `HookProgressCallback`, so the hook fires automatically.

## Tests

- `tests/unit/registry/test_cli_registry.py`
- `tests/unit/registry/test_mcp_tool_registry.py`
- `tests/unit/registry/test_pipeline_hooks.py`

Each test uses an isolated registry instance to avoid coupling across
tests.
