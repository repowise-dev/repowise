# Codebase Chat: Technical Reference

The codebase chat feature lets users have an interactive conversation with their
codebase. The agent uses whichever LLM provider the user has configured and
calls the same tools the repository's MCP server exposes (the ten task-shaped
tools by default), plus three chat-only tools (`show_view`, `chart`,
`find_suspects`). It streams responses back to the browser in real time,
showing tool calls as they happen and rendering results in an artifact panel.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Database Schema](#2-database-schema)
3. [ChatProvider Protocol](#3-chatprovider-protocol)
4. [Tool Registry](#4-tool-registry)
5. [Provider Configuration](#5-provider-configuration)
6. [SSE Streaming Protocol](#6-sse-streaming-protocol)
7. [Agentic Loop](#7-agentic-loop)
8. [REST API Endpoints](#8-rest-api-endpoints)
9. [Frontend Architecture](#9-frontend-architecture)
10. [Provider-Specific Notes](#10-provider-specific-notes)

---

## 1. Architecture Overview

```
User types question
        |
        v
POST /api/repos/{repo_id}/chat/messages
        |
        v
+-- ChatSession (router formats SSE) --+
|                                       |
|  1. Create/load conversation          |
|  2. Save user message to DB           |
|  3. Build LLM message history         |
|  4. Call provider.stream_chat()  <----+---- tool_executor callback
|        |                              |
|        v                              |
|  5. Stream text_delta events -------> SSE to browser
|  6. On tool_start:                    |
|     - Execute tool (or provider       |
|       executes internally)            |
|     - Emit tool_result event -------> SSE to browser
|  7. If tool calls found:             |
|     - Append to history, loop to 4   |
|  8. If no tool calls:                |
|     - Save assistant message to DB   |
|     - Emit done event                |
+---------------------------------------+
```

The agentic loop lives in `ChatSession` (`packages/server/src/repowise/server/chat_session.py`),
which imports nothing from FastAPI: the HTTP routes format its events as SSE, and
an in-process caller uses the class directly. For most providers the session
runs the tool calls itself. Gemini runs the loop inside `stream_chat()` using
native Content objects to preserve thought signatures, calling the session's
`tool_executor` callback for each tool.

---

## 2. Database Schema

Two tables, created in migration `0005_chat_conversations.py` and extended by later migrations:

### `conversations`

| Column | Type | Notes |
|--------|------|-------|
| `id` | `String(32)` PK | UUID hex |
| `repository_id` | `String(32)` FK | CASCADE delete |
| `title` | `Text` | First 6 words of the question, retitled by the opening turn from what it read |
| `pinned` | `Boolean` | |
| `deleted_at` | `DateTime(tz)` | Soft delete; a conversation can be restored |
| `namespace`, `external_ref` | `String` | A headless caller's thread key, unique together |
| `scope_json` | `Text` | `{"kind": "repo"` or `"workspace", "alias": ...}`; NULL reads as repo |
| `principal_id` | `String(256)` | The principal that opened a headless session |
| `policy_json` | `Text` | The `SessionPolicy` frozen when the session opened |
| `input_tokens`, `output_tokens`, `turns` | `Integer` | Running totals the policy's budgets check |
| `created_at` | `DateTime(tz)` | |
| `updated_at` | `DateTime(tz)` | Auto-updated on new messages |

**Index:** `ix_conversations_repo_updated` on `(repository_id, updated_at)`

### `chat_messages`

| Column | Type | Notes |
|--------|------|-------|
| `id` | `String(32)` PK | UUID hex |
| `conversation_id` | `String(32)` FK | CASCADE delete |
| `role` | `String(32)` | `user` or `assistant` |
| `content_json` | `Text` | JSON blob (see below) |
| `idempotency_key` | `String(128)` | On a user message: a retry with the same key returns the stored reply |
| `created_at` | `DateTime(tz)` | |

**Index:** `ix_chat_messages_conv_created` on `(conversation_id, created_at)`

### Message content format

**User messages:**
```json
{"text": "What does the auth module do?"}
```

**Assistant messages:**
```json
{
  "text": "The auth module handles...",
  "tool_calls": [
    {
      "id": "call_abc123",
      "name": "get_context",
      "arguments": {"targets": ["src/auth"]},
      "result": { ... }
    }
  ]
}
```

---

## 3. ChatProvider Protocol

Defined in `packages/core/src/repowise/core/providers/llm/base.py`.

The existing `BaseProvider.generate()` is untouched. A new `ChatProvider`
protocol class (using `typing.Protocol` + `@runtime_checkable`) adds streaming
chat with tool use as an opt-in capability.

```python
@runtime_checkable
class ChatProvider(Protocol):
    def stream_chat(
        self,
        messages: list[dict],          # OpenAI-format message list
        tools: list[dict],             # OpenAI-format tool definitions
        system_prompt: str,
        max_tokens: int = 8192,
        temperature: float = 0.7,
        request_id: str | None = None,
        tool_executor: Any | None = None,  # async callable(name, args) -> dict
    ) -> AsyncIterator[ChatStreamEvent]: ...
```

**Supporting dataclasses:**

- `ChatToolCall(id, name, arguments)`: a tool call the LLM wants to make
- `ChatStreamEvent(type, text?, tool_call?, tool_result_data?, stop_reason?, input_tokens, output_tokens, content_blocks?)`: a single event in the stream

**Event types:**

| `type` | Populated fields | Meaning |
|--------|-----------------|---------|
| `text_delta` | `text` | Incremental text token(s) |
| `tool_start` | `tool_call` | LLM wants to call a tool |
| `tool_result` | `tool_call`, `tool_result_data` | Tool executed (by provider internally) |
| `usage` | `input_tokens`, `output_tokens` | Token usage update |
| `assistant_content` | `content_blocks` | The provider's native assistant content, replayed on the next call |
| `stop` | `stop_reason` | Generation ended (`end_turn`, `tool_use`, `max_tokens`) |

**Implementations:** Anthropic, OpenAI, Gemini, Ollama, LiteLLM, OpenRouter, and
the OpenAI-compatible providers (DeepSeek, Eden AI, Kimi). All accept the
`tool_executor` parameter; only Gemini uses it (for thought signature handling).
The agent CLI providers (Claude Code, Codex, Copilot, OpenCode) generate pages
but do not implement `stream_chat()`, and the chat route refuses them with a
422.

---

## 4. Tool Registry

Defined in `packages/server/src/repowise/server/chat_tools.py`, a thin adapter
over the canonical MCP tool registry. Chat keeps no tool list of its own: each
turn it asks `selected_tool_entries(repo_path)`
(`mcp_server/_tool_selection.py`), the same selection the MCP server lists for
external clients. That is the repository's configured surface: the ten
canonical tools by default, `list_repos` added in a workspace, and whatever
`mcp.tools` in `.repowise/config.yaml` selects. `available_when` predicates are
re-evaluated every turn.

The tools a model is shown, and the only ones it can run, are:

1. the configured MCP surface (`ToolBackend.catalog_for`),
2. narrowed to the principal's tool scope (`authorize_tool`),
3. narrowed by the session's `SessionPolicy` (`tool_allowlist` and `safety_ceiling`),
4. plus the chat-only tools the policy admits.

**Key functions and types:**

| Name | Purpose |
|------|---------|
| `get_tool_catalog(repo_path, principal)` | The configured surface as `ChatToolContract`s, each with FastMCP's generated description and parameter schema |
| `tool_schemas(catalog)`, `get_tool_schemas_for_llm(...)` | OpenAI-format tool definitions for the LLM |
| `execute_tool(name, args, ...)` | Runs a tool only when it is in the configured surface; refuses a `mutating` tool without confirmation; pins a repo-restricted principal to its repo |
| `LocalToolBackend` | The default `ToolBackend`: this server's MCP surface, bound to one session's repository and principal |
| `init_tool_state(...)`, `set_tool_workspace(...)` | Bridge FastAPI app state and workspace state to the MCP module globals both surfaces read |

**Artifact types.** Each registry entry carries its own `artifact_type`,
`presentation` and `evidence_basis` (set on its `@mcp.tool(...)` decorator), and
the frontend renders the result by that type. For the canonical ten:

| Tool | Artifact Type |
|------|--------------|
| `get_answer` | `answer` |
| `search_codebase` | `search_results` |
| `get_context` | `context` |
| `get_symbol` | `source` |
| `get_overview` | `overview` |
| `get_why` | `decisions` |
| `get_risk` | `risk` |
| `get_change_risk` | `change_risk` |
| `get_dead_code` | `dead_code` |
| `get_health` | `health` |

**Chat-only tools.** These are never MCP tools. Each is read-only and offered
only when the policy admits it:

| Tool | Offered when | What it does |
|------|--------------|--------------|
| `show_view` | the catalog has at least one tool | Builds a table or chart from a tool result already in the conversation (`chat_views.py`) |
| `chart` | `show_view` is offered and the backend is local | Draws a repository dataset in one call (`chat_chart.py`) |
| `find_suspects` | the backend is local and `get_context` is in the catalog | Lists the changes before a symptom, in reading order (`chat_suspects.py`) |

---

## 5. Provider Configuration

Defined in `packages/server/src/repowise/server/provider_config.py`, on top of
the resolver every entry point shares (`core/providers/llm/resolve.py`).

**Provider, model and base URL** (first match wins):
1. A per-request override from the chat model picker
2. The repo's `.repowise/config.yaml` (`PATCH /api/providers/active` with `repo_id` writes here)
3. The global default in `provider_config.json` (`PATCH` without `repo_id`)
4. `REPOWISE_PROVIDER` / `REPOWISE_MODEL` and the provider's `*_BASE_URL`
5. The provider's default model, or auto-detection from the keys present

**API keys:** environment variable, then the repo's `.repowise/.env`, then the
key stored in `provider_config.json`. `GET /api/providers` reports the resolved
values with the layer each came from (`active.provenance`).

**Chat-capable providers:** Anthropic, OpenAI, Gemini, Ollama (local, no key),
LiteLLM, OpenRouter, DeepSeek, Eden AI, Kimi.

---

## 6. SSE Streaming Protocol

The chat endpoint returns `Content-Type: text/event-stream`. Each event is:

```
event: data
data: {"type": "...", ...}

```

**Event shapes** (the session also emits `grounding` for the read a page
context justifies before the first model call, `view` for a table or chart that
`show_view` or `chart` built, `truncated` when a limit ends the turn, and
`suggestions` for follow-up questions):

```jsonc
// Incremental text from the LLM
{"type": "text_delta", "text": "The auth module..."}

// LLM wants to call a tool
{"type": "tool_start", "tool_id": "call_123", "tool_name": "get_context", "input": {"targets": ["src/auth"]}}

// Tool execution completed
{"type": "tool_result", "tool_id": "call_123", "tool_name": "get_context", "summary": "Context for 1 target(s)", "artifact": {"type": "wiki_page", "data": {...}}}

// Stream complete
{"type": "done", "conversation_id": "abc123", "message_id": "def456"}

// Error
{"type": "error", "message": "Provider error: ..."}
```

**Headers:** `Cache-Control: no-cache`, `X-Accel-Buffering: no`, `Connection: keep-alive`

**Retry:** `retry: 3000` sent at stream start.

**Terminal event:** every stream ends with `done` or `error` on the `data`
channel. `useChat` switches on `type` and nothing else, so an event sent on a
different channel, or without a `type`, is dropped: the client then sees a
stream that simply stopped mid-answer. The client settles its own state when
the reader ends without a terminal event, but the server still owes it one.

---

## 7. Agentic Loop

The loop runs up to `SessionPolicy.max_tool_loops` model calls per turn (20 by
default). The web chat opens sessions under `WEB_POLICY` (every configured tool;
a `mutating` one is still refused at execution without confirmation). A
headless session defaults to `DEFAULT_POLICY` (read-only tools), and a caller may
narrow it but never widen it. Token budgets (`max_turn_tokens`,
`max_session_tokens`) and `max_turns` also stop a turn.

```
for each iteration:
    1. Call provider.stream_chat(messages, tools, system_prompt, tool_executor)
    2. Collect text_delta events -> stream to client
    3. Collect tool_start events -> stream to client
    4. Collect tool_result events (from internal execution) -> stream to client
    5. If there are pending tool calls (not internally executed):
       a. Execute each tool
       b. Emit tool_result to client
       c. Append assistant + tool results to message history
       d. Continue loop
    6. If no tool calls: break
if every call ended in a tool call: emit truncated (loop_cap)
```

After the loop, the assistant message (text, all tool calls with results,
provider, model, usage, follow-ups and views) is saved to the database and a
`done` event is emitted. A cancelled or failed turn stores nothing; the question
stays.

**Charts in chat:** the chat-only `chart` tool draws a repository dataset
(files, findings, changes, health_trend, coverage, dead_code, dependencies,
layers) in one call: the server reads every row from the index (cached per
index state), filters, aggregates and draws it as a bar, horizontal bar,
stacked bar, line, area, donut, scatter, treemap, heatmap or table. Its receipt
carries the exact totals, shares and averages, every repair it made to the spec,
a `kind_note` when the asked kind did not fit, and a `suggested_spec` on error.
`show_view` draws a tool result already in the conversation (by `artifact_id`
or `latest:<tool_name>`), redraws an earlier view by its id, and lets named
views take `options.kind`. The same guards apply: no person fields, no risk
score as a plotted value, and attribution figures only through `changes.provenance`.

---

## 8. REST API Endpoints

### Chat

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/repos/{repo_id}/chat/messages` | SSE stream: send a message and get a streaming response |
| `GET` | `/api/repos/{repo_id}/chat/suggestions` | Questions a page has earned, from the read a first question makes (no model call) |
| `GET` | `/api/repos/{repo_id}/chat/conversations` | List conversations for a repo |
| `GET` | `/api/repos/{repo_id}/chat/conversations/{id}` | Get conversation with all messages |
| `PATCH` | `/api/repos/{repo_id}/chat/conversations/{id}` | Update a conversation (title, pin) |
| `DELETE` | `/api/repos/{repo_id}/chat/conversations/{id}` | Delete a conversation (soft delete) |
| `POST` | `/api/repos/{repo_id}/chat/conversations/{id}/restore` | Restore a deleted conversation |
| `POST` | `/api/repos/{repo_id}/chat/conversations/{id}/fork` | Fork a conversation |
| `GET`, `PATCH` | `/api/repos/{repo_id}/chat/conversations/{id}/artifacts/{artifact_id}` | Read or update one stored artifact |

### Headless sessions

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/chat/sessions` | Open (or resume) a session under a requested policy, narrowed from `DEFAULT_POLICY` |
| `GET` | `/api/chat/sessions` | List sessions |
| `POST` | `/api/chat/sessions/{session_id}/messages` | Run one turn: SSE when `stream` is true, else the turn's result; an idempotency key makes a retry safe |

**POST body:**
```json
{
  "message": "What does the auth module do?",
  "conversation_id": null,
  "provider": null,
  "model": null
}
```

`conversation_id`: omit or `null` to start a new conversation.
`provider` / `model`: optional per-request overrides.

### Providers

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/providers` | List all providers with status and active selection |
| `PATCH` | `/api/providers/active` | Set active provider, model and base URL |
| `GET` | `/api/providers/{id}/models` | The provider's live model list (cached briefly), or its built-in list with `error`; `?base_url=` tests an unsaved endpoint |
| `POST` | `/api/providers/{id}/key` | Store an API key |
| `DELETE` | `/api/providers/{id}/key` | Remove an API key |

---

## 9. Frontend Architecture

### API Layer

- `packages/api-client/src/chat.ts` (re-exported by `packages/web/src/lib/api/chat.ts`): conversation, message, session and artifact calls; the message call returns the raw `Response` for SSE reading
- `packages/web/src/lib/api/providers.ts`: provider listing, live model lists, active provider and key management

### Hooks (`packages/web/src/lib/hooks/`)

- `useChat(repoId)` (`use-chat.ts`): the chat state machine. Uses `fetch` + `ReadableStream` (not `EventSource`, which is GET-only). Manages messages, streaming state, conversation ID, artifacts, error handling, and abort control.
- `useProviders()`, `useProviderModels()`, `useProviderValidation()`: provider state for the model picker and settings.

### Components

Shared chat components live in `packages/ui/src/chat/` (`ChatInterface`,
`ChatDock`, `ChatMessage`, `ChatMarkdown`, `ToolCallBlock`, `ArtifactPanel`,
`ChatSuggestions`, `SourceCitations`, view parts). The web app wires them to its
API in `packages/web/src/components/chat/` (`RepositoryChatDock`,
`RepositoryChatProvider`, `ModelSelector`, `ConversationHistory`).

| Component | Purpose |
|-----------|---------|
| `ChatInterface` | Main container: empty state (greeting, suggestions, model selector) and active state (message list and composer) |
| `ChatDock` | The chat docked on every repo page, minimized, compact or expanded |
| `ChatMessage` | Renders a user bubble or an assistant message (tool blocks and markdown) |
| `ChatMarkdown` | Client-side markdown renderer using `react-markdown` + `remark-gfm` with design token styling |
| `ToolCallBlock` | Inline tool call: running, done collapsed (summary), done expanded (input and output) |
| `ArtifactPanel` | Panel with tabs for multiple artifacts, rendered by artifact type |
| `ModelSelector` | Per-conversation model menu: lists the chosen provider's live models when opened, takes a typed id, marks the repo default. It never writes config |
| `ConversationHistory` | Dropdown listing past conversations with delete and new-conversation actions |

### Page Structure

The repo layout (`/repos/[id]/layout.tsx`) mounts `RepositoryChatDock`, so chat
is available on every repo page and carries that page's context into the
question. `/repos/[id]/chat` is the full-page chat. The repo root
(`/repos/[id]`) redirects to the overview.

---

## 10. Provider-Specific Notes

### Anthropic

Uses `client.messages.stream()` with native Anthropic message format. Converts
OpenAI-format messages to Anthropic format (tool results as `user` role with
`tool_result` content blocks, tool calls as `tool_use` content blocks). The
agentic loop runs in `ChatSession`.

### OpenAI

Uses `client.chat.completions.create(stream=True)`. Native OpenAI format,
minimal conversion needed. Tool call fragments are accumulated across stream
chunks and emitted as complete `tool_start` events. The agentic loop runs in
`ChatSession`.

### Gemini

Uses `client.models.generate_content()` (non-streaming, in a thread pool).
**Runs the agentic loop internally** via the `tool_executor` callback to
preserve `thought_signature` on function call parts. Gemini's API requires
these signatures when replaying function calls in conversation history; the
OpenAI-format round-trip through the session would lose them. Native `Content`
objects are used throughout the internal loop.

### Ollama

Uses the OpenAI-compatible endpoint (`localhost:11434/v1`) via `AsyncOpenAI`.
Same streaming pattern as OpenAI. The agentic loop runs in `ChatSession`.

### LiteLLM

Uses `litellm.acompletion(stream=True)`. OpenAI-compatible streaming. The
agentic loop runs in `ChatSession`.
