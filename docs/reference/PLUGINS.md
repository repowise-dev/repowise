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
| Create and change your own tables | `storage_migrations.register(StorageMigration(...))` | `storage-migration` |
| Declare settings and credentials | `register_settings(plugin_setting(...), ...)` | `setting` |

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
| Jira or Linear issues | `issue-source` | Links commits to your tracker's issues, so a commit tied to a Bug counts as a fix in prior defects, change risk and health |
| Internal vulnerability feed | `advisory-source` | Reads advisories from your own feed for `repowise packages import` |
| Compliance report section or format | `report-section`, `report-renderer` | Adds evidence your auditors ask for, or writes the report in their text format |
| Internal API as an agent tool | `mcp-tool` | Gives agents a read-only tool that queries an internal service |
| Team shortcut command | `cli-command` | Wraps a repowise workflow your team runs often in one command |
| Phase timings | `pipeline-hook` | Records when each index phase starts and ends and sends the timings to your metrics system |
| Database statement statistics | `evidence-source` | Reads a database's statement statistics on a schedule and stores them as production `statement_stats` runs |
| Extract an unsupported document type | `document-extractors` | Converts a plugin-supported file type to Markdown for the existing ingestion path |

## Community plugins

Plugins built and maintained outside this repository. Each is owned by its
author, so read its README for install steps and supported versions.

| Plugin | Uses | What it does |
|--------|------|--------------|
| [repowise-markitdown-plugin](https://github.com/digimangos/repowise-markitdown-plugin) | `document-extractors` | Indexes PDFs through Microsoft's MarkItDown, with results cited by the original PDF path |

To list yours, open a pull request that adds a row here.

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
`failed` with the error, `skipped` for a duplicate, or `pending` when a
[storage revision](#storage-revisions) it registered is not applied to the
index in the current directory) and everything it registered as
`"<kind>:<name>"` strings, followed by its storage revisions applied and
pending. It then lists every seam with the
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
      "seams": ["cli-command:hello", "mcp-tool:hello_greet", "event-sink:hello-stderr", "report-section:hello"],
      "storage": {"applied": [], "pending": []}
    }
  ],
  "seams": {
    "event-sink": {"default": "jsonl", "implementations": ["jsonl", "stdout", "hello-stderr"]}
  }
}
```

`api_version` is `PLUGIN_API_VERSION` from `repowise.core.registry`. A
plugin that relies on a contract added in a later API version declares the
lowest version it works with in its module, next to `register`:

```python
MIN_PLUGIN_API_VERSION = 1
```

If the installed repowise has a lower `PLUGIN_API_VERSION`, the plugin is
marked `failed` and `register()` is skipped. The error shows both versions:
upgrade repowise, or install a version of the plugin built for the older API.
repowise reads the attribute after importing your module, so import newer
repowise APIs inside `register()`. Otherwise an older install reports the
import error instead of the version mismatch.

An exception raised from `register()` (or at import) also marks the plugin
`failed`. Either way, repowise records it and carries on without it.

Each plugin row also says whether the plugin is `configured`. `missing` lists
each required [setting](#settings-for-a-plugin) that has no value, with the
command that sets it, so you or an agent can finish setup from the listing.
`settings` lists every setting the plugin declares with `set` true or false
(never the value), `next_command` is the one command to run next, and
`version` is the installed package version. A top-level `sources` list holds
each source on a fetching seam with its `plugin` (`null` for one built into
repowise), whether it is `configured`, and `last_sync` (`at`, `state`,
`stored_at`, `batches`, `message`) from its last
[sync](#syncing-a-source).

The web UI shows the same listing on the repository's Settings > Plugins page,
with a Sync now button for each configured source. The page reads
`GET /api/repos/{id}/plugins`, which returns this JSON, so an agent can call
it too. `POST /api/repos/{id}/plugins/sync` starts a sync of one source
(`{"source": "<name>"}`) or of every configured one (`{}`) and answers `202`
before it finishes.

## Check the contracts

```bash
repowise plugins check
repowise plugins check --format json
```

`repowise plugins check` loads your plugins and inspects each seam
implementation they registered. For a seam defined by a protocol, it checks
that every method and attribute is there, that each method takes the
parameters repowise passes, and that it is `async` when the protocol is. For a
seam built from a repowise class, such as `Section`, it checks the type.

A failure shows the rule that broke and how to fix it. The command exits 1 if
any check fails or any plugin fails to load. Registrations such as
`cli-command` and `mcp-tool` show as `skipped`, because repowise validates
them when they register. The checks never call your methods, so they need no
service or credentials.

```json
{
  "api_version": 1,
  "ok": false,
  "checks": [
    {
      "plugin": "repowise_hello:register",
      "kind": "event-sink",
      "name": "hello-stderr",
      "status": "fail",
      "failures": [
        {"seam": "event-sink", "name": "hello-stderr", "rule": "deliver is not async", "fix": "make `deliver` an `async def`"}
      ]
    }
  ]
}
```

## Settings for a plugin

A plugin declares its settings with `plugin_setting` and adds them from
`register()`. They join the settings registry in the `plugins` area, so
`repowise config list`, `config explain`, `config set`, `repowise doctor` and
the settings page show them like any other setting while the plugin is
installed.

```python
from repowise.core.settings.plugins import plugin_setting, register_settings

TEAM = plugin_setting("linear", "team", "str", None, "Linear team key to read issues from.")
TOKEN = plugin_setting("linear", "token", "str", None, "Linear API key.", secret=True)


def register():
    register_settings(TEAM, TOKEN)
```

| Kind | Key | Where its value comes from |
|------|-----|----------------------------|
| Plain | `plugins.<plugin>.<name>` | `.repowise/config.yaml`, or `REPOWISE_PLUGIN_<PLUGIN>_<NAME>` when the file is unset |
| Secret (`secret=True`) | `REPOWISE_PLUGIN_<PLUGIN>_<NAME>` | that environment variable only; listings show it as set, never its value |

The person using the plugin then sets them like any other setting:

```bash
repowise config set plugins.linear.team ENG
export REPOWISE_PLUGIN_LINEAR_TOKEN=lin_api_...
```

A setting with no default (`None`) is required. If a key or variable is
already in use, `register_settings` raises `ValueError` and the plugin is
marked `failed`. A
`plugins.<plugin>.*` key in `config.yaml` for a plugin that is not installed is
an unknown key, reported by `config` and `doctor` like any other.

## When plugins load

Plugins load once per process, the first time repowise needs them:

| Moment | What triggers loading |
|--------|-----------------------|
| CLI | an unknown command name or a command listing (`repowise --help`); a built-in command runs without loading plugins until it opens the index |
| Opening the index | `init_db`, and the read path's check for a store that is behind, so the store sees every [storage revision](#storage-revisions) |
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
| `storage-migration` | `storage_migrations.register(StorageMigration(...))` from `repowise.core.registry.storage_migrations` | every, in registration order within a namespace | when the index is opened, after the core schema, once per store | `StorageMigrationError` naming the plugin, namespace and revision; on a store that cannot take the write the plugin is `pending` instead | `packages/core/src/repowise/core/registry/storage_migrations.py` |

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

### Storage revisions

A plugin that keeps its own tables, such as the records an
`index-persistence` implementation writes, registers a revision for each
change to them. Registering touches no database:

```python
import hashlib
from pathlib import Path

from repowise.core.registry.storage_migrations import StorageMigration, storage_migrations

from . import v1  # one module per revision, with `async def apply(connection)`


def register() -> None:
    checksum = hashlib.sha256(Path(v1.__file__).read_bytes()).hexdigest()
    storage_migrations.register(StorageMigration("example.plugin", "v1", checksum, v1.apply))
```

Opening an index applies each revision not yet applied there, after the core
schema, in its own transaction with its row in `plugin_storage_revisions`,
under the writer lock (`BEGIN IMMEDIATE` on SQLite, an advisory lock on
PostgreSQL). `apply` receives the `AsyncConnection` and must not commit or roll
back. Revisions apply in the order a namespace registers them; namespaces are
independent. Alembic never sees them, so removing the plugin leaves nothing for
core to resolve. A failure raises `StorageMigrationError` naming the plugin,
namespace and revision.

- A changed checksum fails every open, and `repowise plugins` lists the plugin
  as `failed`. Add a new revision instead of editing one. The checksum above
  hashes the module's bytes, so a line-ending change or a comment edit to an
  applied revision fails every open too: keep an applied revision's file
  byte-stable, or hash a normalised string instead.
- An index that cannot take the write (read-only, or a SQLite store another
  writer holds past the busy timeout) still opens; the revision stays pending
  there and the plugin is `pending` until a later open in the same process
  applies it, or finds it applied, on every store where it was pending.
- A pending plugin's seam implementations are unready: `all()` leaves them out
  and `get()` raises `KeyError` naming the plugin and revision. Its MCP tools,
  CLI commands and pipeline hooks stay registered.

### Seams

Each seam is a `SeamRegistry` instance. Import the registry object from the
module in the last column and call `.register(name, impl)`, for example
`from repowise.core.events.sinks import event_sinks`.

| Seam | Registry object | One or every | When it runs | On failure | Defined in |
|------|-----------------|--------------|--------------|------------|------------|
| `event-sink` | `event_sinks` | every sink named in `events.sinks` (default: the seam default, `jsonl`) | after an index run stores component events | logged; the events stay in the store | `packages/core/src/repowise/core/events/sinks.py` |
| `index-persistence` | `index_persistence` | every | `apply` inside each index write's transaction, before commit, including init's checkpoint (mode `checkpoint`); `publish` after commit and cleanup, except after the checkpoint | an `apply` failure rolls back and propagates, except in the checkpoint, which logs it and leaves the final write to apply; a `publish` failure propagates and the commit stays; a cleanup failure after the commit skips `publish` | `packages/core/src/repowise/core/pipeline/index_persistence.py` |
| `report-section` | `report_sections` | every | `repowise report` builds a document | the section becomes one inconclusive item marked `failed`; other sections run | `packages/core/src/repowise/core/report/model.py` |
| `first-look-section` | `first_look_sections` | every | `repowise report repo` collects its sections | `UnavailableError` marks the section unavailable with its message; any other exception is logged and the section reads "Could not be read from this index."; other sections run | `packages/core/src/repowise/core/report/first_look/model.py` |
| `report-renderer` | `report_renderers` | one, chosen by `repowise report assessment --format` | when a report is written | propagates | `packages/core/src/repowise/core/report/render.py` |
| `change-outcome` | `change_outcome_providers` | every | when a change record's outcomes are read | `OutcomeUnknownError` marks the outcome unknown; any other exception propagates, and in `repowise report` it marks the outcomes item unavailable | `packages/core/src/repowise/core/changes/outcomes.py` |
| `change-metadata` | `change_metadata_sources` | one | when change records are stored, and by the `changes` report section | if a plugin source fails, repowise logs `change_metadata_failed` and stores the records with metadata from the built-in `git` source, marked `git`. If no implementation is registered under the configured name, it logs `changes_refresh_failed` and stores no new records. In the report, the `report-section` rule applies | `packages/core/src/repowise/core/changes/source.py` |
| `profile-format` | `profile_formats` | every, sniffed in registration order (built-in formats first); `evidence add --input-format` picks one | `repowise evidence add` reads a profile or benchmark results that are not `repowise-evidence-v1` | `EvidenceFormatError` refuses the file with its hint, and any other exception (including from `sniff`) propagates; a format gives `read` (a profile run) or `to_v1` (a v1 document of another kind, such as benchmark results, from the bytes; with `needs_checkout=True` it also gets the checkout the run used, for a tool that names packages, not files) | `packages/core/src/repowise/core/evidence/profile_formats/__init__.py` |
| `evidence-source` | `evidence_sources` | every; repowise syncs a source that declares `settings` once the required ones have values, and any other runs only when a host calls `run_fetch` | `repowise sources sync`, the end of `repowise update`, or a host calling `run_fetch` | `SourceUnavailable` keeps the cursor to retry from; `SourceRefused` returns its message for the user; any other exception fails that fetch. Batches stored before the failure stay, and a failing batch leaves nothing of itself | `packages/core/src/repowise/core/evidence/sources.py` |
| `issue-source` | `issue_sources` | every; repowise syncs a source that declares `settings` once the required ones have values, and any other runs only when a host fetches itself | `repowise sources sync`, the start of `repowise update`, or a host calling `run_issue_fetch` with its own store | `SourceUnavailable` and `SourceRefused` as for `evidence-source`; any other exception, or a link that breaks the contract, fails that fetch. Links read before a failure are stored; only a complete fetch removes links it no longer returns | `packages/core/src/repowise/core/issues/sources.py` |
| `suspect-signal` | `suspect_signals` | every | `repowise suspects` ranks candidate changes | that signal does not fire; the others still rank | `packages/core/src/repowise/core/suspects/rank.py` |
| `advisory-source` | `advisory_sources` | one | `repowise packages import` reads an advisory file | propagates | `packages/core/src/repowise/core/inventory/advisories.py` |
| `document-extractors` | `document_extractors` | every; later extension claim wins | During traversal and source reads for otherwise unsupported extensions | indexing propagates with plugin name and original path; optional previews omit unavailable content | `packages/core/src/repowise/core/ingestion/document_extractors.py` |
| `team-directory` | `team_directories` | one | when components and declared owners load | propagates | `packages/core/src/repowise/core/ownership/directory.py` |
| `authenticator` | `authenticators` | one | each `repowise serve` request and MCP HTTP request | propagates | `packages/server/src/repowise/server/auth.py` |
| `authorizer` | `authorizers` | one | each repository or tool access check in `repowise serve`, MCP over HTTP and chat | propagates | `packages/server/src/repowise/server/auth.py` |
| `chat-tools` | `tool_backends` | one | each chat turn builds its tool backend | propagates | `packages/server/src/repowise/server/chat_seams.py` |
| `chat-store` | `conversation_stores` | one | each chat request opens its conversation store | propagates | `packages/server/src/repowise/server/chat_seams.py` |
| `chat-hooks` | `turn_hooks` | one | each chat turn (preflight, usage, events) | a `preflight`, `on_usage` or `on_event` failure is logged and ends the turn with an error event | `packages/server/src/repowise/server/chat_seams.py` |
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

### Writing an evidence source

A `profile-format` turns a file someone hands over into a run. An
`evidence-source` fetches: it reads a window of measurements from a service
(a database's statement statistics, a continuous profiler), may need
credentials only the host holds, and can return several runs per fetch.
The protocol, `EvidenceSource`, is in
`packages/core/src/repowise/core/evidence/sources.py`:

```python
from repowise.core.evidence.sources import SourceBatch, SourceRequest, evidence_sources


class StatementSource:
    name = "postgres-statements"
    kinds = ("statement_stats",)

    def fetch(self, request: SourceRequest):
        rows, cursor = read_statements(request.credentials, after=request.cursor)
        yield SourceBatch(
            kind="statement_stats", workload="prod db", workload_class=None,
            commit=None, environment="production",
            entries=rows, document={"window": {"start": ..., "end": ...}},
            next_cursor=cursor,
        )


def register():
    evidence_sources.register(StatementSource.name, StatementSource())
```

| `SourceRequest` field | Meaning |
|-----------------------|---------|
| `repository_id`, `repository_name` | The repository the runs are for |
| `since`, `until` | The window to read; `None` leaves the bound to the source |
| `cursor` | What the last stored batch returned as `next_cursor`; `None` on the first fetch |
| `settings` | Plain settings such as a database or service name. Never secrets |
| `credentials` | The host's credentials object, passed through untouched and left out of `repr` |

| `SourceBatch` field | Meaning |
|---------------------|---------|
| `kind`, `workload`, `workload_class`, `environment` | The run header, as `evidence add` takes it; with no `environment`, a production run reads as `production` and any other as `unknown` |
| `commit` | The full commit SHA measured; `None` only for a production-only kind (`route_latency`, `statement_stats`) whose deployed commit is unknown |
| `measured_at` | When the run was measured (for a window, its end) |
| `entries` and `document` | `repowise-evidence-v1` entries of `kind`, and the document's other fields (`value_kind`, `unit`, totals, `tool`, `window`, `base_commit_sha`) |
| `data` and `format` | Or a file's bytes in a registered `profile-format`, such as pprof bytes a profiler service returns; `value_kind` names the clock when the bytes do not. The bytes must read as a run of `kind` |
| `next_cursor` | Where the next fetch resumes once this batch is stored; `None` keeps the cursor as it was |

The rules a source follows, which the contract tests in
`tests/unit/evidence/test_sources.py` check against a test double:

- **Credentials stay with the host.** repowise passes `credentials` through
  and never stores, logs or prints it. Do not copy it, or anything derived
  from it, into a batch or an exception message.
- **Cursor.** The cursor is opaque to repowise and only moves past a batch
  once that batch is stored, so return batches oldest first, each with the
  cursor that resumes after it. A batch that joins to no function is still
  read (its result says `stored: false` and why), as a file is added once.
  Fetching again from an old cursor is safe: a batch whose entries, header
  and index are unchanged is the same run, and storing it again writes
  nothing new.
- **Commit.** A measured run names the full commit it measured, including a
  `production` profile. Only a production-only kind whose deployed commit is
  unknown leaves `commit` as `None`; it is stored as measured at an unknown
  commit.
- **Class.** A source is trusted by the host that installed it, so its
  batches may carry `production` where the kind allows it. A production-only
  kind (`route_latency`, `statement_stats`) needs no class. `evidence add
  --class production` stays refused.
- **Failure.** Raise `SourceUnavailable` when the far side is down, times out
  or rate limits: the fetch stops, keeps what it stored and returns the
  cursor to retry from. Raise `SourceRefused` when it refuses the credentials
  or settings: the host shows its message to the user. Anything else fails
  that fetch, as does a batch repowise refuses (a missing commit, a class
  the kind does not take). A batch is stored whole or not at all, and the
  batches stored before a failure stay: the host commits them together with
  the cursor the fetch returns.

A host runs a fetch with `run_fetch(source, request, store)`, where `store`
stores one batch. Against a repowise index, `store` is
`repowise.core.evidence.add.ingest_batch`, which reads the batch the way
`evidence add` reads a file of the same entries, so the stored rows,
hotness and payloads are the same. A host with no checkout (a server that
keeps only the index) passes a `repo_path` that does not exist: a batch of
entries is stored with no checkout root, while a batch of profile bytes is
refused, since its frames are placed on the checkout's source. `fetch` is a plain iterable, so a source
that waits on the network holds the caller's event loop while it reads; a
server runs `run_fetch` in its own task or worker. `repowise plugins` lists
each registered source under `evidence-source`.

#### Syncing a source

Give a source a `settings` attribute and repowise runs it for you. List the
[plugin settings](#settings-for-a-plugin) it reads:

```python
class StatementSource:
    name = "postgres-statements"
    kinds = ("statement_stats",)
    settings = (DATABASE, DSN)  # DSN is declared with secret=True

    def fetch(self, request: SourceRequest):
        database = request.settings[DATABASE.key]
        dsn = request.credentials[DSN.key]
        ...
```

`repowise sources sync` runs each source once all its required settings have
a value. Plain values arrive in `request.settings` and secrets in
`request.credentials`, both keyed by setting key. `credentials` is `None` when
the source declares no secret.

Batches are stored with `ingest_batch`, and each source's cursor is kept in
`.repowise/state.json`. Secrets are read from the environment on every run and
are never written down. A secret that shows up in a failure message is printed
as `***`.

`repowise update` runs the same sync after its own work. A failing source
prints one line and the update still succeeds. With no configured source the
update does exactly what it did before.

A source with `settings = ()` needs nothing, so it runs on every sync and
every update. A source with no `settings` attribute is never run by repowise:
it is left to a host that calls `run_fetch` with its own credentials.

### Writing an issue source

An `issue-source` tells repowise which tracker issue each commit belongs to.
Use one when your team files bugs in a tracker and commit messages often do
not say "fix". A commit like `Handle empty carts (ENG-42)` reads as a feature
to repowise, but if ENG-42 is a Bug in your tracker, that commit is a fix.

Once a source is installed and configured, a commit linked to a `bug` or
`incident` issue counts as a fix everywhere repowise counts fixes:
`prior_defect_count` and the health markers built on it, the fix events,
`git_commits.is_fix`, and the fix history that `get_context` and
`get_change_risk` show. Those fix histories also quote the issue, for example
`ENG-42 (bug): Checkout fails on an empty cart`. A link can only add a fix. It
never removes one that the commit message already names.

Here is a complete source for a tracker with a JSON API. It declares its
settings, so `repowise sources sync` and `repowise update` run it:

```python
import json
import urllib.error
import urllib.request

from repowise.core.issues import (
    IssueLink, SourceRefused, SourceUnavailable, issue_sources, tracker_keys,
)
from repowise.core.settings.plugins import plugin_setting, register_settings

URL = plugin_setting("mytracker", "url", "str", None, "Tracker base URL.")
TOKEN = plugin_setting("mytracker", "token", "str", None, "Tracker API token.", secret=True)
TYPES = {"Bug": "bug", "Incident": "incident", "Story": "feature", "Task": "task"}


class TrackerIssues:
    name = "my-tracker"
    settings = (URL, TOKEN)

    def fetch(self, request):
        base, token = request.settings[URL.key], request.credentials[TOKEN.key]
        for commit in request.commits:
            for key in tracker_keys(f"{commit.subject}\n{commit.body}"):
                issue = self._issue(base, key, token)
                if issue is not None:
                    yield IssueLink(
                        key=key, type=TYPES.get(issue["type"], "other"), sha=commit.sha,
                        status=issue["status"], title=issue["title"],
                        url=f"{base}/browse/{key}", basis=f"message names {key}",
                    )

    def _issue(self, base, key, token):
        req = urllib.request.Request(
            f"{base}/api/issue/{key}", headers={"Authorization": f"Bearer {token}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code in (401, 403):
                raise SourceRefused("the tracker refused the token") from None
            raise SourceUnavailable(f"the tracker answered {exc.code}") from None
        except OSError:
            raise SourceUnavailable("the tracker did not answer") from None


def register():
    register_settings(URL, TOKEN)
    issue_sources.register(TrackerIssues.name, TrackerIssues())
```

Then configure it and sync:

```bash
repowise config set plugins.mytracker.url https://tracker.example.com
export REPOWISE_PLUGIN_MYTRACKER_TOKEN=...
repowise sources sync
```

What a source gets, in `IssueRequest`:

| Field | Meaning |
|-------|---------|
| `repository_id`, `repository_name` | The repository the commits belong to |
| `commits` | Every commit in the defect window, each with `sha`, `subject`, `body` and `change` (the PR or MR number its message names, or `None`) |
| `settings` | Plain setting values, keyed by setting key |
| `credentials` | Secret values, keyed by setting key. Never printed or stored |

What it returns, one `IssueLink` per commit and issue:

| Field | Meaning |
|-------|---------|
| `key` | The issue key as the tracker shows it, up to 64 characters |
| `type` | `bug`, `incident`, `feature`, `task` or `other`. Map the tracker's own types onto these |
| `sha` or `change` | The commit: one of the request's full shas, or a change number, which links every requested commit with that number |
| `title` | Quoted next to the key in fix history, cut at 80 characters there |
| `status`, `url` | Stored with the link |
| `basis` | How the commit names the issue, such as `message names ENG-42` or `PR #12 closes #40` |

`repowise plugins check` confirms a source has a `name` and a `fetch` method. When a sync runs, repowise also checks each link it returns. `tests/unit/issues/test_issue_sources.py` covers these rules:

- Each tracker writes keys its own way, so the source finds them.
  `tracker_keys(text, projects=None)` finds `ENG-42` keys (pass your project
  keys to skip look-alikes like `UTF-8`), and `number_refs(text)` finds `#42`.
- Keep credentials out of links and exception messages. A secret that still
  shows up in a failure message is printed as `***`.
- Raise `SourceUnavailable` when the tracker is down or rate limits, and
  `SourceRefused` when it rejects the token or settings. The user sees your
  message as written. Any other exception fails the fetch, and so does a link
  with an unknown type, a sha the request did not include, or both or neither
  of `sha` and `change`.
- Links read before a failure are kept. Only a fetch that finished removes
  links the tracker no longer returns. When two sources return the same key
  for the same commit, the later sync wins.

`repowise update` syncs issue sources before it reads git history, so a new
commit is counted with its issue in the same update. When a sync adds or
removes a bug link on a commit that is already indexed, the next update
reads git history again once, so the counts match a fresh index.

A host with no checkout can fetch links itself and store them with
`repowise.core.persistence.crud.issue_links.store_issue_links`. Indexing and
scoring only read stored links. They never fetch.

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
The converted Markdown also yields `documents` edges from the original path to
the repository files it links to or names
([edge types](../layers/GRAPH.md#edge-types)).
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

Size limits for claimed documents:

| Limit | Default | Notes |
|-------|---------|-------|
| Source file | 500 KB, the limit for any file with no parser | Set `max_source_bytes` (a positive `int`) on the extractor to change it for the extensions it claims. Registration rejects any other value |
| Source file, hard ceiling | 100 MiB | A larger `max_source_bytes` is clamped to it. The parent process reads the whole file and sends it to a worker |
| Extracted Markdown | 2 MB, the limit for parseable source files | Fixed |

The source limit is checked during traversal, for historical Git blobs and for
MCP reads, before the extractor runs. A document whose Markdown is over 2 MB is
not indexed and its Markdown is not cached. The run continues and reports it
with the other skipped files as
`Not indexed: <path> (<size> KB, extracted Markdown over the 2,048 KB limit)`;
traversal stats record the reason `extracted_over_max_size`. When an update
finds that an indexed document has crossed either limit, the document is
removed from the index as if it were deleted.

Extraction runs in the existing spawned parse-worker pool with the
`REPOWISE_PARSE_TIMEOUT_S` per-file deadline (60 seconds by default; `0`
disables the deadline). A timeout, worker crash, unavailable pool or extractor
exception fails the indexing run;
Repowise deliberately does not retry plugin code in-process. Workers also check
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
the way repowise does and checks it against every seam contract. Pass your
plugin's `module:register` target to `assert_plugin_passes`; it runs the same
checks as `repowise plugins check`:

```python
from repowise.core.plugin_testing import assert_plugin_passes


def test_plugin_meets_every_contract(monkeypatch):
    monkeypatch.syspath_prepend("src")  # not needed once the package is installed
    assert_plugin_passes("repowise_hello:register")
```

`assert_plugin_passes` raises `AssertionError` listing each failure with its
fix. `check_plugin(target)` returns the same results as `SeamCheck` objects,
and `check_implementation(seam, impl)` checks one object before you register
it, returning a list of `ContractFailure` (`seam`, `name`, `rule`, `fix`).

| Need | Use |
|------|-----|
| A registry with nothing in it | `CLIRegistry()`, `MCPToolRegistry()`, `PipelineHookRegistry()` from `repowise.core.registry` |
| Load plugins again | `repowise.core.registry.plugins.reset()`, then `load_plugins()` |
| Load only your plugin | set `REPOWISE_PLUGINS` with `monkeypatch.setenv` and patch `importlib.metadata.entry_points` to return `[]` |
| What loaded | the `PluginRecord` list from `load_plugins()`: `status`, `error`, `seams` |
| Contract checks | `assert_plugin_passes`, `check_plugin`, `check_implementation` from `repowise.core.plugin_testing` |

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
  and is named in [CHANGELOG.md](../CHANGELOG.md). Breaking changes include
  adding, removing or renaming a protocol method or required attribute,
  changing a method's parameters, and changing which exceptions reach the
  caller.
- Additive changes (a new seam, a new optional field) keep the version.
- Set `MIN_PLUGIN_API_VERSION` to the oldest API version your plugin works
  with. A plugin written for an older API still loads, so after upgrading
  repowise, run `repowise plugins check` to see whether it still meets the
  contracts.

## Proposing a new seam

When the point you need to extend has no seam, open an issue before any code.
Describe:

- the contract: the protocol or callable an implementation provides,
- when it runs and whether repowise uses one implementation or every one,
- what a failure does: propagate, log and continue, or mark a result unknown,
- the local default that ships in core.

Core contributors add seams following
[pluggable-storage.md](../architecture/pluggable-storage.md#adding-a-new-seam).
