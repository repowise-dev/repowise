# Writing a repowise plugin

A plugin is a Python package that repowise imports at startup. It can add CLI
commands, MCP tools, pipeline hooks and model providers, or plug into a named
seam: an issue tracker, an event sink, a report section, an authenticator, and
so on. You never fork repowise to do it.

This page gets you from nothing to a working, published plugin. The
[plugin reference](PLUGINS_REFERENCE.md) has the full contract for every seam,
the failure rules, the settings and sync details, and the testing helpers.

The plugin API is experimental. Read [Stability](#stability) before you publish.

## What a plugin can do

| Idea | Uses | What it does |
|------|------|--------------|
| Slack or Teams notifier | `event-sink` | Posts component events (a new hotspot, an ownership shift) to a channel |
| Webhook or SIEM forwarder | `event-sink` | Sends each event as CloudEvents JSON to an HTTP endpoint or a log pipeline |
| Self-hosted model gateway | `llm-provider` | Routes wiki generation and chat through your own model gateway |
| Local embedding model | `embedder` | Embeds pages with a model that runs on your machines |
| LDAP or Okta teams | `team-directory` | Reads team membership from your directory for declared ownership |
| SSO for `repowise serve` | `authenticator` | Accepts your identity provider's tokens on the HTTP API |
| Jira or Linear issues | `issue-source` | Links commits to your tracker's issues (GitHub and GitLab issues are built in), so a commit tied to a Bug counts as a fix in prior defects, change risk and health, and decisions and changes show the issue |
| Internal vulnerability feed | `advisory-source` | Reads advisories from your own feed for `repowise packages import` |
| Compliance report section or format | `report-section`, `report-renderer` | Adds evidence your auditors ask for, or writes the report in their text format |
| Internal API as an agent tool | `mcp-tool` | Gives agents a read-only tool that queries an internal service |
| Team shortcut command | `cli-command` | Wraps a repowise workflow your team runs often in one command |
| Phase timings | `pipeline-hook` | Records when each index phase starts and ends and sends the timings to your metrics system |
| Database statement statistics | `evidence-source` | Reads a database's statement statistics on a schedule and stores them as production `statement_stats` runs |
| Extract an unsupported document type | `document-extractors` | Converts a plugin-supported file type to Markdown for the existing ingestion path |

## Quickstart

Let repowise write the package for you. It installs, loads and passes the
contract checks before you change a line:

```bash
repowise plugins new issue-source --name linear
pip install -e repowise-linear
repowise plugins check
```

`repowise plugins new` takes `issue-source`, `evidence-source` or
`authorizer`. The package has a working stub, a test, and an `AGENTS.md` that
walks you (or a coding agent) through turning the stub into the real thing.
It copies the seam's contract (the types, link rules and failure exceptions)
from the repowise you have installed, so it matches your version, and for an
issue or evidence source it ends after `repowise update`, with the command
that shows the links or runs it stored.
`repowise plugins check` inspects every seam implementation your plugin
registers and prints the rule that broke and how to fix it. See
[Check the contracts](PLUGINS_REFERENCE.md#check-the-contracts).

## Install and configure a plugin

Install the package into the environment repowise runs from, then see what
loaded and what it still needs:

```bash
pip install repowise-linear
repowise plugins
```

Each plugin shows its status, such as `loaded` or `failed` with the error. A
plugin that needs settings lists the missing ones with the command that sets
each, so you or an agent can finish setup from the listing. Plain settings go in
`.repowise/config.yaml`. Secrets such as tokens come from the environment only
and are never written down:

```bash
repowise config set plugins.linear.team ENG
export REPOWISE_PLUGIN_LINEAR_TOKEN=lin_api_...
```

A source that fetches from another system (an issue tracker, a metrics
service) runs once its required settings have a value. Run it by hand, or let
`repowise update` run it for you:

```bash
repowise sources sync
repowise sources sync --source linear
```

A failing source prints one line and the update still succeeds. The web UI
shows the same listing on the repository's Settings > Plugins page, with a
Sync now button for each configured source.

More detail: [settings for a plugin](PLUGINS_REFERENCE.md#settings-for-a-plugin),
[the listing and its JSON](PLUGINS_REFERENCE.md#check-it-loaded), and
[syncing a source](PLUGINS_REFERENCE.md#syncing-a-source).

## A complete example

The smallest plugin adds one CLI command. The same package is in
[`examples/plugins/hello`](../../examples/plugins/hello/), with an MCP tool,
an event sink and a report section on top.

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

3. Install it and check it loaded.

   ```bash
   pip install -e .
   repowise plugins
   repowise hello
   ```

Register inside `register()`, not at module import time, so a test can import
your module without side effects. To try a module without installing it, see
[Loading a plugin](PLUGINS_REFERENCE.md#loading-a-plugin).

## Seams

A seam is a named point where repowise uses an implementation you register.
For a "one" seam you can choose the implementation with `REPOWISE_SEAM_<NAME>`;
for an "every" seam, registering is enough. The
[extension points catalog](PLUGINS_REFERENCE.md#extension-points-catalog) has
each seam's registry, timing, failure rule and source file.

| Seam | Use it to |
|------|-----------|
| `cli-command`, `mcp-tool`, `pipeline-hook`, `llm-provider`, `embedder` | Add commands, agent tools, phase callbacks and model providers ([registries](PLUGINS_REFERENCE.md#registries)) |
| `storage-migration` | Create and change your own tables ([storage revisions](PLUGINS_REFERENCE.md#storage-revisions)) |
| `event-sink` | Deliver component events after an index run |
| `index-persistence` | Write your own records inside each index write's transaction |
| `report-section`, `report-renderer` | Add a section to `repowise report`, or write it in another format |
| `first-look-section` | Add a section to `repowise report repo` |
| `change-outcome`, `change-metadata` | Supply outcomes and metadata for change records |
| `profile-format` | Read a profile or benchmark file format in `repowise evidence add` |
| `evidence-source` | Fetch runtime evidence from a service ([guide](PLUGINS_REFERENCE.md#writing-an-evidence-source)) |
| `issue-source` | Link commits to tracker issues ([guide](PLUGINS_REFERENCE.md#writing-an-issue-source)) |
| `suspect-signal` | Add a signal to `repowise suspects` |
| `advisory-source` | Read an advisory file for `repowise packages import` |
| `document-extractors` | Turn an unsupported file type into Markdown ([rules](PLUGINS_REFERENCE.md#document-extractors)) |
| `team-directory` | Read team membership for declared owners |
| `authenticator`, `authorizer` | Sign in callers of `repowise serve` and decide what they reach ([guide](PLUGINS_REFERENCE.md#authenticator-and-authorizer)) |
| `chat-tools`, `chat-store`, `chat-hooks`, `chat-dataset`, `chat-view` | Change chat's tools, storage, turn hooks, datasets and views |

## GitHub and GitLab issues, built in

repowise ships one issue source, `forge`, so you need no plugin when your bugs
live in the repository's own GitHub or GitLab project. Give it a token that can
read pull or merge requests and issues, then sync:

```bash
export REPOWISE_PLUGIN_FORGE_TOKEN=$(gh auth token)   # or a GitLab token with read_api
repowise sources sync --source forge
```

A commit linked to a `bug` or `incident` issue then counts as a fix in prior
defects, change risk and health. How commits are matched to issues, which
labels count as bugs, and how to point it at a self-managed host are in
[Issues from GitHub and GitLab](PLUGINS_REFERENCE.md#issues-from-github-and-gitlab).

## Community plugins

Plugins built and maintained outside this repository. Each is owned by its
author, so read its README for install steps and supported versions.

| Plugin | Uses | What it does |
|--------|------|--------------|
| [repowise-markitdown-plugin](https://github.com/digimangos/repowise-markitdown-plugin) | `document-extractors` | Indexes PDFs through Microsoft's MarkItDown, with results cited by the original PDF path |

To list yours, open a pull request that adds a row here. To find others,
search GitHub for the `repowise-plugin` topic or PyPI for the
`repowise-plugin` keyword.

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
5. Add the `repowise-plugin` keyword to your `pyproject.toml` and the
   `repowise-plugin` topic to your GitHub repository, so people searching for
   plugins find it. A package from `repowise plugins new` already has the
   keyword.

## Stability

The plugin API is experimental and can change in a minor release; a breaking
change bumps `PLUGIN_API_VERSION` and is named in
[CHANGELOG.md](../CHANGELOG.md). Set `MIN_PLUGIN_API_VERSION` in your module to
the oldest version your plugin works with, and see
[the version policy](PLUGINS_REFERENCE.md#stability) for what counts as
breaking.

Need a seam that does not exist yet? See
[Proposing a new seam](PLUGINS_REFERENCE.md#proposing-a-new-seam).
