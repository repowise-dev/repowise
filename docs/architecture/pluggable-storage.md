# Pluggable storage and capability seams

repowise ships with sensible defaults — a local SQLite database, an
in-process NetworkX graph, an in-memory or LanceDB vector store, a
hard-coded Click CLI, a fixed set of MCP tools. For a single developer
on a single machine those defaults are the right answer.

The minute a deployment grows past that — a larger repo that wants to
checkpoint its index, a team that backs persistence with PostgreSQL, a
plugin author who wants to add a CLI subcommand, an internal tool that
needs to observe pipeline phase transitions — the defaults stop being
the only answer. The seams documented below let those use cases be
served *without* forking repowise.

This document is the public-facing reference. It deliberately avoids
implementation details that change between releases; the source of
truth for each contract is the ABC in
[`packages/core/src/repowise/core/persistence/_interfaces`](../../packages/core/src/repowise/core/persistence/_interfaces)
and the registries in
[`packages/core/src/repowise/core/registry`](../../packages/core/src/repowise/core/registry).

---

## What's pluggable

Three storage contracts and three capability registries.

### Storage contracts

| ABC | Default impl | What it stores |
|---|---|---|
| `IndexStore` | `SqlIndexStore` | Repositories, pages, decisions, git metadata, dead code, health, coverage — every relational table |
| `GraphStore` | `InProcessGraphStore` | The code dependency graph: nodes, edges, file-level metrics (pagerank, betweenness, communities) |
| `JobStore` | `SqlJobStore` | Pipeline checkpoint/resume state, keyed on phase × repository |
| `VectorStore` | `InMemoryVectorStore`, `LanceDBVectorStore`, `PgVectorStore` | Embedding storage for page text and decision-record snippets |

`VectorStore` already existed before this release; the other three are
new. All four follow the same pattern — an async ABC plus one or more
concrete implementations bundled with repowise.

### Capability registries

| Registry | What it collects | When the host applies it |
|---|---|---|
| `cli_registry` | Click commands and groups | At CLI import time, via `cli_registry.apply(cli)` |
| `mcp_tool_registry` | MCP tool functions | At MCP server boot, via `mcp_tool_registry.apply(mcp)` |
| `pipeline_hooks` | Pre/post phase callbacks | On every phase transition, via `HookProgressCallback` wrapping the orchestrator's progress callback |

---

## When to author a plugin

Reach for these seams when you want one of:

- **A different storage backend.** PostgreSQL, MySQL, a hosted vector
  database — anything that honors the ABC methods works. Write the
  implementation, register it with the application boot code, and
  every caller that depends on the ABC is suddenly using your backend.
- **A larger repository than the defaults handle gracefully.** The
  `JobStore` contract is the foundation for checkpoint/resume; pair it
  with a `GitIndexTier`-aware indexer and a custom pipeline hook that
  flushes state on a fast cadence.
- **An additional CLI subcommand.** Register it through `cli_registry`
  and the root group picks it up at startup.
- **An additional MCP tool.** Decorate the function with
  `@mcp_tool_registry.register` (or the `@mcp_tool_registry.tool()`
  alias) and the FastMCP server attaches it at boot.
- **Cross-cutting observability.** Register a hook on
  `pipeline_hooks` to fire around any phase the orchestrator announces
  — traverse, parse, graph, git, co_change, dead_code, decisions,
  generation.

---

## Writing an `IndexStore`

```python
from repowise.core.persistence._interfaces import IndexStore
from repowise.core.persistence.stores import SqlIndexStore


class MyScopedIndexStore(SqlIndexStore):
    """Adds tenant-scoped filtering on top of the SQL default."""

    def __init__(self, session, *, tenant_id: str) -> None:
        super().__init__(session)
        self._tenant_id = tenant_id

    async def upsert_repository(self, **kwargs):
        kwargs.setdefault("settings", {})["tenant_id"] = self._tenant_id
        return await super().upsert_repository(**kwargs)

    # Override the read paths to filter by tenant_id, etc.
```

Or replace the storage entirely:

```python
from repowise.core.persistence._interfaces import IndexStore

class RemoteIndexStore(IndexStore):
    async def upsert_repository(self, *, name, local_path, **_):
        ...
    # implement every abstract method
```

The shared contract test in
[`tests/unit/persistence/test_interfaces_contract.py`](../../tests/unit/persistence/test_interfaces_contract.py)
parametrises over registered implementations. Adding your backend to
the parametrisation surfaces any contract drift immediately.

---

## Writing a `GraphStore`

```python
from repowise.core.persistence._interfaces import GraphStore

class RemoteGraphStore(GraphStore):
    def add_node(self, node_id, **attrs): ...
    def add_edge(self, source, target, **attrs): ...
    # ...
```

The contract is intentionally small. Implementations may compute
pagerank / betweenness / community membership however they like — the
in-tree default uses NetworkX, but a hosted graph database can return
its native metric outputs.

---

## Writing a `JobStore`

```python
from repowise.core.persistence._interfaces import JobRecord, JobState, JobStore

class FileJobStore(JobStore):
    async def create_job(self, *, repository_id, phase, metadata=None) -> JobRecord:
        ...
    async def checkpoint(self, job_id, cursor) -> JobRecord:
        ...
    # ...
```

`JobStore` is the foundation for crash-safe long-running pipelines.
The orchestrator integration that calls `create_job` / `checkpoint`
on every phase transition lands in a follow-up release; the contract
is published now so plugin authors can target it.

---

## Adding a CLI subcommand

```python
import click
from repowise.core.registry import register_command


@click.command()
def my_thing() -> None:
    """Description shown in `repowise --help`."""
    click.echo("hello")


register_command(my_thing)
```

Import the module that calls `register_command` before
`cli_registry.apply()` runs. In a plugin distributed as a separate
wheel, the simplest pattern is a top-level import in your plugin's
`__init__.py`.

---

## Adding an MCP tool

```python
from repowise.core.registry import mcp_tool_registry as mcp


@mcp.tool()
async def my_tool(arg: str) -> dict:
    """Tool description visible to MCP clients."""
    return {"arg": arg}
```

The host server calls `mcp_tool_registry.apply(mcp_instance)` once at
boot, attaching every registered tool to the FastMCP server. The
`.tool()` alias on the registry exists so call sites read the same as
they did against the FastMCP instance directly.

---

## Observing pipeline phases

```python
from repowise.core.registry import register_hook


def warm_caches(phase: str) -> None:
    ...


register_hook("graph", warm_caches, when="post")
register_hook("parse", lambda phase: ..., when="pre")
```

Hook callbacks receive the phase name as their only positional
argument. Exceptions raised inside a hook are caught and logged so a
broken plugin cannot derail the pipeline.

---

## Defaults stay the defaults

The seams add a layer of indirection but no behavior change for users
of the OSS defaults: `repowise init` still writes to
`.repowise/wiki.db`, the in-process graph still owns ingestion, the
nine MCP tools still expose the same surface, and `repowise --help`
lists the same commands in the same order. Plugins extend the
defaults; they don't replace them unless an integration explicitly
swaps an implementation in.
