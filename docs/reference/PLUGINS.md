# Writing a repowise plugin

A plugin is a Python package that repowise imports at startup. It adds CLI
commands, MCP tools, pipeline hooks, model providers, or an implementation of a
named seam (an event sink, a report section, an authenticator, and so on),
without a fork. The working example is
[`examples/plugins/hello`](../../examples/plugins/hello/); this page explains
each step.

The plugin API is experimental. Read [Stability](#stability) before you publish.

## What a plugin can do

| Goal | Register with | Kind in `repowise plugins` |
|------|---------------|----------------------------|
| Add a CLI command | `register_command(cmd)` or `register_lazy_command(name, "module:attr")` | `cli-command` |
| Add an MCP tool | `mcp_tool_registry.register(default=False)(fn)` | `mcp-tool` |
| Run code before or after a pipeline phase | `register_hook(phase, callback, when="pre"\|"post")` | `pipeline-hook` |
| Add an LLM provider | `register_provider(name, factory)` | `llm-provider` |
| Add an embedding provider | `register_embedder(name, factory)` | `embedder` |
| Add or replace an implementation of a seam | `<seam registry>.register(name, impl)` | the seam name, for example `event-sink` |

Every row is listed in the [extension points catalog](#extension-points-catalog).

## Plugin ideas

| Idea | Uses | What it does |
|------|------|--------------|
| Slack or Teams notifier | `event-sink` | Posts component events (a new hotspot, an ownership shift) to a channel |
| Webhook or SIEM forwarder | `event-sink` | Sends each event as CloudEvents JSON to an HTTP endpoint or a log pipeline |
| Self-hosted model gateway | `llm-provider` | Routes wiki generation and chat through your own model gateway |
| Local embedding model | `embedder` | Embeds pages with a model that runs on your machines |
| LDAP or Okta teams | `team-directory` | Reads team membership from your directory for declared ownership |
| SSO for `repowise serve` | `authenticator` | Accepts your identity provider's tokens on the HTTP API |
| Jira or Linear links | `change-metadata` | Fills linked issues, reviewers and labels on change records from your tracker |
| Internal vulnerability feed | `advisory-source` | Reads advisories from your own feed for `repowise packages import` |
| Compliance report section or format | `report-section`, `report-renderer` | Adds evidence your auditors ask for, or writes the report in their text format |
| Internal API as an agent tool | `mcp-tool` | Gives agents a read-only tool that queries an internal service |
| Team shortcut command | `cli-command` | Wraps a repowise workflow your team runs often in one command |
| Phase timings | `pipeline-hook` | Records when each index phase starts and ends and sends the timings to your metrics system |
| Extract an unsupported document type | `document-extractors` | Converts a plugin-supported file type to Markdown for the existing ingestion path |

## Build your first plugin

1. Write a module with a `register()` function. repowise calls it once per
   process, with no arguments.

   ```python
   # src/repowise_hello/__init__.py
   import click

   from repowise.core.registry import register_command


   @click.command("hello")
   def hello_command() -> None:
       """Say hello."""
       click.echo("hello")


   def register() -> None:
       register_command(hello_command)
   ```

2. Declare an entry point in the `repowise.plugins` group. The value is a
   module (imported, nothing called) or `module:callable` (imported, then
   called).

   ```toml
   # pyproject.toml
   [project]
   name = "repowise-hello"
   version = "0.1.0"
   dependencies = ["repowise>=0.59"]

   [project.entry-points."repowise.plugins"]
   hello = "repowise_hello:register"
   ```

3. Install it into the environment repowise runs from.

   ```bash
   pip install -e .
   ```

For local work without installing, list targets in `REPOWISE_PLUGINS`
(comma-separated, same `module` or `module:callable` form). The module must be
importable, so put its source directory on `PYTHONPATH`:

```bash
PYTHONPATH=src REPOWISE_PLUGINS=repowise_hello:register repowise plugins
```

| Source | Form | Read from |
|--------|------|-----------|
| Entry point | `name = "module"` or `name = "module:callable"` in group `repowise.plugins` | installed package metadata |
| `REPOWISE_PLUGINS` | `module` or `module:callable`, comma-separated | the process environment only, never a repository's `.repowise/.env` |

A target listed both ways loads once. Register inside `register()`, not at
module import time, so a test can import your module without side effects.

## Check it loaded

```bash
repowise plugins
repowise plugins --format json
```

The listing shows each plugin with its source, target, status (`loaded`,
`failed` with the error, or `skipped` for a duplicate) and everything it
registered as `"<kind>:<name>"` strings. It then lists every seam with the
implementation `get()` picks and all registered names. The JSON form carries
the same data:

```json
{
  "api_version": 1,
  "plugins": [
    {
      "name": "hello",
      "source": "entry point",
      "target": "repowise_hello:register",
      "status": "loaded",
      "error": null,
      "seams": ["cli-command:hello", "mcp-tool:hello_greet", "event-sink:hello-stderr", "report-section:hello"]
    }
  ],
  "seams": {
    "event-sink": {"default": "jsonl", "implementations": ["jsonl", "stdout", "hello-stderr"]}
  }
}
```

`api_version` is `PLUGIN_API_VERSION` from `repowise.core.registry`. Check it
in your plugin if you depend on a contract that may change:

```python
from repowise.core.registry import PLUGIN_API_VERSION

if PLUGIN_API_VERSION != 1:
    raise RuntimeError(f"repowise-hello supports plugin API 1, found {PLUGIN_API_VERSION}")
```

An exception raised from `register()` (or at import) marks the plugin
`failed`. repowise records it and carries on without it.

## When plugins load

Plugins load once per process, the first time repowise needs them:

| Moment | What triggers loading |
|--------|-----------------------|
| CLI | an unknown command name or a command listing (`repowise --help`); a built-in command runs without loading plugins |
| MCP server | server start, before tools attach |
| `repowise serve` | app creation |
| Index and update | each pipeline run, before hooks are wrapped |
| A seam | `get()` or `all()` on any seam |
| A provider | a lookup of an LLM provider or embedder name that is not built in |

## Extension points catalog

"One" means repowise uses a single selected implementation. "Every" means
repowise runs each registered implementation in registration order.

### Registries

| Kind | Register with | One or every | When it runs | On failure | Defined in |
|------|---------------|--------------|--------------|------------|------------|
| `cli-command` | `register_command(cmd, parent=None)`, `register_lazy_command(name, "module:attr", parent=None)` from `repowise.core.registry` | every | when the command is invoked | a plugin command whose name a built-in already holds is ignored with a warning | `packages/core/src/repowise/core/registry/cli_registry.py` |
| `mcp-tool` | `mcp_tool_registry.register(...)` from `repowise.core.registry` | every, filtered by tool selection | when an MCP client calls the tool | invalid `tier`/`safety` metadata raises `ValueError` at registration, so the plugin is recorded `failed` (registrations made before it stay) | `packages/core/src/repowise/core/registry/mcp_tool_registry.py` |
| `pipeline-hook` | `register_hook(phase, callback, when=...)` from `repowise.core.registry` | every | before (`pre`) or after (`post`) the named phase of `repowise init` and `repowise update` | an exception is logged and the pipeline continues | `packages/core/src/repowise/core/registry/pipeline_hooks.py` |
| `llm-provider` | `register_provider(name, factory)` from `repowise.core.providers.llm` | one, by name | when that provider name is selected | a name that a built-in provider holds raises `ValueError` | `packages/core/src/repowise/core/providers/llm/registry.py` |
| `embedder` | `register_embedder(name, factory)` from `repowise.core.providers.embedding` | one, by name | when that embedder name is selected | a name that a built-in embedder holds raises `ValueError` | `packages/core/src/repowise/core/providers/embedding/registry.py` |

`parent=` attaches a command under an existing Click group instead of the
root. For `register_lazy_command`, `name` must equal the Click command's own
name: the CLI lists it before importing the module.

Plugin MCP tools join the server's registered surface. Register with
`default=False` (an opt-in tool) and users enable it with
`repowise mcp --tools "+your_tool"` or `mcp: {tools: ["+your_tool"]}` in
`.repowise/config.yaml`. A tool registered with `default=True` joins every
default surface, which costs every agent context.

Phase names a hook can target include `traverse`, `parse`, `graph.metrics`,
`git`, `co_change`, `dead_code`, `decisions`, `health`, `doc_drift`,
`generation` and `embedding`. A phase that a run skips fires no hooks. The
callback is a plain synchronous function that receives the phase name as its
only argument; an `async def` callback never runs its body.

```python
from repowise.core.registry import register_hook


def after_parse(phase: str) -> None:
    print(f"{phase} finished")


def register() -> None:
    register_hook("parse", after_parse, when="post")
```

An LLM provider factory receives the keyword arguments repowise builds for it
(`model`, plus `api_key` or `base_url` when set) and returns a
`BaseProvider` (`repowise.core.providers.llm.base`). An embedder factory
returns an `Embedder` (`repowise.core.providers.embedding.base`). Users select a
registered provider by name like a built-in one, for example
`REPOWISE_PROVIDER=<name>` or `REPOWISE_EMBEDDER=<name>`.

### Seams

Each seam is a `SeamRegistry` instance. Import the registry object from the
module in the last column and call `.register(name, impl)`, for example
`from repowise.core.events.sinks import event_sinks`.

| Seam | Registry object | One or every | When it runs | On failure | Defined in |
|------|-----------------|--------------|--------------|------------|------------|
| `event-sink` | `event_sinks` | every sink named in `events.sinks` (default: the seam default, `jsonl`) | after an index run stores component events | logged; the events stay in the store | `packages/core/src/repowise/core/events/sinks.py` |
| `index-persistence` | `index_persistence` | every | `apply` inside each index write's transaction, before commit, including init's checkpoint (mode `checkpoint`); `publish` after commit and cleanup, except after the checkpoint | an `apply` failure rolls back and propagates, except in the checkpoint, which logs it and leaves the final write to apply; a `publish` failure propagates and the commit stays; a cleanup failure after the commit skips `publish` | `packages/core/src/repowise/core/pipeline/index_persistence.py` |
| `report-section` | `report_sections` | every | `repowise report` builds a document | the section becomes one inconclusive item marked `failed`; other sections run | `packages/core/src/repowise/core/report/model.py` |
| `report-renderer` | `report_renderers` | one, chosen by `repowise report assessment --format` | when a report is written | propagates | `packages/core/src/repowise/core/report/render.py` |
| `change-outcome` | `change_outcome_providers` | every | when a change record's outcomes are read | `OutcomeUnknownError` marks the outcome unknown; any other exception propagates | `packages/core/src/repowise/core/changes/outcomes.py` |
| `change-metadata` | `change_metadata_sources` | one | when change records are stored | propagates | `packages/core/src/repowise/core/changes/source.py` |
| `suspect-signal` | `suspect_signals` | every | `repowise suspects` ranks candidate changes | that signal does not fire; the others still rank | `packages/core/src/repowise/core/suspects/rank.py` |
| `advisory-source` | `advisory_sources` | one | `repowise packages import` reads an advisory file | propagates | `packages/core/src/repowise/core/inventory/advisories.py` |
| `document-extractors` | `document_extractors` | every; later extension claim wins | During traversal and source reads for otherwise unsupported extensions | indexing propagates with plugin name and original path; optional previews omit unavailable content | `packages/core/src/repowise/core/ingestion/document_extractors.py` |
| `team-directory` | `team_directories` | one | when components and declared owners load | propagates | `packages/core/src/repowise/core/ownership/directory.py` |
| `authenticator` | `authenticators` | one | each `repowise serve` request and MCP HTTP request | propagates | `packages/server/src/repowise/server/auth.py` |
| `authorizer` | `authorizers` | one | each repository or tool access check in `repowise serve`, MCP over HTTP and chat | propagates | `packages/server/src/repowise/server/auth.py` |
| `chat-tools` | `tool_backends` | one | each chat turn builds its tool backend | propagates | `packages/server/src/repowise/server/chat_seams.py` |
| `chat-store` | `conversation_stores` | one | each chat request opens its conversation store | propagates | `packages/server/src/repowise/server/chat_seams.py` |
| `chat-hooks` | `turn_hooks` | one | each chat turn (preflight, usage, events) | an `on_event` failure ends the turn with an error; `preflight` and `on_usage` failures propagate | `packages/server/src/repowise/server/chat_seams.py` |
| `chat-dataset` | `datasets` | every | the chat `chart` tool lists or loads a dataset | returned to the model as a tool error; the turn continues | `packages/server/src/repowise/server/chat_datasets/__init__.py` |
| `chat-view` | `view_builders` | every | chat builds a view of a tool result | logged and returned to the model as a tool error; the turn continues | `packages/server/src/repowise/server/chat_views.py` |

"Propagates" means the exception reaches the caller: the command, request or
chat turn fails with it. `get(name)` with a name no implementation holds raises
`KeyError` that lists the registered names; repowise never falls back to the
local default silently.

Each module defines the protocol its implementations follow next to the
registry, for example `EventSink.deliver` in `events/sinks.py`, `Section` and
`check()` in `report/model.py`, `TeamDirectory` in `ownership/directory.py`.
Read that protocol before you write an implementation. The
`chat-dataset` and `chat-view` seams have decorators,
`register_dataset(...)` and `register_view_builder(...)`, in the same modules.

### Authenticator and authorizer

An `Authenticator` turns a bearer token or a request into a `Principal`. An
`Authorizer` decides what that principal may reach. Both live in
`repowise.server.auth`.

| `Principal` field | Meaning |
|-------------------|---------|
| `id`, `kind` | Who is calling. `kind` is free text for your own principals, such as `"sso"` |
| `repo_scope`, `tool_scope` | Built-in scopes. `None` means unrestricted |
| `claims` | Your own data about the caller (roles, groups, tenant). repowise never reads it. It survives the MCP HTTP token round trip, so keep it JSON-serializable |

| Authorizer method | Arguments |
|-------------------|-----------|
| `can_access_repo(principal, identifiers, action=Action.READ)` | `identifiers` are the forms one repo is known by: its id, stored name or workspace alias, any of which may be `None`. On a workspace MCP server the id is read from each repo's index, so a scope written in repo ids works there too. An empty tuple is a server-wide route that names no repo, such as registering one: decide from `action` alone |
| `can_use_tool(principal, tool, action=Action.READ)` | `action` is `WRITE` for a tool registered with `safety="mutating"`, or a tool the registry does not know |

`action` is `Action.READ` or `Action.WRITE`. A REST route's action follows its
HTTP method: GET, HEAD and OPTIONS read, anything else writes. A POST route that
only reads, such as chat, `generate/estimate` or `preflight`, declares it with
`@read_route` under the router decorator. Webhook deliveries are writes.

Declaring `action` opts in. An authorizer whose `can_access_repo` accepts an
`action` keyword (or `**kwargs`) is asked on every call, for every principal,
including routes that name no repo. One that does not keeps the original
contract: called without `action`, and asked about repos only for a principal
with a `repo_scope`. Both satisfy the `Authorizer` protocol, so an existing
plugin needs no change.

On MCP, `repo="all"` is refused for a principal with a `repo_scope`. For any
other principal, an action-aware authorizer must allow every repo it expands
to, or the call is refused.

A read-only role, with repos chosen by id:

```python
from repowise.server.auth import Action, authorizers


class RoleAuthorizer:
    def can_access_repo(self, principal, identifiers, action=Action.READ):
        roles = principal.claims.get("roles", ())
        if action is Action.WRITE and "writer" not in roles:
            return False
        allowed = principal.claims.get("repo_ids")
        ids = [i for i in identifiers if i]
        return allowed is None or not ids or any(i in allowed for i in ids)

    def can_use_tool(self, principal, tool, action=Action.READ):
        return action is Action.READ or "writer" in principal.claims.get("roles", ())


def register():
    authorizers.register("roles", RoleAuthorizer())
```

Pair it with an authenticator that returns
`Principal(id=..., kind="sso", claims={"roles": [...], "repo_ids": [...]})`,
then select both with `REPOWISE_SEAM_AUTHENTICATOR` and
`REPOWISE_SEAM_AUTHORIZER`.

The `document-extractors` seam is format-agnostic. A plugin claims lower-case
extensions that Repowise does not already process and returns Markdown from
`extract(path, data)`. Registration requires a non-empty set of valid
single-suffix extensions (for example `.pptx`, not `.drawio.xml`), a non-empty
string `version`, and a callable `extract` method.
Supported language extensions and files whose bytes identify a supported
language (including shebang scripts) keep their normal parser and cannot be
claimed by an extractor.

Repowise keeps the original path and owns discovery, exclusions, size checks,
change detection and deletion handling. The same current plugin claim is used
when parsing working-tree files and historical Git blobs. Historical blobs are
converted with the currently installed extractor; Repowise does not retain
historical plugin implementations. The extractor's name, version and original
source bytes participate in the parse-cache fingerprint. Extracted Markdown is
cached behind the shared source reader under `.repowise/document-cache/`, so
ingestion, historical blob parsing and appropriate MCP reads reuse conversion.
Entries are keyed by original path, source bytes, extractor name and version.
Cache corruption or unavailable storage causes reconversion, not lost content.
The cache has no automatic eviction; removing this directory only causes
reconversion. Bump `version` when
extraction behaviour changes, then run `repowise init --force` to rebuild the
repository index; an otherwise clean repository is not scanned just because a
plugin version changed. Check `repowise plugins` to see which extractor owns a
claimed extension. Change-history diffs use the current plugin for the
historical blob's path and bytes; they do not replay old plugin versions or
run a full repository traversal over each historical tree. Historical blob
routing uses the changed path and that blob's bytes, applying the current
invocation's exclusions, directory boundaries and source-size rules before
conversion. It does not reconstruct historical ignore files or configuration.

Extraction runs in the existing spawned parse-worker pool with the
`REPOWISE_PARSE_TIMEOUT_S` per-file deadline (60 seconds by default; `0`
disables the deadline). A timeout, worker crash, unavailable pool or extractor
exception fails the indexing run;
Repowise deliberately does not retry plugin code in-process. Source eligibility
still applies to the original document before conversion. There is no separate
size limit on extracted Markdown: output is expected to be smaller than the
eligible source, but this is an assumption, not a guarantee. A plugin that
expands small input can increase processing, cache and parent-process memory
usage. Workers also check
that the discovered extractor version matches the fingerprinted version. The
existing worker memory cap (`REPOWISE_PARSE_WORKER_MEMORY_MB`, 2048 MiB by
default; `0` disables it) is applied where the platform supports it, so these
are best-effort resource bounds rather than a hard host-memory
guarantee. Because workers use `spawn`, the plugin must be importable there:
install its entry point or make its module available through `REPOWISE_PLUGINS`
and the worker's environment. A registration made only dynamically in the
parent process is not sufficient. The deadline terminates Repowise's worker
processes, but cannot guarantee termination of a separate subprocess that a
plugin starts and detaches. Crash recovery may retry in-flight work, so plugins
must tolerate repeated calls. These limits do not bound aggregate extracted
content held by the parent process.

## Choosing an implementation

For a "one" seam, `get()` picks, first match wins:

| Order | Source | Example |
|-------|--------|---------|
| 1 | a name the caller passes, such as `--format html` | `report_renderers.get("html")` |
| 2 | `REPOWISE_SEAM_<NAME>` (seam name upper-cased, `-` becomes `_`) | `REPOWISE_SEAM_TEAM_DIRECTORY=backstage` |
| 3 | the last implementation registered with `default=True` | `team_directories.register("backstage", impl, default=True)` |
| 4 | the first implementation registered | the local default |

`REPOWISE_SEAM_<NAME>` is read from the process environment only, never from a
repository's `.repowise/.env`, so a cloned repository cannot choose code to
run. Register with `default=True` only when installing your package should
switch the seam on its own; otherwise register plainly and let the user opt in
with the variable.

For an "every" seam, registering is enough: repowise reads
`SeamRegistry.all()`, which loads plugins and returns every `(name, impl)` pair
in registration order. Registering a name that already exists replaces that
implementation.

```python
from repowise.core.report.model import report_sections

for name, section in report_sections.all():
    print(name, section.title)
```

## Testing a plugin

Test your implementations directly, then add one test that loads the plugin
the way repowise does.

| Need | Use |
|------|-----|
| A registry with nothing in it | `CLIRegistry()`, `MCPToolRegistry()`, `PipelineHookRegistry()` from `repowise.core.registry` |
| Load plugins again | `repowise.core.registry.plugins.reset()`, then `load_plugins()` |
| Load only your plugin | set `REPOWISE_PLUGINS` with `monkeypatch.setenv` and patch `importlib.metadata.entry_points` to return `[]` |
| What loaded | the `PluginRecord` list from `load_plugins()`: `status`, `error`, `seams` |

```python
import importlib.metadata

from repowise.core.registry import plugins


def test_plugin_loads(monkeypatch):
    monkeypatch.syspath_prepend("src")  # not needed once the package is installed
    monkeypatch.setenv(plugins.ENV_VAR, "repowise_hello:register")
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kw: [])
    plugins.reset()
    try:
        [record] = plugins.load_plugins()
        assert record.status == "loaded", record.error
        assert "report-section:hello" in record.seams
    finally:
        plugins.reset()
```

Seams are process-wide: a section or sink your test registers stays registered
for later tests in the same process. The repository's own test for the example,
[`tests/unit/registry/test_example_plugin.py`](../../tests/unit/registry/test_example_plugin.py),
shows how to restore each registry afterwards.

## Publishing

1. Name the distribution `repowise-<thing>` (for example `repowise-backstage`)
   and the import package `repowise_<thing>`, so users find it and it does not
   collide with repowise's own `repowise` namespace.
2. Depend on `repowise` with a lower bound on the version you tested against.
3. Build and upload to PyPI:

   ```bash
   pip install build twine
   python -m build
   python -m twine upload dist/*
   ```

4. In your README, give the install line, the seam names or commands you add,
   and any `REPOWISE_SEAM_<NAME>` value a user sets.

## Stability

The plugin API is experimental and can change in a minor release.

- A breaking change to a registry or seam contract bumps `PLUGIN_API_VERSION`
  and is named in [CHANGELOG.md](../CHANGELOG.md).
- Additive changes (a new seam, a new optional field) keep the version.
- Pin `repowise` to a range you test, and check `PLUGIN_API_VERSION` in
  `register()` if a mismatch would break silently.

## Proposing a new seam

When the point you need to extend has no seam, open an issue before any code.
Describe:

- the contract: the protocol or callable an implementation provides,
- when it runs and whether repowise uses one implementation or every one,
- what a failure does: propagate, log and continue, or mark a result unknown,
- the local default that ships in core.

Core contributors add seams following
[pluggable-storage.md](../architecture/pluggable-storage.md#adding-a-new-seam).
