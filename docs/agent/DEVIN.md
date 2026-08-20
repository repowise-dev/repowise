# Devin Integration

Repowise integrates with Devin in two independent ways. This document explains both and the trade-offs between them.

## The two options

| Option | What it is | Complexity | Dependencies | Token usage | Output |
|---|---|---|---|---|---|
| **`devin_cli` provider** | Wrap the local `devin` CLI binary and run `devin -p` for one-shot generation. | Low. Follows the same pattern as `codex_cli` and `opencode`. | None new. | Estimated/missing — the CLI returns plain text, not token counts. | Plain text stdout. |
| **`devin_acp` provider** | Launch the local `devin` CLI in Agent Client Protocol mode (`devin acp`) and speak ACP over stdio. | Medium. Uses the `agent-client-protocol` Python SDK and the ACP JSON-RPC/NDJSON transport. | `agent-client-protocol`. | Real token counts from `PromptResponse.usage`. | Streamed `agent_message_chunk` text with `end_turn` stop reason. |

The two options are **not mutually exclusive**. `devin_cli` is the simple, low-dependency starting point. `devin_acp` adds structured streaming, real usage data, and model/mode control with a relatively small dependency footprint.

A third option — a direct `server.codeium.com` network client based on `oh-my-pi` — is possible but intentionally not implemented. The ACP route gives the same Devin capabilities without protobuf, Connect framing, generated schemas, or custom OAuth.

## What Paseo does

The Paseo project (<https://github.com/.../paseo>) uses Devin through ACP:

- Its ACP catalog entry is `id: "devin"`, `command: ["devin", "acp"]`.
- `packages/server/src/server/agent/providers/acp-agent.ts` is a generic ACP client.
- It spawns the CLI, performs `initialize`, then `session/new` with `cwd` and `mcpServers: []`.
- It sends prompts with `session/prompt` and consumes `session/update` notifications for `agent_message_chunk`, `agent_thought_chunk`, `tool_call`, `usage_update`, `config_option_update`, and `current_mode_update`.
- It sets Devin-specific config options, including `mode` and `model`.

This validated the `devin_acp` approach and showed that Devin ACP is a full session/conversation interface, not merely a summarizer.

## Why `devin_cli` first

1. **Familiar pattern.** Repowise already has `codex_cli` and `opencode` providers that shell out to a local agent CLI. Adding `devin_cli` uses the same registry, CLI selection, validation, and test patterns.
2. **Low dependency footprint.** No new Python packages, no protobuf code generation, no OAuth server.
3. **Fast validation.** A single `devin -p` call is enough to prove the provider works end-to-end.
4. **Good enough for docs generation.** Repowise primarily needs markdown output from a system + user prompt. Plain-text stdout satisfies that.

The main downsides are the lack of real token counts and the CLI's potential to make file edits depending on its permission mode. The implementation chooses a safe non-interactive mode and adds "do not edit files" guidance to the prompt.

## Why `devin_acp` second

1. **Real token usage.** The `PromptResponse.usage` field returns `input_tokens`, `output_tokens`, and `cached_read_tokens`.
2. **Streaming and session control.** ACP exposes `config_option_update` for `mode` and `model`, so the provider can set `mode=ask` (answer without file edits) and a specific model before the prompt.
3. **No protobuf or network auth.** The CLI still handles authentication; the Python side only needs the `agent-client-protocol` SDK.
4. **Composable with existing abstractions.** `devin_acp` still implements `BaseProvider` and returns `GeneratedResponse`, so it slots into the same registry, CLI, server, and web wiring as `devin_cli`.

## `devin_acp` design

### How it runs

The provider spawns a short-lived ACP session:

```bash
devin acp
```

Then it performs:

1. `initialize` with `ClientCapabilities()` (no `fs` or `terminal` support) and client info `repowise`.
2. `session/new` with `cwd=<repo>` and `mcpServers=[]`.
3. `set_config_option("mode", session_id, "ask")` to avoid unsolicited file edits.
4. `set_config_option("model", session_id, "<model>")` when the user selected a specific model.
5. `session/prompt` with the combined system + user prompt as a single text content block.
6. Collect `agent_message_chunk` notifications until `prompt` returns with a `PromptResponse`.
7. Read `PromptResponse.stop_reason` and `PromptResponse.usage` for the final response.

The provider advertises no file-system or terminal capabilities, denies any `request_permission` calls, and instructs the model not to use tools. The session is terminated after each `generate()` call.

### Model discovery

Like `devin_cli`, `devin_acp` uses:

```bash
devin models list --format json
```

The parser is shared and produces `ProviderModelOption` entries. Persisted model labels are `devin_acp/<model_uid>` with a default of `devin_acp/default`.

### Registry and wiring

- `packages/core/src/repowise/core/providers/llm/devin_acp.py` implements `DevinAcpProvider`.
- `packages/core/src/repowise/core/providers/llm/registry.py` registers `devin_acp` as a built-in, keyless, repo-path provider.
- `packages/core/src/repowise/core/rate_limiter.py` gives `devin_acp` the same default as `devin_cli`.
- `packages/cli/src/repowise/cli/ui/provider_selection.py` and `helpers.py` add `devin_acp` to the interactive picker and validation.
- `packages/cli/src/repowise/cli/commands/init_cmd/command.py` and `workspace.py` include `devin_acp` in the CLI init flow.
- `packages/server/src/repowise/server/provider_config.py` adds `devin_acp` to the server catalog.
- `packages/web/src/components/settings/provider-section.tsx` adds `devin_acp` to the web settings UI.
- `tests/unit/test_providers/test_devin_acp_provider.py` has unit tests with a mock ACP transport.

## `devin_cli` design (first implementation)

The `devin_cli` provider invokes the Devin CLI in non-interactive print mode:

```bash
devin -p --prompt-file <tmpfile> --cd /absolute/path/to/repo --model <model> --respect-workspace-trust false --permission-mode auto
```

The combined `system_prompt` and `user_prompt` are written to a temp file. The provider parses stdout as the response and sets `usage["estimated"] = True` because the CLI does not emit token counts.

## Future: direct Devin network provider

If the ACP route ever becomes impractical, a direct `server.codeium.com` network client based on `oh-my-pi` remains an option. That would require:

- `GetUserJwt` to exchange a `DEVIN_API_KEY` session token for a JWT.
- Connect streaming to `https://server.codeium.com/exa.api_server_pb.ApiServerService/GetChatMessage`.
- Protobuf `GetChatMessageRequest` / `GetChatMessageResponse` serialization.
- Model discovery via `GetCliModelConfigs`.
- Reasoning effort routing by selecting sibling model IDs.

This is intentionally out of scope because ACP provides the same capabilities with a simpler dependency and maintenance story.

## Official Devin docs

- [Devin CLI](https://docs.devin.ai/cli)
- [Devin CLI commands and flags](https://docs.devin.ai/cli/reference/commands)
- [Devin CLI models](https://docs.devin.ai/cli/models)
- [Agent Client Protocol](https://agentclientprotocol.com/)
