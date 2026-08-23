# Devin Integration

Repowise can integrate with Devin in two independent ways. This document explains both, the trade-offs between them, and why the first implementation will be the `devin_cli` CLI wrapper.

## The two options

| Option | What it is | Complexity | Dependencies | Token usage | Output |
|---|---|---|---|---|---|
| **`devin_cli` provider** (first) | Wrap the local `devin` CLI binary and run `devin -p` for one-shot generation. | Low. Follows the same pattern as `codex_cli` and `opencode`. | None new. | Estimated/missing — the CLI returns plain text, not token counts. | Plain text stdout. |
| Direct `devin` network provider | Re-implement the Devin/Codeium chat API client from `oh-my-pi` in Python. | High. Requires Connect/gRPC protobuf, streaming, JWT auth, and model discovery. | `protobuf`/`grpcio` (or `httpx` + `protobuf` for Connect over HTTP/1.1), generated Python schemas. | Real token counts from streaming frames. | Full streaming response with thinking, tool calls, usage. |

The two options are **not mutually exclusive**. `devin_cli` is the pragmatic starting point. A direct network provider can be added later if we need real usage data, reasoning controls, or model routing.

## What `oh-my-pi` did

The `oh-my-pi` codebase implements the direct network provider:

- `packages/ai/src/providers/devin.ts` streams to `https://server.codeium.com` using the Connect protocol, protobuf `GetChatMessage` requests, gzip framing, and a JWT from `GetUserJwt`.
- `packages/ai/src/registry/oauth/devin.ts` runs a PKCE OAuth flow against `https://app.devin.ai` and exchanges the code at `https://api.devin.ai/auth/cli/token`.
- `packages/catalog/src/discovery/devin.ts` fetches the model catalog via the unary Connect RPC `GetCliModelConfigs`.

That implementation is valuable reference material, but it is not a CLI wrapper. Reproducing it in repowise means porting ~600+ lines of TypeScript, a large set of protobuf definitions, and the variant-collapse reasoning effort routing into Python.

## Why `devin_cli` first

1. **Familiar pattern.** Repowise already has `codex_cli` and `opencode` providers that shell out to a local agent CLI. Adding `devin_cli` uses the same registry, CLI selection, validation, and test patterns.
2. **Low dependency footprint.** No new Python packages, no protobuf code generation, no OAuth server.
3. **Fast validation.** A single `devin -p` call is enough to prove the provider works end-to-end.
4. **Good enough for docs generation.** Repowise primarily needs markdown output from a system + user prompt. Plain-text stdout satisfies that.

The main downsides are the lack of real token counts and the CLI's potential to make file edits depending on its permission mode. The implementation will choose a safe non-interactive mode and add "do not edit files" guidance to the prompt.

## `devin_cli` design

### How it runs

The provider will invoke the Devin CLI in non-interactive print mode:

```bash
devin -p --prompt-file <tmpfile> --cd /absolute/path/to/repo --model <model> --respect-workspace-trust false --permission-mode <safe-mode>
```

- `-p` / `--print` — single-turn, print the response and exit.
- `--prompt-file <tmpfile>` — avoids command-line length limits and shell-escaping issues.
- `--cd /absolute/path/to/repo` — makes the CLI reason about the target repository.
- `--model <model>` — only passed when the user selects a specific model; `devin_cli/default` omits the flag.
- `--respect-workspace-trust false` — required for non-interactive runs; otherwise the CLI can hang on a trust prompt.
- `--permission-mode <safe-mode>` — will be chosen to avoid unsolicited file edits.

The combined `system_prompt` and `user_prompt` are written to a temp file. The provider parses stdout as the response and sets `usage["estimated"] = True` because the CLI does not emit token counts.

### Model discovery

```bash
devin models list --format json
```

The provider will attempt to parse the JSON output and produce `ProviderModelOption` entries. The exact JSON shape is not documented, so the parser will be defensive and fall back to a single `devin_cli/default` option if it cannot understand the output.

### Registry and wiring

- Add `devin_cli.py` under `packages/core/src/repowise/core/providers/llm/`.
- Register it in `packages/core/src/repowise/core/providers/llm/registry.py` as a built-in provider.
- Add it to the CLI provider selection and validation (`provider_selection.py`, `helpers.py`).
- Add it to the server provider catalog (`provider_config.py`).
- Add it to the web settings UI (`provider-section.tsx`).
- Add unit tests in `tests/unit/test_providers/test_devin_cli_provider.py` following `test_codex_cli_provider.py`.

## Future: direct `devin` network provider

If real token usage, streaming, or reasoning effort control become important, the next step is to port the `oh-my-pi` network client. That provider would be named `devin` (not `devin_cli`) and would:

- Send `GetUserJwt` to exchange a `DEVIN_API_KEY` session token for a JWT.
- Open a Connect streaming session to `https://server.codeium.com/exa.api_server_pb.ApiServerService/GetChatMessage`.
- Serialize protobuf `GetChatMessageRequest` messages with gzip framing.
- Parse the streamed `GetChatMessageResponse` frames for text, thinking, tool calls, and usage.
- Discover models via `GetCliModelConfigs`.
- Route reasoning effort by selecting sibling model IDs, matching `oh-my-pi`'s variant-collapse behavior.

This is intentionally out of scope for the first milestone.

## Official Devin docs

- [Devin CLI](https://docs.devin.ai/cli)
- [Devin CLI commands and flags](https://docs.devin.ai/cli/reference/commands)
- [Devin CLI models](https://docs.devin.ai/cli/models)
