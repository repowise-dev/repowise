# Agent platform internals

How repowise describes the coding agents it works with, and the recipes for
adding one. The public view of the same thing is
[agent/INTEGRATIONS.md](../agent/INTEGRATIONS.md), which is generated from the
registries described here.

An agent can touch repowise in two independent ways:

- Integration: repowise wires the agent up as an MCP host: it writes the
  server config, and depending on the agent, an instructions block, skills,
  hooks and a transcript reader.
- Indexing backend: the agent's own CLI runs as the LLM that writes the
  wiki, using the user's existing login, with no API key.

An agent can have either, both or neither. GitHub Copilot CLI, for example, is
an integration target today and has no indexing backend yet.

**Contents**

- [How it fits](#how-it-fits)
- [Recipe A: adding an indexing backend](#recipe-a-adding-an-indexing-backend)
- [Recipe B: adding an integration target](#recipe-b-adding-an-integration-target)
- [Recipe C: hook and transcript adapters](#recipe-c-hook-and-transcript-adapters)
- [Guards you will hit](#guards-you-will-hit)

---

## How it fits

```
AgentIdentity (core/agents/identity.py)
  slug, display name, executable, install and login hints,
  hook_adapter, session_adapter, indexing_provider
     |                         |                          |
     | indexing_provider       | cli_target_id            | hook_adapter / session_adapter
     v                         v                          v
ProviderSpec               AgentTarget                 AgentAdapter (cli/agent_adapters)
(providers/llm/specs.py)   (cli/agent_targets/         HarnessAdapter (core/sessions/adapters)
     |                      targets/<id>.py)
     | impl                    |
     v                         v
AgentCliProvider subclass   derive_tier -> Full / Good / Basic / Paste-config
(providers/llm/<name>.py)
```

All paths below are under `packages/core/src/repowise/core/` or
`packages/cli/src/repowise/cli/` unless written in full.

| Piece | File | Holds |
|---|---|---|
| `AgentIdentity` | `core/agents/identity.py` | One record per agent: slug, display name, `executable`, `login_check`, `install_hint`, `login_hint`, the adapter names, and `indexing_provider`. Every other namespace derives its spelling of the agent from here. |
| `ProviderSpec` | `core/providers/llm/specs.py` | One record per LLM provider. `KEYLESS_PROVIDERS`, `REPO_PATH_PROVIDERS`, `PROVIDER_DEFAULTS`, `ZERO_COST_MODEL_PREFIXES`, the server catalog and the init picker rows are all computed from `PROVIDER_SPECS`. |
| `AgentCliProvider` | `core/providers/llm/agent_cli.py` | The shared subprocess base for agent CLI backends: executable lookup, stdin prompt, timeout and kill, cancellation, a per-loop concurrency semaphore, error tails, model labels, cost booking. |
| Integration targets | `cli/agent_targets/` | `types.py` (the `AgentTarget` protocol and `derive_tier`), `registry.py` (`_TARGET_MODULES`), `targets/<id>.py` (one descriptor per agent), `instructions.py` (the shared managed block), `formats/` (JSON, TOML, YAML and marker-block writers). |
| Hook adapters | `cli/agent_adapters/` | `AgentAdapter` in `base.py`, registered in `_REGISTRY` in `__init__.py`. |
| Transcript adapters | `core/sessions/adapters/` | `HarnessAdapter` in `base.py`, registered with `@register_adapter` and imported in `__init__.py`. |

The two registries meet in exactly one place: `ProviderSpec.agent` names the
agent slug, and `AgentIdentity.indexing_provider` names the spec back. A test
holds the two links to each other.

The tier is never declared. `derive_tier` in `agent_targets/types.py` reads the
descriptor:

- no install methods: Paste-config
- names both a hook adapter and a session adapter: Full
- wires MCP plus instructions or skills: Good
- anything else: Basic

The server exposes the provider specs at `/api/providers`
(`packages/server/src/repowise/server/provider_config.py`), and the web UI reads
its provider list, models and setup hints from there. No UI file names a
provider.

---

## Recipe A: adding an indexing backend

The worked reference is `core/providers/llm/opencode.py`. The steps below use
`copilot_cli` as the example name. Name the module after the provider: the
provider-name guard (see [Guards](#guards-you-will-hit)) lets a provider's own
module spell its name, matched by file stem.

### Step 1: the provider module

Create `packages/core/src/repowise/core/providers/llm/copilot_cli.py` <!-- repowise-drift-ignore -->
with a subclass of `AgentCliProvider`.

```python
from repowise.core.providers.llm.agent_cli import AgentCliProvider, iter_jsonl, tail
from repowise.core.providers.llm.base import GeneratedResponse, ProviderError
from repowise.core.reasoning import ReasoningMode


class CopilotCliProvider(AgentCliProvider):
    provider_name = "copilot_cli"
    agent_slug = "copilot"                    # the AgentIdentity slug
    command_label = "copilot"                 # how errors name the invocation
    concurrency_env = "REPOWISE_COPILOT_CLI_CONCURRENCY"

    def build_command(self, system_prompt: str, reasoning: ReasoningMode) -> list[str]:
        cmd = [self._executable, ...]         # non-interactive, read-only flags
        if self._model:
            cmd += ["--model", self._model]
        return cmd

    def parse_output(self, stdout: str, stderr: str) -> GeneratedResponse:
        ...                                   # text plus token counts
```

The base derives `executable_name` and the not-found message from the identity
named by `agent_slug`, so the class fails at import if that identity has no
`executable`. The prompt goes on stdin; never put it in argv.

Required:

- `build_command(system_prompt, reasoning)` returns argv for one call. Pass the
  model as `--model <slug>` when `self._model` is set; the contract test reads
  it from there. Use the CLI's read-only or no-tools mode: indexing must never
  edit the repo.
- `parse_output(stdout, stderr)` builds the `GeneratedResponse` from a
  successful run. Raise `ProviderError` when the output carries no text.

Optional hooks on `AgentCliProvider`, override only what the CLI needs:

| Hook | Default | Override when |
|---|---|---|
| `build_stdin(system_prompt, user_prompt)` | the user prompt alone | the CLI has no system-prompt flag and the two must be combined (opencode, codex) |
| `working_dir()` | no cwd change | the CLI should run somewhere neutral, such as a scratch dir (claude_cli) |
| `subprocess_env()` | inherit the parent env | the CLI needs env vars, such as a locked-down config (opencode) |
| `exec_timeout_seconds()` | `EXEC_TIMEOUT_SECONDS` (600) | the CLI needs a different ceiling |
| `exit_error(returncode, stdout, stderr)` | `ProviderError` with the stderr tail | a non-zero exit needs a clearer message, such as "not logged in" (claude_cli) |
| `stdout_error(stdout)` | `None` | the CLI reports failures as structured events on stdout (opencode) |

Class attributes with defaults: `default_model` (`None` lets the CLI's own
config choose), `validates_model_name` (`True`; codex turns it off and lets its
model catalog decide), `records_cost` (`False`; only claude_cli
books `llm_costs` rows), `error_tail_chars`.

From `BaseProvider`, override `available_model_options()` when the CLI can list
its models (opencode runs `opencode models`, cached with `lru_cache`, with
`stdin=subprocess.DEVNULL`), and `supported_reasoning_modes()` when the CLI
takes a reasoning flag. The defaults offer one option, the configured model,
with reasoning `auto`.

`self._repo_path` holds the repo the CLI should read. Pass it with the CLI's
directory flag (opencode uses `--dir`, codex `--cd`) if the CLI has one.

### Step 2: the `ProviderSpec`

Add a record to `_SPECS` in `core/providers/llm/specs.py`. Declaration order is
the server catalog's order.

```python
ProviderSpec(
    name="copilot_cli",
    picker_rank=9,
    impl=f"{_LLM}.copilot_cli:CopilotCliProvider",
    label="GitHub Copilot (Local CLI)",
    default_model="copilot_cli/default",
    models=("copilot_cli/default",),
    keyless=True,
    needs_repo_cwd=True,
    local=True,
    zero_cost=True,
    agent="copilot",
    note="uses your Copilot CLI login",
),
```

| Field | What reads it |
|---|---|
| `name`, `impl` | `get_provider` imports `impl` lazily; `_BUILTIN_PROVIDERS` is derived from it |
| `label` | the server catalog's display name, shown in the web UI settings |
| `default_model`, `models` | the init picker's model list and the server catalog; agent CLI models are prefixed `<name>/` |
| `keyless` | `KEYLESS_PROVIDERS`: resolution never rejects the provider for a missing key |
| `needs_repo_cwd` | `REPO_PATH_PROVIDERS`: `provider_kwargs` passes `repo_path`, and a workspace init rebinds the provider to each repo. Leave it off if the CLI runs in a scratch dir |
| `local` | init warns that concurrency above 4 may time out; the server prices unknown local models at zero |
| `zero_cost` | `ZERO_COST_MODEL_PREFIXES`: the cost estimator and cost tracker price `<name>/...` models at zero |
| `agent` | the agent slug; links the spec to the identity and supplies `setup_hint` from its install and login hints |
| `picker_rank` | the row in the `repowise init` picker and inclusion in `/api/providers`; `None` makes it flag-only. Pick a slot beside the other agent CLIs and renumber the specs after it |
| `note` | the dim note beside the row in the picker, and the reason line when the picker defaults to it |

Agent CLIs keep `rate_limit=None`, `api_key_envs=()` and `autodetect_rank=None`.

### Step 3: link the identity

In `core/agents/identity.py`, set `indexing_provider="copilot_cli"` on the
agent's record. The record also needs `executable`, `install_hint` and
`login_hint` (the CLI prints them when the executable is missing). Add
`login_check` if the CLI has a cheap, side-effect-free command that exits 0
only when signed in; the init picker uses it to decide whether the backend is
ready.

### Step 4: the contract test

Add one `Backend` to `BACKENDS` in
`tests/unit/test_providers/test_agent_cli_contract.py`, and import the class at
the top of the file:

```python
@dataclass(frozen=True)
class Backend:
    cls: type[AgentCliProvider]
    model: str  # a native slug the CLI receives after ``--model``
    success_stdout: Callable[[str], str]  # CLI output whose answer is the argument
    tokens: tuple[int, int, int]  # parsed (input, output, cached)
    books_cost: bool
    repo_flag: str | None  # flag that points the CLI at the repo; None = scratch cwd
    env: dict[str, str] | None  # entries the subprocess env must carry
```

`success_stdout` is a function that returns real CLI output with the answer
swapped in. Build it from a real run: run the CLI once in the mode
`build_command` uses, keep the events `parse_output` reads (the text and the
usage), and write them as a builder next to `_opencode_stdout`. Provider output
samples live inline in the tests, not in `tests/fixtures/`. Do not invent
fields the CLI does not emit.

The contract then checks names, the missing-executable error, argv and stdin,
the repo flag or scratch dir, the env, cost booking, the non-zero exit, timeout
and cancellation kills, and the concurrency semaphore. CLI-specific parsing
(noise lines, error events, missing usage, the model catalog) goes in a
`tests/unit/test_providers/test_copilot_cli_provider.py` <!-- repowise-drift-ignore -->
modelled on `test_opencode_provider.py`.

### Step 5: update the frozen lists

Two tests freeze the picker on purpose, so a new row is a deliberate edit:

- `test_picker_rows_keep_their_order` in `tests/unit/cli/test_init_ux.py`
- `test_the_keyless_providers_get_setup_help_instead_of_a_key_prompt` in the
  same file

Then regenerate the matrix, whose `Indexing` column now reads `Yes` for the
agent:

```bash
python scripts/gen_agent_matrix.py
```

### What you get without further edits

- A row in the `repowise init` picker with install and login help when the CLI
  is missing. When the agent is set up as a target in the repo and its CLI is
  ready, the picker defaults to it (`agent_providers_set_up` in
  `cli/ui/provider_selection.py`).
- `repowise init --provider copilot_cli` and keyless resolution in the CLI and
  the MCP server.
- An entry in `/api/providers` with its setup hint, and so in the web UI.
- Zero-cost pricing for its models, no client-side rate limiter, and the
  local-concurrency warning.
- Per-repo binding in a workspace init when `needs_repo_cwd` is set.
- The shared contract tests and the registry link tests.

### Verify

```bash
pytest tests/unit/test_providers/ tests/providers/ -q
pytest tests/unit/cli/test_init_ux.py tests/unit/cli/test_init_agent_backend.py \
  tests/unit/cli/test_agent_identity.py tests/unit/cli/test_agent_matrix.py \
  tests/unit/server/test_provider_config.py -q
ruff check .
```

Then by hand, with the CLI installed and logged in, on a small repo:

```bash
repowise init --provider copilot_cli --yes
repowise init            # interactive: check the row, the note and the default
```

---

## Recipe B: adding an integration target

Before writing anything, check every host fact (config file paths, the MCP
entry shape, which instruction files the host reads, how to tell it is
installed) against the host's own docs, and cite the source in the module
docstring. `targets/copilot.py` shows the shape. Where a fact is assumed and
not documented, say so there.

### Step 1: the identity

Add an `AgentIdentity` in `core/agents/identity.py` and append it to the
`for _shipped in (...)` tuple at the bottom of the file. The `--target` id is
derived: `kiro` for slug `kiro`, `claude-code` for `claude_code`. Leave
`hook_adapter` and `session_adapter` unset until Recipe C.

### Step 2: the target module

Write `packages/cli/src/repowise/cli/agent_targets/targets/<id>.py` exporting
`TARGET`, an object that satisfies the `AgentTarget` protocol in `types.py`.
`targets/vscode.py` is the smallest complete example. The usual top of the
module:

```python
from repowise.core.agents import identity

IDENTITY = identity.KIRO
ID = IDENTITY.cli_target_id
DISPLAY_NAME = IDENTITY.display_name
DOCS_URL = "https://..."
PROJECT_FILE_ID = "kiro_mcp"   # key under editor_files in .repowise/config.yaml

METHODS = (
    InstallMethod(
        id="direct",
        provides=frozenset({Capability.MCP, Capability.INSTRUCTIONS}),
        managed_by="repowise",
        preferred=True,
    ),
)
```

and on the class, `hook_adapter = IDENTITY.hook_adapter` and
`session_adapter = IDENTITY.session_adapter`.

Every protocol method must be safe when nothing was ever installed:
`supports_scope`, `is_present` (a PATH or directory probe, never a
subprocess), `detect` (a list of `Registration`s, never raises), `install`,
`uninstall` (removes only what `install` wrote), `print_config` (no filesystem
access), `describe_paths` and `doctor`. Use the writers in `formats/` for
JSON, TOML, YAML and marker blocks; do not hand-roll a merge.

For the instructions block, write the shared text from `instructions.py`
(`DISTILL_SECTION` between `DISTILL_MARKER_START` and `DISTILL_MARKER_END`)
with `formats.marker_block.upsert`. Text outside the markers belongs to the
user and must round-trip byte for byte.

If two targets write the same file, one owns the block and the other shares
it. `copilot` reuses `vscode.write_instructions` for
`.github/copilot-instructions.md`, and `vscode.remove_instructions` asks <!-- repowise-drift-ignore -->
`registry.other_managers_of` before stripping the block, so removing one agent
leaves the block in place while the other still reads it.

### Step 3: register it

Append one line to `_TARGET_MODULES` in `agent_targets/registry.py`. Order is
the order agents appear in prompts, in `--target=all` and in listings, so new
ids go at the end. Append the same id to the frozen list in
`test_registry_exposes_the_shipped_targets` in
`tests/unit/cli/test_agent_targets.py`.

`repowise init` writes project files for Claude Code, Codex and VS Code
through `cli/editor_integrations/`. Other targets are wired with
`repowise agents add --target=<id>`, so add a row to the host table in
`docs/start/QUICKSTART.md`.

### Step 4: the README badge and the matrix

Add a badge to the `## Supported agents and editors` section of `README.md`,
under the row for the tier `derive_tier` gives it, and update the count in the
sentence above the badges. Then:

```bash
python scripts/gen_agent_matrix.py
```

### Step 5: verify

```bash
pytest tests/unit/cli/test_agent_targets.py tests/unit/cli/test_agent_identity.py \
  tests/unit/cli/test_agent_matrix.py tests/unit/agents/ -q
repowise agents add --target=<id> --scope=project --yes
repowise agents
repowise agents remove --target=<id> --scope=project
```

The protocol, idempotency, `print_config` and `describe_paths` tests in
`test_agent_targets.py` are parameterized over the registry, so the new target
is covered without new test code.

---

## Recipe C: hook and transcript adapters

A target reaches Full only when its identity names both adapters. Declare only
what is implemented and verified against the host's docs or real data. A name
on the identity is a public claim: it moves the agent to the Full row of the
README and the matrix.

### Hook adapter

Subclass `AgentAdapter` in `cli/agent_adapters/base.py` and add it to
`_REGISTRY` in `cli/agent_adapters/__init__.py`, keyed by the name its hook
config passes as `--client`. Abstract methods: `detect`,
`parse_hook_payload`, `render_response`, `install_rewrite_hook`,
`uninstall_rewrite_hook`, `rewrite_hook_installed`.

The class variables answer what the host's hook protocol can do:
`rewrite_permissions`, `replaces_tool_output`, `shell_tool_names`,
`read_tool_names`, `edit_tool_names`, `search_tool_names`, `savings_source`.
Leave a default in place until the host's docs or a captured payload show
otherwise. A wrong `replaces_tool_output=True` records savings the agent never
saw.

Module scope stays stdlib-only: this runs on the PreToolUse hot path.
`agent_adapters` must never import `agent_targets`.

Set `hook_adapter="<registry key>"` on the identity, and add
`Capability.HOOKS` to the target's install method.

### Transcript adapter

Subclass `HarnessAdapter` in `core/sessions/adapters/base.py`, decorate it
with `@register_adapter`, and import it in `core/sessions/adapters/__init__.py`.
Implement `discover(repo_root, *, projects_root=None)` and
`normalize(raw_line)`; declare `edit_tool_names` and `shell_tool_names` in the
host's own vocabulary. Test it against a real transcript, trimmed and
anonymized, as `tests/unit/sessions/test_claude_code_adapter.py` does.

Set `session_adapter="<adapter name>"` on the identity, and add
`Capability.TRANSCRIPTS` to the target's install method.

---

## Guards you will hit

`tests/unit/test_providers/test_provider_specs.py`:

- `test_no_module_branches_on_a_provider_name` parses every Python source
  under `packages/*/src` and fails when a provider name is used in a
  comparison, a `match` case, a collection, a dict key or a `.get()` key
  outside the registries. Read a `ProviderSpec` field (`spec.agent`,
  `spec.needs_repo_cwd`, `spec.local`) or call `identity_for_provider`
  instead of spelling the name. A provider's own module may spell its own name.
- `test_no_ui_source_spells_a_provider_name` scans `packages/ui`,
  `packages/web` and `packages/api-client` for quoted provider or embedder
  names. The UI reads them from `/api/providers`.
- The link tests fail when `indexing_provider` and `ProviderSpec.agent` do not
  name each other, when the identity lacks `executable`, `install_hint` or
  `login_hint`, or when an agent spec gets a rate limiter.

`tests/unit/cli/test_agent_matrix.py`:

- `docs/agent/INTEGRATIONS.md` must match `python scripts/gen_agent_matrix.py`
  output. Run the script; never edit the file. `--check` reports drift
  without writing.
- The README badge rows must match the derived tiers, and the headline count
  must match the registry.
- A target's `Capability.HOOKS` must agree with its `hook_adapter`.

`tests/unit/cli/test_agent_identity.py` fails when an identity names a hook or
session adapter that is not registered.

CI also runs `repowise doc-drift --check`, which flags a doc that names a file,
symbol or command that does not exist. A path to a file in a user's repo, or a
file you are about to create, needs `<!-- repowise-drift-ignore -->` on its
line.
